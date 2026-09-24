"""Dark dashboard at out/index.html — always renders the latest report stored in the DB.

Self-contained: inline CSS, vendored Chart.js copied next to index.html, no CDN dependency.
The dated out/<date>.html files remain the plain email-style archives; this page is the
live overview: memo, flags, KPIs, graphs, and everything divided by entity.
"""
from __future__ import annotations

import json
import shutil
from datetime import date, timedelta
from pathlib import Path

import markdown

from . import config, db

_CHART_JS_SRC = Path(__file__).parent / "assets" / "chart.umd.min.js"
_CHART_JS_CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"

# One color per entity, assigned in display order and reused across every chart.
PALETTE = ["#6e9bff", "#3ecf8e", "#f5a623", "#f472b6", "#a78bfa", "#4dd0e1", "#ff7a59", "#b8e986"]

CSS = """
:root{--bg:#0b0e13;--card:#131822;--card2:#171e2b;--line:#232b3a;--text:#e2e8f0;--muted:#8b98ac;
      --red:#ff7a72;--green:#3ecf8e;--amber:#f5a623;--accent:#6e9bff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
     font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:28px 20px 60px}
header.top{display:flex;flex-wrap:wrap;align-items:baseline;gap:10px;margin-bottom:18px}
header.top h1{font-size:22px;margin:0;letter-spacing:.2px}
header.top .sub{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 20px;
      margin-bottom:18px;box-shadow:0 1px 2px rgba(0,0,0,.25)}
.card>h2{margin:0 0 12px;font-size:13px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);font-weight:600}
/* memo */
.memo h2{font-size:16px;margin:20px 0 8px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.memo h2:first-child{margin-top:0}
.memo table{border-collapse:collapse;width:100%;font-size:13px;margin:8px 0}
.memo th,.memo td{padding:5px 10px;border-bottom:1px solid var(--line);text-align:left}
.memo td:last-child,.memo th:last-child{text-align:right;font-variant-numeric:tabular-nums}
.memo th{color:var(--muted);font-weight:600}
.memo strong{color:#fff}
/* flags */
.flag{display:flex;gap:10px;align-items:flex-start;padding:8px 10px;border-radius:9px;margin-bottom:6px;
      background:var(--card2);font-size:13.5px}
.flag .dot{flex:0 0 auto;width:8px;height:8px;border-radius:50%;margin-top:7px}
.flag.warn .dot{background:var(--red);box-shadow:0 0 8px rgba(255,122,114,.6)}
.flag.info .dot{background:var(--accent)}
.flag .ent{color:var(--muted);font-size:12px;background:rgba(255,255,255,.05);padding:1px 8px;border-radius:20px;
           margin-right:6px;white-space:nowrap}
/* KPI grid */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:18px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 18px}
.kpi .label{font-size:11.5px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted)}
.kpi .value{font-size:24px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi .delta{font-size:12.5px;margin-top:2px;font-variant-numeric:tabular-nums}
.pos{color:var(--green)}.neg{color:var(--red)}.muted{color:var(--muted)}
/* charts */
.charts{display:grid;grid-template-columns:2fr 1fr;gap:18px}
@media(max-width:900px){.charts{grid-template-columns:1fr}}
.chartbox{position:relative;height:280px}
/* entity sections */
.ent-head{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.ent-head .swatch{width:11px;height:11px;border-radius:3px}
.ent-head h3{margin:0;font-size:16px}
.ent-stats{display:flex;flex-wrap:wrap;gap:22px;margin:8px 0 14px;font-size:13.5px}
.ent-stats b{display:block;font-size:17px;font-variant-numeric:tabular-nums}
.ent-stats span{font-size:11.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
.ent-grid{display:grid;grid-template-columns:1.5fr 1fr;gap:18px;align-items:start}
@media(max-width:800px){.ent-grid{grid-template-columns:1fr}}
table.accts{border-collapse:collapse;width:100%;font-size:13px}
table.accts th,table.accts td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:left}
table.accts th{color:var(--muted);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}
table.accts td.num,table.accts th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
table.accts td:last-child,table.accts th:last-child{white-space:nowrap}
.donut{position:relative;height:230px}
/* transactions */
.chips{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px}
.chip{border:1px solid var(--line);background:var(--card2);color:var(--muted);border-radius:20px;
      padding:4px 14px;font-size:12.5px;cursor:pointer}
.chip.active{background:var(--accent);border-color:var(--accent);color:#0b0e13;font-weight:600}
.txscroll{max-height:520px;overflow-y:auto;border:1px solid var(--line);border-radius:10px}
table.txns{border-collapse:collapse;width:100%;font-size:13px}
table.txns th{position:sticky;top:0;background:var(--card2);color:var(--muted);font-size:11.5px;
              text-transform:uppercase;letter-spacing:.06em;padding:8px 10px;text-align:left;z-index:1}
table.txns td{padding:6px 10px;border-bottom:1px solid var(--line)}
table.txns td:first-child,table.txns td:nth-child(3){white-space:nowrap}
table.txns td.num,table.txns th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.transfer td{color:var(--muted)}
tr.transfer td.amt{color:var(--muted)!important}
/* archive */
.archive{display:flex;flex-wrap:wrap;gap:8px}
.archive a{color:var(--muted);border:1px solid var(--line);border-radius:8px;padding:4px 12px;
           font-size:12.5px;text-decoration:none}
.archive a:hover{color:var(--text);border-color:var(--accent)}
footer{color:var(--muted);font-size:12px;margin-top:26px;text-align:center}
"""

