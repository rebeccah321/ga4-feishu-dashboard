#!/usr/bin/env python3
"""
partial_week_v3.py — 生成“本周至今”部分周方案区周表（口径与 generate_tables_v3.py 完全一致）。

用法：
  python3 scripts/partial_week_v3.py --start 2026-10-19 --end 2026-10-21 --ref-top 3

输出到 data/analysis_v3_partial/：
  01_方案区总览.csv / 02_流量类别.csv / 03_页面流量明细.csv
环比对比对象 = 上周同期（同样天数窗口往前推 7 天），列名保持与 8 周表一致。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ga4_fetch  # noqa: E402
import generate_tables_v3 as g  # noqa: E402

OUT_DIR = ROOT / "data" / "analysis_v3_partial"


def fetch_flow(token, start: str, end: str):
    """按 date 维度拉整树流量，返回 (cur_agg, prev_agg, titles, dead_now_table)。
    cur = [start, end]，prev = 同样天数往前推 7 天；两者都包含在查询范围内。"""
    d_start = dt.date.fromisoformat(start)
    d_end = dt.date.fromisoformat(end)
    n = (d_end - d_start).days + 1
    prev_start = d_start - dt.timedelta(days=7)
    prev_end = d_end - dt.timedelta(days=7)
    q_start = prev_start.isoformat()

    body = {
        "dateRanges": [{"startDate": q_start, "endDate": end}],
        "dimensions": [{"name": "date"}, {"name": "pagePath"}, {"name": "pageTitle"}],
        "metrics": [{"name": m} for m in g.TRAFFIC_METS],
        "dimensionFilter": g.path_or_filter(),
        "limit": 250000,
    }
    report = g.run_report(body, token)

    week_titles: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    titles: dict[str, str] = {}
    cur: list[dict] = []
    prev: list[dict] = []
    for r in report.get("rows", []):
        date_raw = r["dimensionValues"][0]["value"]
        path = r["dimensionValues"][1]["value"]
        title = r["dimensionValues"][2]["value"]
        if g.GARBAGE.search(path):
            continue
        date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        tl = title.strip().lower()
        if tl not in g.JUNK_TITLES:
            titles.setdefault(path, title)
        week_titles[path][date_str].add(tl)
        mets = [float(v["value"]) for v in r["metricValues"]]
        rec = {"path": path, "pv": mets[0], "users": mets[1], "sessions": mets[2],
               "dur": mets[3], "eng": mets[4]}
        d = dt.date.fromisoformat(date_str)
        if prev_start <= d <= prev_end:
            prev.append(rec)
        elif d_start <= d <= d_end:
            cur.append(rec)

    dead_now: set[str] = set()
    for path, by_date in week_titles.items():
        latest = max(by_date)
        if by_date[latest] and by_date[latest] <= g.NOT_FOUND_TITLES:
            dead_now.add(path)
    return cur, prev, titles, dead_now


def agg_rows(records):
    a: dict[str, dict] = defaultdict(lambda: {"pv": 0.0, "users": 0.0, "sessions": 0.0, "dur": 0.0, "eng": 0.0})
    for r in records:
        x = a[r["path"]]
        x["pv"] += r["pv"]; x["users"] += r["users"]; x["sessions"] += r["sessions"]
        x["dur"] += r["dur"]; x["eng"] += r["eng"] * max(r["sessions"], 0)
    return a


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--ref-top", type=int, default=3)
    args = ap.parse_args()
    end = args.end

    token = g.get_token()
    print(f"Querying partial week {args.start} -> {args.end} ...", flush=True)
    cur, prev, titles, dead_now = fetch_flow(token, args.start, end)
    cur_agg = agg_rows(cur)
    prev_agg = agg_rows(prev)

    # 02 流量类别
    cat_fields = ["week_ending", "流量类别", "页面数", "浏览PV", "访客数", "会话数",
                  "平均停留时长(秒)", "参与率", "浏览量周环比"]
    cat_rows = []
    per_cat: dict[str, dict] = defaultdict(lambda: {"pages": set(), "pv": 0.0, "users": 0.0,
                                                    "sessions": 0.0, "dur": 0.0, "eng": 0.0})
    prev_by_cat: dict[str, float] = defaultdict(float)
    for path, a in cur_agg.items():
        cat = g.final_cat(path, titles.get(path, ""), dead_now)
        c = per_cat[cat]
        c["pages"].add(path)
        c["pv"] += a["pv"]; c["users"] += a["users"]; c["sessions"] += a["sessions"]
        c["dur"] += a["dur"]; c["eng"] += a["eng"]
    for path, a in prev_agg.items():
        cat = g.final_cat(path, titles.get(path, ""), dead_now)
        prev_by_cat[cat] += a["pv"]
    for cat in ["solution", "refdesign", "scenario", "legacy", "other"]:
        if cat not in per_cat:
            continue
        c = per_cat[cat]
        sessions = c["sessions"] or 1
        last = prev_by_cat.get(cat, 0)
        cat_rows.append({
            "week_ending": end, "流量类别": g.CATEGORY_CN.get(cat, cat),
            "页面数": len(c["pages"]), "浏览PV": round(c["pv"]),
            "访客数": round(c["users"]), "会话数": round(c["sessions"]),
            "平均停留时长(秒)": round(c["dur"] / sessions, 1),
            "参与率": round(min(c["eng"] / sessions, 1.0), 4),
            "浏览量周环比": round(100 * (c["pv"] - last) / last, 1) if last > 0 else "",
        })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "02_流量类别.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cat_fields); w.writeheader(); w.writerows(cat_rows)

    # 03 页面明细
    page_fields = ["week_ending", "流量类别", "页面名称", "页面路径", "浏览PV", "访客数",
                   "会话数", "平均停留时长(秒)", "参与率", "浏览量周环比"]
    items = []
    for path, a in cur_agg.items():
        cat = g.final_cat(path, titles.get(path, ""), dead_now)
        sessions = a["sessions"] or 1
        items.append({"path": path, "category": cat, "name": g.page_name(path, titles, week=end),
                      "pv": a["pv"], "users": a["users"], "sessions": a["sessions"],
                      "avg_eng_s": round(a["dur"] / sessions, 1),
                      "engagement_rate": round(min(a["eng"] / sessions, 1.0), 4)})
    items.sort(key=lambda x: -x["pv"])
    prev_pv = {p: a["pv"] for p, a in prev_agg.items()}
    page_rows = []
    ref_top = [it for it in items if it["category"] == "refdesign"][:args.ref_top]
    listed = [it for it in items if it["category"] in ("solution", "scenario")] + ref_top
    listed.sort(key=lambda x: -x["pv"])
    listed_ids = {id(it) for it in listed}
    tail = [it for it in items if id(it) not in listed_ids]
    for it in listed:
        last = prev_pv.get(it["path"], 0)
        it["wow"] = round(100 * (it["pv"] - last) / last, 1) if last > 0 and it["pv"] >= 5 and last >= 5 else ""
    for it in listed:
        page_rows.append({
            "week_ending": end, "流量类别": g.CATEGORY_CN.get(it["category"], it["category"]),
            "页面名称": it["name"], "页面路径": it["path"], "浏览PV": it["pv"], "访客数": it["users"],
            "会话数": it["sessions"], "平均停留时长(秒)": it["avg_eng_s"],
            "参与率": it["engagement_rate"], "浏览量周环比": it["wow"],
        })
    if tail:
        tail_pv = sum(x["pv"] for x in tail)
        tail_users = sum(x["users"] for x in tail)
        tail_sessions = sum(x["sessions"] for x in tail)
        last = sum(prev_pv.get(x["path"], 0) for x in tail)
        wow = round(100 * (tail_pv - last) / last, 1) if last > 0 and tail_pv >= 5 and last >= 5 else ""
        page_rows.append({
            "week_ending": end, "流量类别": "其余页面",
            "页面名称": f"其余页面（含旧目录页、未上榜参考设计等 {len(tail)} 个页面）",
            "页面路径": "", "浏览PV": tail_pv,
            "访客数": round(tail_users), "会话数": round(tail_sessions),
            "平均停留时长(秒)": round(sum(x["avg_eng_s"] * (x["sessions"] or 1) for x in tail) / max(tail_sessions, 1), 1),
            "参与率": round(sum(x["engagement_rate"] * (x["sessions"] or 1) for x in tail) / max(tail_sessions, 1), 4),
            "浏览量周环比": wow,
        })
    with open(OUT_DIR / "03_页面流量明细.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=page_fields); w.writeheader(); w.writerows(page_rows)

    # 01 方案区总览
    ov_fields = ["week_ending", "solution_pv", "solution_users", "solution_sessions",
                 "cta_clicks", "solution_wow_pct"]
    total_pv = sum(a["pv"] for a in cur_agg.values())
    prev_pv_total = sum(a["pv"] for a in prev_agg.values())
    tree = g.query_tree_totals(token, args.start, end)
    cta_total = sum(g.query_cta(token, args.start, end).values())
    ov_rows = [{
        "week_ending": end,
        "solution_pv": round(total_pv),
        "solution_users": round(tree[0]) if tree else round(sum(a["users"] for a in cur_agg.values())),
        "solution_sessions": round(tree[1]) if tree else round(sum(a["sessions"] for a in cur_agg.values())),
        "cta_clicks": int(round(cta_total)),
        "solution_wow_pct": round(100 * (total_pv - prev_pv_total) / prev_pv_total, 1) if prev_pv_total > 0 else "",
    }]
    with open(OUT_DIR / "01_方案区总览.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ov_fields); w.writeheader(); w.writerows(ov_rows)

    last = ov_rows[0]
    shown = sum(r["浏览PV"] for r in page_rows)
    print(f"\nWrote 3 partial-week tables to {OUT_DIR}/")
    print(f"本周至今 {last['week_ending']}: 方案区浏览PV={last['solution_pv']} "
          f"独立访客={last['solution_users']} 会话={last['solution_sessions']} "
          f"CTA={last['cta_clicks']} 环比上周同期={last['solution_wow_pct']}% "
          f"(明细覆盖 {100*shown/last['solution_pv']:.1f}%)")


if __name__ == "__main__":
    main()
