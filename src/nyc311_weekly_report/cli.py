from dotenv import load_dotenv
load_dotenv()


import argparse
from datetime import datetime, timezone

from pathlib import Path

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

    p_analyze = sub.add_parser("analyze", help="Analyze latest raw snapshot and save weekly metrics.")

    p_report = sub.add_parser("report", help="Generate a Markdown weekly report from the latest metrics.")


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

    if args.cmd == "report":
        from nyc311_weekly_report.report import generate_report
        out = generate_report()
        print(f"Saved report to {out.resolve()}")
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

    parser.print_help()