JS = """
const DATA = JSON.parse(document.getElementById('finsum-data').textContent);
Chart.defaults.color = '#93a0b4';
Chart.defaults.borderColor = 'rgba(148,163,184,0.10)';
Chart.defaults.font.family = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif";
const compact$ = v => (v<0?'-$':'$') + Intl.NumberFormat('en',{notation:'compact',maximumFractionDigits:1}).format(Math.abs(v));
const full$ = v => (v<0?'-$':'$') + Math.abs(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
const tip = {backgroundColor:'#0d1117',borderColor:'#2a3342',borderWidth:1,titleColor:'#e2e8f0',
             bodyColor:'#c3ccd9',padding:10,cornerRadius:8};

// Daily spend stacked by entity + income line
new Chart(document.getElementById('dailyChart'),{data:{labels:DATA.days,datasets:[
  ...DATA.entities.map((e,i)=>({type:'bar',label:e,data:DATA.daily_spend[e],stack:'spend',
      backgroundColor:DATA.colors[e],borderRadius:3,barPercentage:.65})),
  {type:'line',label:'income',data:DATA.daily_income,borderColor:'#3ecf8e',backgroundColor:'#3ecf8e',
   borderWidth:2,pointRadius:3,tension:.3}
]},options:{responsive:true,maintainAspectRatio:false,interaction:{mode:'index'},
  plugins:{legend:{position:'bottom',labels:{boxWidth:10,boxHeight:10,padding:14}},
    tooltip:{...tip,callbacks:{label:c=>' '+c.dataset.label+': '+full$(c.parsed.y)}}},
  scales:{x:{stacked:true,grid:{display:false}},
          y:{stacked:true,ticks:{callback:compact$},border:{display:false}}}}});

// Net position by entity
new Chart(document.getElementById('netChart'),{type:'bar',data:{labels:DATA.entities,
  datasets:[{data:DATA.entities.map(e=>DATA.net_worth[e]),
             backgroundColor:DATA.entities.map(e=>DATA.colors[e]),borderRadius:6,barPercentage:.6}]},
  options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false},tooltip:{...tip,callbacks:{label:c=>' '+full$(c.parsed.x)}}},
    scales:{x:{ticks:{callback:compact$},border:{display:false}},y:{grid:{display:false}}}}});

// One category donut per entity
for(const e of DATA.entities){
  const el=document.getElementById('donut-'+CSS.escape(e)); if(!el) continue;
  const cats=DATA.by_category[e];
  new Chart(el,{type:'doughnut',data:{labels:cats.map(c=>c[0]),
    datasets:[{data:cats.map(c=>c[1]),backgroundColor:cats.map((_,i)=>DATA.catColors[i%DATA.catColors.length]),
               borderColor:'#131822',borderWidth:2}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'58%',
      plugins:{legend:{position:'right',labels:{boxWidth:9,boxHeight:9,padding:8,font:{size:11}}},
        tooltip:{...tip,callbacks:{label:c=>' '+c.label+': '+full$(c.parsed)}}}}});
}

// Transaction entity filter
for(const chip of document.querySelectorAll('.chip'))
  chip.addEventListener('click',()=>{
    document.querySelectorAll('.chip').forEach(c=>c.classList.remove('active'));
    chip.classList.add('active');
    const f=chip.dataset.entity;
    for(const tr of document.querySelectorAll('table.txns tbody tr'))
      tr.style.display=(f==='*'||tr.dataset.entity===f)?'':'none';
  });
"""


