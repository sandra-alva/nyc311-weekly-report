from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

DEFAULT_WEEKLY_PATH = Path("data/processed/weekly_metrics.json")
DEFAULT_TREND_PATH = Path("data/processed/trend_metrics.json")
DEFAULT_TREND_DAILY_PATH = Path("data/processed/trend_daily.json")
DEFAULT_OUT_PATH = Path("data/processed/dashboard.json")
NY_TZ = ZoneInfo("America/New_York")

BASELINE_WEEKS = 52
TOP_COMPLAINTS_TRACKED = 10
BORING_LEADER_THRESHOLD = 0.70
SPIKE_Z_THRESHOLD = 2.5


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_optional(path: Path) -> dict[str, Any] | None:
    return _load_json(path) if path.exists() else None


def _parse_date(dt_str: str | None) -> pd.Timestamp | None:
    if not dt_str:
        return None
    try:
        return pd.to_datetime(dt_str)
    except Exception:
        return None


def _ny_date_from_ts(ts: pd.Timestamp | None) -> datetime.date | None:
    if ts is None:
        return None
    try:
        if ts.tzinfo is None:
            ts = ts.tz_localize(NY_TZ)
        return ts.tz_convert(NY_TZ).date()
    except Exception:
        return None


def _align_week(stamps: pd.Series, ref_start: pd.Timestamp | None) -> pd.Series:
    """Align arbitrary dates into 7-day buckets anchored to ref_start."""
    if ref_start is None:
        return stamps.dt.to_period("W-SUN").start_time
    ref = pd.Timestamp(ref_start).normalize()
    days_from_ref = (stamps.dt.normalize() - ref).dt.days
    week_index = (days_from_ref // 7).astype(int)
    return ref + pd.to_timedelta(week_index * 7, unit="D")


def _weekly_counts_from_daily(
    rows: Iterable[Mapping[str, Any]],
    *,
    category_field: str,
    window_start: str | None,
) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty or category_field not in df.columns:
        return pd.DataFrame()
    df["day"] = pd.to_datetime(df["day"])
    df["count"] = pd.to_numeric(df["count"], errors="coerce").fillna(0).astype(int)
    df[category_field] = df[category_field].fillna("Unknown")
    df["week_start"] = _align_week(df["day"], _parse_date(window_start))
    pivot = df.pivot_table(
        index="week_start",
        columns=category_field,
        values="count",
        aggfunc="sum",
        fill_value=0,
    )
    return pivot.sort_index()


def _weekly_share(pivot: pd.DataFrame) -> pd.DataFrame:
    if pivot.empty:
        return pivot
    totals = pivot.sum(axis=1)
    totals = totals.replace(0, pd.NA)
    shares = pivot.div(totals, axis=0).fillna(0.0)
    return shares


def _float(val: Any) -> float | None:
    try:
        fval = float(val)
    except Exception:
        return None
    if pd.isna(fval):
        return None
    return fval


def _delta_z(current: float | None, mean: float | None, std: float | None) -> tuple[float | None, float | None]:
    if current is None or mean is None:
        return None, None
    delta_pp = (current - mean) * 100
    if std is None or std <= 0:
        return delta_pp, None
    return delta_pp, (current - mean) / std


def _current_share_from_counts(counts: Mapping[str, Any]) -> dict[str, float]:
    total = sum(_float(v) or 0 for v in counts.values())
    if total <= 0:
        return {k: 0.0 for k in counts}
    return {k: (float(v) / total) if _float(v) is not None else 0.0 for k, v in counts.items()}


def _merge_current_with_history(
    *,
    history: pd.DataFrame,
    current_week_start: pd.Timestamp | None,
    current_counts: Mapping[str, Any],
    top_movers: int,
    exclude: set[str] | None = None,
) -> dict[str, Any]:
    if history.empty and not current_counts:
        return {}
    exclude = exclude or set()

    current_shares = {k: v for k, v in _current_share_from_counts(current_counts).items() if k not in exclude}
    # If current week already in history (from trends), prefer that share row
    if current_week_start is not None and not history.empty and current_week_start in history.index:
        current_shares = {
            k: _float(v) or 0.0 for k, v in history.loc[current_week_start].to_dict().items() if k not in exclude
        }

    if not history.empty:
        if exclude:
            history = history.drop(columns=list(exclude), errors="ignore")
        if current_week_start is not None and current_week_start in history.index:
            baseline_slice = history.loc[history.index != current_week_start].tail(BASELINE_WEEKS)
        else:
            baseline_slice = history.tail(BASELINE_WEEKS)
    else:
        baseline_slice = pd.DataFrame()

    baseline_mean = baseline_slice.mean() if not baseline_slice.empty else pd.Series(dtype=float)
    baseline_std = baseline_slice.std(ddof=0) if not baseline_slice.empty else pd.Series(dtype=float)

    movers: list[dict[str, Any]] = []
    all_categories = sorted(set(current_shares.keys()) | set(baseline_mean.index))
    for cat in all_categories:
        cur_share = _float(current_shares.get(cat))
        base_mean = _float(baseline_mean.get(cat)) if not baseline_mean.empty else None
        base_std = _float(baseline_std.get(cat)) if not baseline_std.empty else None
        delta_pp, z = _delta_z(cur_share, base_mean, base_std)
        movers.append(
            {
                "name": cat,
                "current_share": cur_share,
                "baseline_share": base_mean,
                "volatility": base_std,
                "delta_pp": delta_pp,
                "z": z,
            }
        )

    movers_sorted = sorted(
        movers,
        key=lambda m: abs(m.get("z") if m.get("z") is not None else m.get("delta_pp") or 0),
        reverse=True,
    )
    movers_top = movers_sorted[:top_movers]

    return {
        "this_week_share": current_shares,
        "baseline_share": {k: _float(v) for k, v in baseline_mean.to_dict().items()} if not baseline_mean.empty else {},
        "volatility": {k: _float(v) for k, v in baseline_std.to_dict().items()} if not baseline_std.empty else {},
        "top_movers": movers_top,
        "history": [
            {"week_start": idx.strftime("%Y-%m-%d"), "shares": {k: _float(v) for k, v in row.dropna().to_dict().items()}}
            for idx, row in history.tail(BASELINE_WEEKS).iterrows()
        ]
        if not history.empty
        else [],
    }


def _compute_boring_leaders(share_history: pd.DataFrame) -> dict[str, Any]:
    if share_history.empty:
        return {"boring_types": [], "rank1_pct": {}, "top3_pct": {}}

    rank1_counts: dict[str, int] = {}
    top3_counts: dict[str, int] = {}
    weeks = 0
    for _, row in share_history.iterrows():
        if row.empty:
            continue
        weeks += 1
        sorted_types = row.sort_values(ascending=False)
        top1 = sorted_types.index[0]
        rank1_counts[top1] = rank1_counts.get(top1, 0) + 1
        for name in sorted_types.index[:3]:
            top3_counts[name] = top3_counts.get(name, 0) + 1

    if weeks == 0:
        return {"boring_types": [], "rank1_pct": {}, "top3_pct": {}}

    rank1_pct = {k: v / weeks for k, v in rank1_counts.items()}
    top3_pct = {k: v / weeks for k, v in top3_counts.items()}
    boring = [k for k, v in rank1_pct.items() if v >= BORING_LEADER_THRESHOLD]

    return {
        "boring_types": boring,
        "rank1_pct": rank1_pct,
        "top3_pct": top3_pct,
    }


def _compute_daily_spikes(trend_daily: dict[str, Any]) -> dict[str, Any]:
    daily = trend_daily.get("daily_totals") or []
    if not daily:
        return {"spikes": [], "max_spike": None, "message": "No daily totals available."}
    df = pd.DataFrame(daily)
    df["day"] = pd.to_datetime(df["day"])
    df["count"] = pd.to_numeric(df["count"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values("day")

    z_vals: list[tuple[pd.Timestamp, float, float, float]] = []
    for _, row in df.iterrows():
        day = row["day"]
        count = row["count"]
        prior_same_weekday = df[(df["day"] < day) & (df["day"].dt.weekday == day.weekday())].tail(8)
        if prior_same_weekday.empty:
            continue
        base_mean = prior_same_weekday["count"].mean()
        base_std = prior_same_weekday["count"].std(ddof=0)
        z = (count - base_mean) / base_std if base_std and base_std > 0 else None
        if z is None:
            continue
        z_vals.append((day, count, base_mean, z))

    if not z_vals:
        return {"spikes": [], "max_spike": None, "message": "No significant daily spikes detected"}

    spikes = [
        {
            "day": day.strftime("%Y-%m-%d"),
            "count": int(count),
            "baseline_mean": float(base_mean),
            "z": float(z),
        }
        for day, count, base_mean, z in z_vals
        if z >= SPIKE_Z_THRESHOLD
    ]
    spikes = sorted(spikes, key=lambda x: x["z"], reverse=True)[:3]
    max_spike = max(z_vals, key=lambda t: t[3])
    max_spike_entry = {
        "day": max_spike[0].strftime("%Y-%m-%d"),
        "count": int(max_spike[1]),
        "baseline_mean": float(max_spike[2]),
        "z": float(max_spike[3]),
    }
    message = "No significant daily spikes detected" if not spikes else ""
    return {"spikes": spikes, "max_spike": max_spike_entry, "message": message}


def _seasonal_anomalies(
    *,
    share_history: pd.DataFrame,
    current_week_start: pd.Timestamp | None,
    current_shares: Mapping[str, Any],
    tracked_types: list[str],
    boring: set[str],
) -> dict[str, Any]:
    if current_week_start is None or share_history.empty:
        return {"increases": [], "decreases": [], "seasonality_baseline": {}, "current_month": None}
    history = share_history.tail(BASELINE_WEEKS)
    if current_week_start in history.index:
        history = history.loc[history.index != current_week_start]
    if history.empty:
        return {"increases": [], "decreases": [], "seasonality_baseline": {}, "current_month": current_week_start.month}

    current_month = current_week_start.month
    results: list[dict[str, Any]] = []

    seasonality_baseline: dict[str, dict[int, float]] = {}
    for name in tracked_types:
        seasonality_baseline[name] = {}
        for month in range(1, 13):
            month_slice = history[history.index.month == month][name] if name in history.columns else pd.Series(dtype=float)
            mean_val = _float(month_slice.mean()) if not month_slice.empty else None
            seasonality_baseline[name][month] = mean_val if mean_val is not None else 0.0

        if name not in current_shares:
            continue
        month_slice = history[history.index.month == current_month][name] if name in history.columns else pd.Series(dtype=float)
        base_mean = _float(month_slice.mean()) if not month_slice.empty else None
        base_std = _float(month_slice.std(ddof=0)) if not month_slice.empty else None
        cur_share = _float(current_shares.get(name))
        z = (cur_share - base_mean) / base_std if base_mean is not None and base_std and base_std > 0 else None
        delta_pp = (cur_share - base_mean) * 100 if cur_share is not None and base_mean is not None else None
        entry = {
            "name": name,
            "current_share": cur_share,
            "baseline_mean": base_mean,
            "baseline_std": base_std,
            "z_month": z,
            "delta_pp": delta_pp,
            "month": current_month,
        }
        if name in boring and not (z is not None and abs(z) >= 2):
            continue
        results.append(entry)

    inc = sorted(
        [r for r in results if r.get("z_month") is not None and r["z_month"] > 0],
        key=lambda x: abs(x["z_month"]),
        reverse=True,
    )[:2]
    dec = sorted(
        [r for r in results if r.get("z_month") is not None and r["z_month"] < 0],
        key=lambda x: abs(x["z_month"]),
        reverse=True,
    )[:2]

    return {
        "increases": inc,
        "decreases": dec,
        "seasonality_baseline": seasonality_baseline,
        "current_month": current_month,
    }


def compute_dashboard(
    *,
    weekly_path: Path = DEFAULT_WEEKLY_PATH,
    trend_path: Path = DEFAULT_TREND_PATH,
    trend_daily_path: Path = DEFAULT_TREND_DAILY_PATH,
) -> dict[str, Any]:
    weekly = _load_json(weekly_path)
    trend = _load_optional(trend_path)
    trend_daily = _load_optional(trend_daily_path)

    window_start = weekly.get("window_start")
    window_end = weekly.get("window_end")
    week_start_ts = _parse_date(window_start)

    total_rows = int(weekly.get("total_rows") or 0)
    latest_record = weekly.get("weekly_latest_created_date")
    weekly_unique = weekly.get("weekly_unique_days_count")
    weekly_expected = weekly.get("weekly_expected_days")
    weekly_missing = weekly.get("weekly_missing_days_est")

    # Headline volume vs baseline
    vol_pct_change = None
    vol_z = None
    trailing_avg = None
    if trend:
        last_week_vs_baseline = trend.get("last_week_vs_baseline") or {}
        vol_pct_change = _float(last_week_vs_baseline.get("pct_change"))
        trailing_avg = _float(last_week_vs_baseline.get("trailing_8_week_avg"))
        overall_spike = trend.get("overall_spike") or {}
        vol_z = _float(overall_spike.get("spike_z"))

    # Borough movers (exclude Unspecified from movers)
    borough_counts_week = {b: c for b, c in weekly.get("top_boroughs", [])}
    unspecified_count = borough_counts_week.pop("Unspecified", None)
    # Also normalize label if present elsewhere
    if "Unknown / Unspecified (missing borough)" in borough_counts_week:
        borough_counts_week.pop("Unknown / Unspecified (missing borough)", None)
    borough_history_counts = pd.DataFrame()
    borough_share_history_full = pd.DataFrame()
    borough_share_history = pd.DataFrame()
    if trend_daily and trend_daily.get("daily_by_borough"):
        borough_history_counts = _weekly_counts_from_daily(
            trend_daily["daily_by_borough"],
            category_field="borough",
            window_start=window_start,
        )
        borough_share_history_full = _weekly_share(borough_history_counts)
        borough_share_history = borough_share_history_full.drop(columns=["Unspecified"], errors="ignore")

    borough_section = _merge_current_with_history(
        history=borough_share_history,
        current_week_start=week_start_ts,
        current_counts=borough_counts_week,
        top_movers=3,
        exclude={"Unspecified"},
    )

    # Complaint movers
    complaint_counts_week = {c: count for c, count in weekly.get("top_complaints", []) if c != "Unspecified"}
    complaint_history_counts = pd.DataFrame()
    complaint_share_history = pd.DataFrame()
    tracked_types: list[str] = []
    if trend_daily and trend_daily.get("daily_by_complaint"):
        complaint_history_counts = _weekly_counts_from_daily(
            trend_daily["daily_by_complaint"],
            category_field="complaint_type",
            window_start=window_start,
        )
        totals_by_type = complaint_history_counts.sum().sort_values(ascending=False)
        tracked_types = [t for t in totals_by_type.head(TOP_COMPLAINTS_TRACKED).index.tolist() if t != "Unspecified"]
        complaint_history_counts = complaint_history_counts[tracked_types]
        complaint_share_history = _weekly_share(complaint_history_counts)

    complaint_section = _merge_current_with_history(
        history=complaint_share_history,
        current_week_start=week_start_ts,
        current_counts=complaint_counts_week,
        top_movers=5,
    )

    # Boring leaders derived from weekly share history
    boring_leaders = _compute_boring_leaders(complaint_share_history.tail(BASELINE_WEEKS))

    anomalies_daily = _compute_daily_spikes(trend_daily or {}) if trend_daily else {"spikes": [], "max_spike": None, "message": "Trend daily not available"}
    anomalies_complaint = _seasonal_anomalies(
        share_history=complaint_share_history,
        current_week_start=week_start_ts,
        current_shares=complaint_section.get("this_week_share", {}),
        tracked_types=tracked_types,
        boring=set(boring_leaders.get("boring_types", [])),
    )

    warnings: list[str] = []
    weekly_lag_days = None
    if window_end and latest_record:
        window_end_dt = _parse_date(window_end)
        latest_dt = _parse_date(latest_record)
        window_end_date = _ny_date_from_ts(window_end_dt)
        latest_date = _ny_date_from_ts(latest_dt)
        if window_end_date is not None and latest_date is not None:
            weekly_lag_days = (window_end_date - latest_date).days
            if weekly_lag_days >= 2:
                warnings.append(f"Weekly data appears {weekly_lag_days} days behind.")

    if weekly_missing:
        try:
            if float(weekly_missing) > 0:
                warnings.append(f"Weekly coverage missing ~{weekly_missing} days.")
        except Exception:
            warnings.append("Weekly coverage incomplete.")

    trend_latest_day = None
    trend_lag_days = None
    if trend and (trend.get("coverage") or {}).get("last_day"):
        trend_latest_day = trend["coverage"]["last_day"]
        today = datetime.now(NY_TZ).date()
        try:
            trend_last_date = pd.to_datetime(trend_latest_day).date()
            trend_lag_days = (today - trend_last_date).days
        except Exception:
            trend_lag_days = None
        freshness = (trend.get("freshness") or {}).get("warning")
        if freshness:
            warnings.append(f"Trend data warning: {freshness}")

    unspecified_share_current = None
    if unspecified_count is not None and total_rows > 0:
        unspecified_share_current = (unspecified_count or 0) / total_rows
        if unspecified_share_current > 0.01:
            warnings.append(f"Unspecified borough share {unspecified_share_current:.2%} exceeds 1%.")
    unspecified_baseline_mean = None
    unspecified_baseline_z = None
    if not borough_share_history_full.empty and "Unspecified" in borough_share_history_full.columns:
        base_series = borough_share_history_full["Unspecified"].tail(BASELINE_WEEKS)
        unspecified_baseline_mean = _float(base_series.mean())
        unspecified_baseline_std = _float(base_series.std(ddof=0))
        if unspecified_baseline_mean is not None and unspecified_baseline_std and unspecified_baseline_std > 0 and unspecified_share_current is not None:
            unspecified_baseline_z = (unspecified_share_current - unspecified_baseline_mean) / unspecified_baseline_std
            if abs(unspecified_baseline_z) >= 2:
                warnings.append("Unspecified borough share is anomalous vs seasonal baseline.")

    data_quality = {
        "weekly_latest_created_date": latest_record,
        "weekly_unique_days_count": weekly_unique,
        "weekly_expected_days": weekly_expected,
        "weekly_missing_days_est": weekly_missing,
        "weekly_lag_days": weekly_lag_days,
        "trend_latest_day": trend_latest_day,
        "trend_lag_days": trend_lag_days,
        "warnings": warnings,
        "unspecified_borough_share": unspecified_share_current,
        "unspecified_borough_baseline_mean": unspecified_baseline_mean,
        "unspecified_borough_baseline_z": unspecified_baseline_z,
    }

    dashboard: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start": window_start, "end": window_end},
        "headline": {
            "weekly_total": total_rows,
            "overall_volume_vs_baseline_pct": vol_pct_change,
            "overall_volume_z": vol_z,
            "volume_baseline": trailing_avg,
            "data_quality": data_quality,
        },
        "borough_share": borough_section,
        "complaint_movers": {
            **complaint_section,
            "tracked_types": tracked_types,
        },
        "boring_leaders": boring_leaders,
        "anomalies": {
            "daily_spikes": anomalies_daily,
            "complaint_seasonality": anomalies_complaint,
        },
        "labels": {
            "unknown_borough": "Unknown / Unspecified (missing borough)",
            "complaint_mover": "Biggest complaint shift",
            "borough_mover": "Biggest borough shift",
            "movers_table": "Biggest shifts vs normal",
        },
        "charts": {},
    }

    return dashboard


def save_dashboard(obj: dict[str, Any], out_path: Path = DEFAULT_OUT_PATH) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(obj, indent=2), encoding="utf-8")
    return out_path


def main(
    weekly_path: Path = DEFAULT_WEEKLY_PATH,
    trend_path: Path = DEFAULT_TREND_PATH,
    trend_daily_path: Path = DEFAULT_TREND_DAILY_PATH,
    out_path: Path = DEFAULT_OUT_PATH,
) -> Path:
    dashboard = compute_dashboard(
        weekly_path=weekly_path,
        trend_path=trend_path,
        trend_daily_path=trend_daily_path,
    )
    return save_dashboard(dashboard, out_path)


if __name__ == "__main__":
    main()
