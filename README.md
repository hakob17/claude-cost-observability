# Cost Observability — Claude Code plugin

Tracks every developer's Claude Code LLM usage (tokens + estimated USD cost) and
aggregates it in one place — cost per developer, per project, per model, per day.

Three destinations, chosen during setup:

| Destination | Setup effort | Best for |
|---|---|---|
| **Local CSV file** (default) | none — works out of the box, no sign-in | individuals, or teams sharing via a synced folder (OneDrive / network share) |
| **Team sync server** | run the bundled [`server/`](server/README.md) app (FastAPI + Postgres/SQLite) | central DB + the **Cost Observatory** analytics dashboard with login and Excel/CSV/JSON export. Deployed on Railway: https://claude-cost-observability-production.up.railway.app |
| **SharePoint List** | one-time Azure AD app registration by an admin | Microsoft 365 shops that want the data in SharePoint (Excel / Power BI on top) |

For the sync server, developers enter just the server URL and an ingest token
during `/cost-setup` (or set `COST_OBS_SERVER_URL` / `COST_OBS_SERVER_TOKEN`
env vars — ideal for VDI images). The admin logs into the **Cost Observatory**
dashboard to generate ingest tokens, browse usage grouped by developer /
project / model / day with sortable columns, and export to Excel / CSV / JSON.
See [server/README.md](server/README.md) for running the server, the auth
model, and the API.

---

## How it works

```
Claude Code session (developer's machine)
  │
  │  SessionStart hook — fires when a session starts
  │    └─ flushes anything left on disk from a prior session that
  │       crashed, lost power, or ran offline (outage recovery)
  │
  │  Stop hook — fires after every Claude response (fast)
  │    └─ parses the transcript → writes one row per turn to the
  │       on-disk queue (local CSV mode also appends immediately)
  │
  │  SessionEnd hook — fires when the session ends
  │    └─ flushes queued rows to the destination (network uploads
  │       happen here + at the next SessionStart, keeping the hook fast)
  ▼
┌──────────────────┐   ┌──────────────────────┐   ┌────────────────────────┐
│ Local CSV        │   │ Team sync server     │   │ SharePoint List        │
│ (=local)         │   │ (=server) → Postgres │   │ (=sharepoint) via      │
│ usage.csv        │   │ + Cost Observatory   │   │ Microsoft Graph        │
│                  │   │   dashboard          │   │                        │
└──────────────────┘   └──────────────────────┘   └────────────────────────┘
  one row per (turn, model):
  row_id · timestamp · user email · machine · project · session ·
  input/output tokens · cache tokens · cost USD
```

1. **Recording** — Claude Code stores every session transcript locally as JSONL,
   including exact token usage per API call. After each response, the plugin's
   `Stop` hook parses that transcript, dedupes API calls by message/request ID,
   and writes one row per turn to an on-disk queue. This step never touches the
   network.
2. **Pricing** — cost is estimated from a built-in pricing table (input, output,
   cache-read at 0.1×, cache-write at 1.25×/2× input price, per million tokens).
   Update `PRICING` in [`scripts/track_usage.py`](scripts/track_usage.py) when
   Anthropic pricing changes.

   > **⚠️ "Cost" means API-equivalent cost, not your bill.** The plugin
   > multiplies exact token counts by Anthropic's pay-as-you-go API list
   > prices. If developers use API keys, this approximates real spend. If they
   > are on **subscription plans (Pro/Max)**, actual spend is the flat monthly
   > fee no matter what this number says — a $100/month plan can easily show
   > $1,000+ of API-equivalent usage (Claude Code re-reads the whole
   > conversation from cache on every turn, so cached-input tokens dominate).
   > Use the numbers for per-developer/project comparison and for answering
   > "what would this cost on API billing?" — not as an invoice.
3. **Delivering** — queued rows flush to the configured destination: appended to
   the local CSV, POSTed to the team sync server, or pushed to the SharePoint
   List via Microsoft Graph. Each row carries a unique `row_id`, so re-syncs and
   retries only ever add **missing** rows — never duplicates.

### Reliability properties

- **No dependencies** — the tracker is a single stdlib-only Python 3 script;
  developers install nothing beyond the plugin itself.
