#!/usr/bin/env python3
"""
generate_tables.py — 生成三张周度报表（周一~周日周期，语言合并）。

表 1: 方案增长总览    — 每周一行，全站 + Solution 汇总
表 2: 单方案流量明细  — 每方案每周一行
表 3: 转换漏斗        — 最新完整周，每方案一行

用法：
  python3 scripts/ga4_fetch.py --days 42
  python3 scripts/generate_tables.py
"""
import csv
import datetime as dt
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SLUG_RE = re.compile(r"^/solutions/([^/?]+?)(?:-zh-hans)?/?$")
CORE_SLUGS = {
    "smart-warehouse-management", "voicecollectionanalysis",
    "conversational-voice-ai", "smart-agriculture-sensing",
    "smart-livestock-farming", "intelligent-video-analytics",
    "indoor-outdoor-positioning", "environment-monitoring",
    "building-energy-management", "campus-safety-management",
    "building-energy-retrofit", "hazard-response",
}
NA = "未接入"


def week_ending(date_str):
    d = dt.date.fromisoformat(date_str)
    return (d + dt.timedelta(days=(6 - d.weekday()) % 7)).isoformat()


def extract_slug(path):
    m = SLUG_RE.match(path)
    return m.group(1) if m else None


def is_garbage(path):
    return "{" in path or "}" in path


def read_normalized():
    norm_dir = ROOT / "data" / "normalized"
    files = list(norm_dir.glob("ga4_daily_*.csv"))
    if not files:
        raise SystemExit("No data. Run: python3 scripts/ga4_fetch.py --days 42")
    best = max(files, key=lambda f: sum(1 for _ in open(f, encoding="utf-8")))
    rows = []
    with open(best, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"Read {len(rows)} rows from {best.name}")
    print(f"Date range: {min(r['date'] for r in rows)} to {max(r['date'] for r in rows)}")
    return rows


def aggregate(rows):
    """按 (week, slug) 聚合，语言合并。同时跟踪 channel 分布。"""
    agg = defaultdict(lambda: {
        "pv": 0, "users": 0, "sessions": 0,
        "eng_s": 0.0, "eng_rate": 0.0, "bounce": 0.0,
        "events": 0, "revenue": 0.0,
        "channels": defaultdict(int),
    })
    site_weekly = defaultdict(lambda: {"pv": 0, "users": 0, "sessions": 0})
    for r in rows:
        we = week_ending(r["date"])
        site_weekly[we]["pv"] += int(r.get("screen_page_views", 0))
        site_weekly[we]["users"] += int(r.get("active_users", 0))
        site_weekly[we]["sessions"] += int(r.get("sessions", 0))
        path = r.get("page_path", "")
        if is_garbage(path):
            continue
        slug = extract_slug(path)
        if not slug or slug not in CORE_SLUGS:
            continue
        key = (we, slug)
        s = max(int(r.get("sessions", 0)), 1)
        agg[key]["pv"] += int(r.get("screen_page_views", 0))
        agg[key]["users"] += int(r.get("active_users", 0))
        agg[key]["sessions"] += int(r.get("sessions", 0))
        agg[key]["eng_s"] += float(r.get("avg_engagement_seconds", 0)) * s
        agg[key]["eng_rate"] += float(r.get("engagement_rate", 0)) * s
        agg[key]["bounce"] += float(r.get("bounce_rate", 0)) * s
        agg[key]["events"] += int(r.get("key_events", 0))
        agg[key]["revenue"] += float(r.get("total_revenue", 0))
        ch = r.get("channel_group", "Unknown")
        agg[key]["channels"][ch] += int(r.get("sessions", 0))
    return agg, site_weekly


