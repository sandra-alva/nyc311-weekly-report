from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

PROCESSED_METRICS_PATH = Path("data/processed/weekly_metrics.json")
PROCESSED_DASHBOARD_PATH = Path("data/processed/dashboard.json")
REPORTS_DIR = Path("reports")
NARRATIVE_MD_PATH = Path("data/processed/narrative.md")
NY_TZ = ZoneInfo("America/New_York")


def load_metrics(path: Path = PROCESSED_METRICS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_dashboard_optional(path: Path = PROCESSED_DASHBOARD_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_dt(s: str | None) -> str:
    if not s:
        return "Unknown"
    return s.replace("T", " ").replace(".000", "")


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "n/a"
    return f"{pct:+.1f}%"


def _to_ny_date(s: str | None) -> datetime.date | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        return dt.astimezone(NY_TZ).date()
    except Exception:
        try:
            return datetime.strptime(s.split("T")[0], "%Y-%m-%d").date()
        except Exception:
            return None


def _report_image_path(path_str: str) -> str:
    path = Path(path_str)
    if path.parts and path.parts[0] == "site":
        path = Path(*path.parts[1:])
    return f"../{path}"


def _what_changed_lines(dashboard: dict[str, Any] | None) -> list[str]:
    if not dashboard:
        return []
    anomalies = (dashboard.get("anomalies") or {})
    spikes = (anomalies.get("daily_spikes") or {}).get("spikes") or []
    season = anomalies.get("complaint_seasonality") or {}
    inc = season.get("increases") or []
    dec = season.get("decreases") or []
    movers = (dashboard.get("complaint_movers") or {}).get("top_movers") or []

    lines: list[str] = []
    if spikes:
        top = spikes[0]
        z_val = top.get("z")
        z_str = f"{z_val:.1f}" if isinstance(z_val, (int, float)) else "n/a"
        lines.append(f"- Daily spike on {top.get('day')}: {top.get('count')} requests (z={z_str})")
    for entry in inc:
        z_val = entry.get("z_month")
        z_str = f"{z_val:.1f}" if isinstance(z_val, (int, float)) else "n/a"
        lines.append(
            f"- {entry.get('name','')} share higher than seasonal norm (z={z_str}, Δ={entry.get('delta_pp') or 0:+.1f}pp)"
        )
    for entry in dec:
        z_val = entry.get("z_month")
        z_str = f"{z_val:.1f}" if isinstance(z_val, (int, float)) else "n/a"
        lines.append(
            f"- {entry.get('name','')} share lower than seasonal norm (z={z_str}, Δ={entry.get('delta_pp') or 0:+.1f}pp)"
        )
    if movers:
        mv = movers[0]
        lines.append(
            f"- Biggest composition shift: {mv.get('name','Unknown')} ({mv.get('delta_pp') or 0:+.1f} percentage points vs baseline)"
        )
    if not lines:
        lines.append("- No statistically notable deviations this week; see seasonality chart for expected patterns.")
    return lines


def _z_label(z: float | None) -> str:
    if not isinstance(z, (int, float)):
        return ""
    az = abs(z)
    if az < 1:
        return "Typical"
    if az < 2:
        return "Moderate"
    if az < 3:
        return "High"
    return "Very unusual"


def _data_quality_lines(
    *,
    dashboard: dict[str, Any] | None,
    metrics: dict[str, Any],
    trend_metrics: dict[str, Any] | None,
) -> list[str]:
    lines: list[str] = []
    if dashboard:
        dq = (dashboard.get("headline") or {}).get("data_quality") or {}
        lines.append(
            f"- Weekly latest record observed: {dq.get('weekly_latest_created_date') or 'Unknown'}"
            + (f" (lag: {dq.get('weekly_lag_days')} days)" if dq.get("weekly_lag_days") is not None else "")
        )
        lines.append(
            "- Weekly day coverage: "
            + (
                f"{dq.get('weekly_unique_days_count')}/{dq.get('weekly_expected_days')} "
                f"(missing ~{dq.get('weekly_missing_days_est')} days)"
                if dq.get("weekly_unique_days_count") is not None and dq.get("weekly_expected_days") is not None
                else "Unknown"
            )
        )
        if dq.get("unspecified_borough_share") is not None:
            share_val = dq.get("unspecified_borough_share")
            lines.append(f"- Unknown / Unspecified (missing borough) share: {share_val:.2%}")
            if dq.get("unspecified_borough_baseline_z") is not None:
                z_val = dq.get("unspecified_borough_baseline_z")
                lines.append(f"- Unknown / Unspecified anomaly z={z_val:.2f}")
        if dq.get("trend_latest_day"):
            lines.append(
                f"- Trend latest day: {dq.get('trend_latest_day')}"
                + (f" (lag: {dq.get('trend_lag_days')} days)" if dq.get("trend_lag_days") is not None else "")
            )
        for warn in dq.get("warnings") or []:
            lines.append(f"- Warning: {warn}")
        return lines

    latest_created = metrics.get("weekly_latest_created_date")
    weekly_unique = metrics.get("weekly_unique_days_count")
    weekly_expected = metrics.get("weekly_expected_days")
    weekly_missing = metrics.get("weekly_missing_days_est")
    window_end_date = _to_ny_date(metrics.get("window_end"))
    latest_created_date = _to_ny_date(latest_created)
    lag_days_weekly = (
        (window_end_date - latest_created_date).days if window_end_date and latest_created_date else None
    )
    lines.append(
        f"- Weekly latest record observed: {latest_created or 'Unknown'}"
        + (f" (lag: {lag_days_weekly} days behind window end)" if lag_days_weekly is not None else "")
    )
    lines.append(
        "- Weekly day coverage: "
        + (
            f"{weekly_unique}/{weekly_expected} (missing ~{weekly_missing} days)"
            if weekly_unique is not None and weekly_expected is not None
            else "Unknown"
        )
    )
    if lag_days_weekly is not None and lag_days_weekly >= 2:
        lines.append(f"- Weekly data appears incomplete: latest record is {lag_days_weekly} days behind window end.")
    if trend_metrics:
        trend_cov = (trend_metrics.get("coverage") or {})
        trend_fresh = (trend_metrics.get("freshness") or {})
        t_last = trend_cov.get("last_day")
        t_warn = trend_fresh.get("warning")
        trend_age = trend_fresh.get("last_day_age_days")
        lines.append(
            "- Trend latest day: "
            + (t_last if t_last else "Unknown")
            + (f" (lag: {trend_age} days)" if trend_age is not None else "")
        )
        if t_warn:
            lines.append(f"- Trend data warning: {t_warn}")
    return lines


def build_markdown(
    metrics: dict[str, Any],
    trend_metrics: dict[str, Any] | None = None,
    dashboard: dict[str, Any] | None = None,
    narrative_text: str | None = None,
) -> str:
    total = metrics.get("total_rows", 0)
    dt_min = _fmt_dt(metrics.get("window_start") or metrics.get("created_date_min"))
    dt_max = _fmt_dt(metrics.get("window_end") or metrics.get("created_date_max"))

    headline = dashboard.get("headline") or {} if dashboard else {}
    if dashboard:
        window = dashboard.get("window") or {}
        dt_min = _fmt_dt(window.get("start") or dt_min)
        dt_max = _fmt_dt(window.get("end") or dt_max)
        total = headline.get("weekly_total") or total

    lines: list[str] = []
    lines.append("# NYC 311 Weekly Report")
    lines.append("")
    lines.append(f"**Window:** {dt_min} → {dt_max}")
    lines.append(f"**Total requests:** {int(total):,}")
    lines.append("")

    lines.append("## Data quality")
    lines.extend(_data_quality_lines(dashboard=dashboard, metrics=metrics, trend_metrics=trend_metrics))
    lines.append("")

    if narrative_text:
        lines.extend(narrative_text.strip().splitlines())
        lines.append("")

    if dashboard:
        changes = _what_changed_lines(dashboard)
        if changes:
            lines.append("## What changed this week")
            lines.extend(changes)
            lines.append("")

    reference_lines: list[str] = []
    vol_pct = headline.get("overall_volume_vs_baseline_pct")
    vol_z = headline.get("overall_volume_z")
    if total:
        reference_lines.append(f"- Weekly total: **{int(total):,}** requests")
    if vol_pct is not None or vol_z is not None:
        z_str = f"{vol_z:.2f}" if isinstance(vol_z, (int, float)) else "n/a"
        reference_lines.append(f"- Volume vs baseline: {_fmt_pct(vol_pct)} (z={z_str})")

    borough_movers = dashboard.get("borough_share") if dashboard else {}
    complaint_movers = dashboard.get("complaint_movers") if dashboard else {}
    boring = set((dashboard.get("boring_leaders") or {}).get("boring_types", [])) if dashboard else set()

    def _pick_mover(movers: list[dict[str, Any]]) -> dict[str, Any] | None:
        for mv in movers:
            if mv.get("name") in boring:
                continue
            return mv
        return movers[0] if movers else None

    comp_mv = _pick_mover((complaint_movers or {}).get("top_movers") or [])
    if comp_mv:
        delta = comp_mv.get("delta_pp")
        z_val = comp_mv.get("z")
        z_str = f"{z_val:.2f}" if isinstance(z_val, (int, float)) else "n/a"
        reference_lines.append(
            f"- Complaint mover: {comp_mv.get('name','Unknown')} "
            f"({_fmt_pct(delta)} vs baseline; z={z_str})"
        )

    borough_mv = _pick_mover((borough_movers or {}).get("top_movers") or [])
    if borough_mv:
        delta = borough_mv.get("delta_pp")
        z_val = borough_mv.get("z")
        z_str = f"{z_val:.2f}" if isinstance(z_val, (int, float)) else "n/a"
        reference_lines.append(
            f"- Borough mover: {borough_mv.get('name','Unknown')} "
            f"({_fmt_pct(delta)} vs baseline; z={z_str})"
        )

    if reference_lines:
        lines.append("## Reference metrics")
        lines.extend(reference_lines)
        lines.append("")

    charts = dashboard.get("charts") if dashboard else {}
    if charts:
        lines.append("## Charts")
        if charts.get("complaint_share_dumbbell"):
            lines.append(f"![Complaint share movers]({_report_image_path(charts['complaint_share_dumbbell'])})")
        if charts.get("seasonality_heatmap"):
            lines.append(f"![Complaint seasonality]({_report_image_path(charts['seasonality_heatmap'])})")
        if charts.get("daily_anomaly_timeline"):
            lines.append(f"![Daily anomalies]({_report_image_path(charts['daily_anomaly_timeline'])})")
        lines.append("")

    mover_rows: list[tuple[str, str, float | None, float | None]] = []
    for mv in (complaint_movers or {}).get("top_movers", []):
        mover_rows.append(("Complaint", mv.get("name", "Unknown"), mv.get("delta_pp"), mv.get("z")))
    for mv in (borough_movers or {}).get("top_movers", []):
        mover_rows.append(("Borough", mv.get("name", "Unknown"), mv.get("delta_pp"), mv.get("z")))

    mover_rows = sorted(
        mover_rows,
        key=lambda x: abs(x[3] if isinstance(x[3], (int, float)) else (x[2] or 0)),
        reverse=True,
    )[:8]

    if mover_rows:
        lines.append("## Biggest shifts vs normal")
        lines.append("")
        lines.append("| Type | Name | Shift (percentage points) | Unusualness (z-score) |")
        lines.append("|---|---|---:|---:|")
        for t, name, delta, z_val in mover_rows:
            z_str = f"{z_val:.2f}" if isinstance(z_val, (int, float)) else "n/a"
            label = _z_label(z_val)
            shift_base = f"{delta:+.1f} pp" if delta is not None else "n/a"
            shift_str = f"{shift_base} · {label}" if label and shift_base != "n/a" else shift_base
            lines.append(f"| {t} | {name} | {shift_str} | {z_str} |")
        lines.append("")

    lines.append("---")
    lines.append("Generated by `nyc311-weekly-report`.")
    lines.append("")
    return "\n".join(lines)


def save_report(markdown: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(markdown, encoding="utf-8")


def generate_report() -> Path:
    metrics = load_metrics()
    try:
        from nyc311_weekly_report.trend import load_trend_metrics

        trend_metrics = load_trend_metrics()
    except FileNotFoundError:
        trend_metrics = None
    dashboard = load_dashboard_optional()
    try:
        narrative_text = NARRATIVE_MD_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        narrative_text = None
    today = datetime.now().strftime("%Y-%m-%d")
    out = REPORTS_DIR / f"weekly_report_{today}.md"
    report_md = build_markdown(
        metrics,
        trend_metrics=trend_metrics,
        dashboard=dashboard,
        narrative_text=narrative_text,
    )
    save_report(report_md, out)
    return out
