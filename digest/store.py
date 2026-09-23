"""SQLite storage for collected articles.

The database is load-bearing, not just a log: feeds are shallow (see
config.COLLECT_INTERVAL_HOURS), so the collector runs several times a day and
accumulates here, and the digest run reads its 24-hour window from this table.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    url            TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    section        TEXT NOT NULL,
    title          TEXT NOT NULL,
    published_at   TEXT NOT NULL,          -- ISO 8601, UTC
    first_seen_at  TEXT NOT NULL,          -- when the collector first saw it
    body_text      TEXT,                   -- NULL until extraction runs
    word_count     INTEGER,
    extract_status TEXT NOT NULL DEFAULT 'pending',  -- pending | ok | failed
    extract_error  TEXT
);
CREATE INDEX IF NOT EXISTS idx_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_status    ON articles(extract_status);
"""


def connect():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_stub(conn, *, url, source, section, title, published_at):
    """Insert a newly seen article. Existing URLs are left untouched so we
    never overwrite text we already extracted."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO articles
           (url, source, section, title, published_at, first_seen_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (url, source, section, title, published_at,
         datetime.now(timezone.utc).isoformat()),
    )
    return cur.rowcount == 1  # True if this was genuinely new


def save_body(conn, url, body_text, word_count):
    conn.execute(
        """UPDATE articles SET body_text=?, word_count=?,
           extract_status='ok', extract_error=NULL WHERE url=?""",
        (body_text, word_count, url),
    )


def save_failure(conn, url, error):
    conn.execute(
        """UPDATE articles SET extract_status='failed', extract_error=?
           WHERE url=?""",
        (str(error)[:300], url),
    )


def _cutoff_iso(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


def pending_in_window(conn, hours=None):
    """Articles inside the lookback window that still need body text."""
    hours = hours or config.LOOKBACK_HOURS
    return conn.execute(
        """SELECT * FROM articles
           WHERE extract_status='pending' AND published_at >= ?
           ORDER BY published_at DESC""",
        (_cutoff_iso(hours),),
    ).fetchall()


def ready_in_window(conn, hours=None):
    """Successfully extracted articles inside the window — the input to Stage 2."""
    hours = hours or config.LOOKBACK_HOURS
    return conn.execute(
        """SELECT * FROM articles
           WHERE extract_status='ok' AND published_at >= ?
           ORDER BY published_at DESC""",
        (_cutoff_iso(hours),),
    ).fetchall()


def window_summary(conn, hours=None):
    hours = hours or config.LOOKBACK_HOURS
    rows = conn.execute(
        """SELECT source, extract_status, COUNT(*) n FROM articles
           WHERE published_at >= ? GROUP BY source, extract_status""",
        (_cutoff_iso(hours),),
    ).fetchall()
    return [dict(r) for r in rows]


def prune(conn, keep_hours=None):
    """Delete articles older than the lookback window, and compact the file.

    The scheduled collector commits this database back to the repository on
    every run. Without pruning, a 5 MB binary file would be re-committed eight
    times a day forever — SQLite files delta-compress badly, so the repo would
    grow without bound. We only ever read the last 24 hours, so anything older
    is dead weight.
    """
    keep_hours = keep_hours or (config.LOOKBACK_HOURS * 2)   # keep a safety margin
    cutoff = _cutoff_iso(keep_hours)
    n = conn.execute("DELETE FROM articles WHERE published_at < ?", (cutoff,)).rowcount
    conn.commit()
    conn.execute("VACUUM")      # actually shrink the file, not just free pages
    return n
