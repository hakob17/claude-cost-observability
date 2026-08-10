# Claude Code cost telemetry

This repo is a **cost-tracking sink** for the [cost-observability](https://github.com/hakob17/claude-cost-observability)
Claude Code plugin. Each developer's plugin appends usage rows to their own CSV
under [`data/`](data/) and pushes. A GitHub Action then computes costs and
writes **[`REPORT.md`](REPORT.md)** — the dashboard, rendered right here in GitHub.

No external service, no database, no Azure app — just a private git repo your
team already has access to.

```
plugin (each dev) ──push──▶ data/<developer>.csv
                                   │  (GitHub Action: on push / manual)
                                   ▼
                     report.py + pricing.json ──▶ REPORT.md   (static, view on GitHub)
                                              └──▶ report.html (sortable, download & open)
```

Two views, both committed by the Action:
- **`REPORT.md`** — static tables rendered right here on GitHub (pre-sorted by cost). GitHub strips JavaScript from Markdown, so it can't be interactive.
- **`report.html`** — the **sortable** version: click any column header to sort, grouped by developer / project / model / day. Download it and open in a browser (it's self-contained, works offline). It stays private to repo members — it is **not** GitHub Pages, nothing is published publicly.
- For editing/filtering, open the `data/` CSVs in **Excel or Power BI**.

## Setup (once, by an admin)

1. **Create a private repo** in your org and copy these files into it
   (`data/`, `report.py`, `pricing.json`, `.github/workflows/cost-report.yml`).
   Commit and push so `main` exists.
2. **Give the dev team push access** (a plain write role is enough — everyone
   writes only their own `data/<name>.csv`, so pushes never conflict).
3. That's it. The Action runs automatically on every push to `data/**`, and can
   also be run by hand from the **Actions → Cost Report → Run workflow** button.

## Each developer

In Claude Code: `/cost-setup` → **GitHub repo** → paste this repo's clone URL.
The plugin uses their existing git credentials — no new secrets. From then on,
usage is pushed automatically at the end of each session.

## Protected `main` (branch protection)

The Action **never pushes to `main`** — it pushes the generated report to a
`cost-report` branch and opens/updates a **pull request** (set `BASE_BRANCH` /
`REPORT_BRANCH` at the top of the workflow if your names differ). So report
publishing already respects a protected default branch.

But branch protection that blocks *all* direct pushes to `main` also blocks the
**plugin's** data push. If that's your case, point the plugin at a dedicated,
unprotected **data branch** instead of `main`:

- Each developer sets `"git_branch": "usage-data"` in their config (or the
  `COST_OBS_GIT_BRANCH=usage-data` env var). The plugin then pushes each
  `data/<user>.csv` to `usage-data`, which needs to allow pushes from the team.
- Create that branch once (`git checkout -b usage-data && git push -u origin usage-data`).
- The Action already triggers on any `data/**` push, reads the latest data, and
  opens its PR to `BASE_BRANCH` — so the data lives on `usage-data`, the report
  PR targets `main`, and `main` is never pushed to directly.

If your `main` allows the team (or the plugin's push identity) to push data
directly, you can skip all this and leave the plugin on `main`.

## Files

| File | Purpose |
|---|---|
| `data/<developer>.csv` | One append-only file per developer (written by the plugin) |
| `report.py` | Reads all CSVs, dedupes by `row_id`, computes cost, writes `REPORT.md` + `report.html` |
| `pricing.json` | **Central** price table — edit here + re-run the Action to reprice; no plugin redeploy |
| `.github/workflows/cost-report.yml` | Runs `report.py` on push / on demand and commits both report files |
| `REPORT.md` | Generated dashboard (static): totals + breakdown by developer / project / model / day |
| `report.html` | Generated dashboard (**sortable**): same breakdowns, click a header to sort — download & open |

## Notes

- **Costs are API list-price equivalents** (tokens × pay-as-you-go rates). On
  Claude subscription plans, real spend is the flat monthly fee — treat these as
  a comparison/allocation metric, not an invoice.
- **Only metadata is stored** — developer email, project folder name, model,
  token counts, cost. No prompt or response content.
- **Prefer analysis in Power BI?** Point it at this repo's `data/` folder (or a
  scheduled clone) instead of, or alongside, `REPORT.md`.
