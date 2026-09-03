#!/usr/bin/env python3
"""
sync_weekly_feishu.py — 把周度三表同步到飞书已有的多维表格。

只更新已有表，不创建、不重命名表：
  - 01_方案增长总览
  - 02_单方案流量明细
  - 03_转化漏斗

upsert key：
  - 01：周次
  - 02：最新周次 + solution名称
  - 03：最新周次 + solution名称

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

# 与飞书现有表最新周方案名称保持一致的中文名
SLUG_CN = {
    "smart-warehouse-management": "智能仓储管理",
    "voicecollectionanalysis": "语音采集与分析",
    "conversational-voice-ai": "对话式语音AI",
    "smart-agriculture-sensing": "智慧农业传感",
    "smart-livestock-farming": "智能畜牧养殖",
    "intelligent-video-analytics": "智能视频分析",
    "indoor-outdoor-positioning": "室内外定位",
    "environment-monitoring": "环境监测",
    "building-energy-management": "楼宇能源管理",
    "campus-safety-management": "校园安全管理",
    "building-energy-retrofit": "楼宇能源改造",
    "hazard-response": "应急响应",
}

# 历史记录里出现过、但已统一为 SLUG_CN 主名称的旧别名；仅在 upsert 匹配时使用。
SLUG_ALIASES = {
    "voicecollectionanalysis": ["语音采集分析"],
    "smart-agriculture-sensing": ["智慧农业感知"],
    "smart-livestock-farming": ["智慧畜牧"],
    "building-energy-retrofit": ["楼宇节能改造"],
}

NAME_TO_SLUG = {name: slug for slug, name in SLUG_CN.items()}
for _slug, _names in SLUG_ALIASES.items():
    for _name in _names:
        NAME_TO_SLUG[_name] = _slug


REQUIRED_FIELDS = {
    "01": ["周次", "截止日期", "方案页独立访客数", "solution页面总访问量",
           "关键事件(CTA)", "CTA转化率", "Solution 页面周环比",
           "流量最高方案", "增长最快solution"],
    "02": ["最新周次", "截止日期", "solution名称", "落地页访问量", "独立访客数",
           "session", "平均停留时长（秒）", "参与率", "主要流量来源",
           "CTA点击量（key event）", "表单提交", "数据状态"],
    "03": ["最新周次", "截止日期", "solution名称", "页面访问量（PV）",
           "参与会话数", "CTA点击量", "表单提交", "页面->CTA转化率",
           "CTA->表单转化率", "数据状态"],
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


def parse_week_ending(value):
    text = str(value or "").strip()
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def is_current_week(value):
    day = parse_week_ending(value)
    return day is not None and day.isocalendar()[:2] == dt.date.today().isocalendar()[:2]


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing env var: {name}")
    return value


def validate_app_token(value: str) -> str:
    if value.startswith("http") or "?" in value or "/" in value:
        raise SystemExit(
            "FEISHU_BITABLE_APP_TOKEN must be the base token only, "
            "e.g. bascn_xxx, not the full Feishu URL."
        )
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


def list_fields(token, app_token, table_id):
    url = (f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields?"
           + urllib.parse.urlencode({"page_size": "100"}))
    payload = request_json("GET", url, token=token)
    return {
        item.get("field_name"): item.get("type")
        for item in payload.get("data", {}).get("items", [])
    }


def datetime_to_ms(value):
    text = str(value or "").strip()
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return value
    return int(parsed.timestamp() * 1000)


def coerce_field_value(field_name, value, field_types):
    if value is None:
        return None
    # Feishu Bitable field type: 5 = Date / DateTime
    if field_name in field_types and field_types[field_name] == 5:
        return datetime_to_ms(value)
    return value


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


def rounded_ratio(numerator, denominator, digits=6):
    try:
        num = float(numerator or 0)
        den = float(denominator or 0)
    except (TypeError, ValueError):
        return None
    if den == 0:
        return 0.0
    return round(num / den, digits)


def display_slug(value):
    text = to_text(value, "")
    return SLUG_CN.get(text, text)


def localize_overview_text(value):
    text = to_text(value, "")
    for slug, name in SLUG_CN.items():
        text = text.replace(slug, name)
    return text


def annotated_slug(value):
    """从 “slug (123 PV)” / “slug (+12.3%)” 这类文本中提取 slug。"""
    text = to_text(value, "")
    if not text:
        return ""
    match = re.match(r"^([A-Za-z0-9_-]+)(?:\s*\(.*\))?$", text)
    return match.group(1) if match else text


def best_growth_text(value):
    """输出 “中文名(+70.0%)”，去掉 slug 与括号之间的多余空格。"""
    text = to_text(value, "")
    if not text:
        return ""
    match = re.match(r"^([A-Za-z0-9_-]+)\s*\((.+)\)\s*$", text)
    if match:
        return f"{display_slug(match.group(1))}({match.group(2)})"
    return localize_overview_text(text).replace(" (", "(")


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
        solution_pv = to_number(row.get("solution_pv"))
        key_events = to_number(row.get("key_events"), 0)
        wow_pct = to_number(row.get("solution_wow_pct"))
        output.append({
            "周次": week_label(week),
            "截止日期": week,
            "方案页独立访客数": to_number(row.get("solution_users"), 0),
            "solution页面总访问量": solution_pv,
            "关键事件(CTA)": key_events,
            "CTA转化率": rounded_ratio(key_events, solution_pv),
            "Solution 页面周环比": (wow_pct / 100.0) if wow_pct is not None else None,
            "流量最高方案": display_slug(annotated_slug(row.get("top_traffic_solution"))),
            "增长最快solution": best_growth_text(row.get("fastest_growing_solution")),
        })
    return output


def data_status_for_detail(week):
    return ["本周（未完整）"] if is_current_week(week) else ["已确认"]


def build_detail(rows):
    output = []
    for row in rows:
        week = row.get("week_ending", "").strip()
        key_events = to_number(row.get("key_events"), 0)
        output.append({
            "最新周次": week_label(week),
            "截止日期": week,
            "solution名称": display_slug(row.get("slug")),
            "落地页访问量": to_number(row.get("landing_pv"), 0),
            "独立访客数": to_number(row.get("users"), 0),
            "session": to_number(row.get("sessions"), 0),
            "平均停留时长（秒）": to_number(row.get("avg_eng_s")),
            "参与率": to_number(row.get("engagement_rate")),
            "主要流量来源": to_text(row.get("top_channel"), ""),
            "CTA点击量（key event）": str(key_events),
            "表单提交": "未接入",
            "数据状态": data_status_for_detail(week),
        })
    return output


def build_funnel(rows):
    output = []
    for row in rows:
        week = row.get("week_ending", "").strip()
        page_pv = to_number(row.get("page_pv"), 0)
        cta_clicks = to_number(row.get("cta_clicks"), 0)
        output.append({
            "最新周次": week_label(week),
            "截止日期": week,
            "solution名称": display_slug(row.get("slug")),
            "页面访问量（PV）": page_pv,
            "参与会话数": to_number(row.get("engaged_sessions"), 0),
            "CTA点击量": cta_clicks,
            "表单提交": "未接入CRM",
            "页面->CTA转化率": rounded_ratio(cta_clicks, page_pv),
            "CTA->表单转化率": None,
            "数据状态": "未接入CRM",
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
    if table_key in {"02", "03"}:
        week = str(row.get("最新周次", "")).strip()
        name = str(row.get("solution名称", "")).strip()
        slug = NAME_TO_SLUG.get(name, name)
        return f"{week}|{slug}"
    return ""


def validate_rows(table_key, rows, field_types):
    if not rows:
        return
    sample = rows[0]
    unknown = [name for name in sample if name not in field_types]
    if unknown:
        raise SystemExit(
            f"{TABLES[table_key]} has unexpected field(s) {unknown}; "
            f"actual fields are {sorted(field_types)}"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
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
    app_token = validate_app_token(env("FEISHU_BITABLE_APP_TOKEN"))
    print("Step 1: requesting tenant_access_token", flush=True)
    token = tenant_access_token(app_id, app_secret)
    print("Step 2: listing tables in Base", flush=True)

    available = list_tables(token, app_token)
    missing = [TABLES[k] for k in ("01", "02", "03") if TABLES[k] not in available]
    if missing:
        raise SystemExit("Existing Feishu tables not found: " + ", ".join(missing))

    schemas = {}
    for table_key in ("01", "02", "03"):
        table_name = TABLES[table_key]
        table_id = available[table_name]
        field_types = list_fields(token, app_token, table_id)
        schemas[table_key] = (table_name, table_id, field_types)
        print(f"Step 3: {table_name} field types: {field_types}", flush=True)
        validate_rows(table_key, table_data[table_key], field_types)

    if args.inspect_only:
        print("inspect-only: field checks passed, no records written.")
        return

    for table_key in ("01", "02", "03"):
        table_name, table_id, field_types = schemas[table_key]
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
            fields = {
                name: coerce_field_value(name, value, field_types)
                for name, value in row.items()
            }
            result = upsert_record(token, app_token, table_id, existing.get(key), fields)
            if result == "created":
                created += 1
            else:
                updated += 1
            time.sleep(0.15)
        print(f"{table_name}: {created} created, {updated} updated")


if __name__ == "__main__":
    main()
