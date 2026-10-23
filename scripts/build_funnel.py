#!/usr/bin/env python3
"""build_funnel.py — 生成飞书 03_转化漏斗 的数据源 data/analysis/转换漏斗.csv。

口径：与 01/02/05 同源，全部取自 data/analysis_v3/02_单方案流量明细.csv（v3 口径，含“本周至今”行），
所以同一方案同一周的 PV / 会话数 / CTA / leads 与 02 表完全一致：
  page_pv        ← 方案页浏览PV
  sessions       ← 会话数
  cta_clicks     ← CTA点击量
  form_submits   ← leads
  pv_to_cta_rate = 100 × CTA / PV（%）
  add_to_cart / cta_to_form_rate = 未接入（站点未接入这两个事件）
  data_status    ← 02 表的数据状态（完整周=GA4+LeadApi已接入；本周=本周（未完整））

行集合与 02 一致：方案页“首次出现流量那一周”起行，之后每周一行（新增方案从上线周逐步进入，
不再固定 15 个方案）。排序：最新周在最前，周内按 PV 从高到低。

幂等：每次按 02 表整表重建（不再需要单独补“本周至今”行）。

用法：
  python3 scripts/build_funnel.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_weekly_feishu as sw  # noqa: E402  （方案名称 → slug：含改名与旧名别名）

DETAIL_PATH = ROOT / "data" / "analysis_v3" / "02_单方案流量明细.csv"
OUT_PATH = ROOT / "data" / "analysis" / "转换漏斗.csv"
MIRROR_PATH = ROOT / "outputs" / "v3口径_03_转化漏斗.csv"

FIELDS = ["week_ending", "slug", "page_pv", "sessions", "cta_clicks", "form_submits",
          "add_to_cart", "pv_to_cta_rate", "cta_to_form_rate", "data_status"]
NA = "未接入"


def as_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def main():
    with open(DETAIL_PATH, newline="", encoding="utf-8-sig") as fh:
        detail = list(csv.DictReader(fh))

    rows = []
    unknown = []
    for r in detail:
        name = str(r.get("方案名称", "")).strip()
        slug = sw.NAME_TO_SLUG.get(name)
        if not slug:
            unknown.append(name)
            continue
        pv = int(round(as_float(r.get("方案页浏览PV"))))
        cta = int(round(as_float(r.get("CTA点击量"))))
        rows.append({
            "week_ending": str(r.get("截止日期", "")).strip(),
            "slug": slug,
            "page_pv": pv,
            "sessions": int(round(as_float(r.get("会话数")))),
            "cta_clicks": cta,
            "form_submits": int(round(as_float(r.get("leads")))),
            "add_to_cart": NA,
            "pv_to_cta_rate": round(100 * cta / pv, 2) if pv else 0,
            "cta_to_form_rate": NA,
            "data_status": str(r.get("数据状态", "")).strip() or "GA4+LeadApi已接入",
        })
    # 最新周在最前，周内按 PV 从高到低
    rows.sort(key=lambda x: (x["week_ending"], x["page_pv"]), reverse=True)

    for path in (OUT_PATH, MIRROR_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    weeks = sorted({r["week_ending"] for r in rows})
    slugs = sorted({r["slug"] for r in rows})
    partial = [w for w in weeks if any(r["data_status"].startswith("本周") for r in rows
                                       if r["week_ending"] == w)]
    print(f"{OUT_PATH.name}: {len(rows)} 行 | {len(weeks)} 周（{weeks[0]} ~ {weeks[-1]}）| {len(slugs)} 个方案")
    if partial:
        print(f"本周（未完整）周：{', '.join(partial)}")
    if unknown:
        print("警告：以下方案名称无法映射 slug，已跳过：" + "、".join(sorted(set(unknown))))
    for we in weeks[-2:]:
        block = [r for r in rows if r["week_ending"] == we]
        print(f"  {we}: {len(block)} 行 PV={sum(r['page_pv'] for r in block)} "
              f"CTA={sum(r['cta_clicks'] for r in block)} leads={sum(r['form_submits'] for r in block)} | "
              + ", ".join(f"{r['slug']}={r['page_pv']}" for r in block[:3]) + " ...")
    print(f"已写入 {OUT_PATH}（镜像 {MIRROR_PATH.name}）")


if __name__ == "__main__":
    main()
