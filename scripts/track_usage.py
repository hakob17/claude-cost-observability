#!/usr/bin/env python3
"""Claude Code cost observability.

Parses Claude Code session transcripts, computes token usage and estimated USD
cost per model, and writes one row per (session, model) to the configured
destination: a local CSV file (default; zero setup) or a central SharePoint
List via Microsoft Graph. Stdlib only — nothing to pip-install.

Invoked automatically by Claude Code hooks (Stop / SessionEnd) with the hook
payload on stdin, or manually:

    track_usage.py login                          interactive device-code sign-in
    track_usage.py create-list --site-url URL [--name NAME]
                                                  create the SharePoint list + columns
    track_usage.py resolve --site-url URL --list NAME
                                                  point config at an existing list
    track_usage.py test                           push a test row
    track_usage.py flush                          push any queued rows now
    track_usage.py report [--days N]              local usage summary
    track_usage.py status                         config / auth / queue health
"""

import argparse
import csv
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(os.environ.get("COST_OBS_HOME", str(Path.home() / ".claude" / "cost-observability")))
CONFIG_PATH = BASE_DIR / "config.json"
TOKENS_PATH = BASE_DIR / "tokens.json"
STATE_DIR = BASE_DIR / "state"
QUEUE_DIR = BASE_DIR / "queue"
LEDGER_PATH = BASE_DIR / "ledger.jsonl"
LOG_PATH = BASE_DIR / "log.txt"

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "https://graph.microsoft.com/Sites.ReadWrite.All offline_access openid profile"

# USD per 1M tokens: (input, output). Longest matching prefix wins.
# Cache read = 0.1x input; cache write = 1.25x (5m TTL) / 2x (1h TTL) input.
# NOTE: these are pay-as-you-go API list prices. The computed cost is an
# "API-equivalent" value — subscription (Pro/Max) users actually pay a flat
# monthly fee, so treat cost_usd as a comparison/allocation metric, not a bill.
PRICING = [
    ("claude-fable-5", 10.0, 50.0),
    ("claude-mythos-5", 10.0, 50.0),
    ("claude-opus-4-8", 5.0, 25.0),
    ("claude-opus-4-7", 5.0, 25.0),
    ("claude-opus-4-6", 5.0, 25.0),
    ("claude-opus-4-5", 5.0, 25.0),
    ("claude-opus-4-1", 15.0, 75.0),
    ("claude-opus-4-2", 15.0, 75.0),
    ("claude-opus-4", 15.0, 75.0),
    ("claude-sonnet-5", 3.0, 15.0),
    ("claude-sonnet-4", 3.0, 15.0),
    ("claude-3-7-sonnet", 3.0, 15.0),
    ("claude-3-5-sonnet", 3.0, 15.0),
    ("claude-haiku-4-5", 1.0, 5.0),
    ("claude-3-5-haiku", 0.8, 4.0),
    ("claude-3-haiku", 0.25, 1.25),
]

CACHE_READ_MULT = 0.1
CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0


# ---------------------------------------------------------------- utilities

def log(msg):
    try:
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")
    except OSError:
        pass


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    if private:
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_config():
    return load_json(CONFIG_PATH, {})


def destination(cfg):
    """'local' (CSV, no auth), 'server' (team sync server), or 'sharepoint'.

    Defaults follow existing config (back-compat), otherwise 'local' — so the
    plugin is useful with zero setup.
    """
    if cfg.get("destination"):
        return cfg["destination"]
    if cfg.get("list_id"):
        return "sharepoint"
    if cfg.get("server_url"):
        return "server"
    return "local"


def local_file(cfg):
    return Path(os.path.expanduser(cfg.get("local_file") or str(BASE_DIR / "usage.csv")))


