from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from nyc311_weekly_report.soda3 import soda3_query

TREND_RAW_PATH = Path("data/processed/trend_daily.json")
TREND_METRICS_PATH = Path("data/processed/trend_metrics.json")
TREND_CHART_PATH = Path("data/processed/trend_chart.png")
NY_TZ = ZoneInfo("America/New_York")


def _ny_midnight_window(days: int) -> tuple[datetime, datetime, str, str]:
    now = datetime.now(NY_TZ)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)

    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000")

    return start, end, _fmt(start), _fmt(end)


def _normalize_date_string(day: str | None) -> str:
    if not day:
        return ""
    # SODA returns ISO strings; keep YYYY-MM-DD
    return day.split("T")[0]


def _normalize_counts(
    rows: Iterable[dict[str, Any]],
    *,
    day_field: str = "day",
    count_field: str = "total",
    extra_fields: Sequence[str] = (),
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in rows:
        entry: dict[str, Any] = {
            "day": _normalize_date_string(str(row.get(day_field, ""))),
            "count": int(row.get(count_field, 0) or 0),
        }
        for field in extra_fields:
            entry[field] = row.get(field)
        normalized.append(entry)
    return normalized


def _escape_value(val: str) -> str:
    # Escape single quotes for a safe IN (...) clause
    return val.replace("'", "''")


def ingest_trends(
    *,
    days: int = 365,
    include_borough: bool = True,
    include_complaints: bool = True,
    top_complaints: int = 5,
    out_path: Path = TREND_RAW_PATH,
) -> dict[str, Any]:
    _start_dt, _end_dt, start_str, end_str = _ny_midnight_window(days)
    where = f"created_date >= '{start_str}' AND created_date < '{end_str}'"

    daily_totals = _normalize_counts(
        soda3_query(
            query=(
                "SELECT date_trunc_ymd(created_date) as day, count(*) as total "
                f"WHERE {where} "
                "GROUP BY day "
                "ORDER BY day ASC"
            ),
            page_number=1,
            page_size=5000,
        )
    )

    daily_by_borough: list[dict[str, Any]] = []
    if include_borough:
        borough_rows = soda3_query(
            query=(
                "SELECT date_trunc_ymd(created_date) as day, borough, count(*) as total "
                f"WHERE {where} AND borough IS NOT NULL "
                "GROUP BY day, borough "
                "ORDER BY day ASC, borough ASC"
            ),
            page_number=1,
            page_size=5000,
        )
        daily_by_borough = _normalize_counts(borough_rows, extra_fields=("borough",))

    top_complaint_totals: list[dict[str, Any]] = []
    daily_by_complaint: list[dict[str, Any]] = []
    if include_complaints and top_complaints > 0:
        complaint_rows = soda3_query(
            query=(
                "SELECT complaint_type, count(*) as total "
                f"WHERE {where} "
                "GROUP BY complaint_type "
                "ORDER BY total DESC "
                f"LIMIT {top_complaints}"
            ),
            page_number=1,
            page_size=top_complaints,
        )
        top_complaint_totals = [
            {
                "complaint_type": row.get("complaint_type") or "Unknown",
                "count": int(row.get("total", 0) or 0),
            }
            for row in complaint_rows
        ]

        if top_complaint_totals:
            in_clause = ",".join(
                f"'{_escape_value(name)}'"
                for name in (t["complaint_type"] for t in top_complaint_totals)
            )
            complaint_daily_rows = soda3_query(
                query=(
                    "SELECT date_trunc_ymd(created_date) as day, complaint_type, count(*) as total "
                    f"WHERE {where} AND complaint_type in ({in_clause}) "
                    "GROUP BY day, complaint_type "
                    "ORDER BY day ASC, complaint_type ASC"
                ),
                page_number=1,
                page_size=5000,
            )
            daily_by_complaint = _normalize_counts(
                complaint_daily_rows, extra_fields=("complaint_type",)
            )

    payload: dict[str, Any] = {
        "meta": {
            "days": days,
            "start": start_str,
            "end": end_str,
            "timezone": "America/New_York",
            "generated_at_utc": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "daily_rows": len(daily_totals),
            "top_complaints_limit": top_complaints if include_complaints else 0,
        },
        "daily_totals": daily_totals,
        "daily_by_borough": daily_by_borough,
        "top_complaints": top_complaint_totals,
        "daily_by_complaint": daily_by_complaint,
    }

    save_trend_raw(payload, out_path)
    return payload


def save_trend_raw(payload: dict[str, Any], out_path: Path = TREND_RAW_PATH) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_trend_raw(path: Path = TREND_RAW_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _render_chart(df: pd.DataFrame, *, out_path: Path = TREND_CHART_PATH) -> str:
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(df.index, df["count"], label="Daily count", color="#1f77b4", linewidth=1.2)
    if "rolling_7d_avg" in df.columns:
        ax.plot(
            df.index,
            df["rolling_7d_avg"],
            label="7-day average",
            color="#d62728",
            linewidth=2,
        )
    ax.set_title("NYC 311 daily requests")
    ax.set_xlabel("Date")
    ax.set_ylabel("Requests")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.autofmt_xdate()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)

    buf.seek(0)
    data_uri = "data:image/png;base64," + base64.b64encode(buf.read()).decode("ascii")
    return data_uri


def _compute_window_spike(
    series: pd.Series,
    *,
    current_end: pd.Timestamp,
    current_window_days: int = 7,
    baseline_window_days: int = 56,
) -> dict[str, Any]:
    """Compute defensible spike metrics using a trailing baseline window."""
    if series.empty:
        return {
            "current_7d_total": None,
            "baseline_mean": None,
            "baseline_std": None,
            "spike_z": None,
            "spike_pct_vs_mean": None,
            "is_spike": False,
        }

    series = series.sort_index()
    rolling = series.rolling(current_window_days).sum()

    current_end = pd.Timestamp(current_end)
    current_start = current_end - pd.Timedelta(days=current_window_days - 1)
    baseline_end = current_start - pd.Timedelta(days=1)
    baseline_start = baseline_end - pd.Timedelta(days=baseline_window_days - 1)

    try:
        current_total = float(rolling.loc[current_end])
    except Exception:
        current_total = None

    if current_total is not None and pd.isna(current_total):
        current_total = None

    baseline_slice = rolling.loc[baseline_start:baseline_end].dropna()
    baseline_mean = float(baseline_slice.mean()) if not baseline_slice.empty else None
    baseline_std = float(baseline_slice.std()) if not baseline_slice.empty else None
    if baseline_mean is not None and pd.isna(baseline_mean):
        baseline_mean = None
    if baseline_std is not None and pd.isna(baseline_std):
        baseline_std = None

    spike_pct_vs_mean = (
        (current_total / baseline_mean) - 1 if baseline_mean and baseline_mean > 0 and current_total is not None else None
    )
    spike_z = (
        (current_total - baseline_mean) / baseline_std
        if baseline_mean is not None and baseline_std and baseline_std > 0 and current_total is not None
        else None
    )
    is_spike = bool(
        spike_z is not None
        and spike_z >= 2.5
        and spike_pct_vs_mean is not None
        and spike_pct_vs_mean >= 0.15
    )

    return {
        "current_7d_total": None if current_total is None else int(round(current_total)),
        "baseline_mean": baseline_mean,
        "baseline_std": baseline_std,
        "spike_z": spike_z,
        "spike_pct_vs_mean": spike_pct_vs_mean,
        "is_spike": is_spike,
        "rule": "z>=2.5 AND >=15% above baseline mean",
    }


def analyze_trends(trend_data: dict[str, Any]) -> dict[str, Any]:
    daily = trend_data.get("daily_totals") or []
    if not daily:
        raise ValueError("No daily_totals found in trend data; run `nyc311 trend` first.")

    df = pd.DataFrame(daily)
    df["day"] = pd.to_datetime(df["day"])
    df["count"] = df["count"].astype(int)
    df = df.sort_values("day").set_index("day")

    if df.empty:
        raise ValueError("Trend data is empty after normalization.")

    full_index = pd.date_range(df.index.min(), df.index.max(), freq="D")
    df = df.reindex(full_index, fill_value=0)
    df.index.name = "day"

    df["rolling_7d_avg"] = df["count"].rolling(window=7, min_periods=3).mean()

    expected_days = int(trend_data.get("meta", {}).get("days") or len(df))
    actual_days = int(df.index.nunique())
    first_day = df.index.min().strftime("%Y-%m-%d")
    last_day = df.index.max().strftime("%Y-%m-%d")

    today_ny = datetime.now(NY_TZ).date()
    last_day_date = df.index.max().date()
    last_day_age_days = (today_ny - last_day_date).days
    data_fresh = last_day_age_days <= 2
    freshness_warning = (
        f"Data feed lagging: latest day in trend series is {last_day} ({last_day_age_days} days behind)"
        if not data_fresh
        else ""
    )

    coverage = {
        "expected_days": expected_days,
        "actual_days": actual_days,
        "first_day": first_day,
        "last_day": last_day,
    }
    freshness = {
        "today_ny": today_ny.isoformat(),
        "last_day_age_days": last_day_age_days,
        "data_fresh": data_fresh,
        "warning": freshness_warning,
    }

    current_window_days = 7
    baseline_window_days = 56
    current_end = min(df.index.max(), pd.Timestamp(today_ny) - pd.Timedelta(days=1))
    window_dates = pd.date_range(
        current_end - pd.Timedelta(days=current_window_days - 1),
        current_end,
        freq="D",
    )

    overall_spike = _compute_window_spike(
        df["count"],
        current_end=current_end,
        current_window_days=current_window_days,
        baseline_window_days=baseline_window_days,
    )

    monthly_series = df["count"].resample("MS").sum()
    monthly_totals = [
        {"month": idx.strftime("%Y-%m"), "total": int(val)}
        for idx, val in monthly_series.items()
    ]

    last_week_total = overall_spike.get("current_7d_total") or 0
    trailing_avg = overall_spike.get("baseline_mean")
    pct_change = (
        (overall_spike.get("spike_pct_vs_mean") or 0) * 100
        if overall_spike.get("spike_pct_vs_mean") is not None
        else None
    )

    mean = df["count"].mean()
    std = df["count"].std()
    spike_days: list[dict[str, Any]] = []
    if std and std > 0:
        zscores = (df["count"] - mean) / std
        for idx, z in zscores.sort_values(ascending=False).head(5).items():
            if z <= 0:
                continue
            count_val = int(df.loc[idx, "count"])
            spike_days.append(
                {
                    "day": idx.strftime("%Y-%m-%d"),
                    "count": count_val,
                    "zscore": round(float(z), 2),
                    "pct_from_mean": round(((count_val - mean) / mean) * 100, 1)
                    if mean
                    else None,
                }
            )

    weekly_roll = df["count"].rolling(window=7).sum().dropna()
    weekly_spikes: list[dict[str, Any]] = []
    if not weekly_roll.empty:
        weekly_mean = weekly_roll.mean()
        weekly_std = weekly_roll.std()
        if weekly_std and weekly_std > 0:
            for end_date, z in (
                ((weekly_roll - weekly_mean) / weekly_std)
                .sort_values(ascending=False)
                .head(3)
                .items()
            ):
                if z <= 0:
                    continue
                total_val = int(weekly_roll.loc[end_date])
                weekly_spikes.append(
                    {
                        "week_start": (end_date - pd.Timedelta(days=6)).strftime("%Y-%m-%d"),
                        "week_end": end_date.strftime("%Y-%m-%d"),
                        "total": total_val,
                        "zscore": round(float(z), 2),
                    }
                )

    chart_data_uri = _render_chart(df, out_path=TREND_CHART_PATH)

    story_volume = {
        "label": "Overall volume (last 7 days)",
        "value": last_week_total,
        "baseline": trailing_avg,
        "pct_change": pct_change,
        "z_score": overall_spike.get("spike_z"),
        "is_spike": overall_spike.get("is_spike", False),
    }

    story_complaint: dict[str, Any] = {}
    complaint_daily = trend_data.get("daily_by_complaint") or []
    if complaint_daily:
        cdf = pd.DataFrame(complaint_daily)
        if not cdf.empty:
            cdf["day"] = pd.to_datetime(cdf["day"])
            cdf["count"] = cdf["count"].astype(int)
            pivot_raw = cdf.pivot_table(index="day", columns="complaint_type", values="count", aggfunc="sum")
            pivot = pivot_raw.reindex(df.index, fill_value=0)

            best = None
            excluded = {None, "", "Unspecified"}
            for col in pivot.columns:
                if col in excluded:
                    continue
                series_full = pivot[col]
                series_raw = pivot_raw[col] if col in pivot_raw else pd.Series(dtype=float)
                coverage_days = series_raw.reindex(window_dates).dropna().shape[0]
                if coverage_days < 5:
                    continue

                stats = _compute_window_spike(
                    series_full,
                    current_end=current_end,
                    current_window_days=current_window_days,
                    baseline_window_days=baseline_window_days,
                )
                current_total = stats.get("current_7d_total")
                baseline_mean = stats.get("baseline_mean")
                pct_change_frac = stats.get("spike_pct_vs_mean")

                if current_total is None or baseline_mean is None:
                    continue
                pct_change = (pct_change_frac or 0) * 100 if pct_change_frac is not None else None
                if pct_change is None:
                    continue

                spike_z = stats.get("spike_z")
                is_spike_high = bool(
                    spike_z is not None and spike_z >= 2.5 and pct_change_frac is not None and pct_change_frac >= 0.15
                )
                is_spike_low = bool(
                    spike_z is not None and spike_z <= -2.5 and pct_change_frac is not None and pct_change_frac <= -0.15
                )

                candidate = {
                    "available": True,
                    "complaint_type": col,
                    "current_7d_total": current_total,
                    "baseline_mean": baseline_mean,
                    "pct_change": pct_change,
                    "z_score": spike_z,
                    "is_spike_high": is_spike_high,
                    "is_spike_low": is_spike_low,
                }
                score = spike_z
                fallback_pct = pct_change
                rank_val = score if score is not None else fallback_pct
                if best is None or (rank_val is not None and rank_val > best.get("rank_val", float("-inf"))):
                    best = {**candidate, "rank_val": rank_val}
            if best:
                best.pop("rank_val", None)
                story_complaint = best
            else:
                story_complaint = {"available": False, "reason": "insufficient coverage"}
        else:
            story_complaint = {"available": False, "reason": "insufficient coverage"}
    else:
        story_complaint = {"available": False, "reason": "insufficient coverage"}

    story_borough: dict[str, Any] = {}
    borough_daily = trend_data.get("daily_by_borough") or []
    if borough_daily:
        bdf = pd.DataFrame(borough_daily)
        if not bdf.empty:
            bdf["day"] = pd.to_datetime(bdf["day"])
            bdf["count"] = bdf["count"].astype(int)
            pivot_raw = bdf.pivot_table(index="day", columns="borough", values="count", aggfunc="sum")
            pivot = pivot_raw.reindex(df.index, fill_value=0)

            best_b = None
            excluded_b = {None, "", "Unspecified"}
            for col in pivot.columns:
                if col in excluded_b:
                    continue
                series_full = pivot[col]
                series_raw = pivot_raw[col] if col in pivot_raw else pd.Series(dtype=float)
                coverage_days = series_raw.reindex(window_dates).dropna().shape[0]
                if coverage_days < 5:
                    continue

                stats = _compute_window_spike(
                    series_full,
                    current_end=current_end,
                    current_window_days=current_window_days,
                    baseline_window_days=baseline_window_days,
                )
                current_total = stats.get("current_7d_total")
                baseline_mean = stats.get("baseline_mean")
                pct_change_frac = stats.get("spike_pct_vs_mean")

                if current_total is None or baseline_mean is None:
                    continue
                pct_change = (pct_change_frac or 0) * 100 if pct_change_frac is not None else None
                if pct_change is None:
                    continue

                spike_z = stats.get("spike_z")
                is_spike_high = bool(
                    spike_z is not None and spike_z >= 2.5 and pct_change_frac is not None and pct_change_frac >= 0.15
                )
                is_spike_low = bool(
                    spike_z is not None and spike_z <= -2.5 and pct_change_frac is not None and pct_change_frac <= -0.15
                )

                candidate = {
                    "available": True,
                    "borough": col,
                    "current_7d_total": current_total,
                    "baseline_mean": baseline_mean,
                    "pct_change": pct_change,
                    "z_score": spike_z,
                    "is_spike_high": is_spike_high,
                    "is_spike_low": is_spike_low,
                }
                score = spike_z
                fallback_pct = pct_change
                rank_val = score if score is not None else fallback_pct
                if best_b is None or (rank_val is not None and rank_val > best_b.get("rank_val", float("-inf"))):
                    best_b = {**candidate, "rank_val": rank_val}
            if best_b:
                best_b.pop("rank_val", None)
                story_borough = best_b
            else:
                story_borough = {"available": False, "reason": "insufficient coverage"}
        else:
            story_borough = {"available": False, "reason": "insufficient coverage"}
    else:
        story_borough = {"available": False, "reason": "insufficient coverage"}

    return {
        "meta": {
            **trend_data.get("meta", {}),
            "points": len(df),
            "chart_path": str(TREND_CHART_PATH),
        },
        "coverage": coverage,
        "freshness": freshness,
        "baseline": {
            "window_days": baseline_window_days,
            "exclude_recent_days": current_window_days,
            "current_window_days": current_window_days,
        },
        "overall_spike": overall_spike,
        "monthly_totals": monthly_totals,
        "last_week_vs_baseline": {
            "last_week_total": last_week_total,
            "trailing_8_week_avg": trailing_avg,
            "pct_change": pct_change,
        },
        "spike_days": spike_days,
        "spike_weeks": weekly_spikes,
        "chart_data_uri": chart_data_uri,
        "storylines": {
            "volume": story_volume,
            "top_complaint_mover": story_complaint,
            "top_borough_mover": story_borough,
        },
    }


def save_trend_metrics(metrics: dict[str, Any], out_path: Path = TREND_METRICS_PATH) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")


def load_trend_metrics(path: Path = TREND_METRICS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
