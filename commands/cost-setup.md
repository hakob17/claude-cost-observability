---
description: One-time setup for the cost-observability plugin (choose local file or SharePoint destination)
---

Walk the user through setting up the cost-observability plugin. The tracker script is at `${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py`; its config lives at `~/.claude/cost-observability/config.json`.

Follow these steps in order:

1. **Check current state**: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" status` and show the result.

2. **Choose a destination** with AskUserQuestion:
   - **Local file** — usage rows are appended to a CSV file on this machine. No sign-in, no admin setup. The file path is configurable, so it can point at a synced folder (OneDrive, network share) to share it with the team.
   - **Team sync server** — rows are uploaded to the team's cost-observability server (the `server/` app in this repo) which stores them in a database and serves an analytics dashboard. Needs only the server URL and an ingest token from whoever runs the server.
   - **SharePoint List** — rows are uploaded to a central SharePoint List via Microsoft Graph. Requires a one-time Azure AD app registration by an admin.

2b. **If "Team sync server" was selected:**
   - First check the environment: if `COST_OBS_SERVER_URL` and `COST_OBS_SERVER_TOKEN` are already set, no config values are needed — just write `destination: "server"`, `enabled: true` and skip to verification.
   - Otherwise ask the user to **paste the server link** (e.g. `https://cost.internal.company.com` or `http://10.0.0.5:8321`) and the **ingest token** they received from the server admin (the admin generates tokens in the dashboard's "Ingest Tokens" panel, or via `python3 manage.py add-token <name>`).
   - Ask (optional) for `user_email`; defaults to `git config --global user.email`.
   - Write the config JSON with: `destination: "server"`, `server_url`, `server_token`, `user_email` (if given), `enabled: true`. Merge with any existing config. (Env vars override config, so teams can also bake `COST_OBS_SERVER_URL`/`COST_OBS_SERVER_TOKEN` into a VDI image or shell profile instead.)
   - Verify: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" test` and confirm success. Done — do NOT ask for tenant/client IDs or run any Microsoft sign-in.

3. **If "Local file" was selected:**
   - Ask (optional) for the CSV path; default is `~/.claude/cost-observability/usage.csv`. Expand `~` before saving.
   - Ask (optional) for `user_email`; defaults to `git config --global user.email`.
   - Write `~/.claude/cost-observability/config.json` with keys: `destination: "local"`, `local_file` (if given), `user_email` (if given), `enabled: true`. Merge with any existing config.
   - Verify: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" test` and confirm the test row landed in the CSV. Done — do NOT ask for tenant/client IDs or run any sign-in.

4. **If "SharePoint List" was selected**, collect with AskUserQuestion (or reuse existing config values):
   - `tenant_id` — the Azure AD (Entra ID) tenant ID or domain (e.g. `contoso.onmicrosoft.com`).
   - `client_id` — the app registration's Application (client) ID. One-time admin task: register a **public client** app in Entra ID with "Allow public client flows" enabled and delegated Microsoft Graph permission `Sites.ReadWrite.All` (admin-consented). Every developer reuses the same client_id.
   - `user_email` — optional; defaults to `git config --global user.email`.
   - The SharePoint **site URL** (e.g. `https://contoso.sharepoint.com/sites/Engineering`).

   Then:
   1. Write the config: merge `destination: "sharepoint"`, `tenant_id`, `client_id`, `user_email`, `enabled: true` into the config JSON. Do not overwrite existing `site_id`/`list_id` unless the user is changing sites.
   2. Sign in: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" login` in the foreground and show the user the device-code URL and code it prints. Wait for it to complete. This is an interactive Microsoft sign-in — the user completes it in their browser; you must never ask for or enter their password.
   3. Create or connect the list:
      - New list: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" create-list --site-url <SITE_URL> --name "Claude Cost Tracking"`
      - Existing list: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" resolve --site-url <SITE_URL> --list "Claude Cost Tracking"`
   4. Verify: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" test` and confirm the test row appears in the list.

5. **Wrap up**: run `status` once more and summarize: tracking is now automatic — usage is recorded after every response and written to the chosen destination when a session ends.
