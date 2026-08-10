#!/usr/bin/env python3
"""Consolidate per-user usage CSVs into cost reports.

Run by the GitHub Action (on push to data/** or manually). Reads every
data/*.csv, dedupes rows by row_id, computes API-equivalent cost from
pricing.json, and writes:
  - REPORT.md   static tables, rendered on GitHub (pre-sorted by cost)
  - report.html sortable tables (click a header) — download & open locally;
                stays private to repo members (not GitHub Pages)
Stdlib only.
"""

import csv
import glob
import html
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PRICING = json.load(open(os.path.join(HERE, "pricing.json")))
CR_MULT = PRICING.get("cache_read_multiplier", 0.1)
CW_MULT = PRICING.get("cache_write_multiplier", 1.25)
MODELS = PRICING["models"]


def price_for(model):
    m = (model or "").lower()
    best = None
    for row in MODELS:
        p = row["prefix"]
        if m.startswith(p) and (best is None or len(p) > len(best["prefix"])):
            best = row
    return best


def _int(r, k):
    try:
        return int(float(r.get(k) or 0))
    except (ValueError, TypeError):
        return 0


def cost_of(r):
    p = price_for(r.get("model"))
    if not p:
        return 0.0
    return (_int(r, "input_tokens") * p["input"]
            + _int(r, "output_tokens") * p["output"]
            + _int(r, "cache_read_tokens") * p["input"] * CR_MULT
            + _int(r, "cache_write_tokens") * p["input"] * CW_MULT) / 1_000_000


def load_rows():
    seen, rows = set(), []
    for path in sorted(glob.glob(os.path.join(HERE, "data", "*.csv"))):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                rid = r.get("row_id")
                if rid and rid in seen:
                    continue
                if rid:
                    seen.add(rid)
                rows.append(r)
    return rows


def ordered_agg(rows, keyfn, top=None):
    """Return [(key, cost, tokens_in, tokens_out, sessions)] sorted by cost desc."""
    acc = defaultdict(lambda: {"cost": 0.0, "tin": 0, "tout": 0, "sessions": set()})
    for r in rows:
        a = acc[keyfn(r) or "—"]
        a["cost"] += cost_of(r)
        a["tin"] += _int(r, "input_tokens") + _int(r, "cache_read_tokens") + _int(r, "cache_write_tokens")
        a["tout"] += _int(r, "output_tokens")
        a["sessions"].add(r.get("session_id"))
    out = [(k, v["cost"], v["tin"], v["tout"], len(v["sessions"])) for k, v in acc.items()]
    out.sort(key=lambda t: -t[1])
    return out[:top] if top else out


SECTIONS = [
    ("By developer", "Developer", lambda r: r.get("user_email"), None),
    ("By project", "Project", lambda r: r.get("project"), None),
    ("By model", "Model", lambda r: r.get("model"), None),
    ("By day (last 30)", "Day", lambda r: (r.get("timestamp") or "")[:10], 30),
]

NOTE = ("Costs are **API list-price equivalents** (tokens x pay-as-you-go rates from "
        "pricing.json). On Claude subscription plans real spend is the flat monthly "
        "fee — use these figures for relative comparison and allocation, not as an invoice.")


# ---------------------------------------------------------------- markdown

def md_table(title, key_header, data):
    lines = [f"### {title}", "",
             f"| {key_header} | API-equiv cost | Tokens in | Tokens out | Sessions |",
             "|---|--:|--:|--:|--:|"]
    for k, cost, tin, tout, sess in data:
        lines.append(f"| {k} | ${cost:,.2f} | {tin:,} | {tout:,} | {sess} |")
    if not data:
        lines.append("| _no data yet_ |  |  |  |  |")
    lines.append("")
    return "\n".join(lines)


def write_md(rows, total, now):
    parts = [
        "# Claude Code — Cost Report", "",
        f"_Generated {now} · {len(rows):,} usage rows_", "",
        f"## Total API-equivalent cost: ${total:,.2f}", "",
        f"> {NOTE}", "",
        "> 💡 For **sortable** tables download `report.html` and open it; for "
        "editing/filtering open the `data/` CSVs in Excel or Power BI.", "",
    ]
    for title, header, keyfn, top in SECTIONS:
        parts.append(md_table(title, header, ordered_agg(rows, keyfn, top)))
    with open(os.path.join(HERE, "REPORT.md"), "w") as f:
        f.write("\n".join(parts) + "\n")


