"""Storage for the cost-observability sync server.

Uses PostgreSQL when DATABASE_URL is set (Railway provides it), otherwise a
local SQLite file. Timestamps are stored as ISO-8601 UTC strings and compared
lexicographically, so date filtering is identical on both backends.
"""

import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL")
PG = bool(DATABASE_URL)

DB_PATH = Path(os.environ.get("COST_OBS_DB", str(Path(__file__).parent / "cost_obs.db")))

if PG:
    import psycopg
    from psycopg.rows import dict_row

    # Railway sometimes hands out postgres:// — psycopg wants postgresql://
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]
    _ID = "id BIGSERIAL PRIMARY KEY"
else:
    import sqlite3
    _ID = "id INTEGER PRIMARY KEY AUTOINCREMENT"

SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS usage_rows (
        {_ID},
        row_id TEXT UNIQUE,
        ts TEXT NOT NULL,
        session_id TEXT,
        user_email TEXT,
        host TEXT,
        project TEXT,
        model TEXT,
        input_tokens BIGINT DEFAULT 0,
        output_tokens BIGINT DEFAULT 0,
        cache_read_tokens BIGINT DEFAULT 0,
        cache_write_tokens BIGINT DEFAULT 0,
        turns BIGINT DEFAULT 0,
        cost_usd DOUBLE PRECISION,
        received_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_rows_ts ON usage_rows (ts)",
    "CREATE INDEX IF NOT EXISTS idx_rows_user ON usage_rows (user_email)",
    """CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS api_tokens (
        token TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'ingest',
        created_at DOUBLE PRECISION NOT NULL
    )""",
]


def connect():
    if PG:
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _q(sql):
    """Translate ?-style placeholders to %s for psycopg."""
    return sql.replace("?", "%s") if PG else sql


def _rows(conn, sql, params=()):
    if PG:
        with conn.cursor() as cur:
            cur.execute(_q(sql), params)
            return cur.fetchall()
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _exec(conn, sql, params=()):
    """Execute a write; return affected row count."""
    if PG:
        with conn.cursor() as cur:
            cur.execute(_q(sql), params)
            return cur.rowcount
    cur = conn.execute(sql, params)
    return cur.rowcount


def init_db():
    conn = connect()
    try:
        for stmt in SCHEMA:
            _exec(conn, stmt)
        if not PG:
            conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------ auth
#
# Single-admin model: the only dashboard user is the admin, configured via
# environment variables. Everyone else is a data sender with an ingest token.

def admin_username():
    return os.environ.get("COST_OBS_ADMIN_USER", "admin")


def verify_admin(username, password):
    expected = os.environ.get("COST_OBS_ADMIN_PASSWORD")
    if not expected:
        return False  # login disabled until the env var is set
    return secrets.compare_digest(username, admin_username()) and \
        secrets.compare_digest(password, expected)


def _write(sql, params=()):
    conn = connect()
    try:
        n = _exec(conn, sql, params)
        if not PG:
            conn.commit()
        return n
    finally:
        conn.close()


def _read(sql, params=()):
    conn = connect()
    try:
        return _rows(conn, sql, params)
    finally:
        conn.close()


