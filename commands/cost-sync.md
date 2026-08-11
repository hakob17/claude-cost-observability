---
description: Check cost-tracking health and push any queued usage rows to the configured destination now
---

> **Python launcher:** use `python3` on macOS/Linux, `python` on Windows (a standard Windows install has `python`, not `python3`). If unsure: `python3 --version || python --version`.

1. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" status` and show the result.
2. If there are queued rows, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" flush` and report how many rows were pushed (to the local CSV or SharePoint, depending on the configured destination).
3. If the destination is SharePoint and status shows the user is not signed in or the token is expired without a refresh token, tell them to run `/cost-setup` (or just the `login` subcommand) to re-authenticate.
4. If the destination is **git** and the flush reports 0 pushed with rows still queued, the cause is almost always uncached git credentials. The plugin never prompts (it runs git non-interactively), so surface the real error: check `~/.claude/cost-observability/log.txt`, and have the user run a plain `git push` to the telemetry repo once to prime Git Credential Manager / their PAT. After that, syncs are silent and automatic.
