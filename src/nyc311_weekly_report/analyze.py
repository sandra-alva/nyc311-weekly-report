from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

RAW_DIR = Path("data/raw")
PROCESSED_DIR = Path("data/processed")



def load_latest_raw() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    files = sorted(RAW_DIR.glob("nyc311_last_*_days_*.json"))
    if not files:
        raise FileNotFoundError("No raw JSON files found in data/raw.")
    latest = files[-1]

    data = json.loads(latest.read_text(encoding="utf-8"))

    # New format: {"meta": {...}, "rows": [...]}
    if isinstance(data, dict) and "rows" in data:
        meta = data.get("meta", {})
        rows = data["rows"]
        return meta, rows

    # Old format fallback: raw file is a list[dict]
    if isinstance(data, list):
        return {}, data

    raise ValueError(f"Unexpected raw snapshot format in {latest.name}")


def compute_weekly_metrics(rows: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:

    meta = meta or {}
    window_start = meta.get("start")
    window_end = meta.get("end")

    total = len(rows)

    created_vals = [r.get("created_date") for r in rows if r.get("created_date")]
    created_min = min(created_vals) if created_vals else None
    created_max = max(created_vals) if created_vals else None

    by_complaint = Counter(r.get("complaint_type") or "Unknown" for r in rows)

    by_borough = Counter(r.get("borough") or "Unknown" for r in rows)
    by_status = Counter(r.get("status") or "Unknown" for r in rows)

    top_complaints = by_complaint.most_common(15)
    top_boroughs = by_borough.most_common()

    # complaint x borough (top 10 complaints, borough distribution)
    top10 = [c for c, _ in by_complaint.most_common(10)]
    complaint_borough: dict[str, dict[str, int]] = {c: Counter() for c in top10}
    for r in rows:
        c = r.get("complaint_type") or "Unknown"
        if c in complaint_borough:
            b = r.get("borough") or "Unknown"
            complaint_borough[c][b] += 1

    return {
        "created_date_min": created_min,
        "created_date_max": created_max,
        "window_start": window_start,
        "window_end": window_end,
        "total_rows": total,
        "top_complaints": top_complaints,
        "top_boroughs": top_boroughs,
        "status_counts": by_status.most_common(),
        "top10_complaint_by_borough": {k: dict(v) for k, v in complaint_borough.items()},
    }

def save_metrics(metrics: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2))
