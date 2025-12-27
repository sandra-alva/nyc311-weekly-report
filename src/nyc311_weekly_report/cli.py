from dotenv import load_dotenv
load_dotenv()


import argparse
import json
from datetime import datetime, timezone

from pathlib import Path
import os

from nyc311_weekly_report.analyze import (
    load_latest_raw,
    compute_weekly_metrics,
    save_metrics,
)
from nyc311_weekly_report.config import RAW_DIR
from nyc311_weekly_report.ingest import fetch_last_n_days_soda3, save_raw_snapshot
from nyc311_weekly_report.soda3 import soda3_query



def main() -> None:
    parser = argparse.ArgumentParser(
        prog="nyc311",
        description="NYC 311 Weekly Report pipeline (WIP).",
    )
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_sample = sub.add_parser("sample", help="Fetch 5 latest NYC 311 rows (smoke test).")
    p_sample.add_argument("--limit", type=int, default=5)

    p_ingest = sub.add_parser("ingest", help="Fetch last 7 days of NYC 311 data and save raw snapshot.")
    p_ingest.add_argument("--days", type=int, default=7)
    p_ingest.add_argument("--limit", type=int, default=50000)

    p_trend = sub.add_parser("trend", help="Fetch daily aggregates for trend analysis.")
    p_trend.add_argument("--days", type=int, default=365)
    p_trend.add_argument("--top-complaints", type=int, default=5, help="How many complaint types to track (default 5).")
    p_trend.add_argument("--skip-borough", action="store_true", help="Skip borough-level daily counts.")
    p_trend.add_argument("--skip-complaints", action="store_true", help="Skip top complaint-type daily counts.")

    p_analyze = sub.add_parser("analyze", help="Analyze latest raw snapshot and save weekly metrics.")

    p_trend_analyze = sub.add_parser("trend-analyze", help="Compute trend metrics (moving avg, spikes, chart).")

    p_dashboard = sub.add_parser("dashboard", help="Compute dashboard metrics and movers.")
    p_dashboard.add_argument("--weekly-path", default="data/processed/weekly_metrics.json")
    p_dashboard.add_argument("--trend-path", default="data/processed/trend_metrics.json")
    p_dashboard.add_argument("--trend-daily-path", default="data/processed/trend_daily.json")
    p_dashboard.add_argument("--out", default="data/processed/dashboard.json")

    p_charts = sub.add_parser("charts", help="Generate charts for the dashboard.")
    p_charts.add_argument("--dashboard-path", default="data/processed/dashboard.json")
    p_charts.add_argument("--trend-daily-path", default="data/processed/trend_daily.json")
    p_charts.add_argument("--assets-dir", default="site/assets")

    p_narrate = sub.add_parser("narrate", help="Generate structured narrative summary via OpenAI.")
    p_narrate.add_argument("--weekly-path", default="data/processed/weekly_metrics.json")
    p_narrate.add_argument("--trend-path", default="data/processed/trend_metrics.json")
    p_narrate.add_argument("--dashboard-path", default="data/processed/dashboard.json")
    p_narrate.add_argument("--out-json", default="data/processed/narrative.json")
    p_narrate.add_argument("--out-md", default="data/processed/narrative.md")
    p_narrate.add_argument("--model", default=None, help="Override OPENAI_MODEL/default model.")

    p_report = sub.add_parser("report", help="Generate a Markdown weekly report from the latest metrics.")

    p_pub = sub.add_parser("publish", help="Render the latest Markdown report into site/ as HTML.")
    p_build = sub.add_parser("build-site", help="Run dashboard, charts, narrate, report, and publish.")
    p_build.add_argument("--model", default=None, help="Override OPENAI_MODEL/default model.")
    p_refresh = sub.add_parser("refresh", help="Run full pipeline: ingest → publish.")
    p_refresh.add_argument("--weekly-days", type=int, default=7, help="Days to fetch for weekly ingest.")
    p_refresh.add_argument("--trend-days", type=int, default=365, help="Days to fetch for trend ingest.")
    p_refresh.add_argument("--skip-narrate", action="store_true", help="Skip LLM narrative (no OPENAI_API_KEY needed).")
    p_refresh.add_argument("--model", default=None, help="Override OPENAI_MODEL/default model.")


    parser.add_argument("--version", action="store_true", help="Print version and exit.")
    args = parser.parse_args()

    if args.version:
        print("nyc311-weekly-report v0.0.1")
        return

    if args.cmd == "sample":
        data = soda3_query(
            query=(
                "SELECT unique_key, created_date, complaint_type "
                "ORDER BY created_date DESC "
                f"LIMIT {args.limit}"
            ),
            page_number=1,
            page_size=args.limit,
        )

        print(f"Fetched {len(data)} rows.")
        if data:
            print("Keys:", list(data[0].keys()))
            print("created_date example:", data[0].get("created_date"))
            print("First row:", data[0])
        return

    if args.cmd == "ingest":
        rows, start_str, end_str = fetch_last_n_days_soda3(n_days=args.days, page_size=5000)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = RAW_DIR / f"nyc311_last_{args.days}_days_{ts}.json"
        payload = {
            "meta": {
                "dataset": "erm2-nwe9",
                "days": args.days,
                "start": start_str,   # we’ll expose these from ingest
                "end": end_str,
                "timezone": "America/New_York",
                "row_count": len(rows),
            },
            "rows": rows,
        }
        save_raw_snapshot(payload, out)
        print(f"Saved {len(rows)} rows to {out}")
        return

    if args.cmd == "trend":
        from nyc311_weekly_report.trend import ingest_trends, TREND_RAW_PATH

        payload = ingest_trends(
            days=args.days,
            include_borough=not args.skip_borough,
            include_complaints=not args.skip_complaints,
            top_complaints=args.top_complaints,
            out_path=TREND_RAW_PATH,
        )
        print(f"Saved trend aggregates to {TREND_RAW_PATH.resolve()}")
        print(
            f"Daily totals: {len(payload.get('daily_totals', []))} rows; "
            f"Top complaints tracked: {len(payload.get('top_complaints', []))}"
        )
        return

    if args.cmd == "analyze":
        meta, rows = load_latest_raw()
        metrics = compute_weekly_metrics(rows, meta=meta)

        out = Path("data/processed/weekly_metrics.json")
        save_metrics(metrics, out)

        print(f"Saved weekly metrics to {out.resolve()}")
        print("Total rows:", metrics["total_rows"])
        print("Top 5 complaints:", metrics["top_complaints"][:5])
        return

    if args.cmd == "trend-analyze":
        from nyc311_weekly_report.trend import (
            analyze_trends,
            load_trend_raw,
            save_trend_metrics,
            TREND_METRICS_PATH,
        )

        trend_raw = load_trend_raw()
        trend_metrics = analyze_trends(trend_raw)
        save_trend_metrics(trend_metrics, TREND_METRICS_PATH)

        summary = trend_metrics.get("last_week_vs_baseline", {})
        coverage = trend_metrics.get("coverage", {})
        freshness = trend_metrics.get("freshness", {})
        overall_spike = trend_metrics.get("overall_spike", {})
        print(f"Saved trend metrics to {TREND_METRICS_PATH.resolve()}")
        print(
            "Coverage:",
            f"{coverage.get('actual_days')} / {coverage.get('expected_days')} days",
            f"{coverage.get('first_day')}→{coverage.get('last_day')}",
        )
        print(
            "Freshness:",
            freshness.get("warning") or f"Latest {freshness.get('last_day_age_days')} days old"
        )
        print(
            "Last week total:",
            summary.get("last_week_total"),
            "| Trailing 8-week avg:",
            summary.get("trailing_8_week_avg"),
        )
        print(
            "Overall spike:",
            "YES" if overall_spike.get("is_spike") else "no",
            "| z=",
            overall_spike.get("spike_z"),
            "| pct vs mean=",
            overall_spike.get("spike_pct_vs_mean"),
        )
        return
    if args.cmd == "dashboard":
        from nyc311_weekly_report.dashboard import main as dashboard_main

        out = dashboard_main(
            weekly_path=Path(args.weekly_path),
            trend_path=Path(args.trend_path),
            trend_daily_path=Path(args.trend_daily_path),
            out_path=Path(args.out),
        )
        print(f"Saved dashboard metrics to {Path(out).resolve()}")
        return

    if args.cmd == "charts":
        from nyc311_weekly_report.charts import generate_charts

        charts = generate_charts(
            dashboard_path=Path(args.dashboard_path),
            trend_daily_path=Path(args.trend_daily_path),
            assets_dir=Path(args.assets_dir),
        )
        if charts:
            print("Generated charts:")
            for name, path in charts.items():
                print(f"- {name}: {path}")
        else:
            print("No charts generated (missing inputs).")
        return
    if args.cmd == "narrate":
        from nyc311_weekly_report.narrate import (
            build_messages,
            build_payload,
            call_openai_for_json,
            load_trend_metrics_optional,
            load_weekly_metrics,
            render_markdown,
            save_json,
            save_md,
            validate_narrative_schema,
            load_dashboard_metrics_optional,
        )

        dashboard = load_dashboard_metrics_optional(Path(args.dashboard_path))
        weekly = load_weekly_metrics(Path(args.weekly_path)) if dashboard is None else None
        trend = load_trend_metrics_optional(Path(args.trend_path)) if dashboard is None else None
        payload = build_payload(dashboard=dashboard, weekly=weekly, trend=trend)
        messages = build_messages(payload)

        try:
            raw_obj = call_openai_for_json(messages, model=args.model)
            narrative_obj = validate_narrative_schema(raw_obj, payload=payload)
        except Exception as exc:
            raise SystemExit(f"narrate failed: {exc}") from exc

        out_json = Path(args.out_json)
        out_md = Path(args.out_md)
        save_json(out_json, narrative_obj)
        save_md(out_md, render_markdown(narrative_obj))
        print(f"Wrote {out_json.resolve()}")
        print(f"Wrote {out_md.resolve()}")
        return

    if args.cmd == "report":
        from nyc311_weekly_report.report import generate_report
        out = generate_report()
        print(f"Saved report to {out.resolve()}")
        return


    if args.cmd == "publish":
        from nyc311_weekly_report.publish import publish_latest
        index_path, report_path = publish_latest()
        print(f"Wrote {index_path.resolve()}")
        print(f"Wrote {report_path.resolve()}")
        return
    if args.cmd == "refresh":
        from nyc311_weekly_report.dashboard import main as dashboard_main
        from nyc311_weekly_report.charts import generate_charts
        from nyc311_weekly_report.narrate import (
            build_messages,
            build_payload,
            call_openai_for_json,
            load_dashboard_metrics_optional,
            load_trend_metrics_optional,
            load_weekly_metrics,
            render_markdown,
            save_json,
            save_md,
            validate_narrative_schema,
        )
        from nyc311_weekly_report.report import generate_report
        from nyc311_weekly_report.publish import publish_latest
        from nyc311_weekly_report.trend import (
            ingest_trends,
            TREND_RAW_PATH,
            analyze_trends,
            save_trend_metrics,
            TREND_METRICS_PATH,
        )

        def _log(msg: str) -> None:
            print(f"[refresh] {msg}")

        try:
            _log(f"Ingesting last {args.weekly_days} days...")
            rows, start_str, end_str = fetch_last_n_days_soda3(n_days=args.weekly_days, page_size=5000)
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            out = RAW_DIR / f"nyc311_last_{args.weekly_days}_days_{ts}.json"
            payload = {
                "meta": {
                    "dataset": "erm2-nwe9",
                    "days": args.weekly_days,
                    "start": start_str,
                    "end": end_str,
                    "timezone": "America/New_York",
                    "row_count": len(rows),
                },
                "rows": rows,
            }
            save_raw_snapshot(payload, out)

            _log("Analyzing weekly metrics...")
            meta, rows_loaded = load_latest_raw()
            metrics = compute_weekly_metrics(rows_loaded, meta=meta)
            weekly_out = Path("data/processed/weekly_metrics.json")
            save_metrics(metrics, weekly_out)

            _log(f"Ingesting trend data ({args.trend_days} days)...")
            ingest_trends(
                days=args.trend_days,
                include_borough=True,
                include_complaints=True,
                top_complaints=5,
                out_path=TREND_RAW_PATH,
            )

            _log("Analyzing trends...")
            trend_raw = json.loads(TREND_RAW_PATH.read_text(encoding="utf-8"))
            trend_metrics = analyze_trends(trend_raw)
            save_trend_metrics(trend_metrics, TREND_METRICS_PATH)

            _log("Building dashboard...")
            dashboard_main()

            _log("Generating charts...")
            generate_charts()

            if not args.skip_narrate:
                api_key = os.getenv("OPENAI_API_KEY")
                if not api_key:
                    _log("OPENAI_API_KEY not set; skipping narrate.")
                else:
                    try:
                        _log("Running narrate via OpenAI...")
                        dashboard = load_dashboard_metrics_optional(Path("data/processed/dashboard.json"))
                        weekly = load_weekly_metrics(Path("data/processed/weekly_metrics.json")) if dashboard is None else None
                        trend = load_trend_metrics_optional(Path("data/processed/trend_metrics.json")) if dashboard is None else None
                        payload = build_payload(dashboard=dashboard, weekly=weekly, trend=trend)
                        messages = build_messages(payload)
                        raw_obj = call_openai_for_json(messages, model=args.model)
                        narrative_obj = validate_narrative_schema(raw_obj, payload=payload)
                        save_json(Path("data/processed/narrative.json"), narrative_obj)
                        save_md(Path("data/processed/narrative.md"), render_markdown(narrative_obj))
                    except Exception as exc:
                        _log(f"Narrate failed but continuing: {exc}")
            else:
                _log("Skipping narrate (per flag).")

            _log("Rendering report...")
            report_path = generate_report()
            _log(f"Report written to {report_path}")

            _log("Publishing site...")
            index_path, published_report = publish_latest()
            _log(f"Published site at {index_path}")
            _log(f"Report HTML at {published_report}")
        except Exception as exc:
            raise SystemExit(f"refresh failed: {exc}") from exc
        return
    if args.cmd == "build-site":
        from nyc311_weekly_report.dashboard import main as dashboard_main
        from nyc311_weekly_report.charts import generate_charts
        from nyc311_weekly_report.narrate import (
            build_messages,
            build_payload,
            call_openai_for_json,
            load_dashboard_metrics_optional,
            load_trend_metrics_optional,
            load_weekly_metrics,
            render_markdown,
            save_json,
            save_md,
            validate_narrative_schema,
        )
        from nyc311_weekly_report.report import generate_report
        from nyc311_weekly_report.publish import publish_latest

        dashboard_main()
        generate_charts()

        dashboard = load_dashboard_metrics_optional(Path("data/processed/dashboard.json"))
        weekly = None
        trend = None
        if dashboard is None:
            weekly = load_weekly_metrics()
            trend = load_trend_metrics_optional(Path("data/processed/trend_metrics.json"))
        payload = build_payload(dashboard=dashboard, weekly=weekly, trend=trend)
        messages = build_messages(payload)
        raw_obj = call_openai_for_json(messages, model=args.model)
        narrative_obj = validate_narrative_schema(raw_obj, payload=payload)
        save_json(Path("data/processed/narrative.json"), narrative_obj)
        save_md(Path("data/processed/narrative.md"), render_markdown(narrative_obj))

        report_path = generate_report()
        index_path, published_report = publish_latest()
        print(f"Built dashboard, charts, narrative, report.\nIndex: {index_path.resolve()}\nReport: {published_report.resolve()}\nReport (md): {report_path.resolve()}")
        return



    parser.print_help()
