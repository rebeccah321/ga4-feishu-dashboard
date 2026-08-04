#!/usr/bin/env python3
"""
weekly_report.py — 按周一～周日截断的周度方案流量报告。

读取 data/normalized/ 的日级明细，按「周一→周日」周期切周，
输出两张表（所有语言合并，每方案每周一行）：
  1. 方案增长总览    — PV、用户、会话、人均停留、周环比
  2. 单方案流量明细  — PV、用户、会话、人均停留、参与率、跳出率、关键事件

用法：
  python3 scripts/ga4_fetch.py --days 42   # 先拉 42 天数据（覆盖 6 周）
  python3 scripts/weekly_report.py          # 再生成周报
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


def week_ending(date_str: str) -> str:
    """计算周日截断的周末日期（周一到周日为一周）。"""
    d = dt.date.fromisoformat(date_str)
    days_to_sun = (6 - d.weekday()) % 7  # Sunday=6
    return (d + dt.timedelta(days=days_to_sun)).isoformat()


def extract_slug(path: str):
    m = SLUG_RE.match(path)
    return m.group(1) if m else None


def is_garbage(path: str) -> bool:
    return "{" in path or "}" in path


def read_normalized():
    """读取行数最多的 normalized CSV。"""
    norm_dir = ROOT / "data" / "normalized"
    files = list(norm_dir.glob("ga4_daily_*.csv"))
    if not files:
        raise SystemExit("No normalized data. Run: python3 scripts/ga4_fetch.py --days 42")
    best = max(files, key=lambda f: sum(1 for _ in open(f, encoding="utf-8")))
    rows = []
    with open(best, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    print(f"Read {len(rows)} rows from {best.name}")
    print(f"Date range: {min(r['date'] for r in rows)} to {max(r['date'] for r in rows)}")
    return rows


def aggregate(rows):
    """按 (week_ending, slug) 聚合，所有语言合并。"""
    agg = defaultdict(lambda: {
        "pv": 0, "users": 0, "sessions": 0,
        "eng_s": 0.0, "eng_rate": 0.0, "bounce": 0.0, "events": 0
    })
    for r in rows:
        path = r.get("page_path", "")
        if is_garbage(path):
            continue
        slug = extract_slug(path)
        if not slug or slug not in CORE_SLUGS:
            continue
        we = week_ending(r["date"])
        key = (we, slug)
        s = max(int(r.get("sessions", 0)), 1)
        agg[key]["pv"] += int(r.get("screen_page_views", 0))
        agg[key]["users"] += int(r.get("active_users", 0))
        agg[key]["sessions"] += int(r.get("sessions", 0))
        agg[key]["eng_s"] += float(r.get("avg_engagement_seconds", 0)) * s
        agg[key]["eng_rate"] += float(r.get("engagement_rate", 0)) * s
        agg[key]["bounce"] += float(r.get("bounce_rate", 0)) * s
        agg[key]["events"] += int(r.get("key_events", 0))
    return agg


def write_overview(agg, path):
    """方案增长总览：每方案每周一行。"""
    weeks = sorted({we for (we, _) in agg})
    slug_weeks = defaultdict(dict)
    for (we, slug), v in agg.items():
        avg = round(v["eng_s"] / v["sessions"], 1) if v["sessions"] else 0
        slug_weeks[slug][we] = {**v, "avg_s": avg}

    slugs = sorted(slug_weeks.keys(), key=lambda s: -slug_weeks[s].get(weeks[-1], {}).get("pv", 0))

    fields = ["week_ending", "slug", "pv", "users", "sessions", "avg_eng_s", "wow_pct"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for slug in slugs:
            prev_pv = None
            for we in weeks:
                v = slug_weeks[slug].get(we)
                if not v:
                    continue
                wow = ""
                if prev_pv is not None and prev_pv > 0:
                    wow = round(100 * (v["pv"] - prev_pv) / prev_pv, 1)
                w.writerow({
                    "week_ending": we, "slug": slug,
                    "pv": v["pv"], "users": v["users"],
                    "sessions": v["sessions"], "avg_eng_s": v["avg_s"],
                    "wow_pct": wow,
                })
                prev_pv = v["pv"]
    print(f"Wrote: {path} ({len(slugs)} slugs × {len(weeks)} weeks)")


def write_detail(agg, path):
    """单方案流量明细：每方案每周一行（语言合并）。"""
    fields = ["week_ending", "slug", "pv", "users", "sessions",
              "avg_eng_s", "engagement_rate", "bounce_rate", "key_events"]
    rows_out = []
    for (we, slug), v in sorted(agg.items()):
        s = v["sessions"] or 1
        rows_out.append({
            "week_ending": we, "slug": slug,
            "pv": v["pv"], "users": v["users"], "sessions": v["sessions"],
            "avg_eng_s": round(v["eng_s"] / s, 1),
            "engagement_rate": round(v["eng_rate"] / s, 4),
            "bounce_rate": round(v["bounce"] / s, 4),
            "key_events": v["events"],
        })
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows_out)
    print(f"Wrote: {path} ({len(rows_out)} rows)")


def main():
    rows = read_normalized()
    agg = aggregate(rows)

    out_dir = ROOT / "data" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    write_overview(agg, out_dir / "方案增长总览.csv")
    write_detail(agg, out_dir / "单方案流量明细.csv")

    weeks = sorted({we for (we, _) in agg})
    print(f"\n周度区间 ({len(weeks)} 周, 周一~周日):")
    for we in weeks:
        total = sum(v["pv"] for (w, s), v in agg.items() if w == we)
        print(f"  截止 {we} (周日): 方案页合计 {total} PV")


if __name__ == "__main__":
    main()
