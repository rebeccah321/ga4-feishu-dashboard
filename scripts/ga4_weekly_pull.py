#!/usr/bin/env python3
"""Pull GA4 data grouped by ISO week (Mon-Sun) per solution."""
import csv, json, os, subprocess, sys, datetime as dt, re, urllib.request
from collections import defaultdict
from pathlib import Path

PROPERTY = "502086217"
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

def week_monday(d):
    return d - dt.timedelta(days=d.weekday())

def week_label(d):
    ws = week_monday(d)
    we = ws + dt.timedelta(days=6)
    iso = ws.isocalendar()[1]
    return f"W{iso} {ws.strftime('%m/%d')}~{we.strftime('%m/%d')}"

def get_token():
    env = os.environ.copy()
    env["CLOUDSDK_LOG_DIR"] = "/tmp/gcloud-logs"
    r = subprocess.run(["gcloud","auth","application-default","print-access-token"],
        capture_output=True, text=True, timeout=30, env=env)
    tok = r.stdout.strip()
    if tok: return tok
    raise SystemExit(f"Token failed: {r.stderr.strip()}")

def ga4_query(token, dims, mets, start, end, dim_filter=None):
    body = {"dateRanges":[{"startDate":start,"endDate":end}],
            "dimensions":[{"name":d} for d in dims],
            "metrics":[{"name":m} for m in mets], "limit":100000}
    if dim_filter: body["dimensionFilter"] = dim_filter
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
        headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())

def parse(data, dim_names, met_names):
    rows = []
    for r in data.get("rows",[]):
        row = {}
        for i,d in enumerate(dim_names): row[d] = r["dimensionValues"][i]["value"]
        for i,m in enumerate(met_names): row[m] = float(r["metricValues"][i]["value"])
        rows.append(row)
    return rows

