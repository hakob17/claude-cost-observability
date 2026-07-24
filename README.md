# Cost Observability — Claude Code plugin

Tracks every developer's Claude Code LLM usage (tokens + estimated USD cost) and
aggregates it in one place — cost per developer, per project, per model, per day.

Three destinations, chosen during setup:

| Destination | Setup effort | Best for |
|---|---|---|
| **Local CSV file** (default) | none — works out of the box, no sign-in | individuals, or teams sharing via a synced folder (OneDrive / network share) |
| **Team sync server** | run the bundled [`server/`](server/README.md) app (FastAPI + SQLite) | central DB + the **Cost Observatory** analytics dashboard with login and Excel/CSV/JSON export |
| **SharePoint List** | one-time Azure AD app registration by an admin | Microsoft 365 shops that want the data in SharePoint (Excel / Power BI on top) |

For the sync server, developers enter just the server URL and an ingest token
during `/cost-setup`; see [server/README.md](server/README.md) for running the
server, creating dashboard users, and the API.

---

## How it works

```
Claude Code session (developer's machine)
  │
  │  Stop hook — fires after every Claude response (local, fast)
  │    └─ parses the session transcript → queues one row per turn
  │       (local CSV mode: the row is appended immediately)
  │
  │  SessionEnd hook — fires when the session ends
  │    └─ flushes any queued rows to the destination (network uploads
  │       happen here so the per-turn hook stays fast)
  ▼
┌───────────────────────────────┐   ┌─────────────────────────────────────┐
│ Local CSV (destination=local) │ or │ SharePoint List (destination=       │
│ ~/.claude/cost-observability/ │   │ sharepoint) via Microsoft Graph      │
│ usage.csv — path configurable │   │ "Claude Cost Tracking"               │
└───────────────────────────────┘   └─────────────────────────────────────┘
  one row per (turn, model):
  user email · machine · project · session · input/output tokens · cache tokens · cost USD
```

1. **Recording** — Claude Code stores every session transcript locally as JSONL,
   including exact token usage per API call. After each response, the plugin's
   `Stop` hook parses that transcript, dedupes API calls by message/request ID,
   and snapshots per-model totals. This step never touches the network.
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
3. **Delivering** — when the session ends, the `SessionEnd` hook computes the
   *delta* since the last upload and writes one row per model to the configured
   destination: appended to the local CSV, or pushed to the SharePoint List
   through the Microsoft Graph API (authenticated as the developer).

### Reliability properties

- **No dependencies** — the tracker is a single stdlib-only Python 3 script;
  developers install nothing beyond the plugin itself.
- **Offline-safe** — if the upload fails (no network, expired sign-in), rows
  queue locally and are flushed on the next session end or via `/cost-sync`.
- **Crash-safe** — every turn is recorded the moment the response finishes, so
  a killed session, crashed VDI, or lost connection loses at most the single
  response that was in flight. SharePoint rows queued by a crashed session are
  uploaded the next time any session ends (or via `/cost-sync`).
- **Resume-safe** — resumed sessions report only usage accrued since the last
  recorded turn, so nothing is double-counted.
- **Never breaks your session** — all hook errors are swallowed and logged to
  `~/.claude/cost-observability/log.txt`.

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

> Requires Python 3 on the machine (preinstalled on macOS/Linux).

### 2. Run setup (each developer, once)

```
/cost-setup
```

The guided setup first asks **where the data should go**:

**Local file** (no admin setup, no sign-in):
1. Optionally pick the CSV path — default
   `~/.claude/cost-observability/usage.csv`. Point it at a OneDrive or
   network-share folder to share usage with the team without any Azure setup.
2. A test row is written to verify. Done.

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
| `/cost-setup` | Guided one-time setup: config, Microsoft sign-in, list creation |
| `/cost-report` | Local usage summary (by model / project / day) for this machine |
| `/cost-sync` | Health check + push queued rows to SharePoint now |

The underlying script also supports `login`, `test`, `flush`, `status`,
`report --days N`, `create-list`, `resolve`:

```
python3 scripts/track_usage.py --help
```

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
hooks/hooks.json       Stop + SessionEnd hook wiring
scripts/track_usage.py the tracker (stdlib-only Python 3)
commands/              /cost-setup, /cost-report, /cost-sync
```
