from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

from openai import OpenAI

NY_TZ = ZoneInfo("America/New_York")

DEFAULT_WEEKLY_PATH = Path("data/processed/weekly_metrics.json")
DEFAULT_TREND_PATH = Path("data/processed/trend_metrics.json")
DEFAULT_OUT_JSON = Path("data/processed/narrative.json")
DEFAULT_OUT_MD = Path("data/processed/narrative.md")
DEFAULT_MODEL = os.getenv("OPENAI_MODEL") or "gpt-5-mini"
DEFAULT_DASHBOARD_PATH = Path("data/processed/dashboard.json")


def load_weekly_metrics(path: Path = DEFAULT_WEEKLY_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_trend_metrics_optional(path: Path = DEFAULT_TREND_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_dashboard_metrics_optional(path: Path = DEFAULT_DASHBOARD_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


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


def _safe_slice(seq: list[Any], n: int) -> list[Any]:
    return seq[:n] if isinstance(seq, list) else []


def _build_weekly_payload(weekly: dict[str, Any]) -> dict[str, Any]:
    window_start = weekly.get("window_start")
    window_end = weekly.get("window_end")
    latest = weekly.get("weekly_latest_created_date")

    window_end_date = _to_ny_date(window_end)
    latest_date = _to_ny_date(latest)
    weekly_lag_days = (window_end_date - latest_date).days if window_end_date and latest_date else None

    weekly_missing_days_est = weekly.get("weekly_missing_days_est")
    weekly_incomplete_warning = None
    if (weekly_lag_days is not None and weekly_lag_days >= 2) or (
        weekly_missing_days_est is not None and weekly_missing_days_est > 0
    ):
        weekly_incomplete_warning = (
            "Weekly data appears incomplete: latest record lag is "
            f"{weekly_lag_days if weekly_lag_days is not None else 'unknown'} days; "
            f"missing ~{weekly_missing_days_est if weekly_missing_days_est is not None else 'unknown'} days"
        )

    return {
        "window_start": window_start,
        "window_end": window_end,
        "total_rows": weekly.get("total_rows"),
        "top_complaints": _safe_slice(weekly.get("top_complaints", []), 5),
        "top_boroughs": _safe_slice(weekly.get("top_boroughs", []), 5),
        "status_counts": _safe_slice(weekly.get("status_counts", []), 3),
        "data_quality": {
            "weekly_latest_created_date": latest,
            "weekly_lag_days": weekly_lag_days,
            "weekly_unique_days_count": weekly.get("weekly_unique_days_count"),
            "weekly_expected_days": weekly.get("weekly_expected_days"),
            "weekly_missing_days_est": weekly_missing_days_est,
            "weekly_incomplete_warning": weekly_incomplete_warning,
        },
    }


def _storyline_from_mover(data: dict[str, Any] | None, name_key: str) -> dict[str, Any]:
    if not data:
        return {"available": False, "reason": "insufficient coverage"}
    if data.get("available") is False:
        return {"available": False, "reason": data.get("reason", "insufficient coverage")}
    return {
        "available": True,
        "name": data.get("name") or data.get(name_key),
        "current_7d_total": data.get("current_7d_total"),
        "baseline_mean": data.get("baseline_mean"),
        "pct_change": data.get("pct_change"),
        "z_score": data.get("z_score"),
        "is_spike_high": bool(data.get("is_spike_high")),
        "is_spike_low": bool(data.get("is_spike_low")),
    }


def _build_dashboard_payload(dashboard: dict[str, Any]) -> dict[str, Any]:
    dq = (dashboard.get("headline") or {}).get("data_quality") or {}
    return {
        "window": dashboard.get("window"),
        "headline": dashboard.get("headline"),
        "borough_movers": dashboard.get("borough_share") or {},
        "complaint_movers": dashboard.get("complaint_movers") or {},
        "boring_leaders": dashboard.get("boring_leaders") or {},
        "charts": dashboard.get("charts") or {},
        "anomalies": dashboard.get("anomalies") or {},
        "warnings": dq.get("warnings") or [],
    }


def build_payload(
    *,
    dashboard: dict[str, Any] | None,
    weekly: dict[str, Any] | None = None,
    trend: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if dashboard:
        return {"dashboard": _build_dashboard_payload(dashboard)}

    if weekly is None:
        raise ValueError("weekly metrics required when dashboard is missing")

    payload: dict[str, Any] = {"weekly": _build_weekly_payload(weekly)}

    if trend:
        coverage = trend.get("coverage") or {}
        freshness = trend.get("freshness") or {}
        storylines = trend.get("storylines") or {}
        spikes = trend.get("spike_days") or []
        spike_weeks = trend.get("spike_weeks") or []
        monthly = trend.get("monthly_totals") or []

        volume = storylines.get("volume") or {}
        volume_story = {
            "current_7d_total": volume.get("value"),
            "baseline_mean": volume.get("baseline"),
            "pct_change": volume.get("pct_change"),
            "z_score": volume.get("z_score"),
            "is_spike_high": bool(volume.get("is_spike_high") or volume.get("is_spike")),
            "is_spike_low": bool(volume.get("is_spike_low")),
        }

        payload["trend"] = {
            "coverage": {
                "expected_days": coverage.get("expected_days"),
                "actual_days": coverage.get("actual_days"),
                "first_day": coverage.get("first_day"),
                "last_day": coverage.get("last_day"),
            },
            "freshness": {
                "data_fresh": freshness.get("data_fresh"),
                "last_day_age_days": freshness.get("last_day_age_days"),
                "warning": freshness.get("warning"),
            },
            "storylines": {
                "volume": volume_story,
                "top_complaint_mover": _storyline_from_mover(storylines.get("top_complaint_mover"), "complaint_type"),
                "top_borough_mover": _storyline_from_mover(storylines.get("top_borough_mover"), "borough"),
            },
            "biggest_daily_spike": {
                "day": spikes[0].get("day"),
                "count": spikes[0].get("count"),
                "z": spikes[0].get("zscore"),
            }
            if spikes
            else None,
            "spike_weeks": [
                {
                    "start": w.get("week_start"),
                    "end": w.get("week_end"),
                    "total": w.get("total"),
                    "z": w.get("zscore"),
                }
                for w in spike_weeks[:3]
            ]
            if spike_weeks
            else [],
            "monthly_totals_last_12": [
                {"month": m.get("month"), "total": m.get("total")}
                for m in monthly[-12:]
            ],
        }

    return payload


def build_messages(payload: dict[str, Any]) -> list[dict[str, Any]]:
    schema_text = (
        "Output ONLY one JSON object with keys: "
        '{"headlines":[{"title":string,"detail":string,"metric_refs":[string]}] (exactly 3 items), '
        '"what_changed":[{"title":string,"detail":string,"metric_refs":[string]}] (2-4 items), '
        '"summary":string, "data_quality_note":string, "confidence":"high"|"medium"|"low"}. '
        "Title <=60 chars, detail <=160 chars, summary <=130 words. "
        "Do not add Markdown or commentary. Include a short data quality caveat in data_quality_note when warnings are present. "
        "Do not mention long-term boring leaders unless their share is anomalous. "
        "Prefer anomaly signals: daily spikes, seasonal complaint shifts, and share movers; if none, state that no statistically notable deviations were found."
    )
    system_msg = {
        "role": "system",
        "content": (
            "You write factual, concise summaries using only the provided metrics. "
            "Do not invent causes or external events. "
            "Stay deterministic; highlight anomalies (daily spikes, seasonal complaint shifts) and share movers, not raw counts. "
            "Do not restate long-run leaders unless the dashboard flags an anomaly. "
            "Reference chart-related signals when relevant. "
            "Respond with STRICT JSON only, no markdown, no code fences."
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            schema_text
            + "\n\nMetrics payload:\n"
            + json.dumps(payload, ensure_ascii=False)
        ),
    }
    return [system_msg, user_msg]


def call_openai_for_json(messages: list[dict[str, Any]], model: str | None = None) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; cannot run narrate.")

    response_input: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        content_parts: list[dict[str, Any]] = []
        if isinstance(content, str):
            content_parts = [{"type": "input_text", "text": content}]
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    ptype = part.get("type")
                    ptext = part.get("text")
                    if ptype == "text":
                        content_parts.append({"type": "input_text", "text": ptext})
                    elif ptype == "input_text":
                        content_parts.append({"type": "input_text", "text": ptext})
        if not content_parts:
            continue
        response_input.append({"role": msg.get("role", "user"), "content": content_parts})

    client = OpenAI(api_key=api_key)
    use_model = model or DEFAULT_MODEL
    try:
        resp = client.responses.create(
            model=use_model,
            input=response_input,
            response_format={"type": "json_object"},
        )
    except TypeError as exc:
        # Older SDKs may not support response_format; fall back to basic call.
        if "response_format" in str(exc):
            resp = client.responses.create(
                model=use_model,
                input=response_input,
            )
        else:
            raise

    text = getattr(resp, "output_text", None)
    if text is None:
        try:
            text = resp.output[0].content[0].text  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"Could not extract text from OpenAI response: {exc}") from exc

    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"OpenAI returned non-JSON output: {exc}") from exc


def validate_narrative_schema(obj: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError("Narrative output must be a JSON object.")

    headlines = obj.get("headlines")
    if not isinstance(headlines, list) or len(headlines) != 3:
        raise ValueError("headlines must be a list of exactly 3 items.")
    for h in headlines:
        if not isinstance(h, dict):
            raise ValueError("Each highlight must be an object.")
        title = h.get("title")
        detail = h.get("detail")
        mrefs = h.get("metric_refs")
        if not isinstance(title, str) or len(title) > 60:
            raise ValueError("Highlight title must be a string <=60 chars.")
        if not isinstance(detail, str) or len(detail) > 160:
            raise ValueError("Highlight detail must be a string <=160 chars.")
        if not isinstance(mrefs, list) or not all(isinstance(x, str) for x in mrefs):
            raise ValueError("metric_refs must be a list of strings.")

    changes = obj.get("what_changed")
    if not isinstance(changes, list) or not (2 <= len(changes) <= 4):
        raise ValueError("what_changed must be a list of 2–4 items.")
    for h in changes:
        if not isinstance(h, dict):
            raise ValueError("Each what_changed item must be an object.")
        title = h.get("title")
        detail = h.get("detail")
        mrefs = h.get("metric_refs")
        if not isinstance(title, str) or len(title) > 60:
            raise ValueError("what_changed title must be a string <=60 chars.")
        if not isinstance(detail, str) or len(detail) > 160:
            raise ValueError("what_changed detail must be a string <=160 chars.")
        if not isinstance(mrefs, list) or not all(isinstance(x, str) for x in mrefs):
            raise ValueError("metric_refs must be a list of strings.")

    summary = obj.get("summary")
    if not isinstance(summary, str):
        raise ValueError("summary must be a string.")
    if len(summary.split()) > 130:
        raise ValueError("summary must be <=130 words.")

    data_quality_note = obj.get("data_quality_note", "")
    if not isinstance(data_quality_note, str):
        raise ValueError("data_quality_note must be a string.")
    obj["data_quality_note"] = data_quality_note

    confidence = obj.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        raise ValueError("confidence must be one of: high, medium, low.")

    if payload:
        warnings_present = False
        if payload.get("dashboard"):
            dq = (payload["dashboard"].get("headline") or {}).get("data_quality") or {}
            if dq.get("warnings"):
                warnings_present = True
        else:
            dq = ((payload.get("weekly") or {}).get("data_quality")) or {}
            if dq.get("weekly_incomplete_warning"):
                warnings_present = True
            trend = payload.get("trend") or {}
            freshness = trend.get("freshness") or {}
            if freshness.get("warning"):
                warnings_present = True
        if warnings_present and confidence != "low":
            obj["confidence"] = "low"
        if warnings_present and not obj.get("data_quality_note"):
            obj["data_quality_note"] = "Data quality warnings present in metrics."

    return obj


def render_markdown(narrative_obj: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("## Narrative")
    if narrative_obj.get("headlines"):
        lines.append("### Headlines")
        for h in narrative_obj.get("headlines", []):
            lines.append(f"- **{h.get('title','')}**: {h.get('detail','')}")
        lines.append("")
    if narrative_obj.get("what_changed"):
        lines.append("### What changed")
        for h in narrative_obj.get("what_changed", []):
            lines.append(f"- **{h.get('title','')}**: {h.get('detail','')}")
        lines.append("")
    summary = narrative_obj.get("summary", "")
    if summary:
        lines.append(summary)
        lines.append("")
    dq_note = narrative_obj.get("data_quality_note")
    if dq_note:
        lines.append(f"_Data quality note: {dq_note}_")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def save_json(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def save_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
