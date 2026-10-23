#!/usr/bin/env python3
"""append_partial_funnel.py — 给 03_转化漏斗（data/analysis/转换漏斗.csv）补“本周至今”行。

为什么需要：03 的历史行由 generate_tables_correct.py 只在“完整周”产出，所以每逢本周
（未完整周）03 会落后一周。本脚本用与 01/02/05 相同的数据源补上本周行，保证数字对得上。

口径：
  - 页面 PV / 会话数 / CTA 点击量：取自 data/analysis_v3/02_单方案流量明细.csv 里
    “本周（未完整）”那一周（该表由 build_v3_tables.py 用同一批 GA4 数据生成，
    和 01/02 表完全一致，不再单独查 GA4）；
  - 表单提交（Leads）：data/leads/leads_weekly.csv（LeadApi；本周尚未覆盖时记 0）；
  - add_to_cart / cta_to_form_rate：未接入（与历史行一致）；
  - 只为本表历史已跟踪的方案补行，不新增方案、不改动历史周；
  - 截止日期一律写当周周日，是否完整由 data_status=本周（未完整）表达。

幂等：写入前先删掉同一 week_ending 的旧行，可重复执行。

用法：
  python3 scripts/append_partial_funnel.py
  python3 scripts/append_partial_funnel.py --week 2026-10-25   # 指定截止周（周日）
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_weekly_feishu as sw  # noqa: E402  （复用 方案名称 → slug 映射，含改名与旧名别名）

ANALYSIS_V3 = ROOT / "data" / "analysis_v3"
FUNNEL_PATH = ROOT / "data" / "analysis" / "转换漏斗.csv"
DETAIL_PATH = ANALYSIS_V3 / "02_单方案流量明细.csv"
LEADS_PATH = ROOT / "data" / "leads" / "leads_weekly.csv"

FIELDS = ["week_ending", "slug", "page_pv", "sessions", "cta_clicks", "form_submits",
          "add_to_cart", "pv_to_cta_rate", "cta_to_form_rate", "data_status"]
PARTIAL_STATUS = "本周（未完整）"
NA = "未接入"


def read(path: Path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week", default=None,
                    help="截止周（周日，YYYY-MM-DD）；默认取 02 表里“本周（未完整）”那一周")
    args = ap.parse_args()

    funnel_rows = read(FUNNEL_PATH)
    tracked = {r.get("slug", "").strip() for r in funnel_rows}  # 03 历史跟踪的方案
    tracked.discard("")

    detail_rows = read(DETAIL_PATH)
    week = args.week or ""
    if not week:
        days = [r.get("截止日期", "").strip() for r in detail_rows
                if PARTIAL_STATUS in str(r.get("数据状态", ""))]
        if not days:
            raise SystemExit("02 表里没有“本周（未完整）”行，无需给 03 补本周数据")
        week = max(days)

    dup = [r for r in funnel_rows if r.get("week_ending", "").strip() == week]
    if dup:
        print(f"注意：{FUNNEL_PATH.name} 已存在 {week} 的 {len(dup)} 行，按幂等规则先删后写")
    funnel_rows = [r for r in funnel_rows if r.get("week_ending", "").strip() != week]

    leads: dict[str, int] = defaultdict(int)
    if LEADS_PATH.exists():
        for r in read(LEADS_PATH):
            if r.get("week_ending", "").strip() == week:
                leads[r.get("slug", "").strip()] = int(as_float(r.get("form_submits")))

    new_rows = []
    skipped = []
    for r in detail_rows:
        if r.get("截止日期", "").strip() != week:
            continue
        name = str(r.get("方案名称", "")).strip()
        slug = sw.NAME_TO_SLUG.get(name)
        if not slug:
            skipped.append(f"{name}(未识别)")
            continue
        if slug not in tracked:
            skipped.append(f"{name}(03 未跟踪)")
            continue
        pv = int(round(as_float(r.get("方案页浏览PV"))))
        sessions = int(round(as_float(r.get("会话数"))))
        cta = int(round(as_float(r.get("CTA点击量"))))
        new_rows.append({
            "week_ending": week,
            "slug": slug,
            "page_pv": pv,
            "sessions": sessions,
            "cta_clicks": cta,
            "form_submits": leads.get(slug, 0),
            "add_to_cart": NA,
            "pv_to_cta_rate": round(100 * cta / pv, 2) if pv else 0,
            "cta_to_form_rate": NA,
            "data_status": PARTIAL_STATUS,
        })
    new_rows.sort(key=lambda x: -x["page_pv"])

    all_rows = new_rows + funnel_rows  # 最新周在最前，其余年份/周顺序不变
    with open(FUNNEL_PATH, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"{FUNNEL_PATH.name}: {len(funnel_rows)} → {len(all_rows)} 行（{week} 新增 {len(new_rows)} 行）")
    if skipped:
        print("未入表：" + "、".join(skipped))
    print(f"截止周 {week}（当周周日）：PV={sum(r['page_pv'] for r in new_rows)} "
          f"会话={sum(r['sessions'] for r in new_rows)} "
          f"CTA={sum(r['cta_clicks'] for r in new_rows)} "
          f"leads={sum(r['form_submits'] for r in new_rows)}")
    for r in new_rows:
        print(f"  {r['slug']:34s} PV={r['page_pv']:4d} 会话={r['sessions']:4d} "
              f"CTA={r['cta_clicks']:3d} CTA率={r['pv_to_cta_rate']}%")


if __name__ == "__main__":
    main()
