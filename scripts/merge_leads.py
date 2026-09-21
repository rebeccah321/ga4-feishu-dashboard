#!/usr/bin/env python3
"""
merge_leads.py — 从 LeadApi/Typesense 拉取真实线索，并合并到周度三表。

口径：
  - form_submits 只统计能映射到 12 个 solution 落地页的去重 submission_id。
  - lead_rate 已从对外字段中移除，不再写入表格。
  - CTA / key event 的现有列保持原样，不在本脚本中重新定义。

用法：
  # 线上：从 LeadApi 实时拉取
  LEAD_SEARCH_KEY=... python3 scripts/merge_leads.py

  # 本地：读取已落盘的 Typesense pages JSON（用于离线回填）
  python3 scripts/merge_leads.py --pages-dir /tmp/leads_pages
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "analysis"
LEADS_DIR = ROOT / "data" / "leads"

DEFAULT_KEY_FILE = Path(
    "/Users/seeed/Desktop/solution落地页/GA4/LeadApi/searchKey.txt"
)
HOST = "https://rel-search.seeedstudio.com"
CST = dt.timezone(dt.timedelta(hours=8))
NA = "未接入"

CORE_SLUGS = [
    "smart-warehouse-management",
    "voicecollectionanalysis",
    "conversational-voice-ai",
    "smart-agriculture-sensing",
    "smart-livestock-farming",
    "intelligent-video-analytics",
    "indoor-outdoor-positioning",
    "environment-monitoring",
    "building-energy-management",
    "campus-safety-management",
    "building-energy-retrofit",
    "hazard-response",
]


def week_ending(create_at: str) -> str | None:
    if not create_at:
        return None
    try:
        day = dt.date.fromisoformat(create_at[:10])
    except ValueError:
        return None
    return (day + dt.timedelta(days=(6 - day.weekday()) % 7)).isoformat()


def load_key() -> str:
    env_key = os.environ.get("LEAD_SEARCH_KEY", "").strip()
    if env_key:
        return env_key
    if not DEFAULT_KEY_FILE.exists():
        raise SystemExit(
            "LEAD_SEARCH_KEY not set and key file not found: "
            f"{DEFAULT_KEY_FILE}"
        )
    return DEFAULT_KEY_FILE.read_text(encoding="utf-8").strip()


def curl_search(key: str, params: dict) -> dict | None:
    body = json.dumps({"searches": [params]})
    result = subprocess.run(
        [
            "curl", "-sS", "-m", "75", "-X", "POST",
            f"{HOST}/multi_search",
            "-H", "Content-Type: application/json",
            "-H", f"X-TYPESENSE-API-KEY: {key}",
            "-d", body,
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        print(f"  curl error: {result.stderr[:240]}", flush=True)
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  JSON parse error: {result.stdout[:240]}", flush=True)
        return None
    result_item = payload["results"][0]
    if result_item.get("error"):
        print(f"  API error: {result_item['error']}", flush=True)
        return None
    return result_item


def search_leads(key: str) -> list[dict]:
    all_docs: list[dict] = []
    page = 1
    while True:
        params = {
            "collection": "leads",
            "q": "*",
            "query_by": "reason_for_contact",
            "per_page": 250,
            "page": page,
            "sort_by": "create_at_timestamp:desc",
        }
        result_item = curl_search(key, params)
        if not result_item:
            break
        hits = result_item.get("hits", [])
        all_docs.extend(hit["document"] for hit in hits)
        total = int(result_item.get("found", 0))
        print(
            f"  page {page}: got {len(hits)}, total {len(all_docs)}/{total}",
            flush=True,
        )
        if not hits or len(all_docs) >= total:
            break
        page += 1
    return all_docs


def load_cached_docs() -> list[dict]:
    """Live LeadApi 不可用时，从已提交的 leads_weekly.csv 回退，避免管道中断."""
    path = LEADS_DIR / "leads_weekly.csv"
    if not path.exists():
        return []
    docs: list[dict] = []
    idx = 0
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            week = str(row.get("week_ending") or "").strip()
            slug = str(row.get("slug") or "").strip()
            try:
                count = int(float(row.get("form_submits") or 0))
            except (TypeError, ValueError):
                count = 0
            for _ in range(count):
                idx += 1
                docs.append({
                    "submission_id": f"cached-{week}-{slug}-{idx}",
                    "create_at": week,
                    "pageLocation": f"/solutions/{slug}",
                })
    return docs


def load_docs_from_pages_dir(pages_dir: str) -> list[dict]:
    pages = sorted(Path(pages_dir).glob("*.json"))
    docs: list[dict] = []
    for path in pages:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        result_item = payload["results"][0]
        docs.extend(hit["document"] for hit in result_item.get("hits", []))
    print(f"Loaded {len(docs)} lead docs from {pages_dir}", flush=True)
    return docs


def match_slug(doc: dict) -> str | None:
    for field in ("pageLocation", "referrer"):
        value = str(doc.get(field) or "")
        if not value:
            continue
        haystack = value.lower()
        for slug in CORE_SLUGS:
            if re.search(
                rf"/solutions/{re.escape(slug)}(?:-zh-hans)?(?:/|$|\?)",
                haystack,
            ):
                return slug
    return None


def count_leads(docs: list[dict]) -> tuple[dict, dict, int, int]:
    """返回 (每周每方案计数, 每周总方案线索数, 总数, 另有计数错误)"""
    by_week_slug: dict[tuple[str, str], int] = defaultdict(int)
    total_by_week: dict[str, int] = defaultdict(int)
    seen: set[str] = set()
    matched_docs = 0

    for doc in docs:
        submission_id = str(doc.get("submission_id") or "").strip()
        if not submission_id:
            continue
        if submission_id in seen:
            continue
        seen.add(submission_id)

        create_week = week_ending(str(doc.get("create_at") or ""))
        if not create_week:
            continue
        slug = match_slug(doc)
        if not slug:
            continue
        by_week_slug[(create_week, slug)] += 1
        total_by_week[create_week] += 1
        matched_docs += 1

    print(
        f"Total distinct submissions: {len(seen)}, "
        f"solution-matched: {matched_docs}",
        flush=True,
    )
    return by_week_slug, total_by_week, len(seen), matched_docs


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    return fields, rows


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_leads_weekly(by_week_slug: dict[tuple[str, str], int]) -> None:
    fields = ["week_ending", "slug", "form_submits"]
    rows = [
        {"week_ending": week, "slug": slug, "form_submits": count}
        for (week, slug), count in sorted(
            by_week_slug.items(), key=lambda item: (item[0][0], item[0][1])
        )
    ]
    path = LEADS_DIR / "leads_weekly.csv"
    write_csv(path, fields, rows)
    print(f"Wrote {path} ({len(rows)} rows)", flush=True)


def update_tables(
    by_week_slug: dict[tuple[str, str], int],
    total_by_week: dict[str, int],
) -> None:
    overview_path = ANALYSIS_DIR / "方案增长总览.csv"
    detail_path = ANALYSIS_DIR / "单方案流量明细.csv"
    funnel_path = ANALYSIS_DIR / "转换漏斗.csv"

    detail_fields, detail_rows = read_csv(detail_path)
    sessions_by_week: dict[str, float] = defaultdict(float)
    for row in detail_rows:
        try:
            sessions_by_week[row["week_ending"]] += int(float(row.get("sessions") or 0))
        except (KeyError, ValueError, TypeError):
            continue

    overview_fields, overview_rows = read_csv(overview_path)
    if "form_submits" not in overview_fields:
        overview_fields.append("form_submits")
    for row in overview_rows:
        week = row.get("week_ending", "")
        row["form_submits"] = int(total_by_week.get(week, 0))
    write_csv(overview_path, overview_fields, overview_rows)
    print(f"Updated {overview_path.name}", flush=True)

    if "form_submits" not in detail_fields:
        detail_fields.append("form_submits")
    for row in detail_rows:
        key = (row.get("week_ending", ""), row.get("slug", ""))
        row["form_submits"] = int(by_week_slug.get(key, 0))
        row["data_status"] = "GA4+LeadApi已接入"
    write_csv(detail_path, detail_fields, detail_rows)
    print(f"Updated {detail_path.name}", flush=True)

    funnel_fields, funnel_rows = read_csv(funnel_path)
    if "form_submits" not in funnel_fields:
        funnel_fields.append("form_submits")
    for row in funnel_rows:
        key = (row.get("week_ending", ""), row.get("slug", ""))
        row["form_submits"] = int(by_week_slug.get(key, 0))
        row["data_status"] = "GA4+LeadApi已接入"
    write_csv(funnel_path, funnel_fields, funnel_rows)
    print(f"Updated {funnel_path.name}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pages-dir",
        default=None,
        help="本地 Typesense pages JSON 目录；省略则从 LeadApi 实时拉取",
    )
    args = parser.parse_args()

    if args.pages_dir:
        docs = load_docs_from_pages_dir(args.pages_dir)
    else:
        docs = []
        key = None
        try:
            key = load_key()
        except SystemExit as exc:
            print(f"WARNING: LeadApi key unavailable: {exc}", flush=True)
        if key:
            print("Fetching leads from LeadApi...", flush=True)
            try:
                docs = search_leads(key)
            except Exception as exc:  # curl/timeout/网络异常不做为失败
                print(f"WARNING: LeadApi fetch failed: {exc}", flush=True)
                docs = []
        if not docs:
            docs = load_cached_docs()
            if docs:
                print(
                    f"Live LeadApi unavailable; using {len(docs)} cached lead entries",
                    flush=True,
                )
            else:
                print("WARNING: no LeadApi data and no cached leads; skipping merge", flush=True)
                return

    by_week_slug, total_by_week, _distinct, _matched = count_leads(docs)
    write_leads_weekly(by_week_slug)
    update_tables(by_week_slug, total_by_week)
    print("LeadApi merge complete", flush=True)


if __name__ == "__main__":
    main()
