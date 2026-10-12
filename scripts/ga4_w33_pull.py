#!/usr/bin/env python3
"""Pull W33 (08/10~08/11) data for 12 solutions."""
import csv, json, os, subprocess, datetime as dt, re, urllib.request
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
CTA_EVENTS = ["click","click_solution_link","click_contact_expert"]
FORM_EVENTS = ["form_submit_success","form_submit","sample_request_submit"]

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
    start_s, end_s = "2026-08-10", "2026-08-11"
    wk_label = "W33 08/10~08/16"
    wk_end = "08/16"
    print(f"Range: {start_s} ~ {end_s} (W33, partial)")
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

    # Aggregate
    sol_traffic = defaultdict(lambda: {"pv":0,"users":0,"sessions":0,"eng_dur":0.0,"eng_rate":0.0,"key":0})
    sol_channels = defaultdict(lambda: defaultdict(int))
    for r in tr:
        m = SLUG_RE.match(r["pagePath"])
        if not m: continue
        slug = m.group(1)
        if slug not in SLUG_MAP: continue
        pv = int(r["screenPageViews"])
        d = sol_traffic[slug]
        d["pv"] += pv
        d["users"] += int(r["activeUsers"])
        d["sessions"] += int(r["sessions"])
        d["eng_dur"] += float(r["userEngagementDuration"])
        d["eng_rate"] += float(r["engagementRate"]) * pv
        d["key"] += int(r["keyEvents"])
        sol_channels[slug][r["sessionDefaultChannelGroup"]] += int(r["sessions"])

    sol_events = defaultdict(lambda: {"cta":0,"form":0})
    for r in er:
        m = SLUG_RE.match(r["pagePath"])
        if not m: continue
        slug = m.group(1)
        if slug not in SLUG_MAP: continue
        ev = r["eventName"]
        cnt = int(r["eventCount"])
        if ev in CTA_EVENTS: sol_events[slug]["cta"] += cnt
        elif ev in FORM_EVENTS: sol_events[slug]["form"] += cnt

    # Total for overview
    total_pv = sum(t["pv"] for t in sol_traffic.values())
    total_users = sum(t["users"] for t in sol_traffic.values())
    total_key = sum(t["key"] for t in sol_traffic.values())
    cta_rate = total_key / total_pv * 100 if total_pv else 0

    # Prev week (W32) for growth calc
    prev_total_pv = 855  # from existing data
    growth = (total_pv - prev_total_pv) / prev_total_pv * 100 if prev_total_pv else 0

    # Top solution
    top_sol = max(sol_traffic.items(), key=lambda x: x[1]["pv"])

    # Print overview row
    print(f"\n=== 方案增长总览表 - W33 新增行 ===")
    print(f"| {wk_label} | {wk_end} | {total_users} | {total_pv} | {total_key} | {cta_rate:.2f}% | {growth:+.2f}% | {SLUG_MAP[top_sol[0]]} | N/A(首日) |")

    # Save overview CSV
    out1 = ROOT / "data" / "analysis" / "overview_w33.csv"
    with open(out1, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["周次","截止日期","方案页独立访客数","solution页面总访问量","关键事件次数(CTA)","CTA转化率","Solution页面周环比","流量最高方案","增长最快solution"])
        w.writerow([wk_label, wk_end, total_users, total_pv, total_key, f"{cta_rate:.2f}%", f"{growth:+.2f}%", SLUG_MAP[top_sol[0]], "N/A(首日)"])

    # Print detail rows
    print(f"\n=== 单方案流量明细表 - W33 新增12行 ===")
    sorted_sols = sorted(sol_traffic.items(), key=lambda x: -x[1]["pv"])
    out2 = ROOT / "data" / "analysis" / "detail_w33.csv"
    with open(out2, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["最新周次","截止日期","solution名称","落地页访问量","独立访客数","session","平均停留时长（秒）","参与率","主要流量来源","CTA点击量（key event）","表单提交","数据状态"])
        for slug, t in sorted_sols:
            avg_sec = t["eng_dur"]/t["sessions"] if t["sessions"] else 0
            avg_rate = t["eng_rate"]/t["pv"] if t["pv"] else 0
            top_ch = max(sol_channels[slug].items(), key=lambda x: x[1]) if sol_channels[slug] else ("N/A",0)
            ch_str = f"{top_ch[0]}({top_ch[1]})"
            e = sol_events[slug]
            row = [wk_label, wk_end, SLUG_MAP[slug], t["pv"], t["users"], t["sessions"],
                   round(avg_sec,1), f"{avg_rate:.2f}%", ch_str, t["key"], e["form"], "本周（未完整）"]
            print(f"| {wk_label} | {wk_end} | {SLUG_MAP[slug]} | {t['pv']} | {t['users']} | {t['sessions']} | {round(avg_sec,1)} | {avg_rate:.2f}% | {ch_str} | {t['key']} | {e['form']} | 本周（未完整） |")
            w.writerow(row)

    print(f"\nSaved: {out1}")
    print(f"Saved: {out2}")

if __name__ == "__main__":
    main()
