# Cost Observability — Claude Code plugin

Tracks every developer's Claude Code LLM usage (tokens + estimated USD cost) locally
and aggregates it into **one central SharePoint List** for the whole team — cost per
developer, per project, per model, per day, all in one place.

---

## How it works

```
Claude Code session (developer's machine)
  │
  │  Stop hook — fires after every Claude response (local only, fast)
  │    └─ parses the session transcript → per-model token totals → snapshot
  │
  │  SessionEnd hook — fires when the session ends
  │    └─ computes what's new since the last upload → queues a row
  │       → pushes queued rows to SharePoint via Microsoft Graph
  ▼
SharePoint List  "Claude Cost Tracking"
  one row per (session, model):
  user email · machine · project · input/output tokens · cache tokens · turns · cost USD
```

1. **Recording** — Claude Code stores every session transcript locally as JSONL,
   including exact token usage per API call. After each response, the plugin's
   `Stop` hook parses that transcript, dedupes API calls by message/request ID,
   and snapshots per-model totals. This step never touches the network.
2. **Pricing** — cost is estimated from a built-in pricing table (input, output,
   cache-read at 0.1×, cache-write at 1.25×/2× input price, per million tokens).
   Update `PRICING` in [`scripts/track_usage.py`](scripts/track_usage.py) when
   Anthropic pricing changes.
3. **Uploading** — when the session ends, the `SessionEnd` hook computes the
   *delta* since the last upload and pushes one row per model to the SharePoint
   List through the Microsoft Graph API, authenticated as the developer.

### Reliability properties

- **No dependencies** — the tracker is a single stdlib-only Python 3 script;
  developers install nothing beyond the plugin itself.
- **Offline-safe** — if the upload fails (no network, expired sign-in), rows
  queue locally and are flushed on the next session end or via `/cost-sync`.
- **Crash-safe** — sessions killed without a clean exit are swept into the
  queue automatically after 24 h.
- **Resume-safe** — resumed sessions report only usage accrued since the last
  upload, so nothing is double-counted.
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

### 0. One-time admin setup (once per team)

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

The guided setup will:

1. Ask for the **tenant ID**, **client ID** (from the admin) and the SharePoint
   **site URL**; your email defaults to `git config user.email`.
2. Sign you into Microsoft with a **device code** — you open a URL, type the
   code, done. No passwords or secrets are handled by the plugin.
3. Create the SharePoint list (first developer) or connect to the existing one.
4. Push a test row to verify end-to-end.

After that, tracking is fully automatic — nothing else to do.

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
