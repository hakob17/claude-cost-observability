---
description: Show a local summary of Claude Code usage cost (this machine)
---

> **Python launcher:** use `python3` on macOS/Linux, `python` on Windows (a standard Windows install has `python`, not `python3`). If unsure: `python3 --version || python --version`.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/track_usage.py" report --days 30` (use a different `--days` value if the user asked for a specific period, e.g. "$ARGUMENTS" may contain a number of days).

Present the output to the user as a readable summary. Note that this is the local ledger for this machine only; the team-wide view is the SharePoint list.
