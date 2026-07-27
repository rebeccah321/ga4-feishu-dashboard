#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import html
import json
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]


BASE_FIELDS = [
    {"type": "text", "name": "Key"},
    {"type": "datetime", "name": "Date", "style": {"format": "yyyy/MM/dd"}},
    {"type": "text", "name": "Hostname"},
    {"type": "text", "name": "Page Path"},
    {"type": "text", "name": "Page Title"},
    {
        "type": "select",
        "name": "Channel Group",
        "multiple": False,
        "options": [
            {"name": "Organic Search"},
            {"name": "Direct"},
            {"name": "Referral"},
            {"name": "Paid Search"},
            {"name": "Paid Social"},
            {"name": "Organic Social"},
            {"name": "Email"},
            {"name": "Unassigned"}
        ]
    },
    {
        "type": "select",
        "name": "Device Category",
        "multiple": False,
        "options": [
            {"name": "desktop"},
            {"name": "mobile"},
            {"name": "tablet"},
            {"name": "smart tv"}
        ]
    },
    {"type": "number", "name": "Views", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "Active Users", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "Sessions", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "Engagement Rate", "style": {"type": "plain", "precision": 4, "percentage": True}},
    {"type": "number", "name": "Bounce Rate", "style": {"type": "plain", "precision": 4, "percentage": True}},
    {"type": "number", "name": "Avg Engagement Seconds", "style": {"type": "plain", "precision": 2}},
    {"type": "number", "name": "Conversions", "style": {"type": "plain", "precision": 0, "thousands_separator": True}},
    {"type": "number", "name": "Revenue", "style": {"type": "currency", "precision": 2, "currency_code": "USD"}},
    {"type": "text", "name": "Week Start"},
    {"type": "datetime", "name": "Loaded At", "style": {"format": "yyyy/MM/dd HH:mm"}}
]


CSV_FIELDS = [
    "Key",
    "Date",
    "Hostname",
    "Page Path",
    "Page Title",
    "Channel Group",
    "Device Category",
    "Views",
    "Active Users",
    "Sessions",
    "Engagement Rate",
    "Bounce Rate",
    "Avg Engagement Seconds",
    "Conversions",
    "Revenue",
    "Week Start",
    "Loaded At"
]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def resolve_config_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() and path.exists():
        return path
    root_path = ROOT / path
    if root_path.exists():
        return root_path
    cwd_path = Path.cwd() / path
    if cwd_path.exists():
        return cwd_path
    return root_path


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def date_range(lookback_days: int) -> tuple[str, str]:
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=lookback_days - 1)
    return start.isoformat(), end.isoformat()


def week_start(value: str) -> str:
    date = dt.datetime.strptime(value, "%Y-%m-%d").date()
    return (date - dt.timedelta(days=date.weekday())).isoformat()


