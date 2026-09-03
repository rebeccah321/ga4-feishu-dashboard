#!/usr/bin/env python3
"""
sync_weekly_feishu.py — 把周度三表同步到飞书已有的多维表格。

只更新已有表，不创建、不重命名表：
  - 01_方案增长总览
  - 02_单方案流量明细
  - 03_转化漏斗

字段名与 exports/feishu-import/*.csv、飞书现有表保持一致。
upsert key：
  - 01：周次
  - 02：最新周次 + solution名称
  - 03：最新周次 + 方案名称

环境变量：
  FEISHU_APP_ID
  FEISHU_APP_SECRET
  FEISHU_BITABLE_APP_TOKEN
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

FEISHU_BASE = "https://open.feishu.cn/open-apis"
ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_DIR = ROOT / "data" / "analysis"

TABLES = {
    "01": "01_方案增长总览",
    "02": "02_单方案流量明细",
    "03": "03_转化漏斗",
}

# 与飞书现有表方案名称一致的中文名
SLUG_CN = {
    "smart-warehouse-management": "智能仓储管理",
    "voicecollectionanalysis": "语音采集分析",
    "conversational-voice-ai": "对话式语音AI",
    "smart-agriculture-sensing": "智慧农业感知",
    "smart-livestock-farming": "智慧畜牧",
    "intelligent-video-analytics": "智能视频分析",
    "indoor-outdoor-positioning": "室内外定位",
    "environment-monitoring": "环境监测",
    "building-energy-management": "楼宇能源管理",
    "campus-safety-management": "校园安全管理",
    "building-energy-retrofit": "楼宇节能改造",
    "hazard-response": "应急响应",
}


def week_label(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        day = dt.date.fromisoformat(text)
        return f"W{day.isocalendar()[1]}"
    except ValueError:
        return text


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var: {name}")
    return value


def request_json(method, url, token=None, body=None, retries=3):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    last_error = ""
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            raw = urllib.request.urlopen(req, timeout=45).read().decode("utf-8")
            payload = json.loads(raw)
            if payload.get("code") != 0:
                raise RuntimeError(f"Feishu code={payload.get('code')}: {payload.get('msg')}")
            return payload
        except urllib.error.HTTPError as err:
            last_error = err.read().decode("utf-8", errors="replace")
        except Exception as err:
            last_error = str(err)
        if attempt < retries:
            time.sleep(1.5 * attempt)
    raise SystemExit(f"Feishu request failed: {last_error}")


def tenant_access_token(app_id, app_secret):
    payload = request_json("POST", f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
                           body={"app_id": app_id, "app_secret": app_secret})
    token = payload.get("tenant_access_token")
    if not token:
        raise SystemExit("No tenant_access_token returned")
    return token


def list_tables(token, app_token):
    url = f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables?page_size=100"
    payload = request_json("GET", url, token=token)
    return {item.get("name"): item.get("table_id") for item in payload.get("data", {}).get("items", [])}


def list_records(token, app_token, table_id):
    records = []
    page_token = ""
    while True:
        params = {"page_size": "500"}
        if page_token:
            params["page_token"] = page_token
        url = (f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records?"
               + urllib.parse.urlencode(params))
        payload = request_json("GET", url, token=token)
        data = payload.get("data", {})
        records.extend(data.get("items", []))
        if not data.get("has_more"):
            return records
        page_token = data.get("page_token", "")


def upsert_record(token, app_token, table_id, record_id, fields):
    fields = {key: value for key, value in fields.items() if value is not None}
    if record_id:
        url = f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
        request_json("PUT", url, token=token, body={"fields": fields})
        return "updated"
    url = f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    request_json("POST", url, token=token, body={"fields": fields})
    return "created"


def to_number(value, default=None):
    text = str(value or "").strip()
    if text in {"", "未接入", "N/A", "NA", "-"}:
        return default
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return default


def to_text(value, fallback="未接入"):
    text = str(value or "").strip()
    return fallback if text in {"", "NA", "N/A", "-"} else text


def display_slug(value):
    text = to_text(value, "")
    return SLUG_CN.get(text, text)


def localize_overview_text(value):
    text = to_text(value, "")
    for slug, name in SLUG_CN.items():
        text = text.replace(slug, name)
    return text


def read_csv(name):
    path = ANALYSIS_DIR / name
    if not path.exists():
        raise SystemExit(f"Missing analysis CSV: {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def build_overview(rows):
    output = []
    for row in rows:
        week = row.get("week_ending", "").strip()
        output.append({
            "周次": week_label(week),
            "截止日期": week,
            "方案页独立访客": to_number(row.get("solution_users"), 0),
            "Solution页面总访问量": to_number(row.get("solution_pv"), 0),
            "关键事件(CTA)": to_number(row.get("key_events"), 0),
            "CTA转化率": to_number(row.get("cta_click_rate")),
            "Solution 页面周环比": to_number(row.get("solution_wow_pct")),
            "流量最高方案": localize_overview_text(row.get("top_traffic_solution")),
            "增长最快solution": localize_overview_text(row.get("fastest_growing_solution")),
        })
    return output


def build_detail(rows):
    output = []
    for row in rows:
        week = row.get("week_ending", "").strip()
        output.append({
            "最新周次": week_label(week),
            "截止日期": week,
            "solution名称": display_slug(row.get("slug")),
            "落地页访问量": to_number(row.get("landing_pv"), 0),
            "独立访客数": to_number(row.get("users"), 0),
            "session": to_number(row.get("sessions"), 0),
            "平均停留时长(秒)": to_number(row.get("avg_eng_s")),
            "参与率": to_number(row.get("engagement_rate")),
            "主要流量来源": to_text(row.get("top_channel"), ""),
            "CTA点击量（key event）": to_number(row.get("key_events"), 0),
            "表单提交": "未接入",
            "数据状态": to_text(row.get("data_status")),
        })
    return output


def build_funnel(rows):
    output = []
    for row in rows:
        week = row.get("week_ending", "").strip()
        output.append({
            "最新周次": week_label(week),
            "截止日期": week,
            "方案名称": display_slug(row.get("slug")),
            "页面访问量（PV）": to_number(row.get("page_pv"), 0),
            "参与会话数": to_number(row.get("engaged_sessions"), 0),
            "CTA点击量": to_number(row.get("cta_clicks"), 0),
            "表单提交": "未接入",
            "页面->CTA转化率": to_number(row.get("pv_to_cta_rate")),
            "CTA->表单转化率": to_text(row.get("cta_to_form_rate")),
            "数据状态": to_text(row.get("data_status")),
        })
    return output


def build_data():
    return {
        "01": build_overview(read_csv("方案增长总览.csv")),
        "02": build_detail(read_csv("单方案流量明细.csv")),
        "03": build_funnel(read_csv("转换漏斗.csv")),
    }


def key_for(table_key, row):
    if table_key == "01":
        return str(row.get("周次", "")).strip()
    if table_key == "02":
        return f"{str(row.get('最新周次', '')).strip()}|{str(row.get('solution名称', '')).strip()}"
    if table_key == "03":
        return f"{str(row.get('最新周次', '')).strip()}|{str(row.get('方案名称', '')).strip()}"
    return ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    table_data = build_data()
    print(f"Prepared rows: 01={len(table_data['01'])}, 02={len(table_data['02'])}, 03={len(table_data['03'])}")

    if args.dry_run:
        for key in ("01", "02", "03"):
            print(f"\n=== {TABLES[key]} sample ===")
            print(json.dumps(table_data[key][:2], ensure_ascii=False, indent=2))
        return

    app_id = env("FEISHU_APP_ID")
    app_secret = env("FEISHU_APP_SECRET")
    app_token = env("FEISHU_BITABLE_APP_TOKEN")
    token = tenant_access_token(app_id, app_secret)

    available = list_tables(token, app_token)
    missing = [TABLES[k] for k in ("01", "02", "03") if TABLES[k] not in available]
    if missing:
        raise SystemExit("Existing Feishu tables not found: " + ", ".join(missing))

    for table_key in ("01", "02", "03"):
        table_name = TABLES[table_key]
        table_id = available[table_name]
        rows = table_data[table_key]
        existing_records = list_records(token, app_token, table_id)
        existing = {
            key_for(table_key, item.get("fields", {})): item.get("record_id")
            for item in existing_records
            if item.get("fields")
        }
        created = updated = 0
        for row in rows:
            key = key_for(table_key, row)
            result = upsert_record(token, app_token, table_id, existing.get(key), row)
            if result == "created":
                created += 1
            else:
                updated += 1
            time.sleep(0.15)
        print(f"{table_name}: {created} created, {updated} updated")


if __name__ == "__main__":
    main()
