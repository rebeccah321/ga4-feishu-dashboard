#!/usr/bin/env python3
"""
export_weekly_json.py — 把周度三表导出为飞书/Pages 可拉取的 JSON。

输入：data/analysis/ 下的 CSV（由 generate_tables.py 生成）
输出：dashboard/data/weekly/
  - weekly_tables.json  完整三表 JSON
  - weekly_summary.json 单条周度汇总（供飞书自动化 HTTP 拉取后写一条记录）
  - overview.csv/detail.csv/funnel.csv（英文文件名的 CSV，供 Pages 兜底）

字段口径与 generate_tables.py 一致：周一～周日切周，三语言合并，12 个核心方案。
"""

import argparse
import csv
import json
from collections import defaultdict
import datetime as dt
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "analysis"
OUT_DIR = ROOT / "dashboard" / "data" / "weekly"

SLUG_DISPLAY = {
    "voicecollectionanalysis": "语音采集分析",
    "conversational-voice-ai": "对话式语音AI",
    "smart-warehouse-management": "智能仓储管理",
    "smart-agriculture-sensing": "智慧农业感知",
    "smart-livestock-farming": "智慧畜牧",
    "intelligent-video-analytics": "智能视频分析",
    "indoor-outdoor-positioning": "室内外定位",
    "environment-monitoring": "环境监测",
    "building-energy-management": "楼宇能源管理",
    "building-energy-retrofit": "楼宇节能改造",
    "campus-safety-management": "校园安全管理",
    "hazard-response": "应急响应",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def week_label(value) -> str:
    try:
        day = dt.date.fromisoformat(str(value))
        return f"W{day.isocalendar()[1]}"
    except ValueError:
        return str(value)


def clean(value):
    """把 CSV 中的空值、未接入统一转为 JSON null；其余保留数字或字符串。"""
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "未接入", "N/A", "NA", "-"}:
        return None
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return text


def display(text):
    text = str(text or "")
    for slug, name in SLUG_DISPLAY.items():
        text = text.replace(slug, name)
    return text


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))



def sort_overview_newest(rows):
    return sorted(rows, key=lambda r: (r.get("week_ending") or ""), reverse=True)


def sort_detail_newest_by_pv(rows, pv_field):
    weeks = sorted({(r.get("week_ending") or "") for r in rows}, reverse=True)
    out = []
    for week in weeks:
        grp = [r for r in rows if (r.get("week_ending") or "") == week]
        grp.sort(key=lambda r: -(float(r.get(pv_field) or 0)))
        out.extend(grp)
    return out


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def group_overview(rows):
    """兼容两种 overview：有 slug 的按周聚合成方案口径；无 slug 的直接透传。"""
    if not rows:
        return []
    if "slug" in rows[0]:
        grouped = defaultdict(lambda: {"pv": 0, "users": 0, "sessions": 0, "events": 0})
        for row in rows:
            week = row["week_ending"]
            grouped[week]["pv"] += int(row.get("pv", 0) or 0)
            grouped[week]["users"] += int(row.get("users", 0) or 0)
            grouped[week]["sessions"] += int(row.get("sessions", 0) or 0)
            grouped[week]["events"] += int(row.get("key_events", 0) or 0)
        output = []
        previous_pv = None
        for week in sorted(grouped):
            data = grouped[week]
            wow = None
            if previous_pv and previous_pv > 0:
                wow = round(100 * (data["pv"] - previous_pv) / previous_pv, 1)
            output.append({
                "week_ending": week,
                "solution_users": data["users"],
                "solution_pv": data["pv"],
                "solution_sessions": data["sessions"],
                "key_events": data["events"],
                "solution_wow_pct": wow,
            })
            previous_pv = data["pv"]
        return output

    return [
        {
            "week_ending": row["week_ending"],
            "solution_users": clean(row.get("solution_users")),
            "solution_pv": clean(row.get("solution_pv")),
            "solution_sessions": clean(row.get("solution_sessions")),
            "key_events": clean(row.get("key_events")),
            "key_event_conv_rate": clean(row.get("key_event_conv_rate")),
            "solution_wow_pct": clean(row.get("solution_wow_pct")),
            "cta_clicks": clean(row.get("cta_clicks")),
            "cta_click_rate": clean(row.get("cta_click_rate")),
            "social_impressions": clean(row.get("social_impressions")),
            "form_submits": clean(row.get("form_submits")),
            "fastest_growing_solution": display(row.get("fastest_growing_solution")) or None,
            "top_traffic_solution": display(row.get("top_traffic_solution")) or None,
        }
        for row in rows
    ]


