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
PLATFORMS = ["LinkedIn", "X", "FB", "小红书", "抖音"]
SOLUTION_CATEGORY_PATHS = ["/category/solutions-zh-hans", "/category/solutions"]
def solution_item(name: str, slug: str, english: str, extras: Sequence[str] | None = None) -> Dict[str, Any]:
    bare = "/" + slug.removeprefix("/solutions/")
    aliases = [slug, bare]
    if extras:
        aliases.extend(extras)
    return {"name": name, "slug": slug, "aliases": aliases, "english": english}


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


def read_payload() -> Dict[str, Any]:
    return json.loads(DATA_JSON.read_text(encoding="utf-8"))


def source_csv_path(payload: Dict[str, Any]) -> Path:
    source_name = payload.get("source_csv")
    if source_name:
        candidate = ROOT / "data/normalized" / source_name
        if candidate.exists():
            return candidate
    return DATA_CSV


def read_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


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
            return "转化偏弱，需检查表单、咨询入口和购买链路"
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


def build_overview(rows: Sequence[Dict[str, str]], payload: Dict[str, Any]) -> List[Dict[str, str]]:
    start, end = date_bounds(rows)
    current_rows, previous_rows = split_period(rows)
    all_total = totals(rows)
    current_total = totals(current_rows)
    previous_total = totals(previous_rows)
    generated = payload.get("generated_at", dt.datetime.now().isoformat(timespec="seconds"))

    def row(dimension: str, metric: str, value: str, unit: str, trend_text: str, question: str, conclusion: str, action: str, source: str = "GA4") -> Dict[str, str]:
        return {
            "周期开始": start,
            "周期结束": end,
            "分析维度": dimension,
            "核心指标": metric,
            "指标值": value,
            "单位": unit,
            "环比/趋势": trend_text,
            "业务问题": question,
            "判断结论": conclusion,
            "建议动作": action,
            "数据来源": source,
            "更新时间": generated,
        }

    channel_top = aggregate(rows, ["Channel Group"])
    channel_top.sort(key=lambda item: item.get("Views", 0), reverse=True)
    top_channels = " / ".join(str(item.get("Channel Group") or "Unassigned") for item in channel_top[:5])

    page_top = aggregate(rows, ["Page Path", "Page Title"])
    page_top.sort(key=lambda item: item.get("Views", 0), reverse=True)
    top_pages = " / ".join(str(item.get("Page Path") or "/") for item in page_top[:5])

    return [
        row("流量概览", "用户数", fmt_number(all_total["users"]), "人", trend(current_total["users"], previous_total["users"]), "网站整体流量是涨是跌？新客多还是老客多？", "当前为活跃用户数；新老客占比待接入 newUsers 后判断", "下次 GA4 拉数增加 newUsers 指标，并按渠道拆分新客占比"),
        row("流量概览", "会话数", fmt_number(all_total["sessions"]), "次", trend(current_total["sessions"], previous_total["sessions"]), "网站整体流量是涨是跌？新客多还是老客多？", "用于判断整体访问规模变化", "周报中固定追踪会话数变化和异常日期"),
        row("流量概览", "新用户占比", "待接入", "%", "待接入 newUsers", "网站整体流量是涨是跌？新客多还是老客多？", "当前 GA4 导出未包含新用户指标", "在下一版 GA4 API 指标中加入 newUsers"),
        row("流量质量", "平均会话时长", fmt_number(all_total["avg_engagement_seconds"], 2), "秒(以平均互动秒近似)", trend(current_total["avg_engagement_seconds"], previous_total["avg_engagement_seconds"]), "用户对我的网站内容感兴趣吗？落地页质量如何？", judgement("engagement", all_total["avg_engagement_seconds"]), "优先检查低互动高流量页面的首屏信息和CTA"),
        row("流量质量", "跳出率", fmt_pct(all_total["bounce_rate"]), "%", trend(current_total["bounce_rate"], previous_total["bounce_rate"]), "用户对我的网站内容感兴趣吗？落地页质量如何？", judgement("bounce", all_total["bounce_rate"]), "按渠道和热门页面筛出跳出偏高的落地页"),
        row("流量来源 Views by Channel", "Top 5 渠道", top_channels, "渠道", "按浏览量排序", "用户都是从哪些渠道来的？哪个渠道效果最好？", "当前 Top 5 渠道已在渠道表展开", "把资源优先投到高流量且高转化渠道"),
        row("用户行为", "热门页面", top_pages, "页面", "按浏览量排序", "用户最喜欢看哪些内容？核心按钮点击多吗？", "当前能看到热门页面；具体按钮点击需接入事件名称", "下一版加入 click/contact/form_submit 等关键事件明细"),
        row("用户行为", "关键事件触发次数", fmt_number(all_total["conversions"]), "次", trend(current_total["conversions"], previous_total["conversions"]), "用户最喜欢看哪些内容？核心按钮点击多吗？", "当前以 GA4 Conversions 汇总近似关键事件", "把咨询、提交表单、加购设为单独 Key Event"),
        row("转化效果", "转化率", fmt_pct(all_total["conversion_rate"]), "%", trend(current_total["conversion_rate"], previous_total["conversion_rate"]), "多少用户完成了我们期望的动作（如提交表单）？", judgement("conversion", all_total["conversion_rate"]), "按渠道和页面定位高转化入口并复用内容主题"),
        row("转化效果", "加购率", "待接入", "%", "待接入 add_to_cart 事件", "多少用户完成了我们期望的动作（如提交表单）？", "当前 GA4 导出未包含加购事件明细", "在 GA4 事件维度中加入 add_to_cart 并单独成表"),
    ]


