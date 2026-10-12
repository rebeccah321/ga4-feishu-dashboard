#!/usr/bin/env python3
"""Pull 28-day traffic + CTA/form events for 12 solutions."""
import csv, json, os, subprocess, sys, datetime as dt, re, urllib.request
from collections import defaultdict
from pathlib import Path
PROPERTY = "502086217"
QUOTA_PROJECT = "my-project-1579296929285"
ENDPOINT = f"https://analyticsdata.googleapis.com/v1beta/properties/{PROPERTY}:runReport"
ROOT = Path(__file__).resolve().parents[1]
SLUG_MAP = {
    "voicecollectionanalysis": "语音采集与分析",
    "smart-warehouse-management": "智能仓储管理",
    "intelligent-video-analytics": "智能视频分析",
    "indoor-outdoor-positioning": "室内外定位",
    "conversational-voice-ai": "对话式语音AI",
    "environment-monitoring": "环境监测",
    "building-energy-retrofit": "楼宇能源改造",
    "smart-livestock-farming": "智能畜牧养殖",
    "smart-agriculture-sensing": "智慧农业传感",
    "campus-safety-management": "校园安全管理",
    "hazard-response": "应急响应",
    "building-energy-management": "楼宇能源管理",
    "off-grid-mesh-communication": "离网通信",
    "acoustic-audio-ai": "声学与音频AI",
    "home-building-automation": "家庭与楼宇自动化",
}
SLUG_RE = re.compile(r'^/solutions/([a-z0-9-]+?)(?:-zh-hans)?/?$')
CTA_EVENTS = ["click", "click_solution_link", "click_contact_expert"]
FORM_EVENTS = ["form_submit_success", "form_submit", "sample_request_submit"]
def get_token():
    env = os.environ.copy()
    env["CLOUDSDK_LOG_DIR"] = "/tmp/gcloud-logs"
    r = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True, text=True, timeout=30, env=env)
    tok = r.stdout.strip()
    if tok: return tok
    raise SystemExit(f"gcloud token failed: {r.stderr.strip()}")
def ga4_query(token, dimensions, metrics, start, end, dim_filter=None):
    body = {
        "dateRanges": [{"startDate": start, "endDate": end}],
        "dimensions": [{"name": d} for d in dimensions],
        "metrics": [{"name": m} for m in metrics],
        "limit": 100000,
    }
    if dim_filter:
        body["dimensionFilter"] = dim_filter
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}",
                  "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())
def parse_rows(data, dim_names, met_names):
    rows = []
    for r in data.get("rows", []):
        row = {}
        for i, d in enumerate(dim_names):
            row[d] = r["dimensionValues"][i]["value"]
        for i, m in enumerate(met_names):
            row[m] = float(r["metricValues"][i]["value"])
        rows.append(row)
    return rows
