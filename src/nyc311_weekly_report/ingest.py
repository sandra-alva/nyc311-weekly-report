from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from nyc311_weekly_report.soda3 import soda3_query


def fetch_last_n_days_soda3(n_days: int = 7, page_size: int = 5000) -> tuple[list[dict[str, Any]], str, str]:
    ny_tz = ZoneInfo("America/New_York")
    now = datetime.now(ny_tz)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)   # midnight today
    start = end - timedelta(days=n_days)                           # midnight N days ago


    # SODA expects timestamps like 2025-12-22T02:20:56.000
    start_str = start.strftime("%Y-%m-%dT%H:%M:%S.000")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%S.000")

    where = f"created_date >= '{start_str}' AND created_date < '{end_str}'"

    base_query = (
        "SELECT unique_key, created_date, closed_date, agency, agency_name, "
        "complaint_type, descriptor, borough, incident_zip, status "
        f"WHERE {where} "
        "ORDER BY created_date ASC"
    )

    all_rows: list[dict[str, Any]] = []
    page_number = 1

    while True:
        page_rows = soda3_query(
            query=base_query,
            page_number=page_number,
            page_size=page_size,
        )

        if not page_rows:
            break

        all_rows.extend(page_rows)
        print(f"Fetched page {page_number}: {len(page_rows)} rows (total {len(all_rows)})")

        # If we got fewer than a full page, we’re done
        if len(page_rows) < page_size:
            break

        page_number += 1

    return all_rows, start_str, end_str



def save_raw_snapshot(rows: list[dict[str, Any]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)