- **Durable local buffer for every destination** — rows are written to the
  on-disk queue (`~/.claude/cost-observability/queue/`) before any upload is
  attempted, even in server/SharePoint mode. Nothing lives only in memory.
- **Power-outage safe** — every turn is recorded the moment the response
  finishes, so a killed session, crashed VDI, or yanked power loses at most the
  single response in flight. On the **next session start** the queue is flushed
  automatically; a row stranded mid-upload by a crash (a `.inflight` file) is
  reclaimed and retried rather than lost.
- **Idempotent uploads** — dedup on `row_id` means a lost network response,
  or `SessionStart` and `SessionEnd` both flushing, can never double-count.
- **Resume-safe** — resumed sessions report only usage accrued since the last
  recorded turn (delta tracking).
- **Never breaks your session** — all hook errors are swallowed and logged to
  `~/.claude/cost-observability/log.txt`.
- **Cross-platform** — pure-stdlib Python (no shell scripts), and the hooks
  invoke `python3` with a `python` fallback so they run on Windows (python.org
  or Microsoft Store), macOS, and Linux alike.

### Data & privacy

Only **metadata** leaves the machine: session ID, user email, hostname, project
folder name, model, token counts, estimated cost. **Prompt and response content
is never uploaded.** Auth tokens live at `~/.claude/cost-observability/tokens.json`
(mode 600). Set `"enabled": false` in `~/.claude/cost-observability/config.json`
to pause uploads (the local ledger keeps recording).

---

## Installation

### 0. One-time admin setup (SharePoint destination only — skip for local CSV)

1. **Azure AD app registration** (Entra ID → App registrations → New):
   - Supported account types: single tenant.
   - Authentication tab → enable **"Allow public client flows"** (this enables
     device-code sign-in, so no client secret is ever distributed).
   - API permissions: **Microsoft Graph → Delegated → `Sites.ReadWrite.All`**,
     then **Grant admin consent**.
   - Share the **Tenant ID** and **Application (client) ID** with the team.
2. Pick (or create) a **SharePoint site** for the list, e.g.
   `https://<company>.sharepoint.com/sites/Engineering`. The first developer to
   run `/cost-setup` creates the "Claude Cost Tracking" list there automatically,
   with all required columns.

### 1. Install the plugin (each developer)

In Claude Code:

```
/plugin marketplace add hakob17/claude-cost-observability
/plugin install cost-observability@cost-observability-marketplace
```

Or auto-install for everyone working in a repo by committing this to that repo's
`.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "cost-observability-marketplace": {
      "source": { "source": "github", "repo": "hakob17/claude-cost-observability" }
    }
  },
  "enabledPlugins": { "cost-observability@cost-observability-marketplace": true }
}
```

