from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

REPORTS_DIR = Path("reports")
SITE_DIR = Path("site")
SITE_REPORTS_DIR = SITE_DIR / "reports"
ASSETS_DIR = SITE_DIR / "assets"
DASHBOARD_PATH = Path("data/processed/dashboard.json")
NARRATIVE_JSON_PATH = Path("data/processed/narrative.json")


def latest_report_md() -> Path:
    files = sorted(REPORTS_DIR.glob("weekly_report_*.md"))
    if not files:
        raise FileNotFoundError("No weekly_report_*.md found in /reports")
    return files[-1]


def _load_json_optional(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def md_to_html(md_path: Path) -> str:
    md = MarkdownIt()
    return md.render(md_path.read_text(encoding="utf-8"))


def _rel_site_path(path_str: str) -> str:
    p = Path(path_str)
    if p.parts and p.parts[0] == "site":
        return str(Path(*p.parts[1:]))
    return path_str


def _render_cards(dashboard: dict[str, Any] | None) -> str:
    headline = (dashboard or {}).get("headline") or {}
    complaint_mv = ((dashboard or {}).get("complaint_movers") or {}).get("top_movers") or []
    borough_mv = [
        mv for mv in ((dashboard or {}).get("borough_share") or {}).get("top_movers") or []
        if mv.get("name") not in {"Unspecified", "Unknown / Unspecified (missing borough)"}
    ]
    comp = complaint_mv[0] if complaint_mv else {}
    bor = borough_mv[0] if borough_mv else {}
    labels = (dashboard or {}).get("labels") or {}

    cards = [
        {
            "label": "Weekly total",
            "value": f"{int(headline.get('weekly_total') or 0):,}",
            "detail": "Requests in the reporting window",
        },
        {
            "label": "Volume vs baseline",
            "value": f"{headline.get('overall_volume_vs_baseline_pct'):+.1f}%"
            if isinstance(headline.get("overall_volume_vs_baseline_pct"), (int, float))
            else "n/a",
            "detail": f"z={headline.get('overall_volume_z'):.2f}" if isinstance(headline.get("overall_volume_z"), (int, float)) else "no z-score",
        },
        {
            "label": labels.get("complaint_mover", "Biggest complaint shift"),
            "value": comp.get("name", "n/a"),
            "detail": f"{(comp.get('delta_pp') or 0):+.1f} percentage points vs baseline" if comp else "n/a",
        },
        {
            "label": labels.get("borough_mover", "Biggest borough shift"),
            "value": bor.get("name", "n/a"),
            "detail": f"{(bor.get('delta_pp') or 0):+.1f} percentage points vs baseline" if bor else "n/a",
        },
    ]
    card_html = []
    for card in cards:
        card_html.append(
            f"""
            <div class="card">
              <p class="eyebrow">{card['label']}</p>
              <p class="metric">{card['value']}</p>
              <p class="detail">{card['detail']}</p>
            </div>
            """
        )
    return "\n".join(card_html)


def _render_movers_table(dashboard: dict[str, Any] | None) -> str:
    if not dashboard:
        return ""
    rows: list[str] = []
    movers: list[tuple[str, dict[str, Any]]] = []
    for mv in (dashboard.get("complaint_movers") or {}).get("top_movers", []):
        movers.append(("Complaint", mv))
    for mv in (dashboard.get("borough_share") or {}).get("top_movers", []):
        if mv.get("name") in {"Unspecified", "Unknown / Unspecified (missing borough)"}:
            continue
        movers.append(("Borough", mv))
    if not movers:
        return ""
    movers = sorted(
        movers,
        key=lambda pair: abs(pair[1].get("z") if isinstance(pair[1].get("z"), (int, float)) else pair[1].get("delta_pp") or 0),
        reverse=True,
    )[:8]
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
    for mtype, mv in movers:
        delta = mv.get("delta_pp")
        z_val = mv.get("z")
        delta_str = f"{delta:+.1f} pp" if isinstance(delta, (int, float)) else "n/a"
        z_str = f"{z_val:.2f}" if isinstance(z_val, (int, float)) else "n/a"
        label = _z_label(z_val)
        shift_cell = f"{delta_str} · {label}" if label and delta_str != "n/a" else delta_str
        rows.append(
            f"<tr><td>{mtype}</td><td>{mv.get('name','Unknown')}</td><td>{shift_cell}</td><td>{z_str}</td></tr>"
        )
    table = """
    <table class="movers">
      <thead><tr><th>Type</th><th>Name</th><th>Shift (percentage points)</th><th>Unusualness (z-score)</th></tr></thead>
      <tbody>
        {rows}
      </tbody>
    </table>
    """
    return table.format(rows="\n".join(rows))


def _render_chart_block(dashboard: dict[str, Any] | None) -> str:
    charts = (dashboard or {}).get("charts") or {}
    complaint = charts.get("complaint_share_dumbbell")
    heatmap = charts.get("seasonality_heatmap")
    daily = charts.get("daily_anomaly_timeline")
    pieces: list[str] = []
    if complaint:
        pieces.append(f'<figure><img src="{_rel_site_path(complaint)}" alt="Complaint share movers"><figcaption>Complaint share movers</figcaption></figure>')
    if heatmap:
        pieces.append(f'<figure><img src="{_rel_site_path(heatmap)}" alt="Complaint seasonality heatmap"><figcaption>Complaint seasonality</figcaption></figure>')
    if daily:
        pieces.append(f'<figure><img src="{_rel_site_path(daily)}" alt="Daily anomalies timeline"><figcaption>Daily anomalies</figcaption></figure>')
    if not pieces:
        return ""
    return '<div class="chart-grid">' + "\n".join(pieces) + "</div>"


def _fallback_changes(dashboard: dict[str, Any] | None) -> str:
    if not dashboard:
        return "<p class='detail'>No statistically notable deviations this week; see seasonality chart for expected patterns.</p>"
    anomalies = (dashboard.get("anomalies") or {})
    spikes = (anomalies.get("daily_spikes") or {}).get("spikes") or []
    season = anomalies.get("complaint_seasonality") or {}
    inc = season.get("increases") or []
    dec = season.get("decreases") or []
    movers = (dashboard.get("complaint_movers") or {}).get("top_movers") or []

    bullets: list[str] = []
    if spikes:
        top = spikes[0]
        z_val = top.get("z")
        z_str = f"{z_val:.1f}" if isinstance(z_val, (int, float)) else "n/a"
        bullets.append(f"Daily spike on {top.get('day')}: {top.get('count')} requests (z={z_str})")
    for entry in inc:
        z_val = entry.get("z_month")
        z_str = f"{z_val:.1f}" if isinstance(z_val, (int, float)) else "n/a"
        bullets.append(
            f"{entry.get('name','')} share higher than seasonal norm (z={z_str}, Δ={entry.get('delta_pp') or 0:+.1f}pp)"
        )
    for entry in dec:
        z_val = entry.get("z_month")
        z_str = f"{z_val:.1f}" if isinstance(z_val, (int, float)) else "n/a"
        bullets.append(
            f"{entry.get('name','')} share lower than seasonal norm (z={z_str}, Δ={entry.get('delta_pp') or 0:+.1f}pp)"
        )
    if movers:
        mv = movers[0]
        bullets.append(
            f"Biggest composition shift: {mv.get('name','Unknown')} ({mv.get('delta_pp') or 0:+.1f} percentage points vs baseline)"
        )
    if not bullets:
        bullets.append("No statistically notable deviations this week; see seasonality chart for expected patterns.")
    return "<ul>" + "".join(f"<li>{b}</li>" for b in bullets) + "</ul>"


def _render_narrative(narrative: dict[str, Any] | None, dashboard: dict[str, Any] | None) -> str:
    parts: list[str] = []
    if narrative:
        headlines = narrative.get("headlines") or []
        if headlines:
            parts.append("<div class='pill'>Headlines</div>")
            parts.append("<ul>" + "".join(f"<li><strong>{h.get('title','')}</strong>: {h.get('detail','')}</li>" for h in headlines) + "</ul>")
        changes = narrative.get("what_changed") or []
        if changes:
            parts.append("<div class='pill'>What changed</div>")
            parts.append("<ul>" + "".join(f"<li><strong>{h.get('title','')}</strong>: {h.get('detail','')}</li>" for h in changes) + "</ul>")
        summary = narrative.get("summary")
        if summary:
            parts.append(f"<p class='summary'>{summary}</p>")
        dq_note = narrative.get("data_quality_note")
        if dq_note:
            parts.append(f"<p class='dq-note'>Data quality note: {dq_note}</p>")
    else:
        parts.append(_fallback_changes(dashboard))
    return "\n".join(parts)


def _build_index_html(dashboard: dict[str, Any] | None, narrative: dict[str, Any] | None, latest_report_link: str) -> str:
    window = (dashboard or {}).get("window") or {}
    dq = ((dashboard or {}).get("headline") or {}).get("data_quality") or {}
    warning_badge = (
        "<span class='badge warning'>Data warning</span>" if (dq.get("warnings") or dq.get("weekly_lag_days")) else "<span class='badge ok'>Fresh</span>"
    )
    charts_block = _render_chart_block(dashboard)
    movers_table = _render_movers_table(dashboard)
    narrative_block = _render_narrative(narrative, dashboard)
    dq_items: list[str] = []
    if dq.get("weekly_lag_days") is not None:
        dq_items.append(f"Weekly latest record lags {dq.get('weekly_lag_days')} days")
    if dq.get("weekly_unique_days_count") is not None and dq.get("weekly_expected_days") is not None:
        dq_items.append(
            f"Weekly coverage: {dq.get('weekly_unique_days_count')}/{dq.get('weekly_expected_days')} days "
            f"(missing ~{dq.get('weekly_missing_days_est')} days)"
        )
    if dq.get("unspecified_borough_share") is not None:
        share_val = dq.get("unspecified_borough_share")
        dq_items.append(f"Unspecified borough share: {share_val:.2%}")
        if dq.get("unspecified_borough_baseline_z") is not None:
            z_val = dq.get("unspecified_borough_baseline_z")
            dq_items.append(f"Unspecified borough anomaly z={z_val:.2f}")
    if dq.get("trend_latest_day"):
        lag = dq.get("trend_lag_days")
        dq_items.append(f"Trend latest day {dq.get('trend_latest_day')} (lag: {lag} days)" if lag is not None else f"Trend latest day {dq.get('trend_latest_day')}")
    dq_items.extend(dq.get("warnings") or [])
    dq_list_html = "".join(f"<li>{item}</li>" for item in dq_items) or "<li>No warnings reported.</li>"

    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NYC 311 Weekly Insights</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600&family=Manrope:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0f172a;
      --panel: rgba(255,255,255,0.05);
      --card: rgba(255,255,255,0.08);
      --accent: #38bdf8;
      --accent-2: #a855f7;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --border: rgba(255,255,255,0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: 'Space Grotesk', 'Manrope', 'Segoe UI', sans-serif;
      background: linear-gradient(120deg, #0b1220, #111827);
      color: var(--text);
      padding: 24px;
    }}
    a {{ color: var(--accent); text-decoration: none; }}
    header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .title-block h1 {{ margin: 0; font-size: 28px; }}
    .title-block p {{ margin: 4px 0 0; color: var(--muted); }}
    .badge {{
      padding: 6px 10px;
      border-radius: 999px;
      font-size: 12px;
      letter-spacing: 0.5px;
      border: 1px solid var(--border);
      background: var(--panel);
      color: var(--text);
    }}
    .badge.warning {{ border-color: #f97316; color: #f8fafc; }}
    .badge.ok {{ border-color: #22c55e; color: #f8fafc; }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 16px;
      margin-top: 16px;
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      box-shadow: 0 12px 32px rgba(0,0,0,0.25);
    }}
    .eyebrow {{ margin: 0; text-transform: uppercase; letter-spacing: 0.08em; font-size: 12px; color: var(--muted); }}
    .metric {{ margin: 6px 0; font-size: 24px; font-weight: 600; color: #f8fafc; }}
    .detail {{ margin: 0; color: var(--muted); font-size: 14px; }}
    .chart-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
      margin-top: 8px;
    }}
    figure {{ margin: 0; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 12px; }}
    figcaption {{ margin-top: 6px; color: var(--muted); font-size: 14px; }}
    img {{ max-width: 100%; height: auto; border-radius: 8px; display: block; }}
    h2 {{ margin: 12px 0 6px; }}
    ul {{ padding-left: 18px; color: var(--text); }}
    .pill {{
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      background: var(--card);
      border: 1px solid var(--border);
      font-size: 12px;
      color: var(--muted);
      margin: 4px 0;
    }}
    .summary {{ color: var(--text); }}
    .dq-note {{ color: #f97316; font-weight: 600; }}
    table.movers {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 6px;
      color: var(--text);
    }}
    table.movers th, table.movers td {{
      border: 1px solid var(--border);
      padding: 8px;
      text-align: left;
    }}
    table.movers th {{ background: rgba(255,255,255,0.04); }}
    .glossary {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 10px 12px;
      max-width: 100%;
      color: var(--muted);
      font-size: 13px;
      margin: 6px 0;
    }}
    .glossary summary {{
      cursor: pointer;
      font-weight: 600;
      color: var(--text);
      list-style: none;
    }}
    .glossary summary::-webkit-details-marker {{ display: none; }}
    .glossary summary::after {{
      content: "▾";
      margin-left: 6px;
      font-size: 11px;
      color: var(--muted);
    }}
    .glossary[open] summary::after {{ content: "▴"; }}
    .glossary ul {{
      margin: 6px 0 0 12px;
      padding: 0;
    }}
    .glossary li {{
      margin-bottom: 4px;
    }}
    .footer {{ color: var(--muted); margin-top: 16px; font-size: 13px; }}
    @media (max-width: 640px) {{
      body {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="title-block">
      <h1>NYC 311 Weekly Insights</h1>
      <p>Window: {window.get('start','?')} → {window.get('end','?')}</p>
    </div>
    {warning_badge}
  </header>

  <section class="panel">
    <div class="card-grid">
      {_render_cards(dashboard)}
    </div>
  </section>

  <section class="panel">
    <h2>What changed this week</h2>
    {narrative_block if narrative_block else "<p class='detail'>Narrative not yet generated.</p>"}
  </section>

  <section class="panel">
    <h2>Charts</h2>
    {charts_block if charts_block else "<p class='detail'>Charts not available yet.</p>"}
  </section>

  <section class="panel">
    <h2>Biggest shifts vs normal</h2>
    <details class="glossary">
      <summary>What do these terms mean?</summary>
      <ul>
        <li><strong>Shift</strong>: change in share vs typical levels (percentage points)</li>
        <li><strong>Unusualness (z-score)</strong>: how far from normal the change is</li>
        <li>|z| ≈ 1 → small/common; |z| ≈ 2 → notable; |z| ≥ 3 → rare</li>
      </ul>
    </details>
    {movers_table if movers_table else "<p class='detail'>Mover signals will appear after dashboard is built.</p>"}
  </section>

  <section class="panel">
    <h2>Data quality</h2>
    <ul>
      {dq_list_html}
    </ul>
    <p class="detail"><a href="{latest_report_link}">View latest full report</a></p>
  </section>

  <p class="footer">Generated {datetime.now().strftime("%Y-%m-%d %H:%M")} by nyc311-weekly-report.</p>
</body>
</html>
"""


def publish_latest() -> tuple[Path, Path]:
    dashboard = _load_json_optional(DASHBOARD_PATH)
    narrative = _load_json_optional(NARRATIVE_JSON_PATH)
    md_path = latest_report_md()
    html_body = md_to_html(md_path)

    SITE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    report_html_name = md_path.with_suffix(".html").name
    report_html_path = SITE_REPORTS_DIR / report_html_name

    report_template = """<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <style>
    body {{ max-width: 960px; margin: 32px auto; padding: 0 16px; font-family: 'Manrope', 'Segoe UI', sans-serif; line-height: 1.6; color: #0f172a; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
    th, td {{ border: 1px solid #e5e7eb; padding: 8px; text-align: left; }}
    th {{ background: #f8fafc; }}
    code {{ background: #f1f5f9; padding: 2px 4px; border-radius: 4px; }}
    h1, h2 {{ margin-top: 28px; }}
    .meta {{ color: #475569; }}
    img {{ max-width: 100%; height: auto; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""
    report_html_path.write_text(report_template.format(title=md_path.stem.replace("_", " "), body=html_body), encoding="utf-8")

    latest_link = f"reports/{report_html_name}"
    index_path = SITE_DIR / "index.html"
    index_path.write_text(_build_index_html(dashboard, narrative, latest_link), encoding="utf-8")

    return index_path, report_html_path


if __name__ == "__main__":
    publish_latest()
