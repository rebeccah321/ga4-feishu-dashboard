#!/usr/bin/env python3
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
DATA_JSON = ROOT / "dashboard/data/latest.json"
DATA_CSV = ROOT / "dashboard/data/latest.csv"
OUT_DIR = ROOT / "exports/feishu-base-import"
EVENTS_DIR = ROOT / "data/events"
PLATFORMS = ["LinkedIn", "X", "FB", "小红书", "抖音"]
SOLUTION_CATEGORY_PATHS = ["/category/solutions-zh-hans", "/category/solutions"]
GA_EVENT_NAMES = ["page_view", "scroll", "first_visit", "session_start", "user_engagement"]
def solution_item(name: str, slug: str, english: str, extras: Sequence[str] | None = None) -> Dict[str, Any]:
    bare = "/" + slug.removeprefix("/solutions/")
    aliases = [slug, bare]
    if extras:
        aliases.extend(extras)
    return {
        "name": name,
        "slug": slug,
        "aliases": aliases,
        "english": english,
        "urls": [
            f"https://www.seeedstudio.com.cn{slug}",
            f"https://www.seeed.cc{slug}",
            f"https://www.seeed.co.jp{slug}",
        ],
    }


SOLUTIONS = [
    solution_item("语音采集与分析", "/solutions/voicecollectionanalysis", "Voice Collection and Analysis", [
        "/respeaker-clip-wearable-ai-recorder.html",
        "/blog/2026/02/03/from-hearing-clearly-to-understanding-sound-how-respeaker-brings-voice-ai-into-real-world-scenarios/",
    ]),
    solution_item("智能仓储管理", "/solutions/smart-warehouse-management", "Smart Warehouse Management"),
    solution_item("智能视频分析", "/solutions/intelligent-video-analytics", "Intelligent Video Analytics", [
        "/intelligent-video-analytics",
    ]),
    solution_item("室内外定位", "/solutions/indoor-outdoor-positioning", "Indoor and Outdoor Positioning", [
        "/sensecap-t1000-tracker",
        "/Positioning-Tracker-c-2495.html",
    ]),
    solution_item("对话式语音 AI", "/solutions/conversational-voice-ai", "Conversational Voice AI", [
        "/Home-Assistant-Voice-p-6998.html",
        "/ReSpeaker-Lite-Voice-Assistant-Kit-p-5929.html",
    ]),
    solution_item("环境监测", "/solutions/environment-monitoring", "Environment Monitoring", [
        "/industries/environment-monitoring",
        "/Environment-Monitoring-c-2535.html",
        "/blog/environment-monitoring/",
    ]),
    solution_item("楼宇能源改造", "/solutions/building-energy-retrofit", "Building Energy Retrofit", [
        "/blog/2024/05/14/introducing-recomputer-r1000-an-industrial-edge-iot-gateway-powered-by-raspberry-pi-ideal-for-smart-building-and-energy-management/",
    ]),
    solution_item("智能畜牧养殖", "/solutions/smart-livestock-farming", "Smart Livestock Farming", [
        "/Farming-Environment-Monitoring.html",
    ]),
    solution_item("智慧农业传感", "/solutions/smart-agriculture-sensing", "Smart Agriculture Sensing", [
        "/Wireless-Smart-Agriculture-Kit-Outdoor-p-4950.html",
    ]),
    solution_item("校园安全管理", "/solutions/campus-safety-management", "Campus Safety Management", [
        "/lorawan-safety-badge",
    ]),
    solution_item("应急响应", "/solutions/hazard-response", "Hazard Response", [
        "/Mission-Pack.html",
        "/blog/2023/11/29/hazard-response-mission-pack-october-workshops-recap/",
    ]),
    solution_item("楼宇能源管理", "/solutions/building-energy-management", "Building Energy Management", [
        "/Energy-Shield.html",
        "/blog/2024/05/14/introducing-recomputer-r1000-an-industrial-edge-iot-gateway-powered-by-raspberry-pi-ideal-for-smart-building-and-energy-management/",
    ]),
]


