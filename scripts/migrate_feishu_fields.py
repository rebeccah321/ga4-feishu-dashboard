#!/usr/bin/env python3
"""幂等地把飞书三张表字段迁移到最终字段名/类型（v3 口径对外表）。

- 01：方案页独立访客数 → 方案区独立访客数；Solution页面总访问量 → 方案区浏览PV；
  Solution页面周环比 → 浏览PV周环比(%)
- 02：落地页访问量 → 方案页浏览PV；Session → 会话数；平均停留时长（秒）→ 平均停留时长(秒)；
  周环比 → 周环比(%)；leads 字段删除（Lead API 未接入，历史值全 0，删除前会校验）
- 03：不变
"""
from __future__ import annotations

import argparse
import sys
import sync_weekly_feishu as s

TABLE_NAMES = {
    "01": "01_方案增长总览",
    "02": "02_单方案流量明细",
    "03": "03_转化漏斗",
}

# 旧字段名 -> 新字段名（按顺序执行，兼容从未迁移过的更旧表）
RENAME = {
    "01": {
        # 早期迁移（幂等：已迁移过的表会跳过）
        "方案独立访客数": "方案页独立访客数",
        "solution页面总访问量": "Solution页面总访问量",
        "CTA转化率": "CTA点击率(%)",
        "Solution 页面周环比": "Solution页面周环比",
        "增长最快solution": "增长最快方案",
        # v3 口径最终字段名
        "方案页独立访客数": "方案区独立访客数",
        "Solution页面总访问量": "方案区浏览PV",
        "Solution页面周环比": "浏览PV周环比(%)",
    },
    "02": {
        "session": "Session",
        "落地页访问量": "方案页浏览PV",
        "Session": "会话数",
        "平均停留时长（秒）": "平均停留时长(秒)",
        "周环比": "周环比(%)",
    },
    "03": {
        "session": "Session",
        "表单提交(Leads)": "表单提交（Leads）",
    },
}

# 字段名 -> 目标字段类型（Feishu Bitable type: 1 文本, 2 数字）
TYPE_OVERRIDES = {
    "01": {},
    "02": {
        "主要流量来源": 1,
        "数据状态": 1,
    },
    "03": {},
}

# 需要新增的缺失字段
ADD_FIELDS = {
    "01": [],
    "02": [
        ("CTA点击量", 2),
        ("leads", 2),
    ],
    "03": [],
}

# 需要删除的废弃字段（删除前校验所有记录值为 0/空）
DELETE_FIELDS = {
    "01": [],
    "02": ["leads"],
    "03": [],
}


def list_fields_full(token, app_token, table_id):
    url = (f"{s.FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields?"
           + "page_size=100")
    payload = s.request_json("GET", url, token=token)
    out = []
    for item in payload.get("data", {}).get("items", []):
        out.append({
            "name": item.get("field_name"),
            "id": item.get("field_id"),
            "type": int(item.get("type")),
        })
    return out


def update_field(token, app_token, table_id, field_id, name, ftype):
    url = f"{s.FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}"
    body = {}
    if name is not None:
        body["field_name"] = name
    if ftype is not None:
        body["type"] = ftype
    try:
        s.request_json("PUT", url, token=token, body=body)
    except SystemExit as exc:
        raise SystemExit(f"update field `{name}` failed: {exc}")


def create_field(token, app_token, table_id, name, ftype):
    url = f"{s.FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
    s.request_json("POST", url, token=token, body={"field_name": name, "type": ftype})


def delete_field(token, app_token, table_id, field_id):
    url = f"{s.FEISHU_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields/{field_id}"
    s.request_json("DELETE", url, token=token)


def field_values_are_empty(token, app_token, table_id, name):
    """返回该字段所有记录值是否都为 0/空（用于安全删除）。"""
    for rec in s.list_records(token, app_token, table_id):
        value = rec.get("fields", {}).get(name)
        if isinstance(value, list):
            value = "".join(seg.get("text", "") for seg in value if isinstance(seg, dict))
        text = str(value).strip() if value is not None else ""
        if text not in ("", "0", "0.0", "None"):
            return False, text
    return True, ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete-fields", action="store_true",
                        help="执行 DELETE_FIELDS 中的字段删除（默认只改名/改类型/加字段，不删除）")
    args = parser.parse_args()

    app_id = s.env("FEISHU_APP_ID")
    app_secret = s.env("FEISHU_APP_SECRET")
    app_token = s.validate_app_token(s.env("FEISHU_BITABLE_APP_TOKEN"))
    token = s.tenant_access_token(app_id, app_secret)
    available = s.list_tables(token, app_token)

    for key in ("01", "02", "03"):
        table_name = TABLE_NAMES[key]
        if table_name not in available:
            raise SystemExit(f"missing table: {table_name}")
        table_id = available[table_name]
        fields = list_fields_full(token, app_token, table_id)
        by_name = {f["name"]: f for f in fields}
        by_id = {f["id"]: f for f in fields}

        # 1) 改名
        for old, new in RENAME.get(key, {}).items():
            f = by_name.get(old)
            if not f:
                print(f"[{table_name}] rename skip (old missing): {old} -> {new}", flush=True)
                continue
            if new in by_name:
                print(f"[{table_name}] rename skip (new exists): {old} -> {new}", flush=True)
                continue
            override_type = TYPE_OVERRIDES.get(key, {}).get(old if new is None else new)
            target_type = override_type if override_type is not None else f["type"]
            update_field(token, app_token, table_id, f["id"], new, target_type)
            print(f"[{table_name}] renamed {old} -> {new} (type {target_type})", flush=True)
            by_name.pop(old)
            by_name[new] = f

        # 2) 只改类型（名称不变）
        for name, ftype in TYPE_OVERRIDES.get(key, {}).items():
            f = by_name.get(name)
            if not f or f["type"] == ftype:
                continue
            update_field(token, app_token, table_id, f["id"], None, ftype)
            print(f"[{table_name}] type {name}: {f['type']} -> {ftype}", flush=True)

        # 3) 新增缺失字段
        for name, ftype in ADD_FIELDS.get(key, []):
            if name in by_name:
                continue
            create_field(token, app_token, table_id, name, ftype)
            print(f"[{table_name}] added field {name} (type {ftype})", flush=True)

        # 4) 删除废弃字段（先校验值全为 0/空）
        if not args.delete_fields:
            continue
        for name in DELETE_FIELDS.get(key, []):
            f = by_name.get(name)
            if not f:
                print(f"[{table_name}] delete skip (missing): {name}", flush=True)
                continue
            clean, sample = field_values_are_empty(token, app_token, table_id, name)
            if not clean:
                print(f"[{table_name}] delete SKIPPED {name}: 存在非零值（如 {sample!r}）", flush=True)
                continue
            delete_field(token, app_token, table_id, f["id"])
            print(f"[{table_name}] deleted field {name}", flush=True)


if __name__ == "__main__":
    main()