def user_identity(cfg):
    email = cfg.get("user_email")
    if not email:
        try:
            email = subprocess.run(
                ["git", "config", "--global", "user.email"],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            email = ""
    if not email:
        email = os.environ.get("USER", "unknown")
    return email


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------- transcript parsing

def price_for(model):
    m = (model or "").lower()
    best = None
    for prefix, inp, out in PRICING:
        if m.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, inp, out)
    return (best[1], best[2]) if best else (None, None)


def parse_transcript(path):
    """Return {model: totals} deduped by (message.id, requestId)."""
    totals = {}
    seen = set()
    try:
        f = open(path, encoding="utf-8")
    except OSError:
        return totals
    with f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "assistant":
                continue
            msg = obj.get("message") if isinstance(obj.get("message"), dict) else obj
            usage = msg.get("usage")
            model = msg.get("model") or ""
            if not isinstance(usage, dict) or not model or model.startswith("<"):
                continue
            key = (msg.get("id"), obj.get("requestId"))
            if key != (None, None) and key in seen:
                continue
            seen.add(key)
            t = totals.setdefault(model, {
                "input": 0, "output": 0, "cache_read": 0,
                "cache_5m": 0, "cache_1h": 0, "turns": 0,
            })
            t["input"] += usage.get("input_tokens") or 0
            t["output"] += usage.get("output_tokens") or 0
            t["cache_read"] += usage.get("cache_read_input_tokens") or 0
            cc = usage.get("cache_creation")
            if isinstance(cc, dict):
                t["cache_5m"] += cc.get("ephemeral_5m_input_tokens") or 0
                t["cache_1h"] += cc.get("ephemeral_1h_input_tokens") or 0
            else:
                t["cache_5m"] += usage.get("cache_creation_input_tokens") or 0
            t["turns"] += 1
    return totals


def compute_cost(model, t):
    inp, out = price_for(model)
    if inp is None:
        return None
    cost = (
        t["input"] * inp
        + t["output"] * out
        + t["cache_read"] * inp * CACHE_READ_MULT
        + t["cache_5m"] * inp * CACHE_WRITE_5M_MULT
        + t["cache_1h"] * inp * CACHE_WRITE_1H_MULT
    ) / 1_000_000
    return round(cost, 6)


# ------------------------------------------------------- state / queueing

FIELDS = ("input", "output", "cache_read", "cache_5m", "cache_1h", "turns")


def state_path(session_id):
    return STATE_DIR / f"{session_id}.json"


def snapshot(session_id, payload, totals):
    st = load_json(state_path(session_id), {}) or {}
    st.update({
        "session_id": session_id,
        "cwd": payload.get("cwd", st.get("cwd", "")),
        "updated_at": now_iso(),
        "totals": totals,
    })
    st.setdefault("started_at", now_iso())
    st.setdefault("pushed", {})
    save_json(state_path(session_id), st)
    return st


