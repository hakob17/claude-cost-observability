# Cost Observatory — sync server & analytics dashboard

A small FastAPI + SQLite service the plugin can sync usage rows to, with an
authenticated analytics dashboard ("Cost Observatory") and data export in
Excel / CSV / JSON.

## Run it

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python3 manage.py init                    # create the database
python3 manage.py add-user alice          # dashboard login (prompts password)
python3 manage.py add-token vdi-fleet     # ingest token for the plugins

uvicorn app:app --host 0.0.0.0 --port 8321
```

Open `http://<host>:8321` → log in → dashboard. Deploy behind HTTPS (reverse
proxy) for anything beyond a trusted LAN. The SQLite file location can be
overridden with `COST_OBS_DB=/path/to/db.sqlite`.

## Point the plugins at it

Each developer runs `/cost-setup`, picks **Team sync server**, and enters the
server URL + the ingest token. Rows are queued locally per turn and uploaded
when a session ends (crash-safe, deduplicated by `row_id`).

## Auth model

| Credential | Created by | Grants |
|---|---|---|
| **User** (username + password) | `manage.py add-user` | dashboard login + analytics + export (session cookie, 7-day expiry, pbkdf2 password hashes) |
| **Ingest token** (`cot_…`) | `manage.py add-token` | `POST /api/ingest` only — cannot read any data |
| **Admin token** | `manage.py add-token --admin` | full API read access via `Authorization: Bearer` (for scripts/BI tools) |

Unauthenticated requests to analytics/export endpoints get `401`.

## API

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/ingest` | ingest token | receive `{"rows": [...]}` from plugins (idempotent via `row_id`) |
| `POST /api/login`, `/api/logout`, `GET /api/me` | — / cookie | session management |
| `GET /api/stats?days=N` | viewer | totals + by day/model/developer/project |
| `GET /api/rows?days=N&user=&project=&limit=` | viewer | raw rows |
| `GET /api/export?format=xlsx|csv|json&days=N&user=&project=` | viewer | download the data |

## Analytics

The dashboard shows: API-equivalent cost, tokens in/out, sessions, developer
count; spend by day; top models / developers / projects; recent turns table;
and export buttons (Excel, CSV, JSON) honoring the selected time window.
For deeper analysis, query the SQLite database directly or pull
`/api/export?format=json` into your BI tool with an admin token.

> Costs are **API list-price equivalents** (tokens × pay-as-you-go rates) —
> for subscription (Pro/Max) users this is a comparison metric, not a bill.
