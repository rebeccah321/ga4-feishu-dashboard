#!/usr/bin/env python3
"""按 v3 口径生成对外表：01 方案区增长总览 / 02 单方案流量明细 / 04 参考设计Top3。

数据来源：
  - data/analysis_v3/         完整周表（generate_tables_v3.py 产物）
  - data/analysis_v3_partial/ 本周至今部分周表（partial_week_v3.py 产物）
  - data/leads/leads_weekly.csv  LeadApi 线索（merge_leads.py 产物）
  - GA4 补查：每页 CTA 事件数、每页主要流量来源（sessionDefaultChannelGroup）

输出：
  - data/analysis_v3/01_方案增长总览.csv
  - data/analysis_v3/02_单方案流量明细.csv
  - data/analysis_v3/04_参考设计Top3.csv
  - outputs/v3口径_*.csv（镜像副本）

字段口径（自解释、只保留必要字段）：
  01：周次 / 截止日期 / 方案区独立访客数 / 方案区浏览PV / CTA点击量 / CTA点击率(%) /
      浏览PV周环比(%) / 流量最高方案 / 增长最快方案 / 社媒总曝光量
      （方案区整树总量：含参考设计/场景页/旧目录页，不单独列出）
  02：最新周次 / 截止日期 / 方案名称 / 页面类型 / 方案页浏览PV / 独立访客数 / 会话数 /
      平均停留时长(秒) / 参与率 / 主要流量来源 / CTA点击量 / leads / 周环比(%) / 数据状态
      （按实际情况：方案页首次出现流量那一周才起行，之后每周一行；没上线/没出现的方案不出行。历史 12 方案 → 新增方案按上线周逐步增加）
      （页面类型 = 场景页/技术页/入口页，与站内 solutionMeta.docType 对齐；
        改名页（门店客服质检、离线语音控制）从改名生效周起换名，之前的周保留旧名）
      （截止日期/最新周次一律是"当周最后一天（周日）"：本周未跑完也写周日，未完整由"数据状态=本周（未完整）"表达）
  04：最新周次 / 截止日期 / 排名 / 页面名称（统一短中文名）/ 浏览PV / 数据状态
      （参考设计单页每周流量前三；只计入 01 总量，不进 02。
       名称按路径登记短中文名（REF_NAME_CN），统一语言、统一长度；未登记页面退化并告警；
       指标只留浏览PV；数据状态体现是否为完整周）
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
import generate_tables_v3 as v3  # noqa: E402

V3 = ROOT / "data" / "analysis_v3"
PART = ROOT / "data" / "analysis_v3_partial"
OUT_DATA = V3
OUT_MIRROR = ROOT / "outputs"

CTA_EVENTS = ["click_solution_link", "click_contact_expert"]
PREFIXES = ["/solutions/", "/solutions-by-scenarios",
            "/category/solutions", "/category/reference-designs", "/category/success-cases"]

# 一级方案页路径归一化：/solutions/<slug>（去掉 -zh-hans 等语言后缀）
PATH_RE = re.compile(r"^/solutions/([^/]+?)(?:-zh-hans)?/?$")
# 404/死页标题的兜底过滤（正常已在 generate_tables_v3 分类为“其他页面”）
NOT_FOUND_NAME = re.compile(r"not\s*found|encontrado|見つかりません|未找到|页面不存在", re.I)
# 参考设计子页（/reference-designs/<slug>/<sub>）
REF_SUBPAGE = re.compile(r"^/solutions/reference-designs/[^/]+/([^/]+)/?$")


def short_ref_name(name: str, path: str = "", limit: int = 42) -> str:
    """参考设计展示名：可识别即可，不追求全称（未登记页面的兜底逻辑）。"""
    text = re.split(r"\s*[|｜]\s*", str(name).strip())[0]
    # GA4 标题被截断后会留下 “Reference D / Referen” 这类残片，直接去掉
    text = re.sub(r"\s*ref(?:erence)?(?:\s+des(?:ign)?)?\w*.*$", "", text, flags=re.I)
    text = re.sub(r"[\s\u2014\u2013\-–—:：,，、;；.。]+$", "", text)
    if len(text) > limit:
        cut = text[:limit]
        pos = cut.rfind(" ")
        text = (cut[:pos] if pos > limit * 0.6 else cut).rstrip() + "…"
    text = text or str(name).strip()
    m = REF_SUBPAGE.match(str(path))
    if m:
        text += "（部署页）" if m.group(1).startswith("deploy") else "（子页）"
    return text


# 参考设计展示名：统一用短中文名（同一路径的英文标题在不同时期会变，所以按路径登记）
REF_NAME_CN = {
    "/solutions/reference-designs/conversational_voice_ai": "端侧对话式语音AI",
    "/solutions/reference-designs/edge-anpr-plate-recognition": "边缘车牌识别",
    "/solutions/reference-designs/edge_security": "边缘安防",
    "/solutions/reference-designs/fall_detection": "端侧跌倒检测",
    # 站内中文标题：Home Assistant 本地语音中枢与全屋智能联动可部署方案
    "/solutions/reference-designs/home-assistant-local-voice-hub": "HA本地语音中枢",
    "/solutions/reference-designs/indoor_positioning_ble_lorawan": "园区蓝牙信标定位",
    "/solutions/reference-designs/industrial_security_jetson": "Jetson工业安防",
    "/solutions/reference-designs/jetson_voice_assistant": "本地语音服务",
    "/solutions/reference-designs/reachy_claw_voice_robot": "桌面对话机器人",
    "/solutions/reference-designs/recamera_ecosystem": "即插即用AI相机",
    "/solutions/reference-designs/recamera_heatmap_grafana": "门店客流热力图",
    "/solutions/reference-designs/recamera_sheep_counter_lora": "LoRa羊只计数",
    "/solutions/reference-designs/respeaker_flex_soarm": "语音控制机械臂",
    "/solutions/reference-designs/retail_voice": "零售语音",
    "/solutions/reference-designs/smart_warehouse": "语音仓库管理",
    "/solutions/reference-designs/unmanned_store_access": "无人门店门禁",
    "/solutions/reference-designs/voice_robot_lekiwi": "LeKiwi语音机器人",
}


def ref_display_name(path: str, name: str) -> str:
    """已登记的参考设计用短中文名；子页带（部署页）标记；未登记的退化并提示补录。"""
    p = str(path).rstrip("/")
    if p in REF_NAME_CN:
        return REF_NAME_CN[p]
    m = REF_SUBPAGE.match(p)
    if m:
        base = REF_NAME_CN.get(p.rsplit("/", 1)[0])
        if base:
            return base + ("（部署页）" if m.group(1).startswith("deploy") else "（子页）")
    print(f"WARNING: 参考设计未登记中文名，暂用英文短名: {path}", flush=True)
    return short_ref_name(name, path)


def num(x):
    return float(x) if x not in ("", "None", None) else 0.0


def load(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def path_or_filter():
    return {"orGroup": {"expressions": [
        {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "BEGINS_WITH", "value": p}}}
        for p in PREFIXES]}}


def path_of(path):
    m = PATH_RE.match(path)
    return ("/solutions/" + m.group(1)) if m else None


def slug_of(path):
    m = PATH_RE.match(path)
    return m.group(1) if m else None


def run(body, token):
    return ga4_fetch.run_report(body, token)


def week_no(we):
    return dt.date.fromisoformat(we).isocalendar().week


def week_ending(d):
    return (d + dt.timedelta(days=(6 - d.weekday()) % 7)).isoformat()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--no-ga4", action="store_true",
                    help="不补查 GA4（CTA/来源留 0/Unassigned），用于离线重建")
    ap.add_argument("--no-partial", action="store_true",
                    help="不合并 data/analysis_v3_partial（CI 周更只输出完整周）")
    args = ap.parse_args()

    # ---- 读入 v3 数据（完整周 + 部分周） ----
    ov = load(V3 / "01_方案区总览.csv")
    det = load(V3 / "03_页面流量明细.csv")
    part_weeks: set[str] = set()
    if not args.no_partial:
        part_ov = load(PART / "01_方案区总览.csv")
        ov += part_ov
        det += load(PART / "03_页面流量明细.csv")
        part_weeks = {r["week_ending"] for r in part_ov}
    weeks_all = [r["week_ending"] for r in ov]
    weeks_sorted = sorted(set(weeks_all))
    partial_week = weeks_sorted[-1] if weeks_sorted else None
    # 部分周判定：最后一周来自 partial 目录（本周至今）即为部分周。
    # 注意 partial 表的 week_ending 写的是当周周日，所以不能用“是否周日”判断。
    is_partial = bool(weeks_sorted) and weeks_sorted[-1] in part_weeks

    print(f"rows: ov={len(ov)} det={len(det)} | weeks={weeks_sorted[0]}~{weeks_sorted[-1]} "
          f"partial={partial_week} ({is_partial})", flush=True)

    cta_by_pageweek = defaultdict(float)
    top_channel = {}

    if not args.no_ga4:
        token = ga4_fetch.get_token()  # 支持本地 ADC 与 CI service_account
        start = weeks_sorted[0]
        # 部分周的 week_ending 是当周周日（可能还没到），GA4 查询截止日取今天
        end = min(weeks_sorted[-1], dt.date.today().isoformat())
        print(f"GA4 CTA/channel range: {start} ~ {end}", flush=True)

        cta_body = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": "date"}, {"name": "pagePath"}],
            "metrics": [{"name": "eventCount"}],
            "dimensionFilter": {"andGroup": {"expressions": [path_or_filter(),
                {"filter": {"fieldName": "eventName", "inListFilter": {"values": CTA_EVENTS}}}]}},
            "limit": 250000,
        }
        for r in run(cta_body, token).get("rows", []):
            d, p = r["dimensionValues"]
            d = f"{d['value'][:4]}-{d['value'][4:6]}-{d['value'][6:8]}"
            we = week_ending(dt.date.fromisoformat(d))
            key = path_of(p["value"])
            if key:
                cta_by_pageweek[(we, key)] += float(r["metricValues"][0]["value"])

        ch_body = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "dimensions": [{"name": "date"}, {"name": "pagePath"}, {"name": "sessionDefaultChannelGroup"}],
            "metrics": [{"name": "sessions"}],
            "dimensionFilter": path_or_filter(),
            "limit": 250000,
        }
        ch_by_pageweek = defaultdict(lambda: defaultdict(float))
        for r in run(ch_body, token).get("rows", []):
            d, p, ch = r["dimensionValues"]
            d = f"{d['value'][:4]}-{d['value'][4:6]}-{d['value'][6:8]}"
            we = week_ending(dt.date.fromisoformat(d))
            key = path_of(p["value"])
            if key:
                ch_by_pageweek[(we, key)][ch["value"]] += float(r["metricValues"][0]["value"])
        for k, chs in ch_by_pageweek.items():
            top_channel[k] = max(chs.items(), key=lambda x: x[1])[0] if chs else "Unassigned"

    # ---------------- 01 方案区增长总览 ----------------
    pagepv = defaultdict(dict)
    for r in det:
        if r["流量类别"] == "方案页" and not NOT_FOUND_NAME.search(r["页面名称"]):
            pagepv[r["week_ending"]][r["页面名称"]] = num(r["浏览PV"])

    o1 = [["周次", "截止日期", "方案区独立访客数", "方案区浏览PV", "CTA点击量", "CTA点击率(%)",
           "浏览PV周环比(%)", "流量最高方案", "增长最快方案", "社媒总曝光量"]]
    prev = {}
    for r in sorted(ov, key=lambda x: x["week_ending"]):
        we = r["week_ending"]
        pv = num(r["solution_pv"])
        cta = num(r["cta_clicks"])
        pv_by_page = pagepv.get(we, {})
        top = max(pv_by_page.items(), key=lambda x: x[1])[0] if pv_by_page else ""
        growth = ""
        if pv_by_page and prev:
            cand = [(100 * (v - prev.get(n, 0)) / prev[n], n)
                    for n, v in pv_by_page.items() if prev.get(n, 0) > 0]
            if cand:
                g, n = max(cand)
                growth = f"{n}({g:+.1f}%)"
        o1.append(["W%d" % week_no(we), we, round(num(r["solution_users"])), round(pv),
                   round(cta), round(100 * cta / pv, 2) if pv else 0,
                   r.get("solution_wow_pct") or "", top, growth, "未接入"])
        prev = dict(pv_by_page)

    # ---------------- 02 单方案流量明细（按实际情况：首次出现流量那一周起行，之后每周一行） ----------------
    det_by_key = {}
    for r in det:
        if r["流量类别"] != "方案页" or NOT_FOUND_NAME.search(r["页面名称"]):
            continue
        slug = slug_of(r["页面路径"])
        if slug:
            det_by_key[(r["week_ending"], slug)] = r

    # 首次出现周（该方案第一次在方案页明细里出现的那一周）；新增方案从这一周起才进 02 表
    first_week = {}
    for (we, slug) in det_by_key:
        if slug not in first_week or we < first_week[slug]:
            first_week[slug] = we

    leads_by_key = {}
    leads_path = ROOT / "data" / "leads" / "leads_weekly.csv"
    if leads_path.exists():
        for r in load(leads_path):
            try:
                leads_by_key[(r["week_ending"], r["slug"])] = int(float(r.get("form_submits") or 0))
            except (KeyError, TypeError, ValueError):
                continue

    o2 = [["最新周次", "截止日期", "方案名称", "页面类型", "方案页浏览PV", "独立访客数", "会话数",
           "平均停留时长(秒)", "参与率", "主要流量来源", "CTA点击量", "leads", "周环比(%)", "数据状态"]]
    live = set()
    for we in weeks_sorted:
        status = "本周（未完整）" if (is_partial and we == weeks_sorted[-1]) else "GA4+LeadApi已接入"
        live |= {s for s in v3.CORE_SLUGS if first_week.get(s) == we}
        for slug in v3.CORE_SLUGS:
            if slug not in live:
                continue
            key = "/solutions/" + slug
            name = v3.solution_name(slug, we)
            doc_type = v3.DOC_TYPE.get(slug, "")
            lead = leads_by_key.get((we, slug), 0)
            r = det_by_key.get((we, slug))
            if r is None:
                o2.append(["W%d" % week_no(we), we, name, doc_type, 0, 0, 0, "", "",
                           "Unassigned", 0, lead, "", status])
            else:
                o2.append(["W%d" % week_no(we), we, name, doc_type,
                           round(num(r["浏览PV"])), round(num(r["访客数"])), round(num(r["会话数"])),
                           r["平均停留时长(秒)"], r["参与率"],
                           top_channel.get((we, key), "Unassigned"),
                           round(cta_by_pageweek.get((we, key), 0)),
                           lead,
                           r.get("浏览量周环比") or "", status])

    # ---------------- 04 参考设计 Top3（每周流量前三；只进 01 总量，不单独列为方案） ----------------
    rd_by_week = defaultdict(list)
    for r in det:
        if r["流量类别"] == "参考设计" and not NOT_FOUND_NAME.search(r["页面名称"]):
            rd_by_week[r["week_ending"]].append(r)

    o4 = [["最新周次", "截止日期", "排名", "页面名称", "浏览PV", "数据状态"]]
    for we in weeks_sorted:
        status = "本周（未完整）" if (is_partial and we == weeks_sorted[-1]) else "完整周"
        top3 = sorted(rd_by_week.get(we, []), key=lambda r: -num(r["浏览PV"]))[:3]
        for idx, r in enumerate(top3, 1):
            o4.append(["W%d" % week_no(we), we, idx,
                       ref_display_name(r["页面路径"], r["页面名称"]),
                       round(num(r["浏览PV"])), status])

    OUT_DATA.mkdir(parents=True, exist_ok=True)
    OUT_MIRROR.mkdir(parents=True, exist_ok=True)
    for name, rows in (("01_方案增长总览", o1), ("02_单方案流量明细", o2), ("04_参考设计Top3", o4)):
        with open(OUT_DATA / f"{name}.csv", "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows(rows)
    # 镜像副本（保留用户之前看到的两份文件）
    with open(OUT_MIRROR / "v3口径_01_方案区增长总览.csv", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(o1)
    with open(OUT_MIRROR / "v3口径_02_单方案流量明细.csv", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(o2)
    with open(OUT_MIRROR / "v3口径_04_参考设计Top3.csv", "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(o4)
    print(f"01 rows: {len(o1)-1} | 02 rows: {len(o2)-1} | 04 rows: {len(o4)-1}")
    print(f"saved {OUT_DATA}/01_方案增长总览.csv + 02_单方案流量明细.csv + 04_参考设计Top3.csv (+ mirrors)")


if __name__ == "__main__":
    main()
