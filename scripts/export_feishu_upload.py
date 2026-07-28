#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import html
import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATA_JSON = ROOT / "dashboard/data/latest.json"
DATA_CSV = ROOT / "dashboard/data/latest.csv"
OUT_DIR = ROOT / "exports/feishu-upload"


def fmt_int(value: Any) -> str:
    return f"{int(float(value or 0)):,}"


def fmt_money(value: Any) -> str:
    return f"${float(value or 0):,.0f}"


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def safe(value: Any) -> str:
    return html.escape(str(value or ""))


def read_payload() -> Dict[str, Any]:
    return json.loads(DATA_JSON.read_text(encoding="utf-8"))


def read_rows(limit: int = 50) -> List[Dict[str, str]]:
    with DATA_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))[:limit]


def bar_rows(items: Iterable[Tuple[str, Any]], label: str) -> str:
    pairs = [(str(name), float(value or 0)) for name, value in items]
    max_value = max((value for _, value in pairs), default=1)
    rows = []
    for name, value in pairs:
        width = max(2, value / max_value * 100)
        rows.append(
            f"""
            <tr>
              <td class="name">{safe(name)}</td>
              <td>
                <div class="bar-track"><div class="bar" style="width:{width:.2f}%"></div></div>
              </td>
              <td class="num">{fmt_int(value)}</td>
            </tr>
            """
        )
    return f"""
      <table class="bar-table" aria-label="{safe(label)}">
        <tbody>{''.join(rows)}</tbody>
      </table>
    """


def line_points(by_day: Dict[str, Any], width: int = 900, height: int = 220) -> List[Tuple[float, float]]:
    values = [float(v or 0) for v in by_day.values()]
    if not values:
        return []
    high = max(values)
    low = min(values)
    span = high - low or 1
    step = width / max(1, len(values) - 1)
    coords = []
    for idx, value in enumerate(values):
        x = idx * step
        y = height - ((value - low) / span * (height - 26)) - 12
        coords.append((x, y))
    return coords


def mini_day_labels(by_day: Dict[str, Any]) -> str:
    dates = list(by_day.keys())
    if not dates:
        return ""
    return f"{safe(dates[0])}<span>{safe(dates[len(dates)//2])}</span>{safe(dates[-1])}"


def top_rows_table(rows: List[Dict[str, str]]) -> str:
    rows = sorted(rows, key=lambda row: float(row.get("Views") or 0), reverse=True)
    body = []
    for row in rows:
        body.append(
            f"""
            <tr>
              <td>{safe(row.get("Date"))}</td>
              <td>{safe(row.get("Channel Group"))}</td>
              <td>{safe(row.get("Device Category"))}</td>
              <td class="path">{safe(row.get("Page Path"))}</td>
              <td class="num">{fmt_int(row.get("Views"))}</td>
              <td class="num">{fmt_int(row.get("Active Users"))}</td>
              <td class="num">{fmt_int(row.get("Conversions"))}</td>
            </tr>
            """
        )
    return "".join(body)