def num(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fmt_number(value: float, digits: int = 0) -> str:
    if math.isclose(value, round(value), abs_tol=0.0001) and digits == 0:
        return str(int(round(value)))
    return f"{value:.{digits}f}"


def fmt_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def weighted_rate(total_weighted: float, total_weight: float) -> float:
    return total_weighted / total_weight if total_weight else 0.0


def trend(current: float, previous: float, label: str = "最近14天 vs 前14天") -> str:
    if not previous:
        return f"{label}: 待观察"
    delta = (current - previous) / previous
    if abs(delta) < 0.01:
        return f"{label}: 基本持平"
    direction = "上升" if delta > 0 else "下降"
    return f"{label}: {direction}{abs(delta) * 100:.1f}%"


def rows_for_week(rows: Sequence[Dict[str, str]], week: str) -> List[Dict[str, str]]:
    return [row for row in rows if row.get("Week Start") == week]


def reporting_week(rows: Sequence[Dict[str, str]]) -> str:
    weeks = week_list(rows)
    if not weeks:
        return latest_week(rows)
    latest = weeks[-1]
    latest_dates = {row.get("Date", "") for row in rows_for_week(rows, latest) if row.get("Date")}
    if len(latest_dates) < 7 and len(weeks) > 1:
        return weeks[-2]
    return latest


def latest_week_rows(rows: Sequence[Dict[str, str]]) -> Tuple[str, List[Dict[str, str]]]:
    week = reporting_week(rows)
    return week, rows_for_week(rows, week)


def week_end_date(week: str) -> str:
    return (dt.date.fromisoformat(week) + dt.timedelta(days=6)).isoformat()


def reporting_weeks(rows: Sequence[Dict[str, str]], count: int = 4) -> List[str]:
    weeks = week_list(rows)
    if not weeks:
        return []
    latest = weeks[-1]
    latest_dates = {row.get("Date", "") for row in rows_for_week(rows, latest) if row.get("Date")}
    if len(latest_dates) < 7 and len(weeks) > 1:
        weeks = weeks[:-1]
    return weeks[-count:]


def week_range_label(week: str) -> str:
    return f"{week} 至 {week_end_date(week)}"


def data_coverage_label(rows: Sequence[Dict[str, str]], week: str) -> str:
    week_rows = rows_for_week(rows, week)
    start, end = date_bounds(week_rows)
    return f"{start} 至 {end}"


def week_list(rows: Sequence[Dict[str, str]]) -> List[str]:
    return sorted({row.get("Week Start", "") for row in rows if row.get("Week Start")})


def solution_rows(rows: Sequence[Dict[str, str]], item: Dict[str, Any]) -> List[Dict[str, str]]:
    return rows_for_paths(rows, item["aliases"])


def solution_week_rows(rows: Sequence[Dict[str, str]], week: str, item: Dict[str, Any]) -> List[Dict[str, str]]:
    return rows_for_paths(rows_for_week(rows, week), item["aliases"])


def is_solution_related_path(path: str) -> bool:
    path = (path or "").lower()
    if path.startswith("/solutions"):
        return True
    if path in SOLUTION_CATEGORY_PATHS:
        return True
    for item in SOLUTIONS:
        if any(path == alias.lower() or path.startswith(f"{alias.lower()}/") for alias in item["aliases"]):
            return True
    return False


def solution_related_rows(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    related = []
    for row in rows:
        if is_solution_related_path(row.get("Page Path", "")):
            related.append(row)
    return related


def read_payload() -> Dict[str, Any]:
    return json.loads(DATA_JSON.read_text(encoding="utf-8"))


def source_csv_path(payload: Dict[str, Any]) -> Path:
    source_name = payload.get("source_csv")
    if source_name:
        candidate = ROOT / "data/normalized" / source_name
        if candidate.exists():
            return candidate
    merged = sorted((ROOT / "data/normalized").glob("ga4_normalized_with_solutions_*.csv"))
    if merged:
        return merged[-1]
    return DATA_CSV


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def latest_event_csv() -> Path | None:
    files = sorted(EVENTS_DIR.glob("ga4_solution_events_*.csv"))
    return files[-1] if files else None


def event_rows_for_week(rows: Sequence[Dict[str, str]], week: str) -> List[Dict[str, str]]:
    return [row for row in rows if row.get("周次") == week]


def event_counts(rows: Sequence[Dict[str, str]], item: Dict[str, Any]) -> Dict[str, float]:
    counts = defaultdict(float)
    for row in rows:
        if row.get("solution名称") != item["name"] and row.get("solution路径") != item["slug"]:
            continue
        event_name = row.get("eventName") or ""
        counts[event_name] += num(row.get("eventCount"))
        counts["keyEvents"] += num(row.get("keyEvents"))
    return dict(counts)


def date_bounds(rows: Sequence[Dict[str, str]]) -> Tuple[str, str]:
    dates = sorted({row.get("Date", "") for row in rows if row.get("Date")})
    if not dates:
        today = dt.date.today().isoformat()
        return today, today
    return dates[0], dates[-1]


def latest_week(rows: Sequence[Dict[str, str]]) -> str:
    weeks = sorted({row.get("Week Start", "") for row in rows if row.get("Week Start")})
    if weeks:
        return weeks[-1]
    start, _ = date_bounds(rows)
    return start


def path_matches(row: Dict[str, str], paths: Sequence[str]) -> bool:
    page_path = row.get("Page Path", "")
    return any(page_path == path or page_path.startswith(f"{path}/") for path in paths)


def rows_for_paths(rows: Sequence[Dict[str, str]], paths: Sequence[str]) -> List[Dict[str, str]]:
    return [row for row in rows if path_matches(row, paths)]


def split_period(rows: Sequence[Dict[str, str]]) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    dates = sorted({row.get("Date", "") for row in rows if row.get("Date")})
    if len(dates) < 2:
        return list(rows), []
    midpoint = len(dates) // 2
    previous_dates = set(dates[:midpoint])
    current_dates = set(dates[midpoint:])
    previous = [row for row in rows if row.get("Date") in previous_dates]
    current = [row for row in rows if row.get("Date") in current_dates]
    return current, previous


def totals(rows: Iterable[Dict[str, str]]) -> Dict[str, float]:
    result = defaultdict(float)
    for row in rows:
        sessions = num(row.get("Sessions"))
        result["views"] += num(row.get("Views"))
        result["users"] += num(row.get("Active Users"))
        result["sessions"] += sessions
        result["conversions"] += num(row.get("Conversions"))
        result["revenue"] += num(row.get("Revenue"))
        result["engagement_weighted"] += num(row.get("Avg Engagement Seconds")) * sessions
        result["bounce_weighted"] += num(row.get("Bounce Rate")) * sessions
        result["engagement_rate_weighted"] += num(row.get("Engagement Rate")) * sessions
    result["avg_engagement_seconds"] = weighted_rate(result["engagement_weighted"], result["sessions"])
    result["bounce_rate"] = weighted_rate(result["bounce_weighted"], result["sessions"])
    result["engagement_rate"] = weighted_rate(result["engagement_rate_weighted"], result["sessions"])
    result["conversion_rate"] = result["conversions"] / result["sessions"] if result["sessions"] else 0.0
    return dict(result)


def aggregate(rows: Iterable[Dict[str, str]], key_fields: Sequence[str]) -> List[Dict[str, Any]]:
    grouped: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(field, "") for field in key_fields)
        item = grouped.setdefault(key, {field: row.get(field, "") for field in key_fields})
        sessions = num(row.get("Sessions"))
        item["Views"] = item.get("Views", 0.0) + num(row.get("Views"))
        item["Active Users"] = item.get("Active Users", 0.0) + num(row.get("Active Users"))
        item["Sessions"] = item.get("Sessions", 0.0) + sessions
        item["Conversions"] = item.get("Conversions", 0.0) + num(row.get("Conversions"))
        item["Revenue"] = item.get("Revenue", 0.0) + num(row.get("Revenue"))
        item["_engagement"] = item.get("_engagement", 0.0) + num(row.get("Avg Engagement Seconds")) * sessions
        item["_bounce"] = item.get("_bounce", 0.0) + num(row.get("Bounce Rate")) * sessions
        item["_engagement_rate"] = item.get("_engagement_rate", 0.0) + num(row.get("Engagement Rate")) * sessions
    output = []
    for item in grouped.values():
        sessions = item.get("Sessions", 0.0)
        item["Avg Engagement Seconds"] = weighted_rate(item.pop("_engagement", 0.0), sessions)
        item["Bounce Rate"] = weighted_rate(item.pop("_bounce", 0.0), sessions)
        item["Engagement Rate"] = weighted_rate(item.pop("_engagement_rate", 0.0), sessions)
        item["Conversion Rate"] = item.get("Conversions", 0.0) / sessions if sessions else 0.0
        output.append(item)
    return output


def judgement(metric: str, value: float) -> str:
    if metric == "bounce":
        if value >= 0.6:
            return "跳出偏高，优先检查落地页首屏、加载速度和CTA清晰度"
        if value <= 0.35:
            return "跳出率较健康，页面承接质量较好"
        return "跳出率中等，继续观察不同渠道和页面差异"
    if metric == "conversion":
        if value <= 0.01:
            return "关键事件偏弱，需检查页面承接和已配置转化事件"
        if value >= 0.05:
            return "转化表现较强，可继续放大有效渠道"
        return "转化处于可优化区间，建议做渠道和页面拆解"
    if metric == "engagement":
        if value < 10:
            return "互动时间偏短，用户可能没有快速找到有效信息"
        if value > 40:
            return "互动时间较长，内容具备进一步承接转化的空间"
        return "互动时间正常，可继续按页面主题细分判断"
    return "用于周度趋势观察"


def dominant_channel(rows: Sequence[Dict[str, str]]) -> str:
    channels = aggregate(rows, ["Channel Group"])
    if not channels:
        return "未命中"
    channels.sort(key=lambda item: item.get("Sessions", 0), reverse=True)
    return str(channels[0].get("Channel Group") or "Unassigned")


def growth_status(views: float, users: float, conversions: float, matched_rows: Sequence[Dict[str, str]]) -> str:
    if not matched_rows:
        return "GA4未命中"
    if views < 20:
        return "样本偏少"
    if conversions <= 0:
        return "有流量但转化偏弱"
    if users and conversions / users >= 0.05:
        return "转化表现较强"
    return "有可观察流量"


def solution_supported_metrics() -> str:
    return "page_view / scroll / first_visit / session_start / user_engagement / keyEvents"


def latest_solution_growth(rows: Sequence[Dict[str, str]]) -> str:
    weeks = week_list(rows)
    current_week = reporting_week(rows)
    if current_week not in weeks or weeks.index(current_week) == 0:
        return "待观察"
    previous_week = weeks[weeks.index(current_week) - 1]
    best_name = ""
    best_delta = float("-inf")
    for item in SOLUTIONS:
        current_views = totals(solution_week_rows(rows, current_week, item)).get("views", 0.0)
        previous_views = totals(solution_week_rows(rows, previous_week, item)).get("views", 0.0)
        delta = current_views - previous_views
        if delta > best_delta:
            best_delta = delta
            best_name = item["name"]
    if not best_name:
        return "待观察"
    return best_name if best_delta > 0 else f"{best_name}(无正增长)"


def solution_growth_for_week(rows: Sequence[Dict[str, str]], week: str) -> str:
    weeks = week_list(rows)
    if week not in weeks or weeks.index(week) == 0:
        return "待观察"
    previous_week = weeks[weeks.index(week) - 1]
    best_name = ""
    best_delta = float("-inf")
    for item in SOLUTIONS:
        current_views = totals(solution_week_rows(rows, week, item)).get("views", 0.0)
        previous_views = totals(solution_week_rows(rows, previous_week, item)).get("views", 0.0)
        delta = current_views - previous_views
        if delta > best_delta:
            best_delta = delta
            best_name = item["name"]
    if not best_name:
        return "待观察"
    return best_name if best_delta > 0 else f"{best_name}(无正增长)"


def build_growth_overview(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    output = []
    for week in reporting_weeks(rows):
        week_rows = rows_for_week(rows, week)
        if not week_rows:
            continue
        total = totals(week_rows)
        solution_rows_in_week = solution_related_rows(week_rows)
        solution_total = totals(solution_rows_in_week)
        key_events = solution_total.get("conversions", 0.0)
        key_event_rate = key_events / solution_total["views"] if solution_total["views"] else 0.0
        output.append({
            "周次": week,
            "周范围": week_range_label(week),
            "数据覆盖": data_coverage_label(rows, week),
            "官网总访问量": fmt_number(total["views"]),
            "独立访客数": fmt_number(total["users"]),
            "solution页面总访问量": fmt_number(solution_total["views"]),
            "CTA总点击量": "GA4未发现可验证CTA事件",
            "表单提交总数": "GA4未发现可验证表单事件",
            "社媒总曝光量": "未接入平台后台数据",
            "增长最快solution": solution_growth_for_week(rows, week),
            "表单转化率": "GA4未发现可验证表单事件",
            "CTA点击率": "GA4未发现可验证CTA事件",
            "解决方案独立访客数": fmt_number(solution_total["users"]),
            "解决方案会话数": fmt_number(solution_total["sessions"]),
            "GA关键事件总数": fmt_number(key_events),
            "GA关键事件转化率": fmt_pct(key_event_rate),
            "已接入事件口径": solution_supported_metrics(),
            "数据说明": "CTA、表单提交、加购、销售跟进当前没有可验证GA事件字段，未作为真实指标填入",
        })
    return output


def solution_event_context(rows: Sequence[Dict[str, str]], week: str) -> Tuple[Path | None, List[Dict[str, str]]]:
    event_path = latest_event_csv()
    event_week_rows: List[Dict[str, str]] = read_rows(event_path) if event_path and event_path.exists() else []
    return event_path, event_rows_for_week(event_week_rows, week)


def build_single_solution_detail(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    output = []
    for week in reporting_weeks(rows):
        week_rows = rows_for_week(rows, week)
        _, event_week_rows = solution_event_context(rows, week)
        for item in SOLUTIONS:
            matched = solution_rows(week_rows, item)
            total = totals(matched)
            event_counts_by_name = event_counts(event_week_rows, item)
            output.append({
                "最新周次": week,
                "周范围": week_range_label(week),
                "数据覆盖": data_coverage_label(rows, week),
                "solution名称": item["name"],
                "solution路径": item["slug"],
                "英文名称": item["english"],
                "中英日URL": " | ".join(item["urls"]),
                "匹配路径别名": " | ".join(item["aliases"]),
                "落地页访问量": fmt_number(total.get("views", 0.0)),
                "独立访客数": fmt_number(total.get("users", 0.0)),
                "会话数": fmt_number(total.get("sessions", 0.0)),
                "平均停留时长秒": fmt_number(total.get("avg_engagement_seconds", 0.0), 2),
                "参与率": fmt_pct(total.get("engagement_rate", 0.0)),
                "跳出率": fmt_pct(total.get("bounce_rate", 0.0)),
                "关键事件数(GA conversions)": fmt_number(total.get("conversions", 0.0)),
                "关键事件转化率": fmt_pct(total.get("conversion_rate", 0.0)),
                "主要流量来源": dominant_channel(matched) if matched else "GA4本周未命中",
                "页面浏览事件(page_view)": fmt_number(event_counts_by_name.get("page_view", 0.0)),
                "滚动事件(scroll)": fmt_number(event_counts_by_name.get("scroll", 0.0)),
                "首次访问(first_visit)": fmt_number(event_counts_by_name.get("first_visit", 0.0)),
                "会话开始(session_start)": fmt_number(event_counts_by_name.get("session_start", 0.0)),
                "用户互动(user_engagement)": fmt_number(event_counts_by_name.get("user_engagement", 0.0)),
                "关键事件(keyEvents)": fmt_number(event_counts_by_name.get("keyEvents", 0.0)),
                "数据状态": "有GA页面数据" if matched else "GA4本周未命中该方案页",
            })
    return output


def build_solution_funnel(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    output = []
    for week in reporting_weeks(rows):
        week_rows = rows_for_week(rows, week)
        _, event_week_rows = solution_event_context(rows, week)
        for item in SOLUTIONS:
            matched = solution_rows(week_rows, item)
            total = totals(matched)
            event_counts_by_name = event_counts(event_week_rows, item)
            page_views = event_counts_by_name.get("page_view", 0.0) or total.get("views", 0.0)
            scrolls = event_counts_by_name.get("scroll", 0.0)
            engagements = event_counts_by_name.get("user_engagement", 0.0)
            key_events = event_counts_by_name.get("keyEvents", 0.0) or total.get("conversions", 0.0)
            sessions = event_counts_by_name.get("session_start", 0.0) or total.get("sessions", 0.0)
            output.append({
                "最新周次": week,
                "周范围": week_range_label(week),
                "数据覆盖": data_coverage_label(rows, week),
                "solution名称": item["name"],
                "solution路径": item["slug"],
                "页面访问量": fmt_number(total.get("views", 0.0)),
                "CTA": "GA4未发现可验证CTA事件",
                "表单提交(leads)": "GA4未发现可验证表单事件",
                "一键加购": "GA4未发现可验证加购事件",
                "销售跟进数": "未接入CRM/销售跟进数据",
                "页面->CTA转化率": "GA4未发现可验证CTA事件",
                "CTA->表单转化率": "GA4未发现可验证表单事件",
                "访问层_page_view": fmt_number(page_views),
                "滚动层_scroll": fmt_number(scrolls),
                "互动层_user_engagement": fmt_number(engagements),
                "关键事件_keyEvents": fmt_number(key_events),
                "GA会话数": fmt_number(sessions),
                "滚动率_scroll/page_view": fmt_pct(scrolls / page_views if page_views else 0.0),
                "互动率_user_engagement/page_view": fmt_pct(engagements / page_views if page_views else 0.0),
                "关键事件转化率_keyEvents/page_view": fmt_pct(key_events / page_views if page_views else 0.0),
                "会话关键事件率_keyEvents/session_start": fmt_pct(key_events / sessions if sessions else 0.0),
                "GA已接入漏斗事件": solution_supported_metrics(),
                "缺失埋点说明": "未发现可验证的CTA点击、表单提交、加购、销售跟进事件；这些不能作为真实漏斗字段填数",
                "数据状态": "有GA事件数据" if any(event_counts_by_name.get(name, 0.0) for name in GA_EVENT_NAMES) else "GA4本周未命中该方案页事件",
            })
    return output


def build_dashboard_main_table(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    details = build_single_solution_detail(rows)
    funnels = build_solution_funnel(rows)
    funnel_by_key = {(row["最新周次"], row["solution名称"]): row for row in funnels}
    output = []
    for row in details:
        funnel = funnel_by_key.get((row["最新周次"], row["solution名称"]), {})
        output.append({
            "周次": row["最新周次"],
            "周范围": row["周范围"],
            "数据覆盖": row["数据覆盖"],
            "solution名称": row["solution名称"],
            "solution路径": row["solution路径"],
            "英文名称": row["英文名称"],
            "中英日URL": row["中英日URL"],
            "匹配路径别名": row["匹配路径别名"],
            "落地页访问量": row["落地页访问量"],
            "独立访客数": row["独立访客数"],
            "会话数": row["会话数"],
            "平均停留时长秒": row["平均停留时长秒"],
            "参与率": row["参与率"],
            "跳出率": row["跳出率"],
            "主要流量来源": row["主要流量来源"],
            "页面浏览事件_page_view": row["页面浏览事件(page_view)"],
            "滚动事件_scroll": row["滚动事件(scroll)"],
            "用户互动_user_engagement": row["用户互动(user_engagement)"],
            "关键事件_keyEvents": row["关键事件(keyEvents)"],
            "关键事件数_GA_conversions": row["关键事件数(GA conversions)"],
            "关键事件转化率": row["关键事件转化率"],
            "滚动率_scroll_page_view": funnel.get("滚动率_scroll/page_view", "0.00%"),
            "互动率_user_engagement_page_view": funnel.get("互动率_user_engagement/page_view", "0.00%"),
            "会话关键事件率": funnel.get("会话关键事件率_keyEvents/session_start", "0.00%"),
            "CTA状态": "GA4未发现可验证CTA事件",
            "表单提交状态": "GA4未发现可验证表单事件",
            "一键加购状态": "GA4未发现可验证加购事件",
            "销售跟进状态": "未接入CRM/销售跟进数据",
            "社媒平台数据状态": "平台后台数据未接入；GA4仅可观察Organic Social网站会话",
            "GA已接入口径": solution_supported_metrics(),
            "数据状态": row["数据状态"],
        })
    return output


def build_channels(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    output = []
    for week in reporting_weeks(rows):
        week_rows = rows_for_week(rows, week)
        start, end = date_bounds(week_rows)
        items = aggregate(solution_related_rows(week_rows), ["Channel Group"])
        items.sort(key=lambda item: item.get("Views", 0), reverse=True)
        for index, item in enumerate(items[:5], start=1):
            conversion_rate = item.get("Conversion Rate", 0.0)
            output.append({
                "周次": week,
                "周范围": week_range_label(week),
                "周期开始": start,
                "周期结束": end,
                "渠道": item.get("Channel Group") or "Unassigned",
                "用户数": fmt_number(item.get("Active Users", 0.0)),
                "会话数": fmt_number(item.get("Sessions", 0.0)),
                "浏览量": fmt_number(item.get("Views", 0.0)),
                "转化数": fmt_number(item.get("Conversions", 0.0)),
                "转化率": fmt_pct(conversion_rate),
                "跳出率": fmt_pct(item.get("Bounce Rate", 0.0)),
                "平均会话时长秒": fmt_number(item.get("Avg Engagement Seconds", 0.0), 2),
                "渠道排序": str(index),
                "业务判断": judgement("conversion", conversion_rate),
                "建议动作": "高转化渠道继续放大；高流量低关键事件渠道优先检查落地页承接和事件配置",
            })
    return output


def infer_topic(path: str, title: str) -> str:
    text = f"{path} {title}".lower()
    if "blog" in text:
        return "内容/博客"
    if "case" in text or "success" in text:
        return "案例"
    if "solution" in text:
        return "解决方案"
    if "checkout" in text or "cart" in text:
        return "购买/购物车"
    if path.endswith(".html"):
        return "产品页"
    if path == "/":
        return "首页"
    return "其他"


def build_solution_pages(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    output = []
    for week in reporting_weeks(rows):
        week_rows = rows_for_week(rows, week)
        start, end = date_bounds(week_rows)
        items = aggregate(solution_related_rows(week_rows), ["Page Path", "Page Title"])
        items.sort(key=lambda item: item.get("Views", 0), reverse=True)
        for item in items[:20]:
            conversion_rate = item.get("Conversion Rate", 0.0)
            path = item.get("Page Path") or "/"
            title = item.get("Page Title") or ""
            output.append({
                "周次": week,
                "周范围": week_range_label(week),
                "周期开始": start,
                "周期结束": end,
                "页面路径": path,
                "页面标题": title,
                "浏览量": fmt_number(item.get("Views", 0.0)),
                "用户数": fmt_number(item.get("Active Users", 0.0)),
                "会话数": fmt_number(item.get("Sessions", 0.0)),
                "参与率": fmt_pct(item.get("Engagement Rate", 0.0)),
                "跳出率": fmt_pct(item.get("Bounce Rate", 0.0)),
                "平均互动秒": fmt_number(item.get("Avg Engagement Seconds", 0.0), 2),
                "关键事件触发次数": fmt_number(item.get("Conversions", 0.0)),
                "转化数": fmt_number(item.get("Conversions", 0.0)),
                "主要事件": solution_supported_metrics(),
                "页面类型/内容主题": infer_topic(path, title),
                "业务判断": judgement("conversion", conversion_rate),
                "建议动作": "保留高浏览页面；对高跳出或低关键事件页面优化首屏价值、内容结构和内部链接",
            })
    return output


def build_social_platforms(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    output = []
    utm_map = {
        "LinkedIn": "utm_source=linkedin&utm_medium=social",
        "X": "utm_source=x&utm_medium=social",
        "FB": "utm_source=facebook&utm_medium=social",
        "小红书": "utm_source=xiaohongshu&utm_medium=social",
        "抖音": "utm_source=douyin&utm_medium=social",
    }
    for week in reporting_weeks(rows):
        week_rows = rows_for_week(rows, week)
        start, end = date_bounds(week_rows)
        social_rows = [row for row in week_rows if row.get("Channel Group") == "Organic Social" and is_solution_related_path(row.get("Page Path", ""))]
        social_total = totals(social_rows)
        output.append({
            "周次": week,
            "周范围": week_range_label(week),
            "周期开始": start,
            "周期结束": end,
            "平台": "GA4 Organic Social 合计",
            "粉丝数": "",
            "发帖数": "",
            "曝光/播放": "未接入平台后台数据",
            "互动数": "未接入平台后台数据",
            "互动率": "未接入平台后台数据",
            "点击数": "GA4仅能观察网站会话，未拆平台点击",
            "网站会话数": fmt_number(social_total.get("sessions", 0.0)),
            "网站用户数": fmt_number(social_total.get("users", 0.0)),
            "转化数": fmt_number(social_total.get("conversions", 0.0)),
            "转化率": fmt_pct(social_total.get("conversion_rate", 0.0)),
            "最佳内容类型": "待接入平台后台数据后判断",
            "适合继续投入": "待判断",
            "建议UTM渠道参数": "utm_medium=social",
            "数据状态": "GA4仅有社媒合计，未拆平台",
            "备注": "平台拆分需后续内容链接统一添加 UTM",
        })
        for platform in PLATFORMS:
            output.append({
                "周次": week,
                "周范围": week_range_label(week),
                "周期开始": start,
                "周期结束": end,
                "平台": platform,
                "粉丝数": "",
                "发帖数": "",
                "曝光/播放": "",
                "互动数": "",
                "互动率": "",
                "点击数": "",
                "网站会话数": "",
                "网站用户数": "",
                "转化数": "",
                "转化率": "",
                "最佳内容类型": "",
                "适合继续投入": "",
                "建议UTM渠道参数": utm_map[platform],
                "数据状态": "待手动导入平台后台数据",
                "备注": "用于判断内容应该发什么、发到哪里",
            })
    return output


def write_csv_file(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_single_base_import_table(sheets: Sequence[Tuple[str, Sequence[Dict[str, str]]]]) -> List[Dict[str, str]]:
    meta_fields = ["资源表", "资源排序", "是否仪表盘主记录"]
    union_fields: List[str] = []
    for _, rows in sheets:
        for row in rows:
            for field in row.keys():
                if field not in union_fields and field not in meta_fields:
                    union_fields.append(field)

    fields = meta_fields + union_fields
    output: List[Dict[str, str]] = []
    for sheet_index, (name, rows) in enumerate(sheets, start=0):
        for row_index, row in enumerate(rows, start=1):
            item = {field: "" for field in fields}
            item["资源表"] = name
            item["资源排序"] = f"{sheet_index:02d}-{row_index:04d}"
            item["是否仪表盘主记录"] = "是" if name == "00_多维仪表盘主表" else "否"
            for field, value in row.items():
                item[field] = str(value)
            output.append(item)
    return output


def column_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def sheet_xml(headers: Sequence[str], rows: Sequence[Dict[str, str]]) -> str:
    all_rows = [list(headers)] + [[str(row.get(header, "")) for header in headers] for row in rows]
    xml_rows = []
    for row_index, values in enumerate(all_rows, start=1):
        cells = []
        for col_index, value in enumerate(values, start=1):
            ref = f"{column_name(col_index)}{row_index}"
            cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        xml_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(xml_rows)}</sheetData>'
        '</worksheet>'
    )


def write_xlsx(path: Path, sheets: Sequence[Tuple[str, Sequence[Dict[str, str]]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        sheet_defs = []
        rel_defs = []
        overrides = [
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        ]
        for index, (name, rows) in enumerate(sheets, start=1):
            headers = list(rows[0].keys()) if rows else []
            zf.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(headers, rows))
            sheet_defs.append(f'<sheet name="{escape(name[:31])}" sheetId="{index}" r:id="rId{index}"/>')
            rel_defs.append(f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>')
            overrides.append(f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>')
        zf.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>' + "".join(overrides) + "</Types>")
        zf.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        zf.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + "".join(sheet_defs) + "</sheets></workbook>")
        zf.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(rel_defs) + "</Relationships>")


def write_readme(path: Path, source_path: Path, sheets: Sequence[Tuple[str, Sequence[Dict[str, str]]]]) -> None:
    names = "\n".join(f"- {name}: {len(rows)} rows" for name, rows in sheets)
    overview_rows = sheets[0][1] if sheets else []
    week_ranges = "; ".join(dict.fromkeys(row.get("周范围", row.get("周次", "")) for row in overview_rows))
    latest_complete_week = overview_rows[-1].get("周范围", "") if overview_rows else ""
    path.write_text(f"""# 飞书多维表格导入包

最快导入方式：

1. 打开飞书多维表格，新建一个空 Base。
2. 选择「导入 Excel/CSV」。
3. 优先上传 `seeed_解决方案增长_飞书单表导入.xlsx`。这是 1 张 Base 表，包含仪表盘主记录和 6 类资源明细，不会在左侧拆成多张表。
4. 导入后用字段 `资源表` / `是否仪表盘主记录` 建视图或仪表盘筛选。
5. `seeed_解决方案增长_资源合集.xlsx` 是同一文件多 sheet 版本，适合当普通表格文档查看；如果导入 Base，飞书可能会拆成多张表。

本包数据来源：

- GA4 网站真实数据：`{source_path.name}`
- 注意：`{source_path.name}` 是本地计算用底表；最终导入表只输出 12 个 solution 及其二级/三级相关页面的聚合结果。
- 本包周口径：过去 4 个完整周；本次包含 `{week_ranges}`。
- 最近完整周：`{latest_complete_week}`。`2026-07-27` 所在周是未完整周，未纳入本包周报口径。
- Seeed 解决方案页面：中文入口 `https://www.seeedstudio.com.cn/category/solutions-zh-hans`，英文入口 `https://www.seeed.cc/category/solutions`，以及 Solutions tab 下 12 个 solution 页面。
- 12 个 solution 的中文 / 英文 / 日文 URL 使用同一 slug 聚合；GA4 底表里出现任一 host 的同一路径都会计入该 solution。
- 社媒平台原生数据：LinkedIn / X / FB / 小红书 / 抖音字段保留，当前等待平台后台或API补录。

包含表：

{names}

建议仪表盘基于单表导入后的筛选 `是否仪表盘主记录 = 是` 创建：

- 折线图：按 `周范围` 看 `落地页访问量`、`独立访客数`、`关键事件数_GA_conversions`
- 柱状图：按 `solution名称` 看 `落地页访问量`、`关键事件转化率`
- 表格：展示 `主要流量来源`、`参与率`、`跳出率`、`CTA状态`、`表单提交状态`

注意：

- 本版按用户要求只围绕「解决方案」及其二级/三级页面，不输出全站热门页面。
- `单方案流量明细表` 使用 GA4 页面维度真实字段：浏览量、独立访客、会话、平均互动秒、参与率、跳出率、关键事件等。
- `转化漏斗表` 仅使用 GA4 当前实际能查到的事件：`page_view / scroll / first_visit / session_start / user_engagement / keyEvents`。
- 未发现可验证的 `CTA点击`、`表单提交`、`一键加购`、`销售跟进` 事件，因此没有把这些字段作为真实转化数据填入。
- `社媒总曝光量` 目前没有接入平台后台数据，标记为未接入。
- 如果当前周数据未满 7 天，导出会自动使用最近完整周，避免周报被不完整数据拉低。
- `增长最快solution` 按每个周次对比上一周的 solution 页面访问量自动计算。
- 如果 12 个 solution 页面显示 `GA4未命中`，优先确认 seeed.cc / 中文站是否接入当前 GA4 Property `258704823`，或在下一次拉数中使用 `/solutions` 过滤专项拉取。
""", encoding="utf-8")


def main() -> None:
    payload = read_payload()
    source_path = source_csv_path(payload)
    rows = read_rows(source_path)
    if not rows:
        raise SystemExit(f"No rows found in {source_path}")

    main_table = build_dashboard_main_table(rows)
    sheets: List[Tuple[str, Sequence[Dict[str, str]]]] = [
        ("00_多维仪表盘主表", main_table),
        ("01_方案增长总览表", build_growth_overview(rows)),
        ("02_单方案流量明细表", build_single_solution_detail(rows)),
        ("03_转化漏斗表", build_solution_funnel(rows)),
        ("04_流量来源渠道", build_channels(rows)),
        ("05_解决方案页面与行为", build_solution_pages(rows)),
        ("06_社媒平台表现", build_social_platforms(rows)),
    ]

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, table_rows in sheets:
        write_csv_file(OUT_DIR / f"{name}.csv", table_rows)

    xlsx_path = OUT_DIR / "seeed_解决方案增长_资源合集.xlsx"
    single_base_xlsx_path = OUT_DIR / "seeed_解决方案增长_飞书单表导入.xlsx"
    single_base_csv_path = OUT_DIR / "seeed_解决方案增长_飞书单表导入.csv"
    main_xlsx_path = OUT_DIR / "seeed_解决方案增长_多维仪表盘主表.xlsx"
    zip_path = OUT_DIR / "seeed_解决方案增长_飞书多维表格导入包.zip"
    readme_path = OUT_DIR / "README_导入说明.md"
    single_base_table = build_single_base_import_table(sheets)
    write_xlsx(xlsx_path, sheets)
    write_xlsx(single_base_xlsx_path, [("解决方案增长数据池", single_base_table)])
    write_csv_file(single_base_csv_path, single_base_table)
    write_xlsx(main_xlsx_path, [("00_多维仪表盘主表", main_table)])
    write_readme(readme_path, source_path, sheets)

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(OUT_DIR.iterdir()):
            if path == zip_path:
                continue
            zf.write(path, arcname=path.name)

    print(json.dumps({
        "ok": True,
        "source": str(source_path),
        "out_dir": str(OUT_DIR),
        "xlsx": str(xlsx_path),
        "single_base_xlsx": str(single_base_xlsx_path),
        "single_base_csv": str(single_base_csv_path),
        "main_xlsx": str(main_xlsx_path),
        "zip": str(zip_path),
        "single_base_rows": len(single_base_table),
        "tables": {name: len(table_rows) for name, table_rows in sheets},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