def enqueue_session(session_id, cfg):
    """Enqueue the delta between current totals and what was already enqueued.

    Deltas make resumed sessions safe: each SessionEnd only reports usage
    accrued since the previous SessionEnd of the same session.
    """
    st = load_json(state_path(session_id))
    if not st:
        return 0
    totals = st.get("totals", {})
    pushed = st.get("pushed", {})
    project = Path(st.get("cwd") or ".").name
    email = user_identity(cfg)
    host = socket.gethostname()
    enqueued = 0
    for model, t in totals.items():
        prev = pushed.get(model, {})
        delta = {k: t.get(k, 0) - prev.get(k, 0) for k in FIELDS}
        if all(v <= 0 for k, v in delta.items() if k != "turns"):
            continue
        delta = {k: max(0, v) for k, v in delta.items()}
        row = {
            "row_id": uuid.uuid4().hex,
            "timestamp": now_iso(),
            "session_id": session_id,
            "user_email": email,
            "host": host,
            "project": project,
            "model": model,
            "input_tokens": delta["input"],
            "output_tokens": delta["output"],
            "cache_read_tokens": delta["cache_read"],
            "cache_write_tokens": delta["cache_5m"] + delta["cache_1h"],
            "turns": delta["turns"],
            "cost_usd": compute_cost(model, delta),
        }
        QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        save_json(QUEUE_DIR / f"{session_id}-{uuid.uuid4().hex[:8]}.json", row)
        try:
            with open(LEDGER_PATH, "a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass
        pushed[model] = dict(t)
        enqueued += 1
    st["pushed"] = pushed
    save_json(state_path(session_id), st)
    return enqueued


def sweep_stale(cfg, max_age_hours=24):
    """Enqueue snapshots of sessions that died without a SessionEnd."""
    if not STATE_DIR.is_dir():
        return
    cutoff = time.time() - max_age_hours * 3600
    for p in STATE_DIR.glob("*.json"):
        st = load_json(p)
        if not st:
            continue
        try:
            updated = datetime.fromisoformat(st.get("updated_at", "")).timestamp()
        except ValueError:
            updated = 0
        if updated < cutoff:
            enqueue_session(st.get("session_id", p.stem), cfg)
            # Fully-reported stale state files can be removed.
            st2 = load_json(p, {})
            if st2.get("pushed") == st2.get("totals"):
                try:
                    p.unlink()
                except OSError:
                    pass


# ------------------------------------------------------------ Microsoft Graph

def http_json(url, data=None, headers=None, method=None, form=False):
    body = None
    headers = dict(headers or {})
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "{}"
            return json.loads(raw), None
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:
            err = {"error": {"code": str(e.code)}}
        return None, err
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, {"error": {"code": "network", "message": str(e)}}


def token_url(cfg):
    return f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/token"


def device_login():
    cfg = load_config()
    if not cfg.get("tenant_id") or not cfg.get("client_id"):
        print("Config missing tenant_id/client_id. Run /cost-setup first.")
        return 1
    data, err = http_json(
        f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/devicecode",
        {"client_id": cfg["client_id"], "scope": SCOPES}, form=True,
    )
    if err:
        print(f"Device code request failed: {err}")
        return 1
    print(f"\n>>> {data['message']}\n")
    interval = data.get("interval", 5)
    deadline = time.time() + data.get("expires_in", 900)
    while time.time() < deadline:
        time.sleep(interval)
        tok, err = http_json(token_url(cfg), {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": cfg["client_id"],
            "device_code": data["device_code"],
        }, form=True)
        if tok:
            tok["expires_at"] = time.time() + tok.get("expires_in", 3600)
            save_json(TOKENS_PATH, tok, private=True)
            print("Signed in. Token saved.")
            return 0
        code = (err or {}).get("error")
        if code == "authorization_pending":
            continue
        if code == "slow_down":
            interval += 5
            continue
        print(f"Login failed: {err}")
        return 1
    print("Login timed out.")
    return 1


def get_token():
    cfg = load_config()
    if not cfg.get("tenant_id") or not cfg.get("client_id"):
        return None
    tok = load_json(TOKENS_PATH)
    if tok and tok.get("expires_at", 0) > time.time() + 60:
        return tok["access_token"]
    if tok and tok.get("refresh_token"):
        new, err = http_json(token_url(cfg), {
            "grant_type": "refresh_token",
            "client_id": cfg["client_id"],
            "refresh_token": tok["refresh_token"],
            "scope": SCOPES,
        }, form=True)
        if new:
            new["expires_at"] = time.time() + new.get("expires_in", 3600)
            new.setdefault("refresh_token", tok["refresh_token"])
            save_json(TOKENS_PATH, new, private=True)
            return new["access_token"]
        log(f"token refresh failed: {err}")
    return None


def graph_headers(token):
    return {"Authorization": f"Bearer {token}"}


# SharePoint List internal column names -> row keys
COLUMNS = {
    "Title": lambda r: f"{r['session_id'][:8]} {r['model']}",
    "SessionId": "session_id",
    "UserEmail": "user_email",
    "Host": "host",
    "Project": "project",
    "Model": "model",
    "InputTokens": "input_tokens",
    "OutputTokens": "output_tokens",
    "CacheReadTokens": "cache_read_tokens",
    "CacheWriteTokens": "cache_write_tokens",
    "Turns": "turns",
    "CostUSD": "cost_usd",
    "Timestamp": "timestamp",
}


def push_row(cfg, token, row):
    fields = {}
    for col, src in COLUMNS.items():
        val = src(row) if callable(src) else row.get(src)
        if val is not None:
            fields[col] = val
    _, err = http_json(
        f"{GRAPH}/sites/{cfg['site_id']}/lists/{cfg['list_id']}/items",
        {"fields": fields}, headers=graph_headers(token),
    )
    return err


CSV_FIELDS = [
    "timestamp", "session_id", "user_email", "host", "project", "model",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "turns", "cost_usd",
]


def append_local(cfg, row):
    """Append one row to the local CSV file; returns error dict or None."""
    path = local_file(cfg)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        write_header = not path.exists() or path.stat().st_size == 0
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if write_header:
                w.writeheader()
            w.writerow(row)
        return None
    except OSError as e:
        return {"error": {"code": "local_write", "message": str(e)}}


def push_row_server(cfg, row):
    """POST one row to the team sync server; returns error dict or None."""
    _, err = http_json(
        cfg["server_url"].rstrip("/") + "/api/ingest",
        {"rows": [row]},
        headers={"Authorization": f"Bearer {cfg['server_token']}"},
    )
    return err


def flush_queue(verbose=False):
    cfg = load_config()
    if not cfg.get("enabled", True):
        return 0
    if not QUEUE_DIR.is_dir():
        return 0
    files = sorted(QUEUE_DIR.glob("*.json"))
    if not files:
        if verbose:
            print("Queue is empty.")
        return 0

    dest = destination(cfg)
    token = None
    if dest == "server":
        if not cfg.get("server_url") or not cfg.get("server_token"):
            log("flush skipped: server config incomplete")
            if verbose:
                print("Server config incomplete — run /cost-setup.")
            return 0
    elif dest == "sharepoint":
        if not all(cfg.get(k) for k in ("tenant_id", "client_id", "site_id", "list_id")):
            log("flush skipped: sharepoint config incomplete")
            if verbose:
                print("SharePoint config incomplete — run /cost-setup.")
            return 0
        token = get_token()
        if not token:
            log("flush skipped: not signed in (run: track_usage.py login)")
            if verbose:
                print("Not signed in — run: track_usage.py login")
            return 0

    pushed = 0
    for p in files:
        inflight = p.with_suffix(".inflight")
        try:
            os.rename(p, inflight)  # atomic claim; concurrent flusher loses gracefully
        except OSError:
            continue
        row = load_json(inflight)
        if not row:
            inflight.unlink(missing_ok=True)
            continue
        if dest == "local":
            err = append_local(cfg, row)
        elif dest == "server":
            err = push_row_server(cfg, row)
        else:
            err = push_row(cfg, token, row)
        if err:
            os.rename(inflight, p)  # keep for next flush
            log(f"push failed for {p.name}: {json.dumps(err)[:300]}")
        else:
            inflight.unlink(missing_ok=True)
            pushed += 1
    if pushed:
        target = {"local": str(local_file(cfg)), "server": cfg.get("server_url", "server")}.get(dest, "SharePoint")
        log(f"pushed {pushed} row(s) to {target}")
    if verbose:
        print(f"Pushed {pushed} row(s); {len(list(QUEUE_DIR.glob('*.json')))} remaining in queue.")
    return pushed


# --------------------------------------------------------- site/list setup

def resolve_site(token, site_url):
    u = urllib.parse.urlparse(site_url)
    path = u.path.strip("/")
    url = f"{GRAPH}/sites/{u.netloc}:/{path}" if path else f"{GRAPH}/sites/{u.netloc}"
    site, err = http_json(url, headers=graph_headers(token))
    if err:
        raise SystemExit(f"Could not resolve site: {err}")
    return site["id"]


LIST_COLUMNS = [
    {"name": "SessionId", "text": {}},
    {"name": "UserEmail", "text": {}},
    {"name": "Host", "text": {}},
    {"name": "Project", "text": {}},
    {"name": "Model", "text": {}},
    {"name": "InputTokens", "number": {"decimalPlaces": "none"}},
    {"name": "OutputTokens", "number": {"decimalPlaces": "none"}},
    {"name": "CacheReadTokens", "number": {"decimalPlaces": "none"}},
    {"name": "CacheWriteTokens", "number": {"decimalPlaces": "none"}},
    {"name": "Turns", "number": {"decimalPlaces": "none"}},
    {"name": "CostUSD", "number": {}},
    {"name": "Timestamp", "text": {}},
]


def cmd_create_list(args):
    token = get_token()
    if not token:
        raise SystemExit("Not signed in — run: track_usage.py login")
    cfg = load_config()
    site_id = resolve_site(token, args.site_url)
    body = {
        "displayName": args.name,
        "columns": LIST_COLUMNS,
        "list": {"template": "genericList"},
    }
    lst, err = http_json(f"{GRAPH}/sites/{site_id}/lists", body, headers=graph_headers(token))
    if err:
        raise SystemExit(f"List creation failed: {err}")
    cfg.update({"site_id": site_id, "list_id": lst["id"]})
    save_json(CONFIG_PATH, cfg)
    print(f"Created list '{args.name}' (id {lst['id']}) and saved config.")
    return 0


def cmd_resolve(args):
    token = get_token()
    if not token:
        raise SystemExit("Not signed in — run: track_usage.py login")
    cfg = load_config()
    site_id = resolve_site(token, args.site_url)
    lists, err = http_json(f"{GRAPH}/sites/{site_id}/lists", headers=graph_headers(token))
    if err:
        raise SystemExit(f"Could not list lists: {err}")
    match = next((l for l in lists.get("value", []) if l.get("displayName") == args.list), None)
    if not match:
        names = ", ".join(l.get("displayName", "?") for l in lists.get("value", []))
        raise SystemExit(f"List '{args.list}' not found. Available: {names}")
    cfg.update({"site_id": site_id, "list_id": match["id"]})
    save_json(CONFIG_PATH, cfg)
    print(f"Config updated: site_id={site_id} list_id={match['id']}")
    return 0


def cmd_test(_args):
    cfg = load_config()
    row = {
        "timestamp": now_iso(),
        "session_id": "test-session",
        "user_email": user_identity(cfg),
        "host": socket.gethostname(),
        "project": "cost-observability-test",
        "model": "claude-opus-4-8",
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "turns": 0, "cost_usd": 0.0,
    }
    if destination(cfg) == "local":
        err = append_local(cfg, row)
        if err:
            raise SystemExit(f"Test write FAILED: {err}")
        print(f"Test row written to {local_file(cfg)}")
        return 0
    if destination(cfg) == "server":
        if not cfg.get("server_url") or not cfg.get("server_token"):
            raise SystemExit("Server config incomplete — set server_url and server_token.")
        row["row_id"] = uuid.uuid4().hex
        err = push_row_server(cfg, row)
        if err:
            raise SystemExit(f"Test push FAILED: {err}")
        print(f"Test row pushed to {cfg['server_url']} — check the dashboard.")
        return 0
    token = get_token()
    if not token:
        raise SystemExit("Not signed in — run: track_usage.py login")
    err = push_row(cfg, token, row)
    if err:
        raise SystemExit(f"Test push FAILED: {err}")
    print("Test row pushed successfully — check the SharePoint list.")
    return 0


# ------------------------------------------------------------------ hook

def handle_hook():
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    event = payload.get("hook_event_name", "")
    session_id = payload.get("session_id")
    tp = payload.get("transcript_path")
    if not session_id or not tp or not os.path.exists(tp):
        return 0
    try:
        totals = parse_transcript(tp)
        snapshot(session_id, payload, totals)
        cfg = load_config()
        # Enqueue on every turn (Stop), not just SessionEnd: a crashed
        # machine/VDI then loses at most the response that was in flight.
        enqueue_session(session_id, cfg)
        if event == "SessionEnd":
            sweep_stale(cfg)
        # Local CSV appends are instant — flush every turn. Network uploads
        # (SharePoint) wait for SessionEnd to keep the Stop hook fast.
        if destination(cfg) == "local" or event == "SessionEnd":
            if QUEUE_DIR.is_dir() and any(QUEUE_DIR.glob("*.json")):
                flush_queue()
    except Exception as e:  # never break the user's session
        log(f"hook error ({event}): {type(e).__name__}: {e}")
    return 0


# ---------------------------------------------------------------- reporting

def live_rows(cutoff):
    """Usage from sessions still open (or not yet flushed): state minus pushed."""
    rows = []
    if not STATE_DIR.is_dir():
        return rows
    for p in STATE_DIR.glob("*.json"):
        st = load_json(p)
        if not st:
            continue
        try:
            ts = datetime.fromisoformat(st.get("updated_at", "")).timestamp()
        except ValueError:
            continue
        if ts < cutoff:
            continue
        pushed = st.get("pushed", {})
        for model, t in st.get("totals", {}).items():
            prev = pushed.get(model, {})
            delta = {k: max(0, t.get(k, 0) - prev.get(k, 0)) for k in FIELDS}
            if all(v <= 0 for k, v in delta.items() if k != "turns"):
                continue
            rows.append({
                "timestamp": st.get("updated_at"),
                "session_id": st.get("session_id", p.stem),
                "project": Path(st.get("cwd") or ".").name,
                "model": model,
                "input_tokens": delta["input"],
                "output_tokens": delta["output"],
                "cache_read_tokens": delta["cache_read"],
                "cache_write_tokens": delta["cache_5m"] + delta["cache_1h"],
                "turns": delta["turns"],
                "cost_usd": compute_cost(model, delta),
            })
    return rows


def cmd_report(args):
    cutoff = time.time() - args.days * 86400
    rows = []
    if LEDGER_PATH.exists():
        with open(LEDGER_PATH) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    ts = datetime.fromisoformat(r["timestamp"]).timestamp()
                    if ts >= cutoff:
                        rows.append(r)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
    live = live_rows(cutoff)
    rows.extend(live)
    if not rows:
        print(f"No usage recorded locally in the last {args.days} day(s).")
        return 0

    def agg(keyfn):
        out = {}
        for r in rows:
            k = keyfn(r)
            a = out.setdefault(k, {"cost": 0.0, "in": 0, "out": 0, "sessions": set()})
            a["cost"] += r.get("cost_usd") or 0
            a["in"] += r.get("input_tokens", 0) + r.get("cache_read_tokens", 0) + r.get("cache_write_tokens", 0)
            a["out"] += r.get("output_tokens", 0)
            a["sessions"].add(r.get("session_id"))
        return out

    total = sum(r.get("cost_usd") or 0 for r in rows)
    print(f"Claude Code usage — last {args.days} day(s) (this machine)")
    print(f"Total API-equivalent cost: ${total:.2f}")
    if live:
        live_cost = sum(r.get("cost_usd") or 0 for r in live)
        print(f"(includes ${live_cost:.2f} from {len({r['session_id'] for r in live})} "
              f"session(s) still open / not yet flushed)")
    print("Note: costs are API list-price equivalents (tokens x pay-as-you-go rates).")
    print("On a subscription plan (Pro/Max) your actual spend is the flat monthly fee;")
    print("use these numbers for relative comparison and API-vs-plan budgeting.")
    print()
    for title, keyfn in (("By model", lambda r: r["model"]),
                         ("By project", lambda r: r["project"]),
                         ("By day", lambda r: r["timestamp"][:10])):
        print(f"{title}:")
        for k, a in sorted(agg(keyfn).items(), key=lambda kv: -kv[1]["cost"]):
            print(f"  {k:<40} ${a['cost']:>8.2f}   in {a['in']:>12,}  out {a['out']:>10,}  sessions {len(a['sessions'])}")
        print()

    if args.turns:
        recent = sorted(rows, key=lambda r: r.get("timestamp") or "")[-args.turns:]
        print(f"Recent turns (last {len(recent)}):")
        for r in recent:
            ts = (r.get("timestamp") or "")[:19].replace("T", " ")
            tokens_in = (r.get("input_tokens", 0) + r.get("cache_read_tokens", 0)
                         + r.get("cache_write_tokens", 0))
            print(f"  {ts}  {str(r.get('project', '?'))[:20]:<20} "
                  f"{str(r.get('model', '?'))[:28]:<28} ${(r.get('cost_usd') or 0):>7.3f}  "
                  f"in {tokens_in:>10,}  out {r.get('output_tokens', 0):>8,}  "
                  f"sess {str(r.get('session_id', ''))[:8]}")
    return 0


def cmd_status(_args):
    cfg = load_config()
    dest = destination(cfg)
    print(f"Config file : {CONFIG_PATH} ({'present' if CONFIG_PATH.exists() else 'MISSING'})")
    print(f"  destination : {dest}")
    print(f"  enabled     : {cfg.get('enabled', True)}")
    print(f"  user_email  : {'set' if cfg.get('user_email') else 'NOT SET (falls back to git config)'}")
    if dest == "local":
        p = local_file(cfg)
        print(f"  local_file  : {p} ({'exists' if p.exists() else 'will be created'})")
    elif dest == "server":
        print(f"  server_url  : {cfg.get('server_url') or 'NOT SET'}")
        print(f"  server_token: {'set' if cfg.get('server_token') else 'NOT SET'}")
    else:
        for k in ("tenant_id", "client_id", "site_id", "list_id"):
            print(f"  {k:<12}: {'set' if cfg.get(k) else 'NOT SET'}")
        tok = load_json(TOKENS_PATH)
        if tok:
            left = int(tok.get("expires_at", 0) - time.time())
            refresh = "yes" if tok.get("refresh_token") else "no"
            print(f"Auth        : token {'valid ' + str(left) + 's' if left > 0 else 'expired'}, refresh token: {refresh}")
        else:
            print("Auth        : not signed in (run: track_usage.py login)")
    q = len(list(QUEUE_DIR.glob("*.json"))) if QUEUE_DIR.is_dir() else 0
    print(f"Queue       : {q} row(s) pending upload")
    n = sum(1 for _ in open(LEDGER_PATH)) if LEDGER_PATH.exists() else 0
    print(f"Ledger      : {n} row(s) recorded locally")
    return 0


# -------------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("hook")
    sub.add_parser("login")
    sub.add_parser("test")
    sub.add_parser("flush")
    sub.add_parser("status")
    p = sub.add_parser("report")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--turns", type=int, default=10,
                   help="number of recent turn rows to list (0 to hide)")
    p = sub.add_parser("create-list")
    p.add_argument("--site-url", required=True)
    p.add_argument("--name", default="Claude Cost Tracking")
    p = sub.add_parser("resolve")
    p.add_argument("--site-url", required=True)
    p.add_argument("--list", required=True)
    args = parser.parse_args()

    if args.cmd in (None, "hook"):
        return handle_hook()
    if args.cmd == "login":
        return device_login()
    if args.cmd == "test":
        return cmd_test(args)
    if args.cmd == "flush":
        flush_queue(verbose=True)
        return 0
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "create-list":
        return cmd_create_list(args)
    if args.cmd == "resolve":
        return cmd_resolve(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
