---
description: Check cost-tracking health and push any queued usage rows to SharePoint now
---

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" status` and show the result.
2. If there are queued rows, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" flush` and report how many rows were pushed.
3. If the status shows the user is not signed in or the token is expired without a refresh token, tell them to run `/cost-setup` (or just the `login` subcommand) to re-authenticate.