def write_table1(agg, site_weekly, path):
    """方案增长总览：每周一行。"""
    weeks = sorted(site_weekly.keys())
    # 每周 solution PV 总计
    sol_pv_by_week = defaultdict(int)
    sol_sess_by_week = defaultdict(int)
    sol_events_by_week = defaultdict(int)
    slug_pv_by_week = defaultdict(lambda: defaultdict(int))
    for (we, slug), v in agg.items():
        sol_pv_by_week[we] += v["pv"]
        sol_sess_by_week[we] += v["sessions"]
        sol_events_by_week[we] += v["events"]
        slug_pv_by_week[we][slug] = v["pv"]

    fields = [
        "week_ending", "total_site_pv", "total_users",
        "solution_pv", "cta_clicks", "cta_click_rate",
        "key_events", "key_event_conv_rate",
        "fastest_growing_solution", "social_impressions",
        "solution_wow_pct",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        prev_sol_pv = None
        prev_slug_pv = None
        for we in weeks:
            sw = site_weekly[we]
            sol_pv = sol_pv_by_week[we]
            sol_sess = sol_sess_by_week[we]
            sol_events = sol_events_by_week[we]
            # solution WoW
            sol_wow = ""
            if prev_sol_pv and prev_sol_pv > 0:
                sol_wow = round(100 * (sol_pv - prev_sol_pv) / prev_sol_pv, 1)
            # fastest growing
            fastest = ""
            if prev_slug_pv:
                growths = []
                for slug, pv in slug_pv_by_week[we].items():
                    prev = prev_slug_pv.get(slug, 0)
                    if prev > 0:
                        g = round(100 * (pv - prev) / prev, 1)
                        growths.append((g, slug, pv))
                if growths:
                    growths.sort(reverse=True)
                    fastest = f"{growths[0][1]} (+{growths[0][0]}%)"
            w.writerow({
                "week_ending": we,
                "total_site_pv": sw["pv"],
                "total_users": sw["users"],
                "solution_pv": sol_pv,
                "cta_clicks": NA,
                "cta_click_rate": NA,
                "key_events": sol_events,
                "key_event_conv_rate": round(sol_events / sol_sess, 4) if sol_sess else 0,
                "fastest_growing_solution": fastest,
                "social_impressions": NA,
                "solution_wow_pct": sol_wow,
            })
            prev_sol_pv = sol_pv
            prev_slug_pv = slug_pv_by_week[we]
    print(f"Wrote: {path} ({len(weeks)} weeks)")


def write_table2(agg, path):
    """单方案流量明细：每方案每周一行。"""
    weeks = sorted({we for (we, _) in agg})
    fields = [
        "week_ending", "slug", "landing_pv", "users", "sessions",
        "avg_eng_s", "engagement_rate", "top_channel",
        "cta_clicks", "form_submits", "key_events",
        "data_status", "wow_pct",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for slug in sorted({s for (_, s) in agg}):
            prev_pv = None
            for we in weeks:
                v = agg.get((we, slug))
                if not v:
                    continue
                s = v["sessions"] or 1
                eng_rate = v["eng_rate"] / s
                top_ch = max(v["channels"].items(), key=lambda x: x[1])[0] if v["channels"] else ""
                wow = ""
                if prev_pv is not None and prev_pv > 0:
                    wow = round(100 * (v["pv"] - prev_pv) / prev_pv, 1)
                w.writerow({
                    "week_ending": we, "slug": slug,
                    "landing_pv": v["pv"], "users": v["users"], "sessions": v["sessions"],
                    "avg_eng_s": round(v["eng_s"] / s, 1),
                    "engagement_rate": round(eng_rate, 4),
                    "top_channel": top_ch,
                    "cta_clicks": NA, "form_submits": NA,
                    "key_events": v["events"],
                    "data_status": "GA4已接入",
                    "wow_pct": wow,
                })
                prev_pv = v["pv"]
    print(f"Wrote: {path} ({len(weeks)} weeks × {len(set(s for _,s in agg))} slugs)")


def write_table3(agg, path):
    """转换漏斗：最新完整周，每方案一行。"""
    weeks = sorted({we for (we, _) in agg})
    # 取最新完整周（倒数第二周，因为最后一周可能不完整）
    if len(weeks) >= 2:
        latest = weeks[-2]
    else:
        latest = weeks[-1]
    fields = [
        "week_ending", "slug", "page_pv",
        "cta_clicks", "form_submits", "add_to_cart",
        "pv_to_cta_rate", "cta_to_form_rate",
        "data_status", "engaged_sessions",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for slug in sorted({s for (we, s) in agg if we == latest}):
            v = agg[(latest, slug)]
            s = v["sessions"] or 1
            eng_rate = v["eng_rate"] / s
            w.writerow({
                "week_ending": latest, "slug": slug,
                "page_pv": v["pv"],
                "cta_clicks": NA, "form_submits": NA, "add_to_cart": NA,
                "pv_to_cta_rate": NA, "cta_to_form_rate": NA,
                "data_status": "GA4部分·CRM未接入",
                "engaged_sessions": round(v["sessions"] * eng_rate),
            })
    print(f"Wrote: {path} (week {latest}, {len(set(s for we,s in agg if we==latest))} slugs)")


def main():
    rows = read_normalized()
    agg, site_weekly = aggregate(rows)
    out_dir = ROOT / "data" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    write_table1(agg, site_weekly, out_dir / "方案增长总览.csv")
    write_table2(agg, out_dir / "单方案流量明细.csv")
    write_table3(agg, out_dir / "转换漏斗.csv")
    weeks = sorted(site_weekly.keys())
    print(f"\n周度区间 ({len(weeks)} 周):")
    for we in weeks:
        sw = site_weekly[we]
        sol_pv = sum(v["pv"] for (w, s), v in agg.items() if w == we)
        print(f"  {we}: 全站 {sw['pv']} PV | 方案页 {sol_pv} PV")


if __name__ == "__main__":
    main()