def _money(v: float | None, sign: bool = False) -> str:
    if v is None:
        return '<span class="muted">—</span>'
    s = f"${abs(v):,.2f}"
    if sign:
        s = ("+" if v >= 0 else "−") + s
    elif v < 0:
        s = "−" + s
    return s


def _delta(v: float | None) -> str:
    if v is None:
        return '<span class="muted">—</span>'
    cls = "pos" if v >= 0 else "neg"
    return f'<span class="{cls}">{_money(v, sign=True)}</span>'


def _window_days(summary: dict) -> list[date]:
    start, end = date.fromisoformat(summary["week_start"]), date.fromisoformat(summary["week_end"])
    days = []
    while start <= end:
        days.append(start)
        start += timedelta(days=1)
    return days


def _entity_order(summary: dict) -> list[str]:
    """Companies by |net worth| desc; personal and unassigned go last."""
    ents = summary["entities"]
    def key(e: str):
        tail = 1 if e in ("personal", "unassigned") else 0
        return (tail, -abs(ents[e].get("net_worth") or 0), e)
    return sorted(ents, key=key)


def _chart_data(summary: dict, order: list[str]) -> dict:
    days = _window_days(summary)
    idx = {d.strftime("%a %b %d"): i for i, d in enumerate(days)}
    daily_spend = {e: [0.0] * len(days) for e in order}
    daily_income = [0.0] * len(days)
    for t in summary["transactions"]:
        i = idx.get(t["date"])
        if i is None or t["transfer"]:
            continue
        if t["amount"] < 0 and t["category"] not in config.NON_SPEND:
            daily_spend.setdefault(t["entity"], [0.0] * len(days))[i] += -t["amount"]
        elif t["amount"] > 0 and t["category"] in ("income_revenue", "refund"):
            daily_income[i] += t["amount"]
    by_category = {}
    for e in order:
        cats = sorted(summary["entities"][e].get("by_category", {}).items(), key=lambda kv: -kv[1])
        top, rest = cats[:8], cats[8:]
        if rest:
            top.append(("(other categories)", round(sum(v for _, v in rest), 2)))
        by_category[e] = [[k, round(v, 2)] for k, v in top]
    return {
        "days": [d.strftime("%a %b %d") for d in days],
        "entities": order,
        "colors": {e: PALETTE[i % len(PALETTE)] for i, e in enumerate(order)},
        "catColors": ["#6e9bff", "#3ecf8e", "#f5a623", "#f472b6", "#a78bfa", "#4dd0e1", "#ff7a59", "#8b98ac", "#5b6b82"],
        "daily_spend": {e: [round(v, 2) for v in vs] for e, vs in daily_spend.items()},
        "daily_income": [round(v, 2) for v in daily_income],
        "net_worth": {e: summary["entities"][e].get("net_worth") or 0 for e in order},
        "by_category": by_category,
    }


def _flags_html(flags: list[dict]) -> str:
    if not flags:
        return ""
    rows = "".join(
        f'<div class="flag {f["severity"]}"><span class="dot"></span><div>'
        f'<span class="ent">{f["entity"]}</span>{f["text"]}</div></div>'
        for f in flags)
    return f'<div class="card"><h2>Flags</h2>{rows}</div>'


