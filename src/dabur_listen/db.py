"""SQLite storage for scraped comments and their enrichment."""

import json
import sqlite3
from contextlib import contextmanager

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,
    source_type   TEXT NOT NULL,           -- 'url' | 'keyword'
    source_value  TEXT NOT NULL,           -- the URL or keyword that produced it
    tracking_tag  TEXT,                    -- user label, e.g. 'vatika-ksa-campaign'
    external_id   TEXT,                    -- comment/post id from the platform
    post_url      TEXT,
    author        TEXT,
    text          TEXT NOT NULL,
    likes         INTEGER DEFAULT 0,
    posted_at     TEXT,                    -- ISO timestamp if the scraper provides it
    scraped_at    TEXT DEFAULT (datetime('now')),
    raw_json      TEXT,
    -- layer 2: translation
    detected_language TEXT,
    is_arabizi    INTEGER,
    translation   TEXT,
    translation_notes TEXT,
    translated_at TEXT,
    -- layer 3: classification
    sentiment     TEXT,                    -- positive | negative | neutral
    sentiment_confidence REAL,
    intent        TEXT,
    themes        TEXT,                    -- JSON array
    topics        TEXT,                    -- JSON array (free-form)
    brand_mentions TEXT,                   -- JSON array
    entity        TEXT,                    -- 'own' | 'competitor' | 'none'
    market        TEXT,                    -- market code guess
    classified_at TEXT,
    manually_corrected INTEGER DEFAULT 0,
    UNIQUE(platform, external_id, text)
);

CREATE INDEX IF NOT EXISTS idx_comments_sentiment ON comments(sentiment);
CREATE INDEX IF NOT EXISTS idx_comments_pending_t ON comments(translated_at);
CREATE INDEX IF NOT EXISTS idx_comments_pending_c ON comments(classified_at);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,            -- 'ingest' | 'translate' | 'classify'
    detail       TEXT,
    items        INTEGER,
    started_at   TEXT DEFAULT (datetime('now'))
);
"""


@contextmanager
def connect():
    DATA_DIR.mkdir(exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    try:
        yield con
        con.commit()
    finally:
        con.close()


def insert_comments(con: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert normalized comment dicts; skip duplicates. Returns inserted count."""
    inserted = 0
    for r in rows:
        try:
            cur = con.execute(
                """INSERT OR IGNORE INTO comments
                   (platform, source_type, source_value, tracking_tag, external_id,
                    post_url, author, text, likes, posted_at, raw_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    r["platform"], r["source_type"], r["source_value"],
                    r.get("tracking_tag"), r.get("external_id"), r.get("post_url"),
                    r.get("author"), r["text"], r.get("likes", 0),
                    r.get("posted_at"), json.dumps(r.get("raw", {}), ensure_ascii=False),
                ),
            )
            inserted += cur.rowcount if cur.rowcount > 0 else 0
        except sqlite3.Error:
            continue
    return inserted


def pending_translation(con, limit=None) -> list[sqlite3.Row]:
    q = "SELECT id, text FROM comments WHERE translated_at IS NULL ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return con.execute(q).fetchall()


def pending_classification(con, limit=None, include_done=False) -> list[sqlite3.Row]:
    q = """SELECT id, text, translation, detected_language FROM comments
           WHERE translated_at IS NOT NULL"""
    if not include_done:
        q += " AND classified_at IS NULL"
    q += " AND manually_corrected = 0 ORDER BY id"
    if limit:
        q += f" LIMIT {int(limit)}"
    return con.execute(q).fetchall()


def save_translation(con, cid: int, result: dict) -> None:
    con.execute(
        """UPDATE comments SET detected_language=?, is_arabizi=?, translation=?,
           translation_notes=?, translated_at=datetime('now') WHERE id=?""",
        (
            result.get("detected_language"),
            1 if result.get("is_arabizi") else 0,
            result.get("translation"),
            result.get("notes"),
            cid,
        ),
    )


def save_classification(con, cid: int, result: dict) -> None:
    con.execute(
        """UPDATE comments SET sentiment=?, sentiment_confidence=?, intent=?,
           themes=?, topics=?, brand_mentions=?, entity=?, market=?,
           classified_at=datetime('now') WHERE id=?""",
        (
            result.get("sentiment"),
            result.get("confidence"),
            result.get("intent"),
            json.dumps(result.get("themes", []), ensure_ascii=False),
            json.dumps(result.get("topics", []), ensure_ascii=False),
            json.dumps(result.get("brand_mentions", []), ensure_ascii=False),
            result.get("entity"),
            result.get("market"),
            cid,
        ),
    )


def apply_manual_label(con, cid: int, sentiment: str | None, intent: str | None) -> None:
    sets, vals = ["manually_corrected=1"], []
    if sentiment:
        sets.append("sentiment=?")
        vals.append(sentiment)
    if intent:
        sets.append("intent=?")
        vals.append(intent)
    vals.append(cid)
    con.execute(f"UPDATE comments SET {', '.join(sets)} WHERE id=?", vals)


def stats(con) -> dict:
    row = con.execute(
        """SELECT COUNT(*) total,
                  SUM(translated_at IS NOT NULL) translated,
                  SUM(classified_at IS NOT NULL) classified,
                  SUM(sentiment='positive') positive,
                  SUM(sentiment='negative') negative,
                  SUM(sentiment='neutral') neutral
           FROM comments"""
    ).fetchone()
    return dict(row)
