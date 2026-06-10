"""SQLite persistence for ReleaseRadar.

Two tables:
  - settings:  single-row-ish key/value store for the password hash, secret key,
               and login lockout bookkeeping.
  - watchlist: one row per piece of software the enterprise supports, plus the
               results of the most recent version check.
"""

import json
import sqlite3
from contextlib import contextmanager

from config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    platform        TEXT NOT NULL DEFAULT 'windows',   -- windows | mac | both
    current_version TEXT,                              -- version enterprise has packaged

    -- Source identifiers (any may be blank; a source is only queried if set)
    winget_id       TEXT,                              -- e.g. Google.Chrome
    choco_id        TEXT,                              -- e.g. googlechrome
    patchmypc_name  TEXT,                              -- substring of PatchMyPC title
    homebrew_cask   TEXT,                              -- e.g. google-chrome
    rss_url         TEXT,                              -- vendor release-notes/RSS URL

    notes           TEXT,

    -- Latest check results
    latest_version  TEXT,
    latest_source   TEXT,
    status          TEXT DEFAULT 'unknown',            -- up_to_date | update_available | error | unknown
    update_type     TEXT DEFAULT 'unknown',            -- security | ui | unknown
    classify_method TEXT,                              -- heuristic | ai | none
    release_notes   TEXT,                              -- excerpt used for classification
    source_results  TEXT,                              -- JSON: {source: version}
    last_checked    TEXT,

    created_at      TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# --- Settings helpers -----------------------------------------------------

def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# --- Watchlist helpers ----------------------------------------------------

WATCHLIST_FIELDS = (
    "name", "platform", "current_version",
    "winget_id", "choco_id", "patchmypc_name", "homebrew_cask", "rss_url",
    "notes",
)


def list_items():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY name COLLATE NOCASE").fetchall()
    return [dict(r) for r in rows]


def get_item(item_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM watchlist WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def add_item(data):
    cols = ", ".join(WATCHLIST_FIELDS)
    placeholders = ", ".join("?" for _ in WATCHLIST_FIELDS)
    values = [data.get(f, "") for f in WATCHLIST_FIELDS]
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO watchlist ({cols}) VALUES ({placeholders})", values
        )
        return cur.lastrowid


def update_item(item_id, data):
    assignments = ", ".join(f"{f} = ?" for f in WATCHLIST_FIELDS)
    values = [data.get(f, "") for f in WATCHLIST_FIELDS]
    values.append(item_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE watchlist SET {assignments} WHERE id = ?", values)


def delete_item(item_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))


def save_check_result(item_id, result):
    """Persist the outcome of a version check for one item."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE watchlist SET
                latest_version  = ?,
                latest_source   = ?,
                status          = ?,
                update_type     = ?,
                classify_method = ?,
                release_notes   = ?,
                source_results  = ?,
                last_checked    = datetime('now')
            WHERE id = ?
            """,
            (
                result.get("latest_version"),
                result.get("latest_source"),
                result.get("status", "unknown"),
                result.get("update_type", "unknown"),
                result.get("classify_method"),
                result.get("release_notes"),
                json.dumps(result.get("source_results", {})),
                item_id,
            ),
        )
