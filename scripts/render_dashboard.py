#!/usr/bin/env python3
"""
render_dashboard.py — 从 dashboard/data/latest.json 渲染自包含 HTML dashboard。

面板：KPI 总览 → 日趋势折线 → 渠道分布 → 方案 CN/EN/JP 对比表 → 漏斗分层 → 占比仪表 → 渠道明细
"""
import json
import html
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "dashboard" / "data" / "latest.json"
OUT_PATH = ROOT / "dashboard" / "index.html"


def render():
    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    payload = json.dumps(data, ensure_ascii=False)
    summary = data.get("summary", {})
    solutions = data.get("solution_summary", [])
    funnel = data.get("funnel_summary", [])
    ratio = data.get("ratio_summary", [])
    channels = data.get("channel_summary", [])
    date_range = data.get("date_range", {})
    generated = data.get("generated_at", "")

    # 方案对比表行（按 pv 降序，合并三语言）
    slug_pvs = {}
    for s in solutions:
        slug_pvs.setdefault(s["slug"], {"EN": 0, "CN": 0, "JP": 0, "total": 0})
        slug_pvs[s["slug"]][s["lang"]] = s["screen_page_views"]
        slug_pvs[s["slug"]]["total"] += s["screen_page_views"]
    solution_rows = sorted(slug_pvs.items(), key=lambda x: -x[1]["total"])

    solution_html = ""
    for slug, pv in solution_rows[:15]:
        solution_html += f"<tr><td>{html.escape(slug)}</td><td>{pv['EN']:,}</td><td>{pv['CN']:,}</td><td>{pv['JP']:,}</td><td><b>{pv['total']:,}</b></td></tr>\n"

    # 漏斗表
    funnel_html = ""
    layers = {}
    for f in funnel:
        layers.setdefault(f["layer"], {})
        layers[f["layer"]][f["lang"]] = f["screen_page_views"]
    for layer in ("category", "list", "lora", "solutions"):
        v = layers.get(layer, {})
        funnel_html += f"<tr><td>{layer}</td><td>{v.get('EN',0):,}</td><td>{v.get('CN',0):,}</td><td>{v.get('JP',0):,}</td></tr>\n"

    # 占比
    ratio_html = ""
    for r in ratio:
        ratio_html += f"<tr><td>{r['lang']}</td><td>{r['solution_pv']:,}</td><td>{r['total_pv']:,}</td><td><b>{r['solution_share_pct']}%</b></td></tr>\n"

    # 渠道明细
    channel_html = ""
    for c in channels[:20]:
        channel_html += f"<tr><td>{c['lang']}</td><td>{html.escape(c['channel_group'])}</td><td>{c['sessions']:,}</td><td>{c['share_pct']}%</td></tr>\n"

    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seeed 官网流量 Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
:root {{ --bg:#f6f7f9; --panel:#fff; --ink:#17202a; --muted:#667085; --line:#d9dee7; --blue:#2f6fed; --green:#0f9f6e; --red:#cf423b; --gold:#b7791f; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--ink); }}
header {{ padding:24px 28px 12px; border-bottom:1px solid var(--line); background:var(--panel); }}
h1 {{ margin:0 0 4px; font-size:22px; }}
.sub {{ color:var(--muted); font-size:13px; }}
main {{ padding:20px 28px 40px; display:grid; gap:16px; }}
.kpis {{ display:grid; grid-template-columns:repeat(5,minmax(140px,1fr)); gap:12px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:16px; }}
.label {{ color:var(--muted); font-size:12px; margin-bottom:6px; }}
.value {{ font-size:22px; font-weight:700; }}
.grid2 {{ display:grid; grid-template-columns:2fr 1fr; gap:16px; }}
.grid3 {{ display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }}
canvas {{ width:100%; min-height:260px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ padding:8px 6px; border-bottom:1px solid var(--line); text-align:left; }}
th {{ color:var(--muted); font-weight:600; }}
td b {{ color:var(--blue); }}
@media(max-width:900px) {{ header,main {{ padding-left:16px; padding-right:16px; }} .kpis,.grid2,.grid3 {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header>
<h1>Seeed 官网流量 Dashboard</h1>
<div class="sub">区间：{date_range.get('start','?')} → {date_range.get('end','?')} · 属性 502086217 · 生成于 {generated}</div>
</header>
<main>
<section class="kpis">
<div class="card"><div class="label">总浏览量</div><div class="value" id="views"></div></div>
<div class="card"><div class="label">活跃用户</div><div class="value" id="users"></div></div>
<div class="card"><div class="label">会话数</div><div class="value" id="sessions"></div></div>
<div class="card"><div class="label">关键事件</div><div class="value" id="events"></div></div>
<div class="card"><div class="label">方案占比</div><div class="value" id="share"></div></div>
</section>
<section class="grid2">
<div class="card"><div class="label">日浏览趋势</div><canvas id="dayChart"></canvas></div>
<div class="card"><div class="label">渠道分布</div><canvas id="channelChart"></canvas></div>
</section>
<section class="grid3">
<div class="card"><div class="label">方案区占全站比例</div><table><thead><tr><th>语言</th><th>方案PV</th><th>全站PV</th><th>占比</th></tr></thead><tbody>{ratio_html}</tbody></table></div>
<div class="card"><div class="label">漏斗分层 PV</div><table><thead><tr><th>层级</th><th>EN</th><th>CN</th><th>JP</th></tr></thead><tbody>{funnel_html}</tbody></table></div>
<div class="card"><div class="label">方案页流量来源</div><table><thead><tr><th>语言</th><th>渠道</th><th>会话</th><th>占比</th></tr></thead><tbody>{channel_html}</tbody></table></div>
</section>
<section class="card">
<div class="label">方案页 CN/EN/JP 对比（按总PV降序）</div>
<table><thead><tr><th>方案 Slug</th><th>EN PV</th><th>CN PV</th><th>JP PV</th><th>合计</th></tr></thead><tbody>{solution_html}</tbody></table>
</section>
</main>
<script>
const data = {payload};
const fmt = new Intl.NumberFormat("en-US");
const s = data.summary;
document.getElementById("views").textContent = fmt.format(s.total_views);
document.getElementById("users").textContent = fmt.format(s.active_users);
document.getElementById("sessions").textContent = fmt.format(s.sessions);
document.getElementById("events").textContent = fmt.format((data.solution_summary||[]).reduce((a,b)=>a+(b.key_events||0),0));
const ratio = data.ratio_summary||[];
const avgShare = ratio.length ? (ratio.reduce((a,b)=>a+b.solution_share_pct,0)/ratio.length).toFixed(1)+"%" : "—";
document.getElementById("share").textContent = avgShare;
new Chart(document.getElementById("dayChart"), {{
type:"line",
data:{{labels:Object.keys(s.by_day),datasets:[{{label:"Views",data:Object.values(s.by_day),borderColor:"#2f6fed",backgroundColor:"rgba(47,111,237,.12)",tension:.25,fill:true}}]}},
options:{{responsive:true,plugins:{{legend:{{display:false}}}}}}
}});
new Chart(document.getElementById("channelChart"), {{
type:"bar",
data:{{labels:Object.keys(s.by_channel).slice(0,10),datasets:[{{label:"Views",data:Object.values(s.by_channel).slice(0,10),backgroundColor:"#0f9f6e"}}]}},
options:{{responsive:true,plugins:{{legend:{{display:false}}}},indexAxis:"y"}}
}});
</script>
</body>
</html>"""

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(html_text, encoding="utf-8")
    print(f"Rendered: {OUT_PATH}")


if __name__ == "__main__":
    render()
