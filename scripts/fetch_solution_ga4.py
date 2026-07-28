#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from ga4_pipeline import (
    CSV_FIELDS,
    aggregate,
    date_range,
    ensure_dir,
    load_config,
    load_env,
    normalize_ga4_rows,
    render_dashboard,
    resolve_config_path,
    write_csv,
    write_dashboard_data,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/pipeline.json"
SOLUTION_PATTERNS = [
    "/category/solutions",
    "/category/solutions-zh-hans",
    "/solutions",
    "/voicecollectionanalysis",
    "/smart-warehouse-management",
    "/intelligent-video-analytics",
    "/indoor-outdoor-positioning",
    "/conversational-voice-ai",
    "/environment-monitoring",
    "/building-energy-retrofit",
    "/smart-livestock-farming",
    "/smart-agriculture-sensing",
    "/campus-safety-management",
    "/hazard-response",
    "/building-energy-management",
]


def fetch_rows_for_pattern(config: Dict[str, Any], start_date: str, end_date: str, pattern: str) -> List[Dict[str, Any]]:
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.analytics.data_v1beta.types import DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest

    ga4_cfg = config["ga4"]
    property_id = os.environ.get(ga4_cfg.get("property_id_env", "GA4_PROPERTY_ID"))
    if not property_id:
        raise SystemExit("GA4_PROPERTY_ID is missing in .env")

    transport = os.environ.get("GA4_API_TRANSPORT", "rest")
    client = BetaAnalyticsDataClient(transport=transport)
    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=[Dimension(name=name) for name in ga4_cfg["dimensions"]],
        metrics=[Metric(name=name) for name in ga4_cfg["metrics"]],
        date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="pagePath",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.CONTAINS,
                    value=pattern,
                    case_sensitive=False,
                ),
            )
        ),
        limit=100000,
    )
    response = client.run_report(request)
    rows: List[Dict[str, Any]] = []
    for item in response.rows:
        row: Dict[str, Any] = {"_matched_pattern": pattern}
        for header, value in zip(response.dimension_headers, item.dimension_values):
            row[header.name] = value.value
        for header, value in zip(response.metric_headers, item.metric_values):
            row[header.name] = value.value
        rows.append(row)
    return rows


def read_csv(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def dedupe(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("Key") or "")
        if not key:
            continue
        by_key[key] = row
    return list(by_key.values())


def write_solution_payload(rows: List[Dict[str, Any]], csv_path: Path, dashboard_file: Path, config: Dict[str, Any]) -> None:
    data_dir = dashboard_file.parent / "data"
    ensure_dir(data_dir)
    json_path = data_dir / "latest.json"
    csv_target = data_dir / "latest.csv"
    published_rows = sorted(rows, key=lambda row: float(row.get("Views") or 0), reverse=True)[: int(config.get("outputs", {}).get("published_row_limit", 5000))]
    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_csv": csv_path.name,
        "summary": aggregate(rows),
        "published_row_limit": len(published_rows),
        "published_rows": len(published_rows),
        "rows": published_rows,
        "note": "Merged GA4 top-page data with a /solutions专项拉取 so Feishu imports include real solution-page data where GA4 has it.",
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(published_rows, csv_target)


def latest_existing_csv(config: Dict[str, Any]) -> Path | None:
    out_dir = ROOT / config["outputs"]["normalized_dir"]
    files = sorted(path for path in out_dir.glob("ga4_normalized_*.csv") if "_with_solutions_" not in path.name)
    return files[-1] if files else None


def main() -> None:
    load_env(ROOT / ".env")
    config = load_config(resolve_config_path(str(CONFIG)))
    start, end = date_range(int(config["ga4"].get("lookback_days", 28)))
    raw_rows: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    for pattern in SOLUTION_PATTERNS:
        rows = fetch_rows_for_pattern(config, start, end, pattern)
        counts[pattern] = len(rows)
        raw_rows.extend(rows)

    normalized_solution_rows = normalize_ga4_rows(raw_rows)
    existing_path = latest_existing_csv(config)
    existing_rows = read_csv(existing_path) if existing_path else []
    merged_rows = dedupe([*existing_rows, *normalized_solution_rows])

    stamp = f"{start}_to_{end}"
    raw_path = ROOT / config["outputs"]["raw_dir"] / f"ga4_solutions_raw_{stamp}.json"
    solution_csv = ROOT / config["outputs"]["normalized_dir"] / f"ga4_solutions_normalized_{stamp}.csv"
    merged_csv = ROOT / config["outputs"]["normalized_dir"] / f"ga4_normalized_with_solutions_{stamp}.csv"
    ensure_dir(raw_path.parent)
    raw_path.write_text(json.dumps(raw_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(normalized_solution_rows, solution_csv)
    write_csv(merged_rows, merged_csv)

    dashboard_file = ROOT / config["outputs"]["dashboard_file"]
    render_dashboard(merged_rows, dashboard_file)
    write_solution_payload(merged_rows, merged_csv, dashboard_file, config)

    print(json.dumps({
        "ok": True,
        "patterns": counts,
        "solution_rows": len(normalized_solution_rows),
        "existing_rows": len(existing_rows),
        "merged_rows": len(merged_rows),
        "raw": str(raw_path),
        "solutions_csv": str(solution_csv),
        "merged_csv": str(merged_csv),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