def main():
    start_s, end_s = "2026-06-01", "2026-08-11"
    print(f"Range: {start_s} ~ {end_s}")
    token = get_token()

    # Traffic
    print("Query 1: traffic...")
    td = ga4_query(token,
        ["date","hostName","pagePath","sessionDefaultChannelGroup"],
        ["screenPageViews","activeUsers","sessions","engagementRate","userEngagementDuration","keyEvents"],
        start_s, end_s)
    tr = parse(td, ["date","hostName","pagePath","sessionDefaultChannelGroup"],
        ["screenPageViews","activeUsers","sessions","engagementRate","userEngagementDuration","keyEvents"])
    print(f"  {len(tr)} rows")

    # Events
    print("Query 2: CTA+form events...")
    ef = {"andGroup":{"expressions":[
        {"filter":{"fieldName":"pagePath","stringFilter":{"matchType":"CONTAINS","value":"/solutions/"}}},
        {"filter":{"fieldName":"eventName","inListFilter":{"values":CTA_EVENTS+FORM_EVENTS}}}]}}
    ed = ga4_query(token, ["date","pagePath","eventName"], ["eventCount"], start_s, end_s, ef)
    er = parse(ed, ["date","pagePath","eventName"], ["eventCount"])
    print(f"  {len(er)} rows")

    # Aggregate by week + solution
    wk_traffic = defaultdict(lambda: {"pv":0,"users":0,"sessions":0,"eng_dur":0.0,"eng_rate":0.0,"key":0})
    wk_channels = defaultdict(lambda: defaultdict(int))
    for r in tr:
        m = SLUG_RE.match(r["pagePath"])
        if not m: continue
        slug = m.group(1)
        if slug not in SLUG_MAP: continue
        d = dt.datetime.strptime(r["date"], "%Y%m%d").date()
        wl = week_label(d)
        key = (wl, slug)
        pv = int(r["screenPageViews"])
        t = wk_traffic[key]
        t["pv"] += pv
        t["users"] += int(r["activeUsers"])
        t["sessions"] += int(r["sessions"])
        t["eng_dur"] += float(r["userEngagementDuration"])
        t["eng_rate"] += float(r["engagementRate"]) * pv
        t["key"] += int(r["keyEvents"])
        wk_channels[key][r["sessionDefaultChannelGroup"]] += int(r["sessions"])

    wk_events = defaultdict(lambda: {"cta":0,"form":0})
    for r in er:
        m = SLUG_RE.match(r["pagePath"])
        if not m: continue
        slug = m.group(1)
        if slug not in SLUG_MAP: continue
        d = dt.datetime.strptime(r["date"], "%Y%m%d").date()
        wl = week_label(d)
        key = (wl, slug)
        ev = r["eventName"]
        cnt = int(r["eventCount"])
        if ev in CTA_EVENTS: wk_events[key]["cta"] += cnt
        elif ev in FORM_EVENTS: wk_events[key]["form"] += cnt

    # Output CSV
    out = ROOT / "data" / "analysis" / "solution_weekly_0601_0811.csv"
    fields = ["周次","方案名称","访问量","独立访客","会话数","平均停留s","参与率","关键事件","CTA点击","表单提交","页面->CTA转化率","CTA->表单转化率","主要流量来源"]
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        # Sort by week start date then solution name
        sorted_keys = sorted(wk_traffic.keys(), key=lambda x: (x[0].split()[1].split('~')[0], x[1]))
        for wl, slug in sorted_keys:
            t = wk_traffic[(wl,slug)]
            e = wk_events[(wl,slug)]
            avg_sec = t["eng_dur"]/t["sessions"] if t["sessions"] else 0
            avg_rate = t["eng_rate"]/t["pv"] if t["pv"] else 0
            cta_rate = e["cta"]/t["pv"]*100 if t["pv"] else 0
            form_rate = e["form"]/e["cta"]*100 if e["cta"] else 0
            top = sorted(wk_channels[(wl,slug)].items(), key=lambda x:-x[1])[:3]
            ch_str = ", ".join(f"{c}({n})" for c,n in top) if top else "N/A"
            w.writerow({"周次":wl,"方案名称":SLUG_MAP[slug],"访问量":t["pv"],"独立访客":t["users"],
                "会话数":t["sessions"],"平均停留s":round(avg_sec,1),"参与率":f"{avg_rate:.1%}",
                "关键事件":t["key"],"CTA点击":e["cta"],"表单提交":e["form"],
                "页面->CTA转化率":f"{cta_rate:.1f}%","CTA->表单转化率":f"{form_rate:.1f}%",
                "主要流量来源":ch_str})
    print(f"\nSaved: {out}")

    # Print summary: month 1 vs month 2
    m1_pv = sum(t["pv"] for (wl,s),t in wk_traffic.items() if wl.startswith("W2") and int(wl.split()[1].split('~')[0].split('/')[1])<=28)
    # Actually let me just calculate by date ranges
    m1_keys = [(wl,s) for (wl,s) in wk_traffic if "06/01"<=wl.split()[1].split('~')[0]<="06/28" or True]  # simplify below

    # Month 1: 6/1-6/28, Month 2: 6/29-8/2, Partial: 8/3-8/8
    def in_range(wl, range_start, range_end):
        ws = wl.split()[1].split('~')[0]  # MM/DD
        we = wl.split()[1].split('~')[1]  # MM/DD
        # Compare as MM-DD strings
        return range_start <= ws <= range_end or range_start <= we <= range_end

    m1 = defaultdict(lambda: {"pv":0,"users":0,"sessions":0,"eng_dur":0.0,"eng_rate":0.0,"key":0})
    m2 = defaultdict(lambda: {"pv":0,"users":0,"sessions":0,"eng_dur":0.0,"eng_rate":0.0,"key":0})
    p3 = defaultdict(lambda: {"pv":0,"users":0,"sessions":0,"eng_dur":0.0,"eng_rate":0.0,"key":0})
    m1e = defaultdict(lambda: {"cta":0,"form":0})
    m2e = defaultdict(lambda: {"cta":0,"form":0})
    p3e = defaultdict(lambda: {"cta":0,"form":0})

    for (wl, slug), t in wk_traffic.items():
        ws = wl.split()[1].split('~')[0]  # MM/DD
        if ws <= "06/28":
            target, target_e = m1, m1e
        elif ws <= "08/02":
            target, target_e = m2, m2e
        else:
            target, target_e = p3, p3e
        for k,v in t.items(): target[slug][k] += v
        e = wk_events[(wl,slug)]
        target_e[slug]["cta"] += e["cta"]
        target_e[slug]["form"] += e["form"]

    print("\n=== 月度对比 ===")
    print(f"{'方案':<12} {'6/1-6/28 PV':>10} {'6/29-8/2 PV':>11} {'8/3-8/8 PV':>10} {'6/1-6/28 CTA':>12} {'6/29-8/2 CTA':>12} {'8/3-8/8 CTA':>11}")
    print("-"*82)
    for slug, name in sorted(SLUG_MAP.items(), key=lambda x: x[1]):
        print(f"{name:<12} {m1[slug]['pv']:>10} {m2[slug]['pv']:>11} {p3[slug]['pv']:>10} {m1e[slug]['cta']:>12} {m2e[slug]['cta']:>12} {p3e[slug]['cta']:>11}")
    print("-"*82)
    print(f"{'合计':<12} {sum(v['pv'] for v in m1.values()):>10} {sum(v['pv'] for v in m2.values()):>11} {sum(v['pv'] for v in p3.values()):>10} {sum(v['cta'] for v in m1e.values()):>12} {sum(v['cta'] for v in m2e.values()):>12} {sum(v['cta'] for v in p3e.values()):>11}")

if __name__ == "__main__":
    main()