def render_html(payload: Dict[str, Any], sample_rows: List[Dict[str, str]]) -> str:
    summary = payload["summary"]
    by_day = summary.get("by_day", {})
    by_channel = summary.get("by_channel", {})
    top_pages = summary.get("top_pages", {})
    start = next(iter(by_day.keys()), "")
    end = next(reversed(by_day.keys()), "") if by_day else ""
    sessions = float(summary.get("sessions") or 0)
    conversions = float(summary.get("conversions") or 0)
    conversion_rate = conversions / sessions if sessions else 0
    generated = payload.get("generated_at", dt.datetime.now().isoformat(timespec="seconds"))
    daily_avg = sum(float(v or 0) for v in by_day.values()) / max(1, len(by_day))
    organic_social = float(by_channel.get("Organic Social", 0))
    social_share = organic_social / float(summary.get("total_views") or 1)
    day_points = line_points(by_day)
    day_polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in day_points)
    day_area = (
        " ".join(["0,250"] + [f"{x:.1f},{y:.1f}" for x, y in day_points] + [f"{day_points[-1][0]:.1f},250"])
        if day_points
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Seeed GA4 Dashboard - Feishu Upload</title>
  <style>
    :root {{
      --bg: #f5f7fb;
      --card: #fff;
      --ink: #17202a;
      --muted: #657286;
      --line: #d8dee8;
      --blue: #2764e6;
      --green: #16875d;
      --gold: #a66b00;
      --red: #bd3d32;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      letter-spacing: 0;
    }}
    header {{
      background: var(--card);
      border-bottom: 1px solid var(--line);
      padding: 24px 28px 16px;
    }}
    h1 {{ margin: 0 0 8px; font-size: 26px; }}
    h2 {{ margin: 0 0 12px; font-size: 16px; }}
    .meta {{ color: var(--muted); font-size: 13px; line-height: 1.55; }}
    main {{ max-width: 1280px; margin: 0 auto; padding: 20px 24px 36px; display: grid; gap: 16px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 12px; }}
    .card {{
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .label {{ color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
    .value {{ font-size: 24px; font-weight: 740; white-space: nowrap; }}
    .grid {{ display: grid; grid-template-columns: 1.45fr 1fr; gap: 16px; }}
    .chart-wrap {{ width: 100%; overflow: hidden; }}
    svg {{ width: 100%; height: 260px; display: block; }}
    .axis {{ display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; margin-top: 4px; }}
    .bar-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    .bar-table td {{ padding: 7px 6px; vertical-align: middle; }}
    .bar-table .name {{ width: 34%; color: var(--ink); overflow-wrap: anywhere; }}
    .bar-track {{ height: 12px; background: #edf1f7; border-radius: 999px; overflow: hidden; }}
    .bar {{ height: 100%; background: var(--green); border-radius: 999px; }}
    .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    table.detail {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    .detail th, .detail td {{ padding: 9px 8px; border-bottom: 1px solid var(--line); text-align: left; }}
    .detail th {{ color: var(--muted); font-weight: 650; }}
    .path {{ max-width: 440px; overflow-wrap: anywhere; }}
    .insights {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
    .insight-value {{ font-size: 20px; font-weight: 720; margin-bottom: 6px; }}
    .note {{ color: var(--muted); font-size: 12px; line-height: 1.5; }}
    @media (max-width: 980px) {{
      header, main {{ padding-left: 14px; padding-right: 14px; }}
      .kpis, .grid, .insights {{ grid-template-columns: 1fr; }}
      .value {{ font-size: 21px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Seeed GA4 Dashboard</h1>
    <div class="meta">Period: {safe(start)} to {safe(end)} · Generated: {safe(generated)} · Published rows in CSV/JSON: {fmt_int(payload.get("published_rows", 0))}</div>
  </header>
  <main>
    <section class="kpis">
      <div class="card"><div class="label">Views</div><div class="value">{fmt_int(summary.get("total_views"))}</div></div>
      <div class="card"><div class="label">Active Users</div><div class="value">{fmt_int(summary.get("active_users"))}</div></div>
      <div class="card"><div class="label">Sessions</div><div class="value">{fmt_int(summary.get("sessions"))}</div></div>
      <div class="card"><div class="label">Conversions</div><div class="value">{fmt_int(summary.get("conversions"))}</div></div>
      <div class="card"><div class="label">Revenue</div><div class="value">{fmt_money(summary.get("revenue"))}</div></div>
    </section>

    <section class="insights">
      <div class="card"><div class="label">Avg Daily Views</div><div class="insight-value">{fmt_int(daily_avg)}</div><div class="note">Calculated from the selected 28-day GA4 window.</div></div>
      <div class="card"><div class="label">Conversion Rate</div><div class="insight-value">{pct(conversion_rate)}</div><div class="note">Conversions divided by sessions.</div></div>
      <div class="card"><div class="label">Organic Social Share</div><div class="insight-value">{pct(social_share)}</div><div class="note">GA4 Organic Social channel views inside this website dataset.</div></div>
    </section>

    <section class="grid">
      <div class="card">
        <h2>Views by Day</h2>
        <div class="chart-wrap">
          <svg viewBox="0 0 900 260" role="img" aria-label="Views by day">
            <defs>
              <linearGradient id="area" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#2764e6" stop-opacity=".25"/>
                <stop offset="100%" stop-color="#2764e6" stop-opacity=".02"/>
              </linearGradient>
            </defs>
            <polygon points="{day_area}" fill="url(#area)" stroke="none"/>
            <polyline points="{day_polyline}" fill="none" stroke="#2764e6" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </div>
        <div class="axis">{mini_day_labels(by_day)}</div>
      </div>
      <div class="card">
        <h2>Views by Channel</h2>
        {bar_rows(by_channel.items(), "Views by channel")}
      </div>
    </section>

    <section class="card">
      <h2>Top Pages</h2>
      {bar_rows(top_pages.items(), "Top pages")}
    </section>

    <section class="card">
      <h2>Top Detail Rows</h2>
      <table class="detail">
        <thead>
          <tr><th>Date</th><th>Channel</th><th>Device</th><th>Page Path</th><th class="num">Views</th><th class="num">Users</th><th class="num">Conversions</th></tr>
        </thead>
        <tbody>{top_rows_table(sample_rows)}</tbody>
      </table>
    </section>
  </main>
</body>
</html>
"""


def write_readme(payload: Dict[str, Any]) -> str:
    summary = payload["summary"]
    return f"""# Feishu upload package

Files:

- seeed-ga4-dashboard.html: upload this file to Feishu Drive or attach it to a Feishu doc.
- seeed-ga4-dashboard.csv: import this file into Feishu Sheets or Base when tabular data is needed.
- seeed-ga4-feishu-upload.zip: the same files packaged for sending.

Current data:

- Views: {fmt_int(summary.get("total_views"))}
- Active users: {fmt_int(summary.get("active_users"))}
- Sessions: {fmt_int(summary.get("sessions"))}
- Conversions: {fmt_int(summary.get("conversions"))}
- Revenue: {fmt_money(summary.get("revenue"))}

This package is generated from dashboard/data/latest.json and dashboard/data/latest.csv.
"""


def main() -> None:
    payload = read_payload()
    sample_rows = read_rows()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    html_path = OUT_DIR / "seeed-ga4-dashboard.html"
    csv_path = OUT_DIR / "seeed-ga4-dashboard.csv"
    readme_path = OUT_DIR / "README-upload.md"
    zip_path = OUT_DIR / "seeed-ga4-feishu-upload.zip"

    html_path.write_text(render_html(payload, sample_rows), encoding="utf-8")
    shutil.copyfile(DATA_CSV, csv_path)
    readme_path.write_text(write_readme(payload), encoding="utf-8")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in (html_path, csv_path, readme_path):
            zf.write(path, arcname=path.name)

    print(json.dumps({
        "ok": True,
        "html": str(html_path),
        "csv": str(csv_path),
        "readme": str(readme_path),
        "zip": str(zip_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
