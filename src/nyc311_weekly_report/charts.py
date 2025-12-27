from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns

from nyc311_weekly_report.dashboard import (
    DEFAULT_OUT_PATH as DASHBOARD_PATH,
    DEFAULT_TREND_DAILY_PATH,
    save_dashboard,
)

ASSETS_DIR = Path("site/assets")


def _plot_complaint_dumbbell(movers: list[dict[str, Any]], out_path: Path, top_n: int = 8) -> str | None:
    if not movers:
        return None
    movers_sorted = sorted(
        movers, key=lambda m: abs(m.get("z") if m.get("z") is not None else m.get("delta_pp") or 0), reverse=True
    )[:top_n]
    df = pd.DataFrame(movers_sorted)
    if df.empty:
        return None
    df["name"] = df["name"].fillna("Unknown")
    df["baseline_share"] = df["baseline_share"].fillna(0.0)
    df["current_share"] = df["current_share"].fillna(0.0)
    df["delta_pp"] = df["delta_pp"].fillna(0.0)

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(8, 6))
    y_pos = range(len(df))
    ax.hlines(y=y_pos, xmin=df["baseline_share"], xmax=df["current_share"], color="#cbd5e1", linewidth=2)
    ax.scatter(df["baseline_share"], y_pos, color="#1f77b4", label="Baseline", zorder=3)
    ax.scatter(df["current_share"], y_pos, color="#d62728", label="This week", zorder=3)

    ax.set_yticks(y_pos, df["name"])
    ax.set_xlabel("Share of weekly requests")
    ax.set_title("Complaint share movers")
    for idx, (_, row) in enumerate(df.iterrows()):
        ax.text(
            max(row["baseline_share"], row["current_share"]) + 0.005,
            idx,
            f"{row['delta_pp']:+.1f}pp" if row["delta_pp"] is not None else "",
            va="center",
            fontsize=8,
            color="#444",
        )
    ax.legend(frameon=False)
    max_val = max(df["baseline_share"].max(), df["current_share"].max())
    ax.set_xlim(0, min(1, max_val * 1.2 + 0.05))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", dpi=200)
    plt.close(fig)
    return str(out_path)


def _plot_seasonality_heatmap(baseline: dict[str, dict[int, float]], out_path: Path) -> str | None:
    if not baseline:
        return None
    df = pd.DataFrame(baseline).T
    if df.empty:
        return None
    # Coerce month columns to ints and keep only 1-12
    coerced_cols = []
    for col in df.columns:
        try:
            coerced_cols.append(int(col))
        except Exception:
            coerced_cols.append(col)
    df.columns = coerced_cols
    df = df[[c for c in df.columns if isinstance(c, int)]]
    for m in range(1, 13):
        if m not in df.columns:
            df[m] = 0.0
    df = df[[m for m in range(1, 13)]]
    sns.set_theme(style="white")
    fig, ax = plt.subplots(figsize=(10, max(4, len(df) * 0.4)))
    sns.heatmap(
        df,
        ax=ax,
        cmap="Blues",
        cbar_kws={"label": "Baseline share"},
        linewidths=0.5,
        linecolor="#e2e8f0",
        xticklabels=[str(m) for m in sorted(df.columns)],
    )
    ax.set_xlabel("Month")
    ax.set_ylabel("Complaint type")
    ax.set_title("Complaint seasonality baseline (share)")
    fig.tight_layout()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", dpi=200)
    plt.close(fig)
    return str(out_path)


def _plot_daily_anomaly_timeline(
    trend_daily: dict[str, Any],
    spikes: list[dict[str, Any]],
    out_path: Path,
) -> str | None:
    daily = trend_daily.get("daily_totals") if trend_daily else None
    if not daily:
        return None
    df = pd.DataFrame(daily)
    if df.empty:
        return None
    df["day"] = pd.to_datetime(df["day"])
    df["count"] = pd.to_numeric(df["count"], errors="coerce").fillna(0).astype(int)
    df = df.sort_values("day")
    df["rolling_7d"] = df["count"].rolling(window=7, min_periods=3).mean()

    sns.set_theme(style="whitegrid")
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["day"], df["count"], label="Daily count", color="#1f77b4", linewidth=1.2)
    ax.plot(df["day"], df["rolling_7d"], label="7-day avg", color="#ef4444", linewidth=2)

    if spikes:
        spike_df = pd.DataFrame(spikes)
        spike_df["day"] = pd.to_datetime(spike_df["day"])
        ax.scatter(spike_df["day"], spike_df["count"], color="#f97316", label="Spike", zorder=4)
        for _, row in spike_df.iterrows():
            ax.text(row["day"], row["count"], f"z={row.get('z', 0):.1f}", fontsize=8, color="#8c2d04", ha="left", va="bottom")

    ax.set_title("Daily requests with anomalies")
    ax.set_xlabel("Date")
    ax.set_ylabel("Requests")
    ax.legend(frameon=False)
    fig.autofmt_xdate()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", dpi=200)
    plt.close(fig)
    return str(out_path)


def generate_charts(
    dashboard_path: Path = DASHBOARD_PATH,
    trend_daily_path: Path = DEFAULT_TREND_DAILY_PATH,
    assets_dir: Path = ASSETS_DIR,
) -> dict[str, str]:
    dashboard = json.loads(Path(dashboard_path).read_text(encoding="utf-8"))
    trend_daily = json.loads(Path(trend_daily_path).read_text(encoding="utf-8")) if Path(trend_daily_path).exists() else {}
    charts: dict[str, str] = {}

    movers = dashboard.get("complaint_movers", {}).get("top_movers") or []
    complaint_path = assets_dir / "complaint_share_dumbbell.png"
    complaint_result = _plot_complaint_dumbbell(movers, complaint_path)
    if complaint_result:
        charts["complaint_share_dumbbell"] = complaint_result

    seasonality = (dashboard.get("anomalies") or {}).get("complaint_seasonality") or {}
    heatmap_path = assets_dir / "seasonality_heatmap.png"
    heatmap_result = _plot_seasonality_heatmap(seasonality.get("seasonality_baseline") or {}, heatmap_path)
    if heatmap_result:
        charts["seasonality_heatmap"] = heatmap_result

    spikes = (dashboard.get("anomalies") or {}).get("daily_spikes", {}).get("spikes") or []
    daily_path = assets_dir / "daily_anomaly_timeline.png"
    daily_result = _plot_daily_anomaly_timeline(trend_daily, spikes, daily_path)
    if daily_result:
        charts["daily_anomaly_timeline"] = daily_result

    dashboard["charts"] = charts
    save_dashboard(dashboard, dashboard_path)
    return charts


def main() -> None:
    generate_charts()


if __name__ == "__main__":
    main()
