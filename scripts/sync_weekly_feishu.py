#!/usr/bin/env python3
"""
sync_weekly_feishu.py — 把周度表同步到飞书已有的多维表格。

只更新已有表，不创建、不重命名表：
  - 01_方案增长总览（v3 口径：data/analysis_v3/01_方案增长总览.csv）
  - 02_单方案流量明细（v3 口径：data/analysis_v3/02_单方案流量明细.csv）
  - 03_转化漏斗
  - 05_参考设计Top3（v3 口径：data/analysis_v3/04_参考设计Top3.csv）

字段名以 v3 口径对外表为准（方案区独立访客数/方案区浏览PV/浏览PV周环比(%)、
方案页浏览PV/会话数/平均停留时长(秒)/周环比(%) 等），旧字段名通过 FIELD_ALIASES 回退兼容。

upsert key：
  - 01：周次
  - 02：最新周次 + 方案名称
  - 03：最新周次 + 方案名称
  - 05：最新周次 + 排名（字段：最新周次 / 截止日期 / 排名 / 页面名称（短中文名）/ 浏览PV / 数据状态）

--prune：按 CSV 为准，删除飞书表里多出来的旧记录（键不在本次 CSV 内），
         用于 02 这类行数会随"实际方案数"变化的表；本轮未被写入的记录（含同键重复行、
         周次被手工改动的错位行）会被清掉，所以手工改表后跑一次即可自动对齐。

环境变量：
  FEISHU_APP_ID
  FEISHU_APP_SECRET
  FEISHU_BITABLE_APP_TOKEN
"""
import argparse
import csv
import subprocess
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
V3_DIR = ROOT / "data" / "analysis_v3"

TABLES = {
    "01": "01_方案增长总览",
    "02": "02_单方案流量明细",
    "03": "03_转化漏斗",
    "05": "05_参考设计Top3",
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
    "off-grid-mesh-communication": "离网通信",
    "acoustic-audio-ai": "声学与音频AI",
    "home-building-automation": "家庭与楼宇自动化",
}

# 历史记录里出现过、但已统一为 SLUG_CN 主名称的旧别名；仅在 upsert 匹配时使用。
SLUG_ALIASES = {
    "voicecollectionanalysis": ["语音采集分析", "门店客服质检"],
    "smart-agriculture-sensing": ["智慧农业感知"],
    "smart-livestock-farming": ["智慧畜牧"],
    "building-energy-retrofit": ["楼宇节能改造"],
    # 2026-10-05~10-11（W41）改名后的新名；用于把旧名行升级为新名而不是重复建档
    "conversational-voice-ai": ["离线语音控制"],
}

NAME_TO_SLUG = {name: slug for slug, name in SLUG_CN.items()}
for _slug, _names in SLUG_ALIASES.items():
    for _name in _names:
        NAME_TO_SLUG[_name] = _slug


REQUIRED_FIELDS = {
    "01": ["周次", "截止日期", "方案区独立访客数", "方案区浏览PV",
           "CTA点击量", "CTA点击率(%)", "浏览PV周环比(%)",
           "流量最高方案", "增长最快方案"],
    "02": ["最新周次", "截止日期", "方案名称", "页面类型", "方案页浏览PV", "独立访客数",
           "会话数", "平均停留时长(秒)", "参与率", "主要流量来源",
           "CTA点击量", "leads", "周环比(%)", "数据状态"],
    "03": ["最新周次", "截止日期", "方案名称", "页面访问量（PV）",
           "Session", "CTA点击量", "表单提交（Leads）", "CTA点击率(%)", "表单转化率(%)",
           "数据状态"],
    "05": ["最新周次", "截止日期", "排名", "页面名称", "浏览PV", "数据状态"],
}

