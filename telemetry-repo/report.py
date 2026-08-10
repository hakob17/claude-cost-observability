#!/usr/bin/env python3
"""Consolidate per-user usage CSVs into a Markdown cost report.

Run by the GitHub Action (on push to data/** or manually). Reads every
data/*.csv, dedupes rows by row_id, computes API-equivalent cost from
pricing.json, and writes REPORT.md. Stdlib only.
"""

import csv
import glob
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


def cost_of(r):
    p = price_for(r.get("model"))
    if not p:
        return 0.0
    def n(k):
        try:
            return int(float(r.get(k) or 0))
        except (ValueError, TypeError):
            return 0
    return (n("input_tokens") * p["input"]
            + n("output_tokens") * p["output"]
            + n("cache_read_tokens") * p["input"] * CR_MULT
            + n("cache_write_tokens") * p["input"] * CW_MULT) / 1_000_000


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


def agg(rows, keyfn):
    out = defaultdict(lambda: {"cost": 0.0, "tin": 0, "tout": 0, "sessions": set()})
    for r in rows:
        a = out[keyfn(r)]
        a["cost"] += cost_of(r)
        for k in ("input_tokens", "cache_read_tokens", "cache_write_tokens"):
            try:
                a["tin"] += int(float(r.get(k) or 0))
            except (ValueError, TypeError):
                pass
        try:
            a["tout"] += int(float(r.get("output_tokens") or 0))
        except (ValueError, TypeError):
            pass
        a["sessions"].add(r.get("session_id"))
    return out


def table(title, rows, keyfn, key_header, top=None):
    data = agg(rows, keyfn)
    ordered = sorted(data.items(), key=lambda kv: -kv[1]["cost"])
    if top:
        ordered = ordered[:top]
    lines = [f"### {title}", "",
             f"| {key_header} | API-equiv cost | Tokens in | Tokens out | Sessions |",
             "|---|--:|--:|--:|--:|"]
    for k, a in ordered:
        lines.append(f"| {k or '—'} | ${a['cost']:,.2f} | {a['tin']:,} | "
                     f"{a['tout']:,} | {len(a['sessions'])} |")
    if not ordered:
        lines.append("| _no data yet_ |  |  |  |  |")
    lines.append("")
    return "\n".join(lines)


def main():
    rows = load_rows()
    total = sum(cost_of(r) for r in rows)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [
        "# Claude Code — Cost Report",
        "",
        f"_Generated {now} · {len(rows):,} usage rows_",
        "",
        f"## Total API-equivalent cost: ${total:,.2f}",
        "",
        "> Costs are **API list-price equivalents** (tokens × pay-as-you-go rates "
        "from `pricing.json`). On Claude subscription plans (Pro/Max) actual spend "
        "is the flat monthly fee — use these figures for relative comparison and "
        "allocation, not as an invoice.",
        "",
        table("By developer", rows, lambda r: r.get("user_email"), "Developer"),
        table("By project", rows, lambda r: r.get("project"), "Project"),
        table("By model", rows, lambda r: r.get("model"), "Model"),
        table("By day (last 30)", rows, lambda r: (r.get("timestamp") or "")[:10],
              "Day", top=30),
    ]
    with open(os.path.join(HERE, "REPORT.md"), "w") as f:
        f.write("\n".join(out) + "\n")
    print(f"Wrote REPORT.md — {len(rows)} rows, ${total:,.2f} total.")


if __name__ == "__main__":
    main()
