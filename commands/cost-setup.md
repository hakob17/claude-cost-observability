---
description: One-time setup for the cost-observability plugin (Azure AD app, SharePoint list, sign-in)
---

Walk the user through setting up the cost-observability plugin. The tracker script is at `${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py`; its config lives at `~/.claude/cost-observability/config.json`.

Follow these steps in order:

1. **Check current state**: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" status` and show the result.

2. **Collect config values** with AskUserQuestion (or read them from an existing config if already set):
   - `tenant_id` — the Azure AD (Entra ID) tenant ID or domain (e.g. `contoso.onmicrosoft.com`). An admin usually provides this once for the whole team.
   - `client_id` — the app registration's Application (client) ID. This is a one-time admin task: register a **public client** app in Entra ID with "Allow public client flows" enabled and delegated Microsoft Graph permission `Sites.ReadWrite.All` (admin-consented). Every developer reuses the same client_id.
   - `user_email` — optional; defaults to `git config --global user.email`.
   - The SharePoint **site URL** (e.g. `https://contoso.sharepoint.com/sites/Engineering`).

3. **Write the config**: merge the values into `~/.claude/cost-observability/config.json` as JSON keys `tenant_id`, `client_id`, `user_email`, `enabled: true`. Do not overwrite existing `site_id`/`list_id` unless the user is changing sites.

4. **Sign in**: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" login` in the foreground and show the user the device-code URL and code it prints. Wait for it to complete. This is an interactive Microsoft sign-in — the user completes it in their browser; you must never ask for or enter their password.

5. **Create or connect the SharePoint list**:
   - If the team list does not exist yet: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" create-list --site-url <SITE_URL> --name "Claude Cost Tracking"` (creates the list with all required columns and saves site/list IDs).
   - If it already exists: `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" resolve --site-url <SITE_URL> --list "Claude Cost Tracking"`.

6. **Verify**: run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" test` and confirm the test row appears. Then run `status` once more and summarize: tracking is now automatic — usage is recorded after every response and uploaded when a session ends.
