from __future__ import annotations

import os
from typing import Any, Dict

import requests

DATASET_ID = "erm2-nwe9"
BASE_URL = f"https://data.cityofnewyork.us/api/v3/views/{DATASET_ID}/query.json"


def soda3_query(query: str, page_number: int = 1, page_size: int = 5000) -> Any:
    token = os.getenv("NYC_OPEN_DATA_APP_TOKEN")

    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "nyc311-weekly-report (learning project)",
    }

    params = {}
    if token:
        params["app_token"] = token
        headers["X-App-Token"] = token

    payload: Dict[str, Any] = {
        "query": query,
        "page": {"pageNumber": page_number, "pageSize": page_size},
        "includeSynthetic": False,
    }

    resp = requests.post(BASE_URL, params=params, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()
