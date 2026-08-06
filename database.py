"""
SQLite quote database with deduplication via content hashing.
Tracks which prompts have been used so art stays diverse.
"""

import sqlite3
import hashlib
import re
from pathlib import Path
from config import QUOTES_DB


def _get_conn(db_path: Path | str = QUOTES_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def quote_key(quote: str) -> str:
    """Normalise a quote to an alphanumeric key for dedup comparisons."""
    return re.sub(r"[^a-z0-9]+", "", quote.lower())


def init_db(db_path: Path | str = QUOTES_DB):
    """Create tables if they don't exist."""
    conn = _get_conn(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_hash TEXT UNIQUE NOT NULL,
            raw_prompt TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_prompt_hash ON used_prompts(prompt_hash)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS used_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quote_key TEXT UNIQUE NOT NULL,
            raw_quote TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_quote_key ON used_quotes(quote_key)"
    )
    conn.commit()
    conn.close()


def prompt_exists(raw_prompt: str, db_path: Path | str = QUOTES_DB) -> bool:
    """Check if a prompt has already been used."""
    h = hashlib.sha256(raw_prompt.strip().lower().encode()).hexdigest()
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT 1 FROM used_prompts WHERE prompt_hash = ?", (h,)
    ).fetchone()
    conn.close()
    return row is not None


def save_prompt(raw_prompt: str, db_path: Path | str = QUOTES_DB):
    """Save a prompt, ignoring duplicates."""
    h = hashlib.sha256(raw_prompt.strip().lower().encode()).hexdigest()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO used_prompts (prompt_hash, raw_prompt) VALUES (?, ?)",
            (h, raw_prompt.strip()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # duplicate — already used
    finally:
        conn.close()


def get_used_quote_keys(db_path: Path | str = QUOTES_DB) -> set[str]:
    """Return the normalised keys of all quotes already used."""
    conn = _get_conn(db_path)
    try:
        rows = conn.execute("SELECT quote_key FROM used_quotes").fetchall()
    except sqlite3.OperationalError:
        rows = []  # table not created yet — nothing used so far
    conn.close()
    return {row[0] for row in rows}


def save_quote(quote: str, db_path: Path | str = QUOTES_DB):
    """Record a quote as used, ignoring duplicates."""
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "INSERT INTO used_quotes (quote_key, raw_quote) VALUES (?, ?)",
            (quote_key(quote), quote.strip()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # duplicate — already used
    finally:
        conn.close()


def get_used_count(db_path: Path | str = QUOTES_DB) -> int:
    """Count total used prompts."""
    conn = _get_conn(db_path)
    row = conn.execute("SELECT COUNT(*) FROM used_prompts").fetchone()
    conn.close()
    return row[0]


def init_generations_table(db_path: Path | str = QUOTES_DB):
    """Create the generations tracking table if it doesn't exist."""
    conn = _get_conn(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS generations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            status TEXT NOT NULL,
            category TEXT,
            theme TEXT,
            quote_text TEXT,
            author TEXT,
            art_prompt TEXT,
            image_filename TEXT,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gen_status ON generations(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gen_category ON generations(category)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_gen_created ON generations(created_at)"
    )
    conn.commit()
    conn.close()


def save_generation(status: str, category: str | None = None, theme: str | None = None,
                    quote_text: str | None = None, author: str | None = None,
                    art_prompt: str | None = None, image_filename: str | None = None,
                    error_message: str | None = None, db_path: Path | str = QUOTES_DB):
    """Record a generation run result."""
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT INTO generations (status, category, theme, quote_text, author, art_prompt, image_filename, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (status, category, theme, quote_text, author, art_prompt, image_filename, error_message),
        )
        conn.commit()
    finally:
        conn.close()


def get_generations(status: str | None = None, category: str | None = None, limit: int = 50) -> list[dict]:
    """Return generation history records."""
    conn = _get_conn(QUOTES_DB)
    try:
        conditions = []
        params = []
        if status:
            conditions.append("status = ?")
            params.append(status)
        if category:
            conditions.append("category = ?")
            params.append(category)

        where_clause = " AND ".join(conditions) if conditions else ""
        query = f"SELECT id, status, category, theme, quote_text, author, art_prompt, image_filename, error_message, created_at FROM generations {where_clause} ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": row[0],
                "status": row[1],
                "category": row[2],
                "theme": row[3],
                "quote_text": row[4],
                "author": row[5],
                "art_prompt": row[6],
                "image_filename": row[7],
                "error_message": row[8],
                "created_at": row[9],
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_used_quotes(limit: int = 100) -> list[dict]:
    """Return all used quotes with their metadata from the generations table."""
    conn = _get_conn(QUOTES_DB)
    try:
        rows = conn.execute(
            "SELECT category, quote_text, author, created_at FROM generations WHERE status = 'success' AND quote_text IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            {
                "category": row[0],
                "quote_text": row[1],
                "author": row[2],
                "created_at": row[3],
            }
            for row in rows
        ]
    finally:
        conn.close()