# ------------------------------------------------------------------- html

def html_table(title, key_header, data):
    rows_html = []
    for k, cost, tin, tout, sess in data:
        rows_html.append(
            f"<tr><td>{html.escape(str(k))}</td>"
            f'<td class="num" data-val="{cost}">${cost:,.2f}</td>'
            f'<td class="num" data-val="{tin}">{tin:,}</td>'
            f'<td class="num" data-val="{tout}">{tout:,}</td>'
            f'<td class="num" data-val="{sess}">{sess}</td></tr>')
    if not data:
        rows_html.append('<tr><td colspan="5" class="empty">no data yet</td></tr>')
    return (
        f"<h2>{html.escape(title)}</h2>"
        '<table class="sortable"><thead><tr>'
        f"<th>{html.escape(key_header)}</th><th class=\"num\">API-equiv cost</th>"
        '<th class="num">Tokens in</th><th class="num">Tokens out</th>'
        '<th class="num">Sessions</th></tr></thead><tbody>'
        + "".join(rows_html) + "</tbody></table>")


HTML_CSS = """
:root{color-scheme:light dark}
body{font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0;
 background:#f7f7f8;color:#1a1a1a;padding:32px 20px}
@media(prefers-color-scheme:dark){body{background:#16171a;color:#e8e8ea}}
.wrap{max-width:980px;margin:0 auto}
h1{margin:0 0 4px;font-size:26px}.sub{color:#888;font-size:13px;margin:0 0 20px}
.total{font-size:22px;font-weight:700;margin:16px 0}
.note{font-size:12px;color:#888;border-left:3px solid #c9a227;padding:8px 12px;
 background:rgba(201,162,39,.08);margin:0 0 24px}
h2{font-size:15px;letter-spacing:.02em;text-transform:uppercase;color:#666;
 margin:28px 0 8px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid rgba(128,128,128,.2)}
th{cursor:pointer;user-select:none;font-size:12px;white-space:nowrap}
th:hover{color:#2a7}th::after{content:" ⇅";opacity:.35;font-size:10px}
th.asc::after{content:" ↑";opacity:1}th.desc::after{content:" ↓";opacity:1}
.num{text-align:right}.empty{color:#999;text-align:center}
tbody tr:hover{background:rgba(128,128,128,.07)}
"""

HTML_JS = """
document.querySelectorAll('table.sortable').forEach(function(table){
 table.querySelectorAll('th').forEach(function(th,idx){
  th.addEventListener('click',function(){
   var tb=table.tBodies[0],rows=Array.prototype.slice.call(tb.rows);
   var asc=!th.classList.contains('asc');
   table.querySelectorAll('th').forEach(function(h){h.classList.remove('asc','desc')});
   th.classList.add(asc?'asc':'desc');
   rows.sort(function(a,b){
    var ac=a.cells[idx],bc=b.cells[idx];
    var av=ac.dataset.val!=null?ac.dataset.val:ac.textContent.trim();
    var bv=bc.dataset.val!=null?bc.dataset.val:bc.textContent.trim();
    var an=parseFloat(av),bn=parseFloat(bv);
    var c=(!isNaN(an)&&!isNaN(bn))?an-bn:String(av).localeCompare(String(bv));
    return asc?c:-c;
   });
   rows.forEach(function(r){tb.appendChild(r)});
  });
 });
});
"""


def write_html(rows, total, now):
    body = [html_table(t, h, ordered_agg(rows, k, top)) for t, h, k, top in SECTIONS]
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Code — Cost Report</title><style>{HTML_CSS}</style></head>
<body><div class="wrap">
<h1>Claude Code — Cost Report</h1>
<p class="sub">Generated {now} · {len(rows):,} usage rows · click any column header to sort</p>
<p class="total">Total API-equivalent cost: ${total:,.2f}</p>
<p class="note">{html.escape(NOTE.replace('**',''))}</p>
{''.join(body)}
</div><script>{HTML_JS}</script></body></html>"""
    # report.html lives at repo root — repo members download & open it locally
    with open(os.path.join(HERE, "report.html"), "w") as f:
        f.write(doc)


def main():
    rows = load_rows()
    total = sum(cost_of(r) for r in rows)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    write_md(rows, total, now)
    write_html(rows, total, now)
    print(f"Wrote REPORT.md + report.html — {len(rows)} rows, ${total:,.2f} total.")


if __name__ == "__main__":
    main()