def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    args = p.parse_args()
    today = dt.date.today()
    if args.start and args.end:
        start_s = args.start
        end_s = args.end
    else:
        end = today - dt.timedelta(days=1)
        start = end - dt.timedelta(days=27)
        start_s = start.strftime("%Y-%m-%d")
        end_s = end.strftime("%Y-%m-%d")
    print(f"Date range: {start_s} -> {end_s}")
    token = get_token()
    print("Got token, querying GA4...")
    # Query 1: traffic by page
    print("Query 1: page traffic...")
    traffic_data = ga4_query(token,
        ["date","hostName","pagePath","pageTitle","sessionDefaultChannelGroup"],
        ["screenPageViews","activeUsers","sessions","engagementRate","bounceRate","userEngagementDuration","keyEvents"],
        start_s, end_s)
    traffic_rows = parse_rows(traffic_data,
        ["date","hostName","pagePath","pageTitle","sessionDefaultChannelGroup"],
        ["screenPageViews","activeUsers","sessions","engagementRate","bounceRate","userEngagementDuration","keyEvents"])
    print(f"  Got {len(traffic_rows)} traffic rows")
    # Query 2: events on solution pages
    print("Query 2: CTA + form events on solution pages...")
    event_filter = {
        "andGroup": {
            "expressions": [
                {"filter": {"fieldName": "pagePath", "stringFilter": {"matchType": "CONTAINS", "value": "/solutions/"}}},
                {"filter": {"fieldName": "eventName", "inListFilter": {"values": CTA_EVENTS + FORM_EVENTS}}}
            ]
        }
    }
    event_data = ga4_query(token,
        ["date","hostName","pagePath","eventName"],
        ["eventCount"],
        start_s, end_s, dim_filter=event_filter)
    event_rows = parse_rows(event_data,
        ["date","hostName","pagePath","eventName"],
        ["eventCount"])
    print(f"  Got {len(event_rows)} event rows")
    # Aggregate traffic by solution (merge languages + zh-hans)
    sol_traffic = defaultdict(lambda: {"pv":0,"users":0,"sessions":0,"eng_sec":0.0,"eng_rate":0.0,"key_events":0})
    for r in traffic_rows:
        m = SLUG_RE.match(r["pagePath"])
        if not m: continue
        slug = m.group(1)
        if slug not in SLUG_MAP: continue
        d = sol_traffic[slug]
        pv = int(r["screenPageViews"])
        d["pv"] += pv
        d["users"] += int(r["activeUsers"])
        d["sessions"] += int(r["sessions"])
        d["eng_sec"] += float(r["userEngagementDuration"])
        d["eng_rate"] += float(r["engagementRate"]) * pv
        d["key_events"] += int(r["keyEvents"])
    # Aggregate events by solution
    sol_events = defaultdict(lambda: {"cta":0,"form":0})
    for r in event_rows:
        m = SLUG_RE.match(r["pagePath"])
        if not m: continue
        slug = m.group(1)
        if slug not in SLUG_MAP: continue
        ev = r["eventName"]
        cnt = int(r["eventCount"])
        if ev in CTA_EVENTS:
            sol_events[slug]["cta"] += cnt
        elif ev in FORM_EVENTS:
            sol_events[slug]["form"] += cnt
    # Also get channel breakdown per solution
    sol_channels = defaultdict(lambda: defaultdict(int))
    for r in traffic_rows:
        m = SLUG_RE.match(r["pagePath"])
        if not m: continue
        slug = m.group(1)
        if slug not in SLUG_MAP: continue
        sol_channels[slug][r["sessionDefaultChannelGroup"]] += int(r["sessions"])
    # Output CSV
    out_dir = ROOT / "data" / "analysis"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"solution_{start_s}_to_{end_s}.csv"
    fields = ["方案名称","采样周","访问量","独立访客","会话数","平均停留s","参与率","关键事件","CTA点击","表单提交","页面->CTA转化率","CTA->表单转化率","主要流量来源"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for slug, name in sorted(SLUG_MAP.items(), key=lambda x: x[1]):
            t = sol_traffic[slug]
            e = sol_events[slug]
            avg_sec = t["eng_sec"]/t["sessions"] if t["sessions"] else 0
            avg_rate = t["eng_rate"]/t["pv"] if t["pv"] else 0
            cta_rate = e["cta"]/t["pv"]*100 if t["pv"] else 0
            form_rate = e["form"]/e["cta"]*100 if e["cta"] else 0
            top_ch = sorted(sol_channels[slug].items(), key=lambda x:-x[1])[:3]
            top_ch_str = ", ".join(f"{c}({n})" for c,n in top_ch) if top_ch else "N/A"
            w.writerow({
                "方案名称": name, "采样周": f"{start_s}~{end_s}",
                "访问量": t["pv"], "独立访客": t["users"], "会话数": t["sessions"],
                "平均停留s": round(avg_sec,1), "参与率": f"{avg_rate:.1%}",
                "关键事件": t["key_events"], "CTA点击": e["cta"], "表单提交": e["form"],
                "页面->CTA转化率": f"{cta_rate:.1f}%", "CTA->表单转化率": f"{form_rate:.1f}%",
                "主要流量来源": top_ch_str,
            })
    print(f"\nSaved: {out_path}")
    # Print summary
    print(f"\n{'方案名称':<12} {'访问量':>6} {'独立访客':>6} {'CTA':>5} {'表单':>4} {'主要来源'}")
    print("-"*70)
    for slug, name in sorted(SLUG_MAP.items(), key=lambda x: x[1]):
        t = sol_traffic[slug]
        e = sol_events[slug]
        top_ch = sorted(sol_channels[slug].items(), key=lambda x:-x[1])[:1]
        ch_str = f"{top_ch[0][0]}({top_ch[0][1]})" if top_ch else "N/A"
        print(f"{name:<12} {t['pv']:>6} {t['users']:>6} {e['cta']:>5} {e['form']:>4} {ch_str}")
    print("-"*70)
    print(f"{'合计':<12} {sum(t['pv'] for t in sol_traffic.values()):>6} {sum(t['users'] for t in sol_traffic.values()):>6} {sum(e['cta'] for e in sol_events.values()):>5} {sum(e['form'] for e in sol_events.values()):>4}")
if __name__ == "__main__":
    main()