def _entity_html(summary: dict, order: list[str], colors: dict) -> str:
    out = []
    for e in order:
        d = summary["entities"][e]
        has7 = any(a["delta_7d"] is not None for a in d["accounts"])
        has30 = any(a["delta_30d"] is not None for a in d["accounts"])
        d7 = (f'<b class="{"pos" if d["net_7d"] >= 0 else "neg"}">{_money(d["net_7d"], sign=True)}</b>'
              if has7 else '<b class="muted">—</b>')
        d30 = (f'<b class="{"pos" if d["net_30d"] >= 0 else "neg"}">{_money(d["net_30d"], sign=True)}</b>'
               if has30 else '<b class="muted">—</b>')
        acct_rows = "".join(
            f"<tr><td>{a['label']}</td><td class='muted'>{a['kind']}</td>"
            f"<td class='num'>{_money(a['balance'])}</td><td class='num'>{_delta(a['delta_7d'])}</td>"
            f"<td class='num'>{_delta(a['delta_30d'])}</td><td class='muted'>{a['as_of']}</td></tr>"
            for a in d["accounts"])
        out.append(f"""<div class="card">
<div class="ent-head"><span class="swatch" style="background:{colors[e]}"></span><h3>{e}</h3></div>
<div class="ent-stats">
  <div><b>{_money(d['net_worth'])}</b><span>net position</span></div>
  <div>{d7}<span>7d change</span></div>
  <div>{d30}<span>30d change</span></div>
  <div><b class="neg">{_money(d['spend'])}</b><span>week spend</span></div>
  <div><b class="pos">{_money(d['income'])}</b><span>week income</span></div>
</div>
<div class="ent-grid">
  <table class="accts"><tr><th>account</th><th>kind</th><th class="num">balance</th>
    <th class="num">Δ 7d</th><th class="num">Δ 30d</th><th>as of</th></tr>{acct_rows}</table>
  <div class="donut"><canvas id="donut-{e}"></canvas></div>
</div></div>""")
    return "\n".join(out)


def _txns_html(summary: dict, order: list[str], colors: dict) -> str:
    txns = [t for t in summary["transactions"]]
    chips = '<button class="chip active" data-entity="*">All</button>' + "".join(
        f'<button class="chip" data-entity="{e}">{e}</button>' for e in order)
    rows = []
    for t in txns:
        amt_cls = "neg" if t["amount"] < 0 else "pos"
        pend = " ⏳" if t["pending"] else ""
        cls = ' class="transfer"' if t["transfer"] else ""
        rows.append(f"<tr{cls} data-entity='{t['entity']}'><td>{t['date']}</td><td>{t['entity']}</td>"
                    f"<td>{t['account']}</td><td>{t['merchant']}{pend}</td><td class='muted'>{t['category']}</td>"
                    f"<td class='num amt {'' if t['transfer'] else amt_cls}'>{_money(t['amount'])}</td></tr>")
    return f"""<div class="card"><h2>All transactions this week ({len(txns)})</h2>
<div class="chips">{chips}</div>
<div class="txscroll"><table class="txns">
<tr><th>date</th><th>entity</th><th>account</th><th>merchant</th><th>category</th><th class="num">amount</th></tr>
<tbody>{''.join(rows)}</tbody></table></div></div>"""