def normalize_date(value: str) -> str:
    if "-" in value:
        return value
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def number(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def int_number(value: Any) -> int:
    return int(round(number(value)))


def build_key(row: Dict[str, Any]) -> str:
    parts = [
        row["Date"],
        row["Hostname"],
        row["Page Path"],
        row["Channel Group"],
        row["Device Category"]
    ]
    return "|".join(str(part).replace("|", "/") for part in parts)


def normalize_ga4_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    loaded_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized: List[Dict[str, Any]] = []
    for raw in rows:
        date = normalize_date(str(raw.get("date", "")))
        engagement_seconds = number(raw.get("userEngagementDuration"))
        sessions = max(int_number(raw.get("sessions")), 1)
        row = {
            "Date": date,
            "Hostname": raw.get("hostName") or "(not set)",
            "Page Path": raw.get("pagePath") or "/",
            "Page Title": raw.get("pageTitle") or "",
            "Channel Group": raw.get("sessionDefaultChannelGroup") or "Unassigned",
            "Device Category": raw.get("deviceCategory") or "desktop",
            "Views": int_number(raw.get("screenPageViews")),
            "Active Users": int_number(raw.get("activeUsers")),
            "Sessions": int_number(raw.get("sessions")),
            "Engagement Rate": number(raw.get("engagementRate")),
            "Bounce Rate": number(raw.get("bounceRate")),
            "Avg Engagement Seconds": round(engagement_seconds / sessions, 2),
            "Conversions": int_number(raw.get("conversions", raw.get("keyEvents"))),
            "Revenue": round(number(raw.get("totalRevenue")), 2),
            "Loaded At": loaded_at
        }
        row["Week Start"] = week_start(date)
        row["Key"] = build_key(row)
        normalized.append(row)
    return normalized


def write_csv(rows: List[Dict[str, Any]], path: Path) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def mock_rows(start_date: str, end_date: str) -> List[Dict[str, Any]]:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    paths = [
        ("/", "Home"),
        ("/category/solutions", "Solutions"),
        ("/reference-designs", "Reference Designs"),
        ("/case-studies", "Case Studies"),
        ("/contact", "Contact")
    ]
    channels = ["Organic Search", "Direct", "Referral", "Paid Search"]
    devices = ["desktop", "mobile", "tablet"]
    rows = []
    rng = random.Random(42)
    current = start
    while current <= end:
        for path, title in paths:
            for channel in channels:
                views = rng.randint(40, 1200)
                users = max(1, int(views * rng.uniform(0.35, 0.75)))
                sessions = max(users, int(users * rng.uniform(1.05, 1.45)))
                rows.append({
                    "date": current.strftime("%Y%m%d"),
                    "hostName": "www.example.com",
                    "pagePath": path,
                    "pageTitle": title,
                    "sessionDefaultChannelGroup": channel,
                    "deviceCategory": rng.choice(devices),
                    "screenPageViews": views,
                    "activeUsers": users,
                    "sessions": sessions,
                    "engagementRate": round(rng.uniform(0.45, 0.78), 4),
                    "bounceRate": round(rng.uniform(0.22, 0.55), 4),
                    "userEngagementDuration": round(sessions * rng.uniform(38, 145), 2),
                    "conversions": rng.randint(0, 28),
                    "totalRevenue": round(rng.uniform(0, 1400), 2)
                })
        current += dt.timedelta(days=1)
    return rows


def fetch_ga4_rows(config: Dict[str, Any], start_date: str, end_date: str) -> List[Dict[str, Any]]:
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
    except ImportError as exc:
        raise SystemExit("Missing GA4 dependencies. Run: pip install -r requirements.txt") from exc

    ga4_cfg = config["ga4"]
    property_id = os.environ.get(ga4_cfg.get("property_id_env", "GA4_PROPERTY_ID"))
    if not property_id:
        raise SystemExit("GA4 property id is missing. Set GA4_PROPERTY_ID in .env.")

    transport = os.environ.get("GA4_API_TRANSPORT", "rest")
    client = BetaAnalyticsDataClient(transport=transport)
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name=name) for name in ga4_cfg["dimensions"]],
        metrics=[Metric(name=name) for name in ga4_cfg["metrics"]],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        limit=int(ga4_cfg.get("row_limit", 100000))
    )
    response = client.run_report(request)
    rows: List[Dict[str, Any]] = []
    for item in response.rows:
        row: Dict[str, Any] = {}
        for header, value in zip(response.dimension_headers, item.dimension_values):
            row[header.name] = value.value
        for header, value in zip(response.metric_headers, item.metric_values):
            row[header.name] = value.value
        rows.append(row)
    return rows


def run_cli(args: List[str], dry_run: bool = False) -> Dict[str, Any]:
    cmd = ["lark-cli", *args]
    if dry_run:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    text = proc.stdout.strip() or proc.stderr.strip()
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{text}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"ok": True, "raw": text}


def base_profile(config: Dict[str, Any]) -> str:
    return os.environ.get(config["base"].get("profile_env", "LARK_CLI_PROFILE"), "rebecca-agent")


def create_base(config: Dict[str, Any], dry_run: bool = False) -> Dict[str, Any]:
    base_cfg = config["base"]
    args = [
        "base", "+base-create",
        "--profile", base_profile(config),
        "--as", "user",
        "--name", base_cfg["base_name"],
        "--time-zone", config.get("timezone", "Asia/Shanghai"),
        "--table-name", base_cfg["table_name"],
        "--fields", json.dumps(BASE_FIELDS, ensure_ascii=False),
        "--format", "json"
    ]
    return run_cli(args, dry_run=dry_run)


def create_dashboard(config: Dict[str, Any], base_token: str, dry_run: bool = False) -> Dict[str, Any]:
    args = [
        "base", "+dashboard-create",
        "--profile", base_profile(config),
        "--as", "user",
        "--base-token", base_token,
        "--name", config["base"]["dashboard_name"],
        "--format", "json"
    ]
    return run_cli(args, dry_run=dry_run)