# 当飞书表的旧字段名尚未迁移时，按优先级回退；新名优先。
FIELD_ALIASES = {
    "01": {
        "方案区独立访客数": ["方案页独立访客数"],
        "方案区浏览PV": ["Solution页面总访问量", "solution页面总访问量"],
        "Solution页面总访问量": ["solution页面总访问量"],
        "CTA点击量": ["关键事件(CTA)"],
        "CTA点击率(%)": ["CTA点击率", "CTA转化率"],
        "浏览PV周环比(%)": ["Solution页面周环比", "Solution 页面周环比"],
        "Solution页面周环比": ["Solution 页面周环比"],
        "增长最快方案": ["增长最快solution"],
    },
    "02": {
        "方案名称": ["solution名称"],
        "页面类型": ["页面类别"],
        "方案页浏览PV": ["落地页访问量"],
        "会话数": ["Session", "session"],
        "平均停留时长(秒)": ["平均停留时长（秒）"],
        "CTA点击量": ["CTA点击量（key event）"],
        "周环比(%)": ["周环比"],
    },
    "03": {
        "方案名称": ["solution名称"],
        "Session": ["参与会话数"],
        "表单提交（Leads）": ["表单提交"],
        "CTA点击率(%)": ["CTA点击率", "页面->CTA转化率"],
        "表单转化率(%)": ["表单转化率", "CTA->表单转化率"],
    },
    "05": {
        "最新周次": ["周次", "周"],
    },
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
    data_text = None if body is None else json.dumps(body, ensure_ascii=False)
    cmd = ["curl", "-sS", "-m", "15", "-X", method, url,
           "-H", "Content-Type: application/json; charset=utf-8"]
    if token:
        cmd += ["-H", f"Authorization: Bearer {token}"]
    if data_text is not None:
        cmd += ["--data-binary", "@-"]
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            proc = subprocess.run(
                cmd, input=data_text, capture_output=True, text=True, timeout=20,
            )
        except subprocess.TimeoutExpired:
            last_error = "curl subprocess timed out"
            if attempt < retries:
                time.sleep(1.5 * attempt)
            continue
        raw = proc.stdout.strip()
        if proc.returncode != 0 or not raw:
            last_error = (proc.stderr or proc.stdout or "empty curl response").strip()[:400]
            if attempt < retries:
                time.sleep(1.5 * attempt)
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            last_error = f"non-JSON response: {raw[:400]}"
            if attempt < retries:
                time.sleep(1.5 * attempt)
            continue
        code = payload.get("code")
        if code == 0:
            return payload
        # Feishu 偶发内部错误可重试（1663 = internal error，偶发）
        if code in {2200, 500, 1663} and attempt < retries:
            last_error = f"Feishu code={code}: {payload.get('msg')}"
            time.sleep(1.5 * attempt)
            continue
        raise SystemExit(f"Feishu code={code}: {payload.get('msg')}")
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
    ftype = field_types.get(field_name)
    # Feishu Bitable field type: 5 = Date / DateTime
    if ftype == 5:
        return datetime_to_ms(value)
    # 文本字段必须给字符串，防止 TextFieldConvFail
    if ftype == 1 and not isinstance(value, str):
        return "" if value == "" else str(value)
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


def batch_delete_records(token, app_token, table_id, record_ids):
    """按 record_id 批量删除（每批 500），用于 --prune 清掉 CSV 里已不存在的旧行。"""
    if not record_ids:
        return 0
    url = f"{FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete"
    for i in range(0, len(record_ids), 500):
        request_json("POST", url, token=token, body={"records": record_ids[i:i + 500]})
    return len(record_ids)


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


def pct_fraction(value):
    """把 “32.2”（百分数写法）转成 0.322（飞书百分比字段按小数存储）。"""
    number = to_number(value)
    return round(number / 100, 6) if number is not None else None


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


def read_csv(name, base=None):
    path = (base or ANALYSIS_DIR) / name
    if not path.exists():
        raise SystemExit(f"Missing analysis CSV: {path}")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def build_overview(rows):
    """01 方案区增长总览：直接读 v3 口径对外表（字段已就位，只做类型/百分比转换）。"""
    output = []
    for row in rows:
        week = str(row.get("截止日期", "")).strip()
        output.append({
            "周次": str(row.get("周次", "")).strip() or week_label(week),
            "截止日期": week,
            "方案区独立访客数": to_number(row.get("方案区独立访客数"), 0),
            "方案区浏览PV": to_number(row.get("方案区浏览PV"), 0),
            "CTA点击量": to_number(row.get("CTA点击量"), 0),
            "CTA点击率(%)": pct_fraction(row.get("CTA点击率(%)")),
            "浏览PV周环比(%)": pct_fraction(row.get("浏览PV周环比(%)")),
            "流量最高方案": to_text(row.get("流量最高方案"), ""),
            "增长最快方案": to_text(row.get("增长最快方案"), ""),
        })
    return output


def data_status_for_detail(week):
    return ["本周（未完整）"] if is_current_week(week) else ["已确认"]


def build_detail(rows):
    """02 单方案流量明细：直接读 v3 口径对外表。"""
    output = []
    for row in rows:
        week = str(row.get("截止日期", "")).strip()
        output.append({
            "最新周次": str(row.get("最新周次", "")).strip() or week_label(week),
            "截止日期": week,
            "方案名称": to_text(row.get("方案名称"), ""),
            "页面类型": to_text(row.get("页面类型"), ""),
            "方案页浏览PV": to_number(row.get("方案页浏览PV"), 0),
            "独立访客数": to_number(row.get("独立访客数"), 0),
            "会话数": to_number(row.get("会话数"), 0),
            "平均停留时长(秒)": to_number(row.get("平均停留时长(秒)")),
            "参与率": to_number(row.get("参与率")),
            "主要流量来源": to_text(row.get("主要流量来源"), ""),
            "CTA点击量": to_number(row.get("CTA点击量"), 0),
            "leads": to_number(row.get("leads"), 0),
            "周环比(%)": pct_fraction(row.get("周环比(%)")),
            "数据状态": to_text(
                row.get("数据状态"),
                "本周（未完整）" if is_current_week(week) else "已确认",
            ),
        })
    return output


def build_funnel(rows):
    output = []
    for row in rows:
        week = row.get("week_ending", "").strip()
        page_pv = to_number(row.get("page_pv"), 0)
        cta_clicks = to_number(row.get("cta_clicks"), 0)
        cta_rate_pct = to_number(row.get("pv_to_cta_rate"))
        form_submits = to_number(row.get("form_submits"), 0)
        output.append({
            "最新周次": week_label(week),
            "截止日期": week,
            "方案名称": display_slug(row.get("slug")),
            "页面访问量（PV）": page_pv,
            "Session": to_number(row.get("sessions"), 0),
            "CTA点击量": cta_clicks,
            "表单提交（Leads）": form_submits,
            "CTA点击率(%)": (round(cta_rate_pct / 100, 6) if cta_rate_pct is not None else 0.0),
            "表单转化率(%)": (round(form_submits / page_pv, 6) if page_pv else 0.0),
            "数据状态": to_text(row.get("data_status"), "未接入CRM"),
        })
    return output


def build_data():
    return {
        "01": build_overview(read_csv("01_方案增长总览.csv", V3_DIR)),
        "02": build_detail(read_csv("02_单方案流量明细.csv", V3_DIR)),
        "03": build_funnel(read_csv("转换漏斗.csv")),
        "05": build_refdesign(read_csv("04_参考设计Top3.csv", V3_DIR)),
    }


def build_refdesign(rows):
    output = []
    for row in rows:
        week = str(row.get("截止日期", "")).strip()
        output.append({
            "最新周次": str(row.get("最新周次") or row.get("周次", "")).strip() or week_label(week),
            "截止日期": week,
            "排名": to_number(row.get("排名"), 0),
            "页面名称": to_text(row.get("页面名称"), ""),
            "浏览PV": to_number(row.get("浏览PV"), 0),
            "数据状态": to_text(row.get("数据状态"), "完整周"),
        })
    return output


def key_for(table_key, row):
    if table_key == "01":
        return str(row.get("周次", "")).strip()
    if table_key in {"02", "03"}:
        week = str(row.get("最新周次", "")).strip()
        name = str(row.get("方案名称", "")).strip()
        slug = NAME_TO_SLUG.get(name, name)
        return f"{week}|{slug}"
    if table_key == "05":
        week = str(row.get("最新周次") or row.get("周次", "")).strip()
        rank = str(row.get("排名", "")).strip()
        return f"{week}|{rank}"
    return ""


def validate_rows(table_key, rows, field_types):
    if not rows:
        return
    sample = rows[0]
    unknown = [name for name in sample if resolve_field(table_key, name, field_types) is None]
    if unknown:
        print(
            f"WARNING: {TABLES[table_key]} will skip field(s) not present "
            f"in Feishu table: {unknown}",
            flush=True,
        )


def resolve_field(table_key, desired, field_types):
    """返回飞书表里实际存在的字段名；新名优先，旧名回退。"""
    if desired in field_types:
        return desired
    for alias in FIELD_ALIASES.get(table_key, {}).get(desired, []):
        if alias in field_types:
            return alias
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--prune", action="store_true",
                        help="删除飞书表里键不在本次 CSV 内的旧记录（01/02/05 生效）")
    parser.add_argument("--only", choices=["01", "02", "03", "05"], default=None, help="只同步指定表")
    args = parser.parse_args()

    table_data = build_data()
    if args.only:
        table_data = {args.only: table_data[args.only]}
    print(f"Prepared rows: " + ", ".join(f"{k}={len(v)}" for k, v in table_data.items()))

    if args.dry_run:
        for key in table_data:
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
    missing = [TABLES[k] for k in table_data if TABLES[k] not in available]
    required_missing = [name for name in missing if name != TABLES["05"]]
    optional_missing = [name for name in missing if name == TABLES["05"]]
    if required_missing:
        raise SystemExit("Existing Feishu tables not found: " + ", ".join(required_missing))
    if optional_missing:
        # 05_参考设计Top3 需要先在飞书手动建表（应用无建表权限）；未建好时跳过，不影响 01/02/03。
        print(f"WARNING: 表未创建，本次跳过：{', '.join(optional_missing)}", flush=True)
        for key in list(table_data):
            if TABLES[key] in optional_missing:
                table_data.pop(key)

    schemas = {}
    for table_key in table_data:
        table_name = TABLES[table_key]
        table_id = available[table_name]
        field_types = list_fields(token, app_token, table_id)
        schemas[table_key] = (table_name, table_id, field_types)
        print(f"Step 3: {table_name} field types: {field_types}", flush=True)
        validate_rows(table_key, table_data[table_key], field_types)

    if args.inspect_only:
        print("inspect-only: field checks passed, no records written.")
        return

    for table_key in table_data:
        table_name, table_id, field_types = schemas[table_key]
        rows = table_data[table_key]
        existing_records = list_records(token, app_token, table_id)
        existing = {
            key_for(table_key, item.get("fields", {})): item.get("record_id")
            for item in existing_records
            if item.get("fields")
        }
        used_ids = set()
        created = updated = 0
        for idx, row in enumerate(rows, 1):
            key = key_for(table_key, row)
            print(f"    {table_name} [{idx}/{len(rows)}] {key}", flush=True)
            fields = {}
            for name, value in row.items():
                actual = resolve_field(table_key, name, field_types)
                if actual:
                    fields[actual] = coerce_field_value(actual, value, field_types)
            record_id = existing.get(key)
            if record_id:
                used_ids.add(record_id)
            result = upsert_record(token, app_token, table_id, record_id, fields)
            if result == "created":
                created += 1
            else:
                updated += 1
            time.sleep(float(os.environ.get("FEISHU_SYNC_DELAY", "0.15")))
        pruned = 0
        if args.prune and table_key in {"01", "02", "05"}:
            # 以 CSV 为准：本轮没有被写入过的记录（含同键重复行、周次错位的旧行）一律清掉
            stale = [item.get("record_id") for item in existing_records
                     if item.get("record_id") not in used_ids]
            pruned = batch_delete_records(token, app_token, table_id, stale)
        print(f"{table_name}: {created} created, {updated} updated"
              + (f", {pruned} pruned" if args.prune else ""))


if __name__ == "__main__":
    main()
