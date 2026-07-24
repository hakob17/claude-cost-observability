"""SQLite storage for the cost-observability sync server."""

import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("COST_OBS_DB", str(Path(__file__).parent / "cost_obs.db")))

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id TEXT UNIQUE,
    ts TEXT NOT NULL,
    session_id TEXT,
    user_email TEXT,
    host TEXT,
    project TEXT,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    turns INTEGER DEFAULT 0,
    cost_usd REAL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rows_ts ON usage_rows (ts);
CREATE INDEX IF NOT EXISTS idx_rows_user ON usage_rows (user_email);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    pw_salt TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    expires_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    token TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'ingest',
    created_at REAL NOT NULL
);
"""


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(SCHEMA)


# ------------------------------------------------------------------ auth

def _hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def add_user(username, password):
    salt = secrets.token_hex(16)
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO users (username, pw_salt, pw_hash, created_at) VALUES (?,?,?,?)",
            (username, salt, _hash_pw(password, salt), time.time()),
        )


def verify_user(username, password):
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return False
    return secrets.compare_digest(row["pw_hash"], _hash_pw(password, row["pw_salt"]))


def create_session(username, ttl_days=7):
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (time.time(),))
        conn.execute(
            "INSERT INTO sessions (token, username, expires_at) VALUES (?,?,?)",
            (token, username, time.time() + ttl_days * 86400),
        )
    return token


def session_user(token):
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT username FROM sessions WHERE token=? AND expires_at > ?",
            (token, time.time()),
        ).fetchone()
    return row["username"] if row else None


def drop_session(token):
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def create_api_token(name, role="ingest"):
    token = "cot_" + secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "INSERT INTO api_tokens (token, name, role, created_at) VALUES (?,?,?,?)",
            (token, name, role, time.time()),
        )
    return token


def token_role(token):
    if not token:
        return None
    with connect() as conn:
        row = conn.execute("SELECT role FROM api_tokens WHERE token=?", (token,)).fetchone()
    return row["role"] if row else None


# ------------------------------------------------------------------ rows

ROW_FIELDS = (
    "row_id", "ts", "session_id", "user_email", "host", "project", "model",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
    "turns", "cost_usd",
)


def insert_rows(rows, received_at):
    inserted = 0
    with connect() as conn:
        for r in rows:
            vals = [r.get(f) for f in ROW_FIELDS] + [received_at]
            cur = conn.execute(
                f"INSERT OR IGNORE INTO usage_rows ({','.join(ROW_FIELDS)}, received_at) "
                f"VALUES ({','.join('?' * (len(ROW_FIELDS) + 1))})",
                vals,
            )
            inserted += cur.rowcount
    return inserted


def query_rows(days=30, user=None, project=None, limit=None):
    q = "SELECT * FROM usage_rows WHERE ts >= datetime('now', ?)"
    params = [f"-{int(days)} days"]
    if user:
        q += " AND user_email = ?"
        params.append(user)
    if project:
        q += " AND project = ?"
        params.append(project)
    q += " ORDER BY ts DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    with connect() as conn:
        return [dict(r) for r in conn.execute(q, params).fetchall()]


def stats(days=30):
    where = "WHERE ts >= datetime('now', ?)"
    p = [f"-{int(days)} days"]
    with connect() as conn:
        def group(col):
            return [dict(r) for r in conn.execute(
                f"SELECT {col} AS key, ROUND(SUM(cost_usd), 4) AS cost, "
                f"SUM(input_tokens + cache_read_tokens + cache_write_tokens) AS tokens_in, "
                f"SUM(output_tokens) AS tokens_out, COUNT(DISTINCT session_id) AS sessions "
                f"FROM usage_rows {where} GROUP BY {col} ORDER BY cost DESC", p
            ).fetchall()]

        totals = dict(conn.execute(
            f"SELECT ROUND(SUM(cost_usd), 2) AS cost, "
            f"SUM(input_tokens + cache_read_tokens + cache_write_tokens) AS tokens_in, "
            f"SUM(output_tokens) AS tokens_out, COUNT(DISTINCT session_id) AS sessions, "
            f"COUNT(DISTINCT user_email) AS users, COUNT(*) AS rows "
            f"FROM usage_rows {where}", p
        ).fetchone())
        by_day = [dict(r) for r in conn.execute(
            f"SELECT substr(ts, 1, 10) AS key, ROUND(SUM(cost_usd), 4) AS cost "
            f"FROM usage_rows {where} GROUP BY key ORDER BY key", p
        ).fetchall()]
    return {
        "totals": totals,
        "by_day": by_day,
        "by_model": group("model"),
        "by_user": group("user_email"),
        "by_project": group("project"),
    }
