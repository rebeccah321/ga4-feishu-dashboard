#!/usr/bin/env python3
"""
generate_tables_correct.py — 用“按 solution 维度过滤”的正确口径生成周度三表。

与旧的 generate_tables.py 的关键区别：
  - 每个 solution 单独查询 GA4：pagePath CONTAINS /solutions/<slug>
  - 查询中不带 pagePath 维度，避免 sessions/users 在同一次会话里被多路径拆散
  - 因此能复现飞书里的正确口径，例如 W35：
      solution_users=800，solution_pv=934，key_events=0

输出：data/analysis/方案增长总览.csv、单方案流量明细.csv、转换漏斗.csv
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

# scripts 目录在 sys.path 中，可直接复用 ga4_fetch 的 GA4 认证函数
import ga4_fetch

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "analysis"
PROPERTY = "502086217"
ENDPOINT = f"https://analyticsdata.googleapis.com/v1beta/properties/{PROPERTY}:runReport"

SOLUTIONS = [
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

DIMS = ["date", "hostName", "sessionDefaultChannelGroup"]
METS = [
    "screenPageViews", "activeUsers", "sessions", "engagementRate",
    "bounceRate", "userEngagementDuration", "keyEvents",
]

NA = "未接入"


def get_token() -> str:
    if os.environ.get("GA4_AUTH_MODE") == "service_account":
        return ga4_fetch.get_token_service_account()
    return ga4_fetch.get_token_adc()


def run_report(body: dict, token: str, retries: int = 4) -> dict:
    data = json.dumps(body).encode("utf-8")
    last_error = ""
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            ENDPOINT,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as err:
            last_error = err.read().decode("utf-8", errors="replace")
        except Exception as err:
            last_error = str(err)
        time.sleep(2 * attempt)
    raise SystemExit(f"GA4 request failed: {last_error}")


def query_solution(token: str, slug: str, start: str, end: str) -> list[dict]:
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": name} for name in DIMS],
        "metrics": [{"name": name} for name in METS],
        "limit": 100000,
        "dimensionFilter": {
            "filter": {
                "fieldName": "pagePath",
                "stringFilter": {
                    "matchType": "CONTAINS",
                    "value": f"/solutions/{slug}",
                },
            }
        },
    }
    report = run_report(body, token)
    rows = []
    for row in report.get("rows", []):
        dims = row.get("dimensionValues", [])
        mets = row.get("metricValues", [])
        date_raw = dims[0]["value"] if len(dims) > 0 else ""
        rows.append({
            "date": f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}" if len(date_raw) == 8 else date_raw,
            "hostName": dims[1]["value"] if len(dims) > 1 else "",
            "channel": dims[2]["value"] if len(dims) > 2 else "Unassigned",
            "pv": float(mets[0]["value"]) if len(mets) > 0 else 0.0,
            "users": float(mets[1]["value"]) if len(mets) > 1 else 0.0,
            "sessions": float(mets[2]["value"]) if len(mets) > 2 else 0.0,
            "engagementRate": float(mets[3]["value"]) if len(mets) > 3 else 0.0,
            "bounceRate": float(mets[4]["value"]) if len(mets) > 4 else 0.0,
            "userEngagementDuration": float(mets[5]["value"]) if len(mets) > 5 else 0.0,
            "keyEvents": float(mets[6]["value"]) if len(mets) > 6 else 0.0,
        })
    return rows


def query_site_pv(token: str, start: str, end: str) -> dict:
    """返回 {week_ending: total_site_pv}。只用于 overview 的非重要列。"""
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "date"}],
        "metrics": [{"name": "screenPageViews"}],
        "limit": 100000,
    }
    report = run_report(body, token)
    by_date = defaultdict(int)
    for row in report.get("rows", []):
        date_raw = row["dimensionValues"][0]["value"]
        date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        by_date[date_str] += int(float(row["metricValues"][0]["value"]))
    by_week = defaultdict(int)
    for date_str, pv in by_date.items():
        by_week[week_ending(date_str)] += pv
    return dict(by_week)


def week_ending(date_str: str) -> str:
    day = dt.date.fromisoformat(date_str)
    days_to_sun = (6 - day.weekday()) % 7
    return (day + dt.timedelta(days=days_to_sun)).isoformat()


def iso_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def date_window(args) -> tuple[str, str]:
    if args.start and args.end:
        return args.start, args.end
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    if args.include_current:
        end = today - dt.timedelta(days=1)
        start = monday - dt.timedelta(weeks=args.weeks - 1)
    else:
        end = monday - dt.timedelta(days=1)
        start = monday - dt.timedelta(weeks=args.weeks)
    return start.isoformat(), end.isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=None, help="YYYY-MM-DD，含起止日")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD，含起止日")
    parser.add_argument("--weeks", type=int, default=7, help="未显式指定 start/end 时使用")
    parser.add_argument("--include-current", action="store_true", help="未显式指定 start/end 时，包含当前未完整周")
    args = parser.parse_args()

    start, end = date_window(args)
    print(f"Correct weekly generation: {start} -> {end}", flush=True)
    token = get_token()

    print("Querying site PV...", flush=True)
    site_pv_by_week = query_site_pv(token, start, end)

    stats = defaultdict(lambda: {
        "pv": 0.0, "users": 0.0, "sessions": 0.0,
        "eng_num": 0.0, "bounce_num": 0.0, "dur": 0.0, "key": 0.0,
        "channels": defaultdict(float),
    })
    for slug in SOLUTIONS:
        rows = query_solution(token, slug, start, end)
        for row in rows:
            we = week_ending(row["date"])
            key = (we, slug)
            stat = stats[key]
            sessions = row["sessions"]
            stat["pv"] += row["pv"]
            stat["users"] += row["users"]
            stat["sessions"] += sessions
            stat["dur"] += row["userEngagementDuration"]
            stat["eng_num"] += row["engagementRate"] * sessions
            stat["bounce_num"] += row["bounceRate"] * sessions
            stat["key"] += row["keyEvents"]
            stat["channels"][row["channel"]] += sessions
        print(f"  queried {slug}", flush=True)

    weeks = sorted({we for (we, _slug) in stats})
    solution_pv_by_week = defaultdict(lambda: defaultdict(float))
    for (we, slug), stat in stats.items():
        solution_pv_by_week[we][slug] = stat["pv"]

    # 01 方案增长总览
    overview_fields = [
        "week_ending", "solution_users", "solution_pv", "total_site_pv",
        "cta_clicks", "cta_click_rate",
        "key_events", "key_event_conv_rate",
        "fastest_growing_solution", "top_traffic_solution",
        "social_impressions", "solution_wow_pct",
    ]
    with open(ANALYSIS_DIR / "方案增长总览.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=overview_fields)
        writer.writeheader()
        prev_week_pv = {}
        for we in weeks:
            sol_pv = sum(solution_pv_by_week[we].values())
            sol_users = sum(stat["users"] for (_w, slug), stat in stats.items() if _w == we)
            sol_sessions = sum(stat["sessions"] for (_w, slug), stat in stats.items() if _w == we)
            key_events = int(round(sum(stat["key"] for (_w, slug), stat in stats.items() if _w == we)))
            site_pv = site_pv_by_week.get(we, 0)

            fastest = ""
            growths = []
            for slug, pv in solution_pv_by_week[we].items():
                prev = prev_week_pv.get(slug, 0)
                if prev > 0:
                    growths.append((round(100 * (pv - prev) / prev, 1), slug))
            if growths:
                growths.sort(reverse=True)
                fastest = f"{growths[0][1]} (+{growths[0][0]:.1f}%)"

            if solution_pv_by_week[we]:
                top_slug = max(solution_pv_by_week[we].items(), key=lambda item: item[1])[0]
                top_text = f"{top_slug} ({int(solution_pv_by_week[we][top_slug])} PV)"
            else:
                top_text = ""

            prev_total = sum(prev_week_pv.values()) if prev_week_pv else 0
            wow_pct = round(100 * (sol_pv - prev_total) / prev_total, 1) if prev_total > 0 else ""

            writer.writerow({
                "week_ending": we,
                "solution_users": int(round(sol_users)),
                "solution_pv": int(round(sol_pv)),
                "total_site_pv": int(site_pv),
                "cta_clicks": key_events,
                "cta_click_rate": round(100 * key_events / site_pv, 2) if site_pv else 0,
                "key_events": key_events,
                "key_event_conv_rate": round(key_events / sol_sessions, 4) if sol_sessions else 0,
                "fastest_growing_solution": fastest,
                "top_traffic_solution": top_text,
                "social_impressions": NA,
                "solution_wow_pct": wow_pct,
            })
            prev_week_pv = dict(solution_pv_by_week[we])

    # 02 / 03
    week_slugs_sorted = {}
    for we in weeks:
        week_slugs_sorted[we] = sorted(
            solution_pv_by_week[we].keys(),
            key=lambda slug: -solution_pv_by_week[we][slug],
        )

    detail_fields = [
        "week_ending", "slug", "landing_pv", "users", "sessions",
        "avg_eng_s", "engagement_rate", "top_channel",
        "cta_clicks", "form_submits", "key_events",
        "data_status", "wow_pct",
    ]
    with open(ANALYSIS_DIR / "单方案流量明细.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=detail_fields)
        writer.writeheader()
        for we in reversed(weeks):
            for slug in week_slugs_sorted[we]:
                stat = stats[(we, slug)]
                sessions = stat["sessions"] or 1
                engagement_rate = stat["eng_num"] / sessions if sessions else 0
                avg_eng_s = stat["dur"] / sessions if sessions else 0
                top_channel = max(stat["channels"].items(), key=lambda item: item[1])[0] if stat["channels"] else ""
                prev_pv = solution_pv_by_week.get(prev_week_for(weeks, we), {}).get(slug, 0)
                wow = round(100 * (stat["pv"] - prev_pv) / prev_pv, 1) if prev_pv > 0 else ""
                writer.writerow({
                    "week_ending": we,
                    "slug": slug,
                    "landing_pv": int(round(stat["pv"])),
                    "users": int(round(stat["users"])),
                    "sessions": int(round(stat["sessions"])),
                    "avg_eng_s": round(avg_eng_s, 1),
                    "engagement_rate": round(engagement_rate, 4),
                    "top_channel": top_channel if top_channel else "Unassigned",
                    "cta_clicks": int(round(stat["key"])),
                    "form_submits": NA,
                    "key_events": int(round(stat["key"])),
                    "data_status": "GA4已接入",
                    "wow_pct": wow,
                })
            prev_week_for.cache = None

    funnel_fields = [
        "week_ending", "slug", "page_pv",
        "cta_clicks", "form_submits", "add_to_cart",
        "pv_to_cta_rate", "cta_to_form_rate",
        "data_status", "engaged_sessions",
    ]
    with open(ANALYSIS_DIR / "转换漏斗.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=funnel_fields)
        writer.writeheader()
        for we in reversed(weeks):
            for slug in week_slugs_sorted[we]:
                stat = stats[(we, slug)]
                sessions = stat["sessions"] or 1
                engagement_rate = stat["eng_num"] / sessions if sessions else 0
                pv = stat["pv"]
                writer.writerow({
                    "week_ending": we,
                    "slug": slug,
                    "page_pv": int(round(pv)),
                    "cta_clicks": int(round(stat["key"])),
                    "form_submits": NA,
                    "add_to_cart": NA,
                    "pv_to_cta_rate": round(100 * stat["key"] / pv, 2) if pv else 0,
                    "cta_to_form_rate": NA,
                    "data_status": "GA4部分·CRM未接入",
                    "engaged_sessions": round(stat["sessions"] * engagement_rate),
                })

    print(f"Wrote three tables for {len(weeks)} weeks: {weeks[0]} .. {weeks[-1]}")


def prev_week_for(weeks: list[str], current: str) -> str | None:
    idx = weeks.index(current)
    return weeks[idx - 1] if idx > 0 else None


if __name__ == "__main__":
    main()