def create_dashboard_blocks(config: Dict[str, Any], base_token: str, dashboard_id: str, dry_run: bool = False) -> None:
    table_name = config["base"]["table_name"]
    blocks = [
        ("Total Views", "statistics", {"table_name": table_name, "series": [{"field_name": "Views", "rollup": "SUM"}]}),
        ("Active Users", "statistics", {"table_name": table_name, "series": [{"field_name": "Active Users", "rollup": "SUM"}]}),
        ("Conversions", "statistics", {"table_name": table_name, "series": [{"field_name": "Conversions", "rollup": "SUM"}]}),
        ("Views by Day", "line", {"table_name": table_name, "series": [{"field_name": "Views", "rollup": "SUM"}], "group_by": [{"field_name": "Date", "mode": "integrated"}]}),
        ("Views by Channel", "column", {"table_name": table_name, "series": [{"field_name": "Views", "rollup": "SUM"}], "group_by": [{"field_name": "Channel Group"}]}),
        ("Device Split", "pie", {"table_name": table_name, "series": [{"field_name": "Sessions", "rollup": "SUM"}], "group_by": [{"field_name": "Device Category"}]})
    ]
    for name, block_type, data_config in blocks:
        args = [
            "base", "+dashboard-block-create",
            "--profile", base_profile(config),
            "--as", "user",
            "--base-token", base_token,
            "--dashboard-id", dashboard_id,
            "--name", name,
            "--type", block_type,
            "--data-config", json.dumps(data_config, ensure_ascii=False),
            "--format", "json"
        ]
        run_cli(args, dry_run=dry_run)
    arrange_args = [
        "base", "+dashboard-arrange",
        "--profile", base_profile(config),
        "--as", "user",
        "--base-token", base_token,
        "--dashboard-id", dashboard_id,
        "--format", "json"
    ]
    run_cli(arrange_args, dry_run=dry_run)