def render_dashboard(memo_md: str, summary: dict, archive: list[str], generated: str) -> str:
    order = _entity_order(summary)
    data = _chart_data(summary, order)
    colors = data["colors"]
    ents = summary["entities"]
    net = sum(ents[e].get("net_worth") or 0 for e in order)
    net7 = sum(ents[e].get("net_7d") or 0 for e in order)
    spend = sum(ents[e].get("spend") or 0 for e in order)
    income = sum(ents[e].get("income") or 0 for e in order)
    warns = sum(1 for f in summary["flags"] if f["severity"] == "warn")
    n_accts = sum(len(ents[e]["accounts"]) for e in order)
    has7 = any(a["delta_7d"] is not None for e in order for a in ents[e]["accounts"])
    net7_html = f"{_delta(net7)} past 7 days" if has7 else '<span class="muted">no prior history yet</span>'

    memo_html = markdown.markdown(memo_md, extensions=["tables"])
    data_json = json.dumps(data).replace("<", "\\u003c")
    archive_links = "".join(f'<a href="{name}">week ending {name[:-5]}</a>' for name in archive)
    archive_html = (f'<div class="card"><h2>Archive</h2><div class="archive">{archive_links}</div></div>'
                    if archive else "")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>finsum — week ending {summary['week_end']}</title>
<style>{CSS}</style><script src="chart.umd.min.js"></script></head><body><div class="wrap">
<header class="top"><h1>finsum</h1>
<span class="sub">week {summary['week_start']} → {summary['week_end']} · generated {generated}</span></header>

<div class="kpis">
  <div class="kpi"><div class="label">Net position</div><div class="value">{_money(net)}</div>
    <div class="delta">{net7_html}</div></div>
  <div class="kpi"><div class="label">Week spend</div><div class="value neg">{_money(spend)}</div>
    <div class="delta muted">{len(summary['transactions'])} transactions</div></div>
  <div class="kpi"><div class="label">Week income</div><div class="value pos">{_money(income)}</div></div>
  <div class="kpi"><div class="label">Flags</div><div class="value{' neg' if warns else ''}">{warns}</div>
    <div class="delta muted">{len(summary['flags']) - warns} info</div></div>
  <div class="kpi"><div class="label">Companies</div><div class="value">{len(order)}</div>
    <div class="delta muted">{n_accts} accounts</div></div>
</div>

<div class="card memo">{memo_html}</div>
{_flags_html(summary['flags'])}

<div class="charts">
  <div class="card"><h2>Daily spend by company</h2><div class="chartbox"><canvas id="dailyChart"></canvas></div></div>
  <div class="card"><h2>Net position by company</h2><div class="chartbox"><canvas id="netChart"></canvas></div></div>
</div>

{_entity_html(summary, order, colors)}
{_txns_html(summary, order, colors)}
{archive_html}
<footer>finsum · latest memo is always shown here · archives above</footer>
</div>
<script type="application/json" id="finsum-data">{data_json}</script>
<script>{JS}</script>
</body></html>"""


def latest_report(conn) -> tuple[str, dict] | None:
    row = conn.execute(
        "SELECT markdown, summary_json FROM reports ORDER BY week_ending DESC, generated_at DESC LIMIT 1"
    ).fetchone()
    return (row["markdown"], json.loads(row["summary_json"])) if row else None


def write_dashboard(out_dir: Path | None = None) -> Path:
    """Regenerate out/index.html from the newest report in the DB. Safe to run any time."""
    out_dir = out_dir or config.OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    index = out_dir / "index.html"

    chart_dest = out_dir / "chart.umd.min.js"
    if _CHART_JS_SRC.exists() and not chart_dest.exists():
        shutil.copy(_CHART_JS_SRC, chart_dest)

    with db.connect(config.DB_PATH) as conn:
        rep = latest_report(conn)
    if rep is None:
        index.write_text("<!doctype html><meta charset='utf-8'><title>finsum</title>"
                         "<body style='background:#0b0e13;color:#e2e8f0;font-family:sans-serif'>"
                         "<p style='padding:40px'>No reports yet — run <code>finsum report</code>.</p></body>")
        return index

    memo_md, summary = rep
    archive = sorted((p.name for p in out_dir.glob("2*.html")), reverse=True)
    generated = ""
    with db.connect(config.DB_PATH) as conn:
        row = conn.execute("SELECT generated_at FROM reports ORDER BY week_ending DESC LIMIT 1").fetchone()
        if row:
            from datetime import datetime
            generated = datetime.fromtimestamp(row["generated_at"]).strftime("%b %d, %Y %H:%M")
    index.write_text(render_dashboard(memo_md, summary, archive, generated))
    return index
