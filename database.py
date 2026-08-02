"""
SQLite quote database with deduplication via content hashing.
Tracks which prompts have been used so art stays diverse.
"""

import sqlite3
import hashlib
from pathlib import Path
from config import QUOTES_DB


def _get_conn(db_path: Path | str = QUOTES_DB) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


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


def get_used_count(db_path: Path | str = QUOTES_DB) -> int:
    """Count total used prompts."""
    conn = _get_conn(db_path)
    row = conn.execute("SELECT COUNT(*) FROM used_prompts").fetchone()
    conn.close()
    return row[0]