def row_to_base_json(row: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in CSV_FIELDS:
        value: Any = row.get(key, "")
        if key in {"Views", "Active Users", "Sessions", "Conversions"}:
            value = int_number(value)
        elif key in {"Engagement Rate", "Bounce Rate", "Avg Engagement Seconds", "Revenue"}:
            value = number(value)
        out[key] = value
    return out


def find_record_id(config: Dict[str, Any], base_token: str, table_id: str, key: str) -> Optional[str]:
    args = [
        "base", "+record-search",
        "--profile", base_profile(config),
        "--base-token", base_token,
        "--table-id", table_id,
        "--keyword", key,
        "--search-field", "Key",
        "--field-id", "Key",
        "--limit", "1",
        "--format", "json"
    ]
    result = run_cli(args)
    data = result.get("data", result)
    items = data.get("items") if isinstance(data, dict) else None
    if not items:
        return None
    return items[0].get("record_id") or items[0].get("id")


def push_base(config: Dict[str, Any], csv_path: Path, dry_run: bool = False, limit: Optional[int] = None) -> None:
    base_cfg = config["base"]
    base_token = os.environ.get(base_cfg.get("base_token_env", "LARK_BASE_TOKEN"))
    table_id = os.environ.get(base_cfg.get("table_id_env", "LARK_BASE_TABLE_ID"))
    if not base_token or not table_id:
        raise SystemExit("LARK_BASE_TOKEN and LARK_BASE_TABLE_ID are required to push rows.")
    rows = read_csv(csv_path)
    if limit:
        rows = rows[:limit]
    for row in rows:
        record_id = None if dry_run else find_record_id(config, base_token, table_id, row["Key"])
        args = [
            "base", "+record-upsert",
            "--profile", base_profile(config),
            "--as", "user",
            "--base-token", base_token,
            "--table-id", table_id,
            "--json", json.dumps(row_to_base_json(row), ensure_ascii=False),
            "--format", "json"
        ]
        if record_id:
            args.extend(["--record-id", record_id])
        run_cli(args, dry_run=dry_run)


def aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_views = sum(int_number(r["Views"]) for r in rows)
    users = sum(int_number(r["Active Users"]) for r in rows)
    sessions = sum(int_number(r["Sessions"]) for r in rows)
    conversions = sum(int_number(r["Conversions"]) for r in rows)
    revenue = sum(number(r["Revenue"]) for r in rows)
    by_day: Dict[str, int] = {}
    by_channel: Dict[str, int] = {}
    top_pages: Dict[str, int] = {}
    for row in rows:
        views = int_number(row["Views"])
        by_day[row["Date"]] = by_day.get(row["Date"], 0) + views
        by_channel[row["Channel Group"]] = by_channel.get(row["Channel Group"], 0) + views
        top_pages[row["Page Path"]] = top_pages.get(row["Page Path"], 0) + views
    return {
        "total_views": total_views,
        "active_users": users,
        "sessions": sessions,
        "conversions": conversions,
        "revenue": revenue,
        "by_day": dict(sorted(by_day.items())),
        "by_channel": dict(sorted(by_channel.items(), key=lambda item: item[1], reverse=True)),
        "top_pages": dict(sorted(top_pages.items(), key=lambda item: item[1], reverse=True)[:10])
    }


def render_dashboard(rows: List[Dict[str, Any]], path: Path) -> None:
    ensure_dir(path.parent)
    data = aggregate(rows)
    payload = json.dumps(data, ensure_ascii=False)
    last_loaded = max((r.get("Loaded At", "") for r in rows), default="")
    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GA4 Weekly Dashboard</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --ink: #17202a;
      --muted: #667085;
      --line: #d9dee7;
      --blue: #2f6fed;
      --green: #0f9f6e;
      --red: #cf423b;
      --gold: #b7791f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }}
    header {{
      padding: 24px 28px 12px;
      border-bottom: 1px solid var(--line);
      background: var(--panel);
    }}
    h1 {{ margin: 0 0 6px; font-size: 24px; letter-spacing: 0; }}
    .sub {{ color: var(--muted); font-size: 13px; }}
    main {{ padding: 20px 28px 32px; display: grid; gap: 16px; }}
    .kpis {{ display: grid; grid-template-columns: repeat(5, minmax(150px, 1fr)); gap: 12px; }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }}
    .label {{ color: var(--muted); font-size: 12px; margin-bottom: 8px; }}
    .value {{ font-size: 24px; font-weight: 700; }}
    .grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }}
    canvas {{ width: 100%; min-height: 280px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; }}
    th {{ color: var(--muted); font-weight: 600; }}
    @media (max-width: 900px) {{
      header, main {{ padding-left: 16px; padding-right: 16px; }}
      .kpis, .grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>GA4 Weekly Dashboard</h1>
    <div class="sub">Last loaded: {html.escape(last_loaded) or "not available"}</div>
  </header>
  <main>
    <section class="kpis">
      <div class="card"><div class="label">Views</div><div class="value" id="views"></div></div>
      <div class="card"><div class="label">Active Users</div><div class="value" id="users"></div></div>
      <div class="card"><div class="label">Sessions</div><div class="value" id="sessions"></div></div>
      <div class="card"><div class="label">Conversions</div><div class="value" id="conversions"></div></div>
      <div class="card"><div class="label">Revenue</div><div class="value" id="revenue"></div></div>
    </section>
    <section class="grid">
      <div class="card"><div class="label">Views by Day</div><canvas id="dayChart"></canvas></div>
      <div class="card"><div class="label">Views by Channel</div><canvas id="channelChart"></canvas></div>
    </section>
    <section class="card">
      <div class="label">Top Pages</div>
      <table>
        <thead><tr><th>Page Path</th><th>Views</th></tr></thead>
        <tbody id="topPages"></tbody>
      </table>
    </section>
  </main>
  <script>
    const data = {payload};
    const fmt = new Intl.NumberFormat("en-US");
    document.getElementById("views").textContent = fmt.format(data.total_views);
    document.getElementById("users").textContent = fmt.format(data.active_users);
    document.getElementById("sessions").textContent = fmt.format(data.sessions);
    document.getElementById("conversions").textContent = fmt.format(data.conversions);
    document.getElementById("revenue").textContent = "$" + fmt.format(Math.round(data.revenue));
    new Chart(document.getElementById("dayChart"), {{
      type: "line",
      data: {{ labels: Object.keys(data.by_day), datasets: [{{ label: "Views", data: Object.values(data.by_day), borderColor: "#2f6fed", backgroundColor: "rgba(47,111,237,.12)", tension: .25, fill: true }}] }},
      options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
    }});
    new Chart(document.getElementById("channelChart"), {{
      type: "bar",
      data: {{ labels: Object.keys(data.by_channel), datasets: [{{ label: "Views", data: Object.values(data.by_channel), backgroundColor: "#0f9f6e" }}] }},
      options: {{ responsive: true, plugins: {{ legend: {{ display: false }} }} }}
    }});
    document.getElementById("topPages").innerHTML = Object.entries(data.top_pages)
      .map(([path, views]) => `<tr><td>${{path}}</td><td>${{fmt.format(views)}}</td></tr>`).join("");
  </script>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def write_dashboard_data(rows: List[Dict[str, Any]], csv_path: Path, dashboard_file: Path) -> Dict[str, str]:
    data_dir = dashboard_file.parent / "data"
    ensure_dir(data_dir)
    json_path = data_dir / "latest.json"
    csv_target = data_dir / "latest.csv"
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_csv": csv_path.name,
        "summary": aggregate(rows),
        "rows": rows
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copyfile(csv_path, csv_target)
    return {"data_json": str(json_path), "data_csv": str(csv_target)}


def latest_csv(config: Dict[str, Any]) -> Path:
    out_dir = ROOT / config["outputs"]["normalized_dir"]
    files = sorted(out_dir.glob("ga4_normalized_*.csv"))
    if not files:
        raise SystemExit("No normalized CSV found. Run fetch first.")
    return files[-1]


def cmd_fetch(args: argparse.Namespace) -> None:
    load_env(ROOT / ".env")
    config = load_config(resolve_config_path(args.config))
    start, end = args.start_date, args.end_date
    if not start or not end:
        start, end = date_range(args.lookback_days or int(config["ga4"].get("lookback_days", 28)))
    raw_rows = mock_rows(start, end) if args.mock else fetch_ga4_rows(config, start, end)
    normalized = normalize_ga4_rows(raw_rows)
    stamp = f"{start}_to_{end}"
    raw_path = ROOT / config["outputs"]["raw_dir"] / f"ga4_raw_{stamp}.json"
    norm_path = ROOT / config["outputs"]["normalized_dir"] / f"ga4_normalized_{stamp}.csv"
    ensure_dir(raw_path.parent)
    raw_path.write_text(json.dumps(raw_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(normalized, norm_path)
    print(json.dumps({"ok": True, "raw": str(raw_path), "normalized": str(norm_path), "rows": len(normalized)}, ensure_ascii=False))


def cmd_setup_base(args: argparse.Namespace) -> None:
    load_env(ROOT / ".env")
    config = load_config(resolve_config_path(args.config))
    if args.print_schema:
        print(json.dumps(BASE_FIELDS, ensure_ascii=False, indent=2))
        return
    result = create_base(config, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    base_token = args.base_token or os.environ.get(config["base"].get("base_token_env", "LARK_BASE_TOKEN"))
    dashboard_id = args.dashboard_id or os.environ.get(config["base"].get("dashboard_id_env", "LARK_BASE_DASHBOARD_ID"))
    if args.dashboard and base_token:
        dash = create_dashboard(config, base_token, dry_run=args.dry_run)
        print(json.dumps(dash, ensure_ascii=False, indent=2))
    if args.dashboard_blocks and base_token and dashboard_id:
        create_dashboard_blocks(config, base_token, dashboard_id, dry_run=args.dry_run)


def cmd_push_base(args: argparse.Namespace) -> None:
    load_env(ROOT / ".env")
    config = load_config(resolve_config_path(args.config))
    csv_path = ROOT / args.csv if args.csv else latest_csv(config)
    push_base(config, csv_path, dry_run=args.dry_run, limit=args.limit)
    print(json.dumps({"ok": True, "csv": str(csv_path)}, ensure_ascii=False))


def cmd_render_dashboard(args: argparse.Namespace) -> None:
    load_env(ROOT / ".env")
    config = load_config(resolve_config_path(args.config))
    csv_path = ROOT / args.csv if args.csv else latest_csv(config)
    rows = read_csv(csv_path)
    target = ROOT / config["outputs"]["dashboard_file"]
    render_dashboard(rows, target)
    data_paths = write_dashboard_data(rows, csv_path, target)
    result = {"ok": True, "dashboard": str(target), "rows": len(rows), **data_paths}
    print(json.dumps(result, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GA4 -> Feishu Base analytics pipeline")
    parser.add_argument("--config", default=os.environ.get("PIPELINE_CONFIG", "config/pipeline.json"))
    sub = parser.add_subparsers(dest="command", required=True)

    fetch = sub.add_parser("fetch")
    fetch.add_argument("--mock", action="store_true")
    fetch.add_argument("--start-date")
    fetch.add_argument("--end-date")
    fetch.add_argument("--lookback-days", type=int, default=int(os.environ.get("PIPELINE_LOOKBACK_DAYS", "0") or 0))
    fetch.set_defaults(func=cmd_fetch)

    setup = sub.add_parser("setup-base")
    setup.add_argument("--dry-run", action="store_true")
    setup.add_argument("--print-schema", action="store_true")
    setup.add_argument("--dashboard", action="store_true")
    setup.add_argument("--dashboard-blocks", action="store_true")
    setup.add_argument("--base-token")
    setup.add_argument("--dashboard-id")
    setup.set_defaults(func=cmd_setup_base)

    push = sub.add_parser("push-base")
    push.add_argument("--csv")
    push.add_argument("--dry-run", action="store_true")
    push.add_argument("--limit", type=int)
    push.set_defaults(func=cmd_push_base)

    render = sub.add_parser("render-dashboard")
    render.add_argument("--csv")
    render.set_defaults(func=cmd_render_dashboard)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