def as_percent(value):
    value = clean(value)
    if isinstance(value, (int, float)):
        return round(value * 100, 2)
    return value


def detail_rows(rows):
    output = []
    for row in rows:
        slug = clean(row.get("slug"))
        output.append({
            "week_ending": row.get("week_ending"),
            "solution_name": display(slug) if slug else None,
            "landing_pv": clean(row.get("landing_pv")),
            "users": clean(row.get("users")),
            "sessions": clean(row.get("sessions")),
            "avg_eng_s": clean(row.get("avg_eng_s")),
            "engagement_rate_pct": as_percent(row.get("engagement_rate")),
            "top_channel": clean(row.get("top_channel")),
            "cta_clicks": clean(row.get("cta_clicks")),
            "form_submits": clean(row.get("form_submits")),
            "key_events": clean(row.get("key_events")),
            "data_status": clean(row.get("data_status")),
            "wow_pct": clean(row.get("wow_pct")),
        })
    return output


def funnel_rows(rows):
    output = []
    for row in rows:
        slug = clean(row.get("slug")) or display(row.get("slug"))
        output.append({
            "week_ending": row.get("week_ending"),
            "solution_name": display(slug) if slug else None,
            "page_pv": clean(row.get("page_pv")),
            "sessions": clean(row.get("sessions")),
            "cta_clicks": clean(row.get("cta_clicks")),
            "form_submits": clean(row.get("form_submits")),
            "add_to_cart": clean(row.get("add_to_cart")),
            "pv_to_cta_rate": clean(row.get("pv_to_cta_rate")),
            "data_status": clean(row.get("data_status")),
        })
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    overview_path = ANALYSIS_DIR / "方案增长总览.csv"
    detail_path = ANALYSIS_DIR / "单方案流量明细.csv"
    funnel_path = ANALYSIS_DIR / "转换漏斗.csv"

    overview_raw = sort_overview_newest(read_csv(overview_path))
    detail_raw = sort_detail_newest_by_pv(read_csv(detail_path), "landing_pv")
    funnel_raw = sort_detail_newest_by_pv(read_csv(funnel_path), "page_pv")

    overview = group_overview(overview_raw)
    detail = detail_rows(detail_raw)
    funnel = funnel_rows(funnel_raw)

    if not overview:
        raise SystemExit(f"Missing overview data: {overview_path}")

    latest_week = max((r["week_ending"] for r in overview), default=None)
    payload = {
        "generated_at": now_iso(),
        "source": "ga4_weekly_tables",
        "property": "502086217",
        "latest_week_ending": latest_week,
        "overview": overview,
        "detail": detail,
        "funnel": funnel,
    }

    (out_dir / "weekly_tables.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = overview[0]
    summary_payload = {
        "generated_at": payload["generated_at"],
        "week_number": week_label(latest_week),
        "week_ending": latest_week,
        "solution_users": summary.get("solution_users"),
        "solution_pv": summary.get("solution_pv"),
        "solution_sessions": summary.get("solution_sessions"),
        "key_events": summary.get("key_events"),
        "key_event_conv_rate": summary.get("key_event_conv_rate"),
        "solution_wow_pct": summary.get("solution_wow_pct"),
        "cta_clicks": summary.get("cta_clicks"),
        "cta_click_rate": summary.get("cta_click_rate"),
        "social_impressions": summary.get("social_impressions"),
        "form_submits": summary.get("form_submits"),
        "fastest_growing_solution": summary.get("fastest_growing_solution"),
        "top_traffic_solution": summary.get("top_traffic_solution"),
    }
    (out_dir / "weekly_summary.json").write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for raw_rows, fieldnames, target in (
        (overview_raw, overview_raw[0].keys() if overview_raw else [], "overview.csv"),
        (detail_raw, detail_raw[0].keys() if detail_raw else [], "detail.csv"),
        (funnel_raw, funnel_raw[0].keys() if funnel_raw else [], "funnel.csv"),
    ):
        if raw_rows:
            write_csv(out_dir / target, list(fieldnames), raw_rows)
            print(f"  wrote {target} ({len(raw_rows)} rows)")

    print(f"Exported weekly JSON to {out_dir}")
    print(f"  latest_week_ending = {latest_week}")
    print(f"  overview rows      = {len(overview)}")
    print(f"  detail rows        = {len(detail)}")
    print(f"  funnel rows        = {len(funnel)}")


if __name__ == "__main__":
    main()
