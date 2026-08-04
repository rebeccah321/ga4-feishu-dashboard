#!/usr/bin/env python3
"""
ga4_fetch.py — Seeed 官网流量日更拉取脚本（基于 traffic-analytics skill 规范）。

认证双模式：
  - adc（本地默认）：gcloud auth application-default print-access-token
  - service_account（CI）：GOOGLE_APPLICATION_CREDENTIALS 指向服务账号 JSON

数据卫生（来自 skill 第 6 节）：
  - CN 双路径合并：/solutions/<slug> + /solutions/<slug>-zh-hans 相加
  - 垃圾 URL 过滤：剔除含 { 或 } 的路径
  - hostName → EN/CN/JP 语言映射

输出：
  data/raw/             — GA4 API 原始 JSON
  data/normalized/      — 日级明细 CSV（标准化字段格式）
  data/analysis/        — 衍生分析表（solution/funnel/ratio/channel）
  dashboard/data/       — latest.json + latest.csv（供飞书/GitHub Pages 消费）
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── 常量（来自 traffic-analytics skill 第 1 节） ──────────────────────────
PROPERTY = "502086217"  # Seeed 企业官网，三语言站同属性
QUOTA_PROJECT = "my-project-1579296929285"
ENDPOINT = f"https://analyticsdata.googleapis.com/v1beta/properties/{PROPERTY}:runReport"

ROOT = Path(__file__).resolve().parents[1]

# 标准化字段格式（日级明细 fact table）
FACT_FIELDS = [
    "date", "week_start", "host_name", "lang", "page_path", "page_title",
    "channel_group", "device_category", "screen_page_views", "active_users",
    "sessions", "engagement_rate", "bounce_rate", "avg_engagement_seconds",
    "key_events", "total_revenue", "pulled_at",
]

# GA4 查询维度和指标
DIMENSIONS = ["date", "hostName", "pagePath", "pageTitle",
              "sessionDefaultChannelGroup", "deviceCategory"]
METRICS = ["screenPageViews", "activeUsers", "sessions", "engagementRate",
           "bounceRate", "userEngagementDuration", "keyEvents", "totalRevenue"]

# hostName → 语言映射（skill 第 1 节）
HOST_LANG = {
    "www.seeed.cc": "EN",
    "www.seeedstudio.com.cn": "CN",
    "www.seeed.co.jp": "JP",
}

# 方案 slug 提取正则
SLUG_RE = re.compile(r"^/solutions/([^/?]+?)(?:-zh-hans)?/?$")


# ── 认证 ──────────────────────────────────────────────────────────────────
def get_token_adc() -> str:
    """本地模式：通过 gcloud ADC 获取 token。"""
    env = os.environ.copy()
    env["CLOUDSDK_LOG_DIR"] = "/tmp/gcloud-logs"
    try:
        result = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=30, env=env,
        )
        token = result.stdout.strip()
        if token:
            return token
        raise RuntimeError(result.stderr.strip() or "empty token")
    except FileNotFoundError:
        raise SystemExit(
            "gcloud not found. Install: brew install --cask google-cloud-sdk\n"
            "Or set GA4_AUTH_MODE=service_account and GOOGLE_APPLICATION_CREDENTIALS"
        )


def get_token_service_account() -> str:
    """CI 模式：通过服务账号 JSON 获取 token（纯标准库 JWT 实现）。"""
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not cred_path or not os.path.isfile(cred_path):
        raise SystemExit(
            "GOOGLE_APPLICATION_CREDENTIALS not set or file not found.\n"
            "For local use, set GA4_AUTH_MODE=adc (default)."
        )
    with open(cred_path) as f:
        creds = json.load(f)

    if creds.get("type") == "authorized_user":
        # ADC authorized_user — 用 refresh_token 换 access_token
        return _refresh_authorized_user(creds)

    # 服务账号 — JWT 换 token
    return _jwt_to_token(creds)


def _refresh_authorized_user(creds: dict) -> str:
    import urllib.parse
    data = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def _jwt_to_token(creds: dict) -> str:
    """用服务账号 JSON 生成 JWT 并换取 access_token（纯标准库）。"""
    import base64
    import hashlib
    import hmac
    import json as _json
    import time as _time
    import urllib.parse

    client_email = creds["client_email"]
    private_key = creds["private_key"]

    header = {"alg": "RS256", "typ": "JWT"}
    now = int(_time.time())
    payload = {
        "iss": client_email,
        "scope": "https://www.googleapis.com/auth/analytics.readonly",
        "aud": "https://oauth2.googleapis.com/token",
        "exp": now + 3600,
        "iat": now,
    }

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = b64(_json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = b64(_json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()

    # 用 RSA-SHA256 签名
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise SystemExit(
            "cryptography package required for service account auth.\n"
            "pip install cryptography  (or use GA4_AUTH_MODE=adc locally)"
        )

    key = serialization.load_pem_private_key(
        private_key.encode(), password=None
    )
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    jwt = f"{header_b64}.{payload_b64}.{b64(signature)}"

    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": jwt,
    }).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["access_token"]


def get_token() -> str:
    mode = os.environ.get("GA4_AUTH_MODE", "adc").lower()
    if mode == "service_account":
        return get_token_service_account()
    return get_token_adc()


# ── GA4 API 调用 ───────────────────────────────────────────────────────────
def run_report(body: dict, token: str, tries: int = 8, use_quota_project: bool = True) -> dict:
    """POST runReport，内置重试（绕过本机网络间歇 404 干扰 + 403 quota fallback）。"""
    data = json.dumps(body).encode()
    last_error = ""
    for attempt in range(1, tries + 1):
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        if use_quota_project:
            headers["x-goog-user-project"] = QUOTA_PROJECT
        req = urllib.request.Request(ENDPOINT, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode()
                if raw.lstrip().startswith("{"):
                    return json.loads(raw)
                last_error = f"non-JSON response: {raw[:200]}"
        except urllib.error.HTTPError as err:
            error_text = err.read().decode(errors="replace")
            last_error = f"HTTP {err.code}: {error_text[:400]}"
            # 403 quota 权限问题 → 去掉 header 重试
            if err.code == 403 and use_quota_project and "serviceusage.services.use" in error_text:
                print("  quota project 403, retrying without x-goog-user-project header...")
                return run_report(body, token, tries=tries, use_quota_project=False)
            if err.code in (400, 401, 403):
                raise SystemExit(f"GA4 API error: {last_error}")
        except Exception as err:
            last_error = str(err)
        if attempt < tries:
            time.sleep(2 * attempt)
    raise SystemExit(f"GA4 API failed after {tries} retries: {last_error}")


def date_range(days: int) -> Tuple[str, str]:
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=days - 1)
    return start.isoformat(), end.isoformat()


def fetch_daily_metrics(token: str, start: str, end: str) -> List[dict]:
    """拉取日级页面明细数据。"""
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": d} for d in DIMENSIONS],
        "metrics": [{"name": m} for m in METRICS],
        "limit": 100000,
    }
    report = run_report(body, token)
    rows = []
    for row in report.get("rows", []):
        dims = row.get("dimensionValues", [])
        mets = row.get("metricValues", [])
        rows.append({
            "date": dims[0]["value"] if dims else "",
            "hostName": dims[1]["value"] if len(dims) > 1 else "",
            "pagePath": dims[2]["value"] if len(dims) > 2 else "",
            "pageTitle": dims[3]["value"] if len(dims) > 3 else "",
            "sessionDefaultChannelGroup": dims[4]["value"] if len(dims) > 4 else "",
            "deviceCategory": dims[5]["value"] if len(dims) > 5 else "",
            "screenPageViews": _mv(mets, 0),
            "activeUsers": _mv(mets, 1),
            "sessions": _mv(mets, 2),
            "engagementRate": _mv(mets, 3),
            "bounceRate": _mv(mets, 4),
            "userEngagementDuration": _mv(mets, 5),
            "keyEvents": _mv(mets, 6),
            "totalRevenue": _mv(mets, 7),
        })
    return rows


def _mv(metric_values: list, index: int) -> float:
    if index < len(metric_values):
        try:
            return float(metric_values[index]["value"])
        except (KeyError, ValueError, TypeError):
            return 0.0
    return 0.0


# ── 数据卫生 + 标准化 ───────────────────────────────────────────────────────
def is_garbage_url(path: str) -> bool:
    """skill 第 6 节：剔除含 { 或 } 的垃圾路径。"""
    return "{" in path or "}" in path


def lang_of(host: str) -> Optional[str]:
    return HOST_LANG.get(host)


def week_start(date_str: str) -> str:
    d = dt.date.fromisoformat(date_str)
    return (d - dt.timedelta(days=d.weekday())).isoformat()


def normalize_rows(raw_rows: List[dict]) -> List[dict]:
    """标准化 + 数据卫生：过滤垃圾 URL、映射语言、计算人均时长。"""
    pulled_at = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    normalized = []
    for raw in raw_rows:
        path = raw.get("pagePath", "")
        if is_garbage_url(path):
            continue
        host = raw.get("hostName", "")
        lang = lang_of(host)
        if lang is None:
            continue  # 只保留三语言站
        date_raw = raw.get("date", "")
        date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}" if len(date_raw) == 8 else date_raw
        sessions = max(int(raw.get("sessions", 0)), 1)
        eng_duration = float(raw.get("userEngagementDuration", 0))
        normalized.append({
            "date": date_str,
            "week_start": week_start(date_str),
            "host_name": host,
            "lang": lang,
            "page_path": path,
            "page_title": raw.get("pageTitle", ""),
            "channel_group": raw.get("sessionDefaultChannelGroup", "") or "Unassigned",
            "device_category": raw.get("deviceCategory", "") or "desktop",
            "screen_page_views": int(raw.get("screenPageViews", 0)),
            "active_users": int(raw.get("activeUsers", 0)),
            "sessions": int(raw.get("sessions", 0)),
            "engagement_rate": round(float(raw.get("engagementRate", 0)), 4),
            "bounce_rate": round(float(raw.get("bounceRate", 0)), 4),
            "avg_engagement_seconds": round(eng_duration / sessions, 2),
            "key_events": int(raw.get("keyEvents", 0)),
            "total_revenue": round(float(raw.get("totalRevenue", 0)), 2),
            "pulled_at": pulled_at,
        })
    return normalized


# ── 衍生分析表 ──────────────────────────────────────────────────────────────
def extract_slug(path: str) -> Optional[str]:
    """从路径提取方案 slug（合并 CN 双路径 /slug 和 /slug-zh-hans）。"""
    m = SLUG_RE.match(path)
    return m.group(1) if m else None


def is_solution_path(path: str) -> bool:
    return (path.startswith("/solutions/") or
            path.startswith("/category/solutions") or
            path == "/lora-solution")


def derive_solution_summary(rows: List[dict]) -> List[dict]:
    """方案级汇总：每个 slug × lang 一行。"""
    agg: dict = defaultdict(lambda: {"pv": 0, "users": 0, "sessions": 0,
                                      "eng_s": 0.0, "key_events": 0})
    for r in rows:
        slug = extract_slug(r["page_path"])
        if not slug:
            continue
        key = (slug, r["lang"])
        agg[key]["pv"] += r["screen_page_views"]
        agg[key]["users"] += r["active_users"]
        agg[key]["sessions"] += r["sessions"]
        agg[key]["eng_s"] += r["avg_engagement_seconds"] * r["sessions"]
        agg[key]["key_events"] += r["key_events"]

    result = []
    for (slug, lang), v in sorted(agg.items()):
        avg_eng = round(v["eng_s"] / v["sessions"], 1) if v["sessions"] else 0
        result.append({
            "slug": slug, "lang": lang,
            "screen_page_views": v["pv"], "active_users": v["users"],
            "sessions": v["sessions"], "avg_engagement_seconds": avg_eng,
            "key_events": v["key_events"],
        })
    return result


def derive_funnel_summary(rows: List[dict]) -> List[dict]:
    """漏斗：category → list → lora → solutions 各层 × lang。"""
    layers: dict = defaultdict(lambda: defaultdict(lambda: {"pv": 0, "users": 0, "sessions": 0, "eng_s": 0.0}))
    for r in rows:
        path = r["page_path"]
        if path.startswith("/category/solutions"):
            layer = "category"
        elif path == "/solutions":
            layer = "list"
        elif path == "/lora-solution":
            layer = "lora"
        elif extract_slug(path):
            layer = "solutions"
        else:
            continue
        bucket = layers[layer][r["lang"]]
        bucket["pv"] += r["screen_page_views"]
        bucket["users"] += r["active_users"]
        bucket["sessions"] += r["sessions"]
        bucket["eng_s"] += r["avg_engagement_seconds"] * r["sessions"]

    result = []
    for layer in ("category", "list", "lora", "solutions"):
        for lang in ("EN", "CN", "JP"):
            v = layers[layer][lang]
            avg_eng = round(v["eng_s"] / v["sessions"], 1) if v["sessions"] else 0
            result.append({
                "layer": layer, "lang": lang,
                "screen_page_views": v["pv"], "active_users": v["users"],
                "sessions": v["sessions"], "avg_engagement_seconds": avg_eng,
            })
    return result


def derive_ratio_summary(rows: List[dict]) -> List[dict]:
    """方案区占全站比例：每语言一行。"""
    totals: dict = defaultdict(lambda: {"total_pv": 0, "solution_pv": 0})
    for r in rows:
        totals[r["lang"]]["total_pv"] += r["screen_page_views"]
        if is_solution_path(r["page_path"]):
            totals[r["lang"]]["solution_pv"] += r["screen_page_views"]

    result = []
    for lang in ("EN", "CN", "JP"):
        t = totals[lang]
        share = round(100 * t["solution_pv"] / t["total_pv"], 2) if t["total_pv"] else 0
        result.append({
            "lang": lang,
            "solution_pv": t["solution_pv"],
            "total_pv": t["total_pv"],
            "solution_share_pct": share,
        })
    return result


def derive_channel_summary(rows: List[dict]) -> List[dict]:
    """方案页流量来源渠道：每渠道 × lang 一行。"""
    agg: dict = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if not is_solution_path(r["page_path"]):
            continue
        agg[r["lang"]][r["channel_group"]] += r["sessions"]

    result = []
    for lang in ("EN", "CN", "JP"):
        total = sum(agg[lang].values())
        for channel, sessions in sorted(agg[lang].items(), key=lambda x: -x[1]):
            result.append({
                "lang": lang, "channel_group": channel,
                "sessions": sessions,
                "share_pct": round(100 * sessions / total, 1) if total else 0,
            })
    return result


# ── 输出 ────────────────────────────────────────────────────────────────────
def write_csv(rows: List[dict], path: Path, fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def build_dashboard_json(fact_rows: List[dict], solutions: List[dict],
                          funnel: List[dict], ratio: List[dict],
                          channels: List[dict], start: str, end: str) -> dict:
    """构建 dashboard/data/latest.json 供飞书/GitHub Pages 消费。"""
    by_day: dict = defaultdict(int)
    by_channel: dict = defaultdict(int)
    by_lang: dict = defaultdict(lambda: {"pv": 0, "users": 0, "sessions": 0})
    top_pages: dict = defaultdict(int)

    for r in fact_rows:
        by_day[r["date"]] += r["screen_page_views"]
        by_channel[r["channel_group"]] += r["screen_page_views"]
        by_lang[r["lang"]]["pv"] += r["screen_page_views"]
        by_lang[r["lang"]]["users"] += r["active_users"]
        by_lang[r["lang"]]["sessions"] += r["sessions"]
        top_pages[r["page_path"]] += r["screen_page_views"]

    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "date_range": {"start": start, "end": end},
        "property": PROPERTY,
        "summary": {
            "total_views": sum(by_day.values()),
            "active_users": sum(v["users"] for v in by_lang.values()),
            "sessions": sum(v["sessions"] for v in by_lang.values()),
            "by_day": dict(sorted(by_day.items())),
            "by_channel": dict(sorted(by_channel.items(), key=lambda x: -x[1])),
            "by_lang": {k: v for k, v in by_lang.items()},
            "top_pages": dict(sorted(top_pages.items(), key=lambda x: -x[1])[:20]),
        },
        "solution_summary": solutions,
        "funnel_summary": funnel,
        "ratio_summary": ratio,
        "channel_summary": channels,
    }


# ── 主流程 ──────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Fetch GA4 daily metrics for Seeed website.")
    parser.add_argument("--days", type=int, default=28, help="Lookback days (default 28)")
    parser.add_argument("--mock", action="store_true", help="Use mock data instead of GA4 API")
    parser.add_argument("--out-dir", default=str(ROOT / "data"), help="Output directory")
    args = parser.parse_args()

    start, end = date_range(args.days)
    out_dir = Path(args.out_dir)
    print(f"Date range: {start} → {end} ({args.days} days)")

    # 拉取数据
    if args.mock:
        raw_rows = _mock_rows(start, end)
        print(f"Mock: generated {len(raw_rows)} raw rows")
    else:
        token = get_token()
        print(f"Auth OK, fetching from property {PROPERTY}...")
        raw_rows = fetch_daily_metrics(token, start, end)
        print(f"Fetched {len(raw_rows)} raw rows from GA4")

    # 保存原始数据
    raw_path = out_dir / "raw" / f"ga4_raw_{start}_to_{end}.json"
    write_json({"rows": raw_rows, "date_range": [start, end]}, raw_path)
    print(f"Saved raw: {raw_path}")

    # 标准化 + 数据卫生
    fact_rows = normalize_rows(raw_rows)
    fact_path = out_dir / "normalized" / f"ga4_daily_{start}_to_{end}.csv"
    write_csv(fact_rows, fact_path, FACT_FIELDS)
    print(f"Saved normalized: {fact_path} ({len(fact_rows)} rows after hygiene)")

    # 衍生分析表
    solutions = derive_solution_summary(fact_rows)
    funnel = derive_funnel_summary(fact_rows)
    ratio = derive_ratio_summary(fact_rows)
    channels = derive_channel_summary(fact_rows)

    analysis_dir = out_dir / "analysis"
    write_csv(solutions, analysis_dir / "solution_summary.csv",
              ["slug", "lang", "screen_page_views", "active_users", "sessions", "avg_engagement_seconds", "key_events"])
    write_csv(funnel, analysis_dir / "funnel_summary.csv",
              ["layer", "lang", "screen_page_views", "active_users", "sessions", "avg_engagement_seconds"])
    write_csv(ratio, analysis_dir / "ratio_summary.csv",
              ["lang", "solution_pv", "total_pv", "solution_share_pct"])
    write_csv(channels, analysis_dir / "channel_summary.csv",
              ["lang", "channel_group", "sessions", "share_pct"])
    print(f"Saved analysis tables to {analysis_dir}")

    # Dashboard JSON + CSV
    dash_data = build_dashboard_json(fact_rows, solutions, funnel, ratio, channels, start, end)
    dash_dir = ROOT / "dashboard" / "data"
    write_json(dash_data, dash_dir / "latest.json")
    write_csv(fact_rows, dash_dir / "latest.csv", FACT_FIELDS)
    print(f"Saved dashboard data: {dash_dir}/latest.json + latest.csv")

    # 同时保存一份稳定的 fact CSV 供飞书同步
    write_csv(fact_rows, out_dir / "normalized" / "latest.csv", FACT_FIELDS)

    print("\n=== SUMMARY ===")
    for r in ratio:
        print(f"  {r['lang']}: solution {r['solution_pv']} / total {r['total_pv']} = {r['solution_share_pct']}%")
    print(f"\nTop solutions:")
    for s in sorted(solutions, key=lambda x: -x["screen_page_views"])[:5]:
        print(f"  {s['slug']} [{s['lang']}]: {s['screen_page_views']} pv, {s['avg_engagement_seconds']}s avg")


def _mock_rows(start: str, end: str) -> List[dict]:
    """生成模拟数据用于无网络环境测试。"""
    import random
    rng = random.Random(42)
    hosts = list(HOST_LANG.keys())
    slugs = ["smart-agriculture-sensing", "building-energy-management",
             "conversational-voice-ai", "environment-monitoring",
             "indoor-outdoor-positioning", "smart-livestock-farming"]
    channels = ["Organic Search", "Direct", "Referral", "Paid Search", "Organic Social"]
    devices = ["desktop", "mobile", "tablet"]
    rows = []
    current = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    while current <= end_d:
        for host in hosts:
            lang = HOST_LANG[host]
            for slug in slugs:
                for channel in channels:
                    pv = rng.randint(0, 50)
                    if pv == 0:
                        continue
                    sessions = max(1, int(pv * rng.uniform(0.5, 0.9)))
                    rows.append({
                        "date": current.strftime("%Y%m%d"),
                        "hostName": host,
                        "pagePath": f"/solutions/{slug}" + ("-zh-hans" if lang == "CN" and rng.random() > 0.5 else ""),
                        "pageTitle": slug.replace("-", " ").title(),
                        "sessionDefaultChannelGroup": channel,
                        "deviceCategory": rng.choice(devices),
                        "screenPageViews": pv,
                        "activeUsers": max(1, int(pv * 0.6)),
                        "sessions": sessions,
                        "engagementRate": round(rng.uniform(0.3, 0.8), 4),
                        "bounceRate": round(rng.uniform(0.2, 0.6), 4),
                        "userEngagementDuration": round(sessions * rng.uniform(30, 120), 2),
                        "keyEvents": rng.randint(0, 5),
                        "totalRevenue": 0.0,
                    })
            # category page
            rows.append({
                "date": current.strftime("%Y%m%d"),
                "hostName": host,
                "pagePath": "/category/solutions" + ("-zh-hans" if lang == "CN" else ""),
                "pageTitle": "Solutions",
                "sessionDefaultChannelGroup": rng.choice(channels),
                "deviceCategory": rng.choice(devices),
                "screenPageViews": rng.randint(20, 100),
                "activeUsers": rng.randint(10, 60),
                "sessions": rng.randint(15, 70),
                "engagementRate": round(rng.uniform(0.3, 0.8), 4),
                "bounceRate": round(rng.uniform(0.2, 0.6), 4),
                "userEngagementDuration": round(50 * rng.uniform(30, 120), 2),
                "keyEvents": 0,
                "totalRevenue": 0.0,
            })
        current += dt.timedelta(days=1)
    return rows


if __name__ == "__main__":
    main()