> **Requirements:** Python 3.8+ on PATH. Preinstalled on macOS/Linux. On
> **Windows**, install from [python.org](https://python.org) (tick *"Add
> python.exe to PATH"*) or the Microsoft Store — the hooks invoke
> `python3` and fall back to `python`, so either the python.org (`python`,
> `py`) or Store (`python`, `python3`) layout works. Native Windows, the
> desktop app, and WSL are all supported; no WSL required.

### Windows troubleshooting — `/plugin marketplace add` fails with a `<username>.claude` path

If adding the marketplace on native Windows errors on a path like
`C:\Users\<username>.claude\plugins\...` (the username glued to `.claude`
instead of `C:\Users\<username>\.claude`), that's Claude Code resolving **its
own** config directory — not this plugin. Two fixes, try in order:

1. **Check for a stray `HOME` variable** (often set by Git for Windows):
   ```powershell
   echo $env:USERPROFILE   # expect C:\Users\<username>
   echo $env:HOME          # if set to anything, that's likely the cause
   ```
   Unset it (PowerShell, permanent), then reopen the terminal:
   ```powershell
   [Environment]::SetEnvironmentVariable("HOME", $null, "User")
   ```
   cmd.exe (current session): `set HOME=`

2. **If `HOME` is already empty/correct**, it's a Claude Code Windows path bug —
   **upgrade Claude Code** (`claude update` / reinstall the latest) which is the
   durable fix, or run Claude Code under **WSL** as a fallback.

Also note: Windows usernames containing a **period** (`first.last`) or **space**
trigger related Claude Code path issues on native Windows — WSL avoids those too.

Once the `.claude` directory resolves correctly, everything else in this plugin
works normally (its runtime uses Python's `Path.home()`, which is unaffected).

### Run setup (each developer, once)

```
/cost-setup
```

The guided setup first asks **where the data should go**:

**Local file** (no admin setup, no sign-in):
1. Optionally pick the CSV path — default
   `~/.claude/cost-observability/usage.csv`. Point it at a OneDrive or
   network-share folder to share usage with the team without any Azure setup.
2. A test row is written to verify. Done.

**Team sync server** (just a URL + token):
1. Paste the **server link** and the **ingest token** the admin generated in the
   dashboard's *Ingest Tokens* panel. Or skip the questions entirely by setting
   `COST_OBS_SERVER_URL` and `COST_OBS_SERVER_TOKEN` in the environment (bake
   them into a VDI image / shell profile for zero-touch onboarding).
2. A test row is pushed to verify. Done.

**SharePoint List** (asks for Azure details only on this path):
1. Enter the **tenant ID** and **client ID** (from the admin) and the SharePoint
   **site URL**; your email defaults to `git config user.email`.
2. Sign into Microsoft with a **device code** — open a URL, type the code, done.
   No passwords or secrets are handled by the plugin.
3. The setup creates the SharePoint list (first developer) or connects to the
   existing one, then pushes a test row to verify end-to-end.

After that, tracking is fully automatic — nothing else to do. Even without
running `/cost-setup` at all, the plugin defaults to local-CSV mode and starts
recording immediately after install.

---

## Commands

| Command | What it does |
|---|---|
| `/cost-setup` | Guided one-time setup: pick a destination and configure it |
| `/cost-report` | Local usage summary (by model / project / day, plus recent turns) for this machine |
| `/cost-sync` | Health check + flush any queued rows to the destination now |

The underlying script also supports `login`, `test`, `flush`, `status`,
`report --days N --turns N`, `create-list`, `resolve`:

```
python3 scripts/track_usage.py --help
```

---

## The team sync server

The bundled [`server/`](server/README.md) app (FastAPI) receives usage from the
plugins into PostgreSQL (or SQLite locally) and serves the **Cost Observatory**
dashboard — login-protected, with:

- Totals (API-equivalent cost, tokens, sessions, developer count) over a
  selectable 7 / 30 / 90-day window.
- **Group by** Developer / Project / Model / Day, each a sortable table.
- A **Recent Turns** table sortable by any column (time, developer, project,
  model, tokens, cost), sorted server-side across the full dataset.
- One-click export to **Excel / CSV / JSON**.
- An **Ingest Tokens** panel to generate (and revoke) the tokens developers use.

Auth is a single admin (username + password from the `COST_OBS_ADMIN_PASSWORD`
env var); everyone else is a data sender holding an ingest token that can only
write, never read. A live instance is deployed on Railway:
https://claude-cost-observability-production.up.railway.app

---

## The SharePoint list

Columns (created automatically by `create-list`):

| Column (internal name) | Type | Column | Type |
|---|---|---|---|
| Title | text | InputTokens | number |
| SessionId | text | OutputTokens | number |
| UserEmail | text | CacheReadTokens | number |
| Host | text | CacheWriteTokens | number |
| Project | text | Turns | number |
| Model | text | CostUSD | number |
| Timestamp | text | | |

For reporting, open the list in Excel (**Export to Excel** keeps a live
connection) or point **Power BI** at it for dashboards: cost per developer,
per project, per model, per day.

---

## Repository layout

```
.claude-plugin/
  plugin.json          plugin manifest
  marketplace.json     lets the repo act as a plugin marketplace
hooks/hooks.json       SessionStart + Stop + SessionEnd hook wiring
scripts/track_usage.py the tracker (stdlib-only Python 3)
commands/              /cost-setup, /cost-report, /cost-sync
server/                team sync server + Cost Observatory dashboard
  app.py               FastAPI: ingest, analytics, export, token mgmt
  db.py                dual-backend storage (Postgres via DATABASE_URL, else SQLite)
  manage.py            admin CLI (init db, generate ingest tokens)
  static/              the dashboard (login, charts, grouping, export)
  README.md            run locally, deploy on Railway, auth model, API
```