def dominant_channel(rows: Sequence[Dict[str, str]]) -> str:
    channels = aggregate(rows, ["Channel Group"])
    if not channels:
        return "未命中"
    channels.sort(key=lambda item: item.get("Sessions", 0), reverse=True)
    return str(channels[0].get("Channel Group") or "Unassigned")


def solution_growth_status(total: Dict[str, float], matched_rows: Sequence[Dict[str, str]]) -> str:
    if not matched_rows:
        return "GA4未命中：需确认 seeed.cc / 中文站是否接入当前 GA4 Property，或下一次用 /solutions 过滤专项拉数"
    if total.get("sessions", 0) < 50:
        return "样本偏少：先确认埋点和入口曝光，再判断增长"
    if total.get("conversion_rate", 0) <= 0.01:
        return "有流量但转化偏弱：优先检查CTA、表单入口和案例承接"
    return "有可观察流量：建议按渠道和页面继续下钻"


def build_solution_growth(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    start, end = date_bounds(rows)
    week = latest_week(rows)
    solution_paths = [path for item in SOLUTIONS for path in item["aliases"]]
    solution_rows = rows_for_paths(rows, solution_paths)
    category_rows = rows_for_paths(rows, SOLUTION_CATEGORY_PATHS)
    all_related_rows = rows_for_paths(rows, [*SOLUTION_CATEGORY_PATHS, *solution_paths])
    total = totals(solution_rows)
    category_total = totals(category_rows)
    all_total = totals(all_related_rows)
    matched_solution_count = sum(1 for item in SOLUTIONS if rows_for_paths(rows, item["aliases"]))
    return [
        {
            "最新周次": week,
            "周期开始": start,
            "周期结束": end,
            "看板模块": "落地页官网增长看板",
            "指标": "解决方案入口页访问量",
            "指标值": fmt_number(category_total.get("views", 0.0)),
            "单位": "PV",
            "口径": "中文 /category/solutions-zh-hans + 英文 /category/solutions",
            "业务问题": "解决方案入口是否有足够访问量？",
            "判断结论": solution_growth_status(category_total, category_rows),
            "下一步动作": "若为0，先确认 GA4 property 是否覆盖 seeed.cc 和 seeedstudio.com.cn",
        },
        {
            "最新周次": week,
            "周期开始": start,
            "周期结束": end,
            "看板模块": "落地页官网增长看板",
            "指标": "12个方案页合计访问量",
            "指标值": fmt_number(total.get("views", 0.0)),
            "单位": "PV",
            "口径": "Solutions tab 下 12 个 solution slug",
            "业务问题": "用户是否继续进入具体方案页？",
            "判断结论": solution_growth_status(total, solution_rows),
            "下一步动作": "为入口页到方案页点击建立事件，追踪 tab 点击与卡片点击",
        },
        {
            "最新周次": week,
            "周期开始": start,
            "周期结束": end,
            "看板模块": "落地页官网增长看板",
            "指标": "有GA4命中的方案数",
            "指标值": f"{matched_solution_count}/12",
            "单位": "个",
            "口径": "12个指定 solution 页面",
            "业务问题": "哪些方案已经被用户访问？",
            "判断结论": "用于判断是否是流量问题、埋点问题或内容露出问题",
            "下一步动作": "对0访问方案检查页面URL、站点埋点、入口链接和UTM",
        },
        {
            "最新周次": week,
            "周期开始": start,
            "周期结束": end,
            "看板模块": "落地页官网增长看板",
            "指标": "平均停留时长",
            "指标值": fmt_number(all_total.get("avg_engagement_seconds", 0.0), 2),
            "单位": "秒(以平均互动秒近似)",
            "口径": "入口页 + 12个方案页",
            "业务问题": "用户是否认真阅读方案内容？",
            "判断结论": judgement("engagement", all_total.get("avg_engagement_seconds", 0.0)) if all_related_rows else "GA4未命中",
            "下一步动作": "对低停留页面优化首屏价值、结构化案例和CTA",
        },
        {
            "最新周次": week,
            "周期开始": start,
            "周期结束": end,
            "看板模块": "落地页官网增长看板",
            "指标": "表单提交/关键事件",
            "指标值": fmt_number(all_total.get("conversions", 0.0)),
            "单位": "次",
            "口径": "当前用 GA4 keyEvents/Conversions 汇总，尚未拆具体表单事件",
            "业务问题": "解决方案页是否带来销售线索？",
            "判断结论": judgement("conversion", all_total.get("conversion_rate", 0.0)) if all_related_rows else "GA4未命中",
            "下一步动作": "把 Request a Solution / 获取专属方案 / contacts 表单设为单独事件",
        },
    ]


def build_solution_detail(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    _, end = date_bounds(rows)
    week = latest_week(rows)
    output = []
    for item in SOLUTIONS:
        matched = rows_for_paths(rows, item["aliases"])
        total = totals(matched)
        output.append({
            "最新周次": week,
            "solution名称": item["name"],
            "英文名称": item["english"],
            "页面路径": item["slug"],
            "中文URL": f"https://www.seeedstudio.com.cn{item['slug']}",
            "英文URL": f"https://www.seeed.cc{item['slug']}",
            "落地页访问量": fmt_number(total.get("views", 0.0)),
            "独立访客数": fmt_number(total.get("users", 0.0)),
            "平均停留时长": fmt_number(total.get("avg_engagement_seconds", 0.0), 2),
            "参与率": fmt_pct(total.get("engagement_rate", 0.0)),
            "CTA点击量": "待接入 CTA 点击事件",
            "表单提交": fmt_number(total.get("conversions", 0.0)),
            "主要流量来源": dominant_channel(matched),
            "数据状态": "GA4已命中" if matched else "GA4未命中",
            "分析结论": solution_growth_status(total, matched),
            "建议动作": "若未命中，确认页面埋点和 GA4 property；若已命中，按渠道/CTA/表单继续拆解",
            "更新时间": end,
        })
    return output


def build_channels(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    start, end = date_bounds(rows)
    items = aggregate(rows, ["Channel Group"])
    items.sort(key=lambda item: item.get("Views", 0), reverse=True)
    output = []
    for index, item in enumerate(items[:5], start=1):
        conversion_rate = item.get("Conversion Rate", 0.0)
        output.append({
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
            "建议动作": "高转化渠道继续放大；高流量低转化渠道优先优化落地页和CTA",
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


def build_pages(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    start, end = date_bounds(rows)
    items = aggregate(rows, ["Page Path", "Page Title"])
    items.sort(key=lambda item: item.get("Views", 0), reverse=True)
    output = []
    for item in items[:30]:
        conversion_rate = item.get("Conversion Rate", 0.0)
        path = item.get("Page Path") or "/"
        title = item.get("Page Title") or ""
        output.append({
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
            "页面类型/内容主题": infer_topic(path, title),
            "业务判断": judgement("conversion", conversion_rate),
            "建议动作": "保留高浏览页面；对高跳出或低转化页面优化首屏价值、CTA和内部链接",
        })
    return output


def build_social_platforms(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    start, end = date_bounds(rows)
    social_rows = [row for row in rows if row.get("Channel Group") == "Organic Social"]
    social_total = totals(social_rows)
    output = [{
        "周期开始": start,
        "周期结束": end,
        "平台": "GA4 Organic Social 合计",
        "粉丝数": "",
        "发帖数": "",
        "曝光/播放": "",
        "互动数": "",
        "互动率": "",
        "点击数": "",
        "网站会话数": fmt_number(social_total.get("sessions", 0.0)),
        "网站用户数": fmt_number(social_total.get("users", 0.0)),
        "转化数": fmt_number(social_total.get("conversions", 0.0)),
        "转化率": fmt_pct(social_total.get("conversion_rate", 0.0)),
        "最佳内容类型": "待接入平台后台数据后判断",
        "适合继续投入": "待判断",
        "建议UTM渠道参数": "utm_medium=social",
        "数据状态": "GA4仅有社媒合计，未拆平台",
        "备注": "平台拆分需后续内容链接统一添加 UTM",
    }]
    utm_map = {
        "LinkedIn": "utm_source=linkedin&utm_medium=social",
        "X": "utm_source=x&utm_medium=social",
        "FB": "utm_source=facebook&utm_medium=social",
        "小红书": "utm_source=xiaohongshu&utm_medium=social",
        "抖音": "utm_source=douyin&utm_medium=social",
    }
    for platform in PLATFORMS:
        output.append({
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


def build_social_content(rows: Sequence[Dict[str, str]]) -> List[Dict[str, str]]:
    start, end = date_bounds(rows)
    output = []
    for platform in PLATFORMS:
        output.append({
            "统计周期开始": start,
            "统计周期结束": end,
            "发布日期": "",
            "平台": platform,
            "内容标题": "",
            "内容类型": "",
            "主题": "",
            "链接/素材": "",
            "曝光/播放": "",
            "点赞": "",
            "评论": "",
            "分享/收藏": "",
            "点击数": "",
            "网站会话数": "",
            "转化数": "",
            "互动率": "",
            "点击率": "",
            "判断结论": "",
            "下次动作": "",
        })
    return output


def build_field_notes() -> List[Dict[str, str]]:
    return [
        {"表名": "01_落地页官网增长看板", "字段": "指标值", "说明": "围绕 Seeed 解决方案入口页和12个方案页输出增长判断。", "维护方式": "每周脚本更新"},
        {"表名": "02_单方案流量明细", "字段": "solution名称", "说明": "固定包含用户指定的12个解决方案；中文站匹配不到时按同 slug 的英文站路径观察。", "维护方式": "每周脚本更新"},
        {"表名": "02_单方案流量明细", "字段": "CTA点击量", "说明": "当前GA4导出没有 eventName 维度，需下一版接入 CTA 点击事件后自动填入。", "维护方式": "事件接入后每周更新"},
        {"表名": "02_单方案流量明细", "字段": "表单提交", "说明": "当前用 GA4 keyEvents/Conversions 汇总近似，后续可拆成具体 contacts/form_submit。", "维护方式": "每周脚本更新"},
        {"表名": "03_流量来源渠道", "字段": "渠道", "说明": "按 GA4 Channel Group 浏览量排序，仅保留前5个渠道。", "维护方式": "每周脚本更新"},
        {"表名": "04_热门页面与行为", "字段": "关键事件触发次数", "说明": "当前用 GA4 Conversions 汇总近似，后续可拆成咨询、表单、加购等事件。", "维护方式": "每周脚本更新"},
        {"表名": "06_社媒平台表现", "字段": "平台", "说明": "LinkedIn/X/FB/小红书/抖音字段保留，用于后续平台后台手动或API导入。", "维护方式": "平台数据手动补录或后续API接入"},
        {"表名": "07_社媒内容表现", "字段": "内容类型", "说明": "建议统一选项：产品发布、案例、教程、活动、观点、短视频、用户故事。", "维护方式": "每次发布内容后补录"},
        {"表名": "07_社媒内容表现", "字段": "下次动作", "说明": "建议统一选项：复投、改标题、换平台、做二创、暂停、转销售跟进。", "维护方式": "周复盘时填写"},
        {"表名": "导入建议", "字段": "视图", "说明": "导入飞书多维表格后，为渠道表建柱状图；为页面表建Top列表；为社媒平台表建平台对比图。", "维护方式": "飞书内配置一次即可"},
    ]


def write_csv_file(path: Path, rows: Sequence[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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
    path.write_text(f"""# 飞书多维表格导入包

最快导入方式：

1. 打开飞书多维表格，新建一个空 Base。
2. 选择「导入 Excel/CSV」。
3. 优先上传 `seeed_解决方案增长_多维表格导入.xlsx`，它已经包含多张工作表。
4. 如果飞书没有自动识别多 Sheet，就逐个导入 `01_落地页官网增长看板.csv` 到 `08_字段说明.csv`。

本包数据来源：

- GA4 网站真实数据：`{source_path.name}`
- Seeed 解决方案页面：中文入口 `https://www.seeedstudio.com.cn/category/solutions-zh-hans`，英文入口 `https://www.seeed.cc/category/solutions`，以及 Solutions tab 下 12 个 solution 页面。
- 社媒平台原生数据：LinkedIn / X / FB / 小红书 / 抖音字段保留，当前等待平台后台或API补录。

包含表：

{names}

注意：

- `新用户占比`、`加购率`、具体 CTA 点击事件目前标记为「待接入」，因为当前 GA4 导出没有 newUsers、add_to_cart、eventName 维度。
- `平均会话时长`当前用 GA4 的 `Avg Engagement Seconds` 近似。
- 如果 12 个 solution 页面显示 `GA4未命中`，优先确认 seeed.cc / 中文站是否接入当前 GA4 Property `258704823`，或在下一次拉数中使用 `/solutions` 过滤专项拉取。
- 后续发布社媒内容时，请统一使用表内建议的 UTM 参数，这样下一版可以把 LinkedIn/X/FB/小红书/抖音的网站效果拆开。
""", encoding="utf-8")


def main() -> None:
    payload = read_payload()
    source_path = source_csv_path(payload)
    rows = read_rows(source_path)
    if not rows:
        raise SystemExit(f"No rows found in {source_path}")

    sheets: List[Tuple[str, Sequence[Dict[str, str]]]] = [
        ("01_落地页官网增长看板", build_solution_growth(rows)),
        ("02_单方案流量明细", build_solution_detail(rows)),
        ("03_流量来源渠道", build_channels(rows)),
        ("04_热门页面与行为", build_pages(rows)),
        ("05_看板指标总览", build_overview(rows, payload)),
        ("06_社媒平台表现", build_social_platforms(rows)),
        ("07_社媒内容表现", build_social_content(rows)),
        ("08_字段说明", build_field_notes()),
    ]

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for name, table_rows in sheets:
        write_csv_file(OUT_DIR / f"{name}.csv", table_rows)

    xlsx_path = OUT_DIR / "seeed_解决方案增长_多维表格导入.xlsx"
    zip_path = OUT_DIR / "seeed_解决方案增长_飞书多维表格导入包.zip"
    readme_path = OUT_DIR / "README_导入说明.md"
    write_xlsx(xlsx_path, sheets)
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
        "zip": str(zip_path),
        "tables": {name: len(table_rows) for name, table_rows in sheets},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