def create_session(username, ttl_days=7):
    token = secrets.token_urlsafe(32)
    _write("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
    _write("INSERT INTO sessions (token, username, expires_at) VALUES (?,?,?)",
           (token, username, time.time() + ttl_days * 86400))
    return token


def session_user(token):
    if not token:
        return None
    rows = _read("SELECT username FROM sessions WHERE token=? AND expires_at > ?",
                 (token, time.time()))
    return rows[0]["username"] if rows else None


def drop_session(token):
    _write("DELETE FROM sessions WHERE token=?", (token,))


def create_api_token(name, role="ingest"):
    token = "cot_" + secrets.token_urlsafe(32)
    _write("INSERT INTO api_tokens (token, name, role, created_at) VALUES (?,?,?,?)",
           (token, name, role, time.time()))
    return token


def token_role(token):
    if not token:
        return None
    rows = _read("SELECT role FROM api_tokens WHERE token=?", (token,))
    return rows[0]["role"] if rows else None


def list_tokens():
    rows = _read("SELECT name, role, token, created_at FROM api_tokens ORDER BY created_at DESC")
    return [{"name": r["name"], "role": r["role"],
             "prefix": r["token"][:12], "created_at": r["created_at"]} for r in rows]


def delete_token(prefix):
    return _write("DELETE FROM api_tokens WHERE substr(token, 1, 12) = ?", (prefix,))


# ------------------------------------------------------------------ rows

ROW_FIELDS = (
    "row_id", "ts", "session_id", "user_email", "host", "project", "model",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "turns", "cost_usd",
)


def _cutoff(days):
    return (datetime.now(timezone.utc) - timedelta(days=int(days))).isoformat()


def insert_rows(rows, received_at):
    cols = ",".join(ROW_FIELDS) + ", received_at"
    ph = ",".join("?" * (len(ROW_FIELDS) + 1))
    conflict = "ON CONFLICT (row_id) DO NOTHING" if PG else ""
    verb = "INSERT INTO" if PG else "INSERT OR IGNORE INTO"
    sql = f"{verb} usage_rows ({cols}) VALUES ({ph}) {conflict}".strip()
    conn = connect()
    inserted = 0
    try:
        for r in rows:
            vals = [r.get(f) for f in ROW_FIELDS] + [received_at]
            inserted += _exec(conn, sql, vals)
        if not PG:
            conn.commit()
    finally:
        conn.close()
    return inserted


# Whitelist of sortable columns for the rows table (key -> SQL expression).
ROW_SORT = {
    "ts": "ts",
    "user": "user_email",
    "project": "project",
    "model": "model",
    "cost": "cost_usd",
    "in": "(input_tokens + cache_read_tokens + cache_write_tokens)",
    "out": "output_tokens",
    "turns": "turns",
}


def query_rows(days=30, user=None, project=None, limit=None, order_by="ts", order="desc"):
    q = "SELECT * FROM usage_rows WHERE ts >= ?"
    params = [_cutoff(days)]
    if user:
        q += " AND user_email = ?"
        params.append(user)
    if project:
        q += " AND project = ?"
        params.append(project)
    col = ROW_SORT.get(order_by, "ts")
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    q += f" ORDER BY {col} {direction} NULLS LAST" if PG else f" ORDER BY {col} {direction}"
    if limit:
        q += f" LIMIT {int(limit)}"
    return _read(q, params)


def stats(days=30):
    cutoff = _cutoff(days)
    conn = connect()
    try:
        def group(col):
            rows = _rows(conn,
                f"SELECT {col} AS key, SUM(cost_usd) AS cost, "
                f"SUM(input_tokens + cache_read_tokens + cache_write_tokens) AS tokens_in, "
                f"SUM(output_tokens) AS tokens_out, COUNT(DISTINCT session_id) AS sessions "
                f"FROM usage_rows WHERE ts >= ? GROUP BY {col} ORDER BY cost DESC",
                (cutoff,))
            for r in rows:
                r["cost"] = round(r["cost"] or 0, 4)
            return rows

        totals = _rows(conn,
            "SELECT SUM(cost_usd) AS cost, "
            "SUM(input_tokens + cache_read_tokens + cache_write_tokens) AS tokens_in, "
            "SUM(output_tokens) AS tokens_out, COUNT(DISTINCT session_id) AS sessions, "
            "COUNT(DISTINCT user_email) AS users, COUNT(*) AS rows "
            "FROM usage_rows WHERE ts >= ?", (cutoff,))[0]
        totals["cost"] = round(totals["cost"] or 0, 2)

        by_day = _rows(conn,
            "SELECT substr(ts, 1, 10) AS key, SUM(cost_usd) AS cost "
            "FROM usage_rows WHERE ts >= ? GROUP BY substr(ts, 1, 10) ORDER BY key",
            (cutoff,))
        for r in by_day:
            r["cost"] = round(r["cost"] or 0, 4)

        result = {
            "totals": totals,
            "by_day": by_day,
            "by_model": group("model"),
            "by_user": group("user_email"),
            "by_project": group("project"),
        }
    finally:
        conn.close()
    return result
