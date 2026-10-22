#!/usr/bin/env python3
"""
generate_tables_v3.py — 方案区三级下钻周表（整树口径）。

与 v2（generate_tables_correct.py）的区别：
  - 页面范围升级为整棵方案树（BEGINS_WITH /solutions/ 与 /solutions-by-scenarios），
    方案页、参考设计、场景案例、子页全部进账，只剔除垃圾路径（含 {}、http:、编码符等）。
  - 按新版方案官网的三大流量承接入口分类：方案页 / 参考设计 / 场景案例。
      01 方案区总览  （每周 1 行：全量 PV/Users/Sessions + CTA + 环比）
      02 流量类别    （每周 每类 1 行合计，全部页面都计入）
      03 页面明细    （每周：全部方案页 + 全部场景案例 + 参考设计 Top3 + “其余页面”合计行）
  - 参考设计只展示流量 Top3 细节，未上榜的参考设计与其余页面仍计入总数，只是不单独列出。
  - 输出目录 data/analysis_v3/，不动原 data/analysis/ 三张表。

口径：
  - 浏览PV = 页面级求和（不去重、按大口径），隐藏“其余页面”合计行保证 01=02=03 对账。
  - 独立访客/会话 = 整树逐周去重查询（query_tree_totals，无 date 维度）。
  - CTA 沿用 v2（click_solution_link + click_contact_expert），作用于整棵方案树。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ga4_fetch  # noqa: E402

OUT_DIR = ROOT / "data" / "analysis_v3"
PROPERTY = "502086217"

CORE_SLUGS = [
    'smart-warehouse-management',
    'voicecollectionanalysis',
    'conversational-voice-ai',
    'smart-agriculture-sensing',
    'smart-livestock-farming',
    'intelligent-video-analytics',
    'indoor-outdoor-positioning',
    'environment-monitoring',
    'building-energy-management',
    'campus-safety-management',
    'building-energy-retrofit',
    'hazard-response',
    'off-grid-mesh-communication',
    'acoustic-audio-ai',
    'home-building-automation',
    'elderly-care-monitoring',
    'whole-home-smart-living',
    'parking-traffic-vision',
    'smart-retail-store',
    'industrial-control-automation',
    'industrial-visual-inspection',
    'in-person-conversation-intelligence',
    'sensecraft-solution',
]

CTA_EVENTS = ["click_solution_link", "click_contact_expert"]

# slug -> 中文名（核心方案沿用现有命名）；参考设计等用 GA4 pageTitle 兜底
SLUG_CN = {
    'smart-warehouse-management': '智能仓储管理',
    'voicecollectionanalysis': '语音采集分析',
    'conversational-voice-ai': '对话式语音AI',
    'smart-agriculture-sensing': '智慧农业感知',
    'smart-livestock-farming': '智慧畜牧',
    'intelligent-video-analytics': '智能视频分析',
    'indoor-outdoor-positioning': '室内外定位',
    'environment-monitoring': '环境监测',
    'building-energy-management': '楼宇能源管理',
    'campus-safety-management': '校园安全管理',
    'building-energy-retrofit': '楼宇节能改造',
    'hazard-response': '应急响应',
    'off-grid-mesh-communication': '离网通信',
    'acoustic-audio-ai': '声学与音频AI',
    'home-building-automation': '家庭与楼宇自动化',
    'elderly-care-monitoring': '老人监护',
    'whole-home-smart-living': '全屋智能',
    'parking-traffic-vision': '停车视觉监测',
    'smart-retail-store': '智慧零售',
    'industrial-control-automation': '工业控制自动化',
    'industrial-visual-inspection': '工业视觉检测',
    'in-person-conversation-intelligence': '现场对话智能',
    'sensecraft-solution': 'SenseCraft工业方案',
}

# 页面类型：与站内 solutionMeta.docType 对齐（入口页指 /solutions/sensecraft-solution 这类品牌/引导页）。
# 用于 02 单方案明细：15 场景页 + 7 技术页 + 1 入口页；
# 场景页与技术页是站点的两层结构（技术页按 by-tech 聚合，向下链接场景页），不是重复页。
DOC_TYPE = {
    'smart-warehouse-management': '场景页',
    'voicecollectionanalysis': '场景页',
    'conversational-voice-ai': '场景页',
    'smart-agriculture-sensing': '场景页',
    'smart-livestock-farming': '场景页',
    'intelligent-video-analytics': '技术页',
    'indoor-outdoor-positioning': '技术页',
    'environment-monitoring': '技术页',
    'building-energy-management': '场景页',
    'campus-safety-management': '场景页',
    'building-energy-retrofit': '场景页',
    'hazard-response': '场景页',
    'off-grid-mesh-communication': '技术页',
    'acoustic-audio-ai': '技术页',
    'home-building-automation': '技术页',
    'elderly-care-monitoring': '场景页',
    'whole-home-smart-living': '场景页',
    'parking-traffic-vision': '场景页',
    'smart-retail-store': '场景页',
    'industrial-control-automation': '技术页',
    'industrial-visual-inspection': '场景页',
    'in-person-conversation-intelligence': '场景页',
    'sensecraft-solution': '入口页',
}

# 页面改名记录：改名发生的那一周（含）起用新名，之前的周保留旧名，不做历史追溯。
# 生效周依据 GA4 逐日 pageTitle 实测：两页均在 2026-10-05~10-11（W41）完成中英文标题切换
# （voicecollectionanalysis 10-08 由 "Voice Collection & Analysis" 切到门店质检定位；
#   conversational-voice-ai 10-08 起由 "On-Device Voice AI" 切到现场/离线语音控制定位）。
SLUG_RENAME = {
    'voicecollectionanalysis': {'old': '语音采集分析', 'new': '门店客服质检', 'since': '2026-10-11'},
    'conversational-voice-ai': {'old': '对话式语音AI', 'new': '离线语音控制', 'since': '2026-10-11'},
}

CATEGORY_CN = {
    "solution": "方案页",
    "refdesign": "参考设计",
    "scenario": "场景案例",
    "legacy": "旧目录页",
    "other": "其他页面",
}

GARBAGE = re.compile(r"[\{\}]|https?:|\(|\)|%")
PATH_RE = re.compile(r"^/solutions/([^/]+?)(?:-zh-hans)?/?$")
NON_PAGE = re.compile(r"\.(txt|xml|json|csv|html?|pdf|md)$")
JUNK_TITLES = {"", "(not set)", "not set", "not found"}
# 404 页的多语言标题（命中即视为死页，归入“其他页面”，不列方案页明细）
NOT_FOUND_TITLES = {"not found", "não encontrado", "no encontrado", "見つかりません", "未找到", "404"}

# 方案区包含的整树前缀：新版方案页 + 场景承接页 + 旧目录树（过渡期并入，全部迁入新地址后再删）
SOL_TREE_PREFIXES = [
    "/solutions/", "/solutions-by-scenarios",
    "/category/solutions", "/category/reference-designs", "/category/success-cases",
]

TRAFFIC_METS = [
    "screenPageViews", "activeUsers", "sessions",
    "userEngagementDuration", "engagementRate",
]


def get_token():
    if __import__("os").environ.get("GA4_AUTH_MODE") == "service_account":
        return ga4_fetch.get_token_service_account()
    return ga4_fetch.get_token_adc()


def run_report(body: dict, token: str) -> dict:
    return ga4_fetch.run_report(body, token)


def week_ending(date_str: str) -> str:
    day = dt.date.fromisoformat(date_str)
    return (day + dt.timedelta(days=(6 - day.weekday()) % 7)).isoformat()


def date_window(args) -> tuple[str, str]:
    today = dt.date.today()
    if args.start and args.end:
        return args.start, args.end
    end = (today - dt.timedelta(days=today.weekday() + 1))  # 最近完整周日
    monday = end - dt.timedelta(days=6)
    start = monday - dt.timedelta(weeks=args.weeks - 1)
    return start.isoformat(), end.isoformat()


def classify(path: str):
    """按新版方案官网的承接页分类：方案页 / 参考设计 / 场景案例 / 其他页面。"""
    if path.startswith("/solutions-by-scenarios") or path.startswith("/solutions/scenario"):
        return "scenario", None
    if path.startswith("/solutions/reference-designs"):
        return "refdesign", None
    if path.startswith("/category/"):
        return ("other" if "/product" in path else "legacy"), None
    m = PATH_RE.match(path)
    if m:
        return "solution", m.group(1)
    return "other", None


def final_cat(path: str, title: str, dead_now: set[str]) -> str:
    """分类后再剔除死页面：非网页文件路径或当前已 404（最新周标题为 Not Found）的死路径，归入“其他页面”（仍计入总数）。"""
    cat, _ = classify(path)
    if NON_PAGE.search(path) or path in dead_now:
        return "other"
    return cat


def path_or_filter() -> dict:
    """方案区整树 orGroup 过滤器（/solutions/ + /solutions-by-scenarios）。"""
    return {
        "orGroup": {
            "expressions": [
                {"filter": {"fieldName": "pagePath",
                            "stringFilter": {"matchType": "BEGINS_WITH", "value": p}}}
                for p in SOL_TREE_PREFIXES
            ]
        }
    }


def solution_name(slug: str, week: str | None = None) -> str:
    """方案展示名：默认取当前名；有改名记录时按周取旧名/新名（改名周起用新名）。"""
    base = SLUG_CN.get(slug, slug)
    rename = SLUG_RENAME.get(slug)
    if not rename or not week:
        return base
    try:
        current = dt.date.fromisoformat(str(week)).isocalendar()[:2]
        since = dt.date.fromisoformat(rename["since"]).isocalendar()[:2]
    except ValueError:
        return base
    return rename["new"] if current >= since else rename["old"]


def page_name(path: str, title_map: dict[str, str], week: str | None = None) -> str:
    """核心页用固定中文名；其他页优先 pageTitle（剔除无效/Seeed 后缀），最后 slug 可读名。"""
    m = PATH_RE.match(path)
    slug = m.group(1) if m else None
    if slug and slug in SLUG_CN:
        return solution_name(slug, week)
    title = title_map.get(path, "").strip()
    if title and title.lower() not in ("(not set)", "not set", ""):
        title = re.sub(r"\s*\|\s*Seeed Studio.*$", "", title)
        if title:
            return title[:45]
    readable = (slug or "").replace("_", " ").replace("-", " ").strip()
    return readable[:45] if readable else path


def query_traffic(token: str, start: str, end: str):
    """整棵方案树：date × pagePath × pageTitle，返回干净行与标题/死页标记。"""
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [
            {"name": "date"}, {"name": "pagePath"}, {"name": "pageTitle"},
        ],
        "metrics": [{"name": m} for m in TRAFFIC_METS],
        "dimensionFilter": path_or_filter(),
        "limit": 250000,
    }
    report = run_report(body, token)
    rows = []
    titles: dict[str, str] = {}
    week_titles: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for r in report.get("rows", []):
        date_raw = r["dimensionValues"][0]["value"]
        path = r["dimensionValues"][1]["value"]
        title = r["dimensionValues"][2]["value"]
        if GARBAGE.search(path):
            continue
        date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        tl = title.strip().lower()
        if tl not in JUNK_TITLES:
            titles.setdefault(path, title)
        week_titles[path][date_str].add(tl)
        mets = [float(v["value"]) for v in r["metricValues"]]
        rows.append({
            "week": week_ending(date_str),
            "path": path,
            "pv": mets[0], "users": mets[1], "sessions": mets[2],
            "dur": mets[3], "eng": mets[4],
        })
    # 当前死页 = 该路径最新日期的所有标题均为 Not Found（即页面目前已 404）
    dead_now: set[str] = set()
    for path, by_date in week_titles.items():
        latest = max(by_date)
        if by_date[latest] and by_date[latest] <= NOT_FOUND_TITLES:
            dead_now.add(path)
    return rows, titles, dead_now


def query_cta(token: str, start: str, end: str) -> dict[str, float]:
    """整树 CTA 事件计数：{week_ending: count}。"""
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": "date"}],
        "metrics": [{"name": "eventCount"}],
        "limit": 100000,
        "dimensionFilter": {
            "andGroup": {
                "expressions": [
                    path_or_filter(),
                    {"filter": {"fieldName": "eventName",
                                "inListFilter": {"values": CTA_EVENTS}}},
                ]
            }
        },
    }
    report = run_report(body, token)
    out: dict[str, float] = defaultdict(float)
    for r in report.get("rows", []):
        date_raw = r["dimensionValues"][0]["value"]
        date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        out[week_ending(date_str)] += float(r["metricValues"][0]["value"])
    return dict(out)



def query_tree_totals(token: str, start: str, end: str) -> tuple[float, float] | None:
    """整树方案区周级去重：无 date 维度，返回 (activeUsers, sessions)。PV 不用这里的（含畸形 URL）。"""
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "metrics": [{"name": "activeUsers"}, {"name": "sessions"}],
        "dimensionFilter": path_or_filter(),
        "limit": 100,
    }
    report = run_report(body, token)
    for r in report.get("rows", []):
        vals = [float(v["value"]) for v in r["metricValues"]]
        return vals[0], vals[1]
    return None



def prev_week_for(weeks: list[str], current: str) -> str | None:
    try:
        idx = weeks.index(current)
    except ValueError:
        return None
    return weeks[idx - 1] if idx > 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--weeks", type=int, default=8,
                        help="展示最近 N 个完整周（多拉一周用于环比计算）")
    parser.add_argument("--ref-top", type=int, default=3,
                        help="页面明细中参考设计按流量展示的 Top N 个（默认 3）")
    args = parser.parse_args()

    start, end = date_window(args)
    # 多拉一周用于环比
    q_start = (dt.date.fromisoformat(start) - dt.timedelta(days=7)).isoformat()
    print(f"v3 weekly generation: {start} -> {end} (query {q_start} ~ {end})", flush=True)

    token = get_token()
    print("Querying /solutions/ tree traffic...", flush=True)
    rows, titles, dead_now = query_traffic(token, q_start, end)
    print("Querying CTA...", flush=True)
    cta_by_week = query_cta(token, start, end)

    # 按 (week, path) 聚合（同 path 可能多语言/多 title）
    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {
        "pv": 0.0, "users": 0.0, "sessions": 0.0, "dur": 0.0, "eng": 0.0,
    })
    for r in rows:
        key = (r["week"], r["path"])
        a = agg[key]
        a["pv"] += r["pv"]; a["users"] += r["users"]; a["sessions"] += r["sessions"]
        a["dur"] += r["dur"]; a["eng"] += r["eng"] * max(r["sessions"], 0)

    weeks = sorted({w for (w, _p) in agg})
    show_weeks = [w for w in weeks if w <= end][-args.weeks:]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 03 页面明细（先算，01/02 依赖它） ----------
    page_rows = []
    for we in reversed(show_weeks):
        items = []
        for (w, path), a in agg.items():
            if w != we:
                continue
            cat = final_cat(path, titles.get(path, ""), dead_now)
            sessions = a["sessions"] or 1
            items.append({
                "path": path,
                "category": cat,
                "name": page_name(path, titles, week=we),
                "pv": a["pv"], "users": a["users"], "sessions": a["sessions"],
                "avg_eng_s": round(a["dur"] / sessions, 1),
                "engagement_rate": round(min(a["eng"] / sessions, 1.0), 4),
            })
        items.sort(key=lambda x: -x["pv"])
        prev_pv = {p: a["pv"] for (w, p), a in agg.items() if w == prev_week_for(weeks, we)}
        # 方案页、场景案例全部展示；参考设计只展示 Top N；其余（旧目录页/产品页/未上榜参考设计/其他）并入合计行
        ref_top = [it for it in items if it["category"] == "refdesign"][:args.ref_top]
        listed = [it for it in items if it["category"] in ("solution", "scenario")] + ref_top
        listed.sort(key=lambda x: -x["pv"])
        listed_ids = {id(it) for it in listed}
        tail = [it for it in items if id(it) not in listed_ids]
        for it in listed:
            last = prev_pv.get(it["path"], 0)
            if last > 0 and it["pv"] >= 5 and last >= 5:
                it["wow_pct"] = round(100 * (it["pv"] - last) / last, 1)
            else:
                it["wow_pct"] = ""
        block = []
        for it in listed:
            block.append({
                "week_ending": we, "流量类别": CATEGORY_CN.get(it["category"], it["category"]),
                "页面名称": it["name"], "页面路径": it["path"], "浏览PV": it["pv"], "访客数": it["users"],
                "会话数": it["sessions"], "平均停留时长(秒)": it["avg_eng_s"],
                "参与率": it["engagement_rate"], "浏览量周环比": it["wow_pct"],
            })
        if tail:
            tail_pv = sum(x["pv"] for x in tail)
            tail_users = sum(x["users"] for x in tail)
            tail_sessions = sum(x["sessions"] for x in tail)
            last = sum(prev_pv.get(x["path"], 0) for x in tail)
            wow = round(100 * (tail_pv - last) / last, 1) if last > 0 and tail_pv >= 5 and last >= 5 else ""
            block.append({
                "week_ending": we, "流量类别": "其余页面",
                "页面名称": f"其余页面（含旧目录页、未上榜参考设计等 {len(tail)} 个页面）",
                "页面路径": "", "浏览PV": tail_pv,
                "访客数": round(tail_users), "会话数": round(tail_sessions),
                "平均停留时长(秒)": round(sum(x["avg_eng_s"] * (x["sessions"] or 1) for x in tail) / max(tail_sessions, 1), 1),
                "参与率": round(sum(x["engagement_rate"] * (x["sessions"] or 1) for x in tail) / max(tail_sessions, 1), 4),
                "浏览量周环比": wow,
            })
        page_rows.extend(block)

    page_fields = ["week_ending", "流量类别", "页面名称", "页面路径", "浏览PV", "访客数",
                   "会话数", "平均停留时长(秒)", "参与率", "浏览量周环比"]
    with open(OUT_DIR / "03_页面流量明细.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=page_fields)
        w.writeheader()
        w.writerows(page_rows)

    # ---------- 02 流量类别 ----------
    cat_fields = ["week_ending", "流量类别", "页面数", "浏览PV", "访客数", "会话数",
                  "平均停留时长(秒)", "参与率", "浏览量周环比"]
    cat_rows = []
    for we in reversed(show_weeks):
        per_cat: dict[str, dict] = defaultdict(lambda: {
            "pages": set(), "pv": 0.0, "users": 0.0, "sessions": 0.0, "dur": 0.0, "eng": 0.0,
        })
        for (w, path), a in agg.items():
            if w != we:
                continue
            cat = final_cat(path, titles.get(path, ""), dead_now)
            c = per_cat[cat]
            c["pages"].add(path)
            c["pv"] += a["pv"]; c["users"] += a["users"]; c["sessions"] += a["sessions"]
            c["dur"] += a["dur"]; c["eng"] += a["eng"]
        prev_by_cat: dict[str, float] = defaultdict(float)
        for (w, path), a in agg.items():
            if w == prev_week_for(weeks, we):
                cat = final_cat(path, titles.get(path, ""), dead_now)
                prev_by_cat[cat] += a["pv"]
        for cat in ["solution", "refdesign", "scenario", "legacy", "other"]:
            if cat not in per_cat:
                continue
            c = per_cat[cat]
            sessions = c["sessions"] or 1
            last_pv = prev_by_cat.get(cat, 0)
            cat_rows.append({
                "week_ending": we, "流量类别": CATEGORY_CN.get(cat, cat),
                "页面数": len(c["pages"]), "浏览PV": round(c["pv"]),
                "访客数": round(c["users"]), "会话数": round(c["sessions"]),
                "平均停留时长(秒)": round(c["dur"] / sessions, 1),
                "参与率": round(min(c["eng"] / sessions, 1.0), 4),
                "浏览量周环比": round(100 * (c["pv"] - last_pv) / last_pv, 1) if last_pv > 0 else "",
            })
    with open(OUT_DIR / "02_流量类别.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cat_fields)
        w.writeheader()
        w.writerows(cat_rows)

    # ---------- 01 方案区总览 ----------
    ov_fields = ["week_ending", "solution_pv", "solution_users", "solution_sessions",
                 "cta_clicks", "solution_wow_pct"]
    ov_rows = []
    for we in reversed(show_weeks):
        print(f"  tree totals for {we}", flush=True)
        week_agg = {p: a for (w, p), a in agg.items() if w == we}
        total_pv = sum(a["pv"] for a in week_agg.values())
        prev_we = prev_week_for(show_weeks, we)
        prev_total = 0.0
        if prev_we:
            prev_total = sum(a["pv"] for (w, p), a in agg.items() if w == prev_we)
        week_start = (dt.date.fromisoformat(we) - dt.timedelta(days=6)).isoformat()
        tree = query_tree_totals(token, week_start, we)
        ov_rows.append({
            "week_ending": we,
            "solution_pv": round(total_pv),
            "solution_users": round(tree[0]) if tree else round(sum(a["users"] for a in week_agg.values())),
            "solution_sessions": round(tree[1]) if tree else round(sum(a["sessions"] for a in week_agg.values())),
            "cta_clicks": int(round(cta_by_week.get(we, 0))),
            "solution_wow_pct": round(100 * (total_pv - prev_total) / prev_total, 1) if prev_total > 0 else "",
        })
    with open(OUT_DIR / "01_方案区总览.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=ov_fields)
        w.writeheader()
        w.writerows(ov_rows)

    # 摘要
    last = ov_rows[0]
    print(f"\nWrote 3 tables to {OUT_DIR}/")
    print(f"最新周 {last['week_ending']}: 方案区浏览PV={last['solution_pv']} "
          f"独立访客={last['solution_users']} 会话={last['solution_sessions']} "
          f"CTA={last['cta_clicks']} 环比={last['solution_wow_pct']}%")
    shown_pv = sum(r["浏览PV"] for r in page_rows if r["week_ending"] == last["week_ending"])
    print(f"页面明细覆盖 PV: {shown_pv:.0f} / 全量 {last['solution_pv']} "
          f"({100 * shown_pv / last['solution_pv']:.1f}%)")


if __name__ == "__main__":
    main()
