"""Unit tests for database generation-history helpers (no network)."""

import database


def _seed(db_path):
    database.init_db(db_path)
    database.init_generations_table(db_path)
    database.save_generation(status="success", category="anime", quote_text="Q1", db_path=db_path)
    database.save_generation(status="failed", category="anime", quote_text="Q2",
                             error_message="boom", db_path=db_path)
    database.save_generation(status="success", category="philosophy", quote_text="Q3",
                             db_path=db_path)


def test_get_generations_no_filter(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    rows = database.get_generations(db_path=db)
    assert len(rows) == 3


def test_get_generations_with_status_filter(tmp_path):
    """Regression: filtered query must include the WHERE keyword."""
    db = tmp_path / "t.db"
    _seed(db)
    rows = database.get_generations(status="success", db_path=db)
    assert len(rows) == 2
    assert all(r["status"] == "success" for r in rows)


def test_get_generations_with_status_and_category(tmp_path):
    db = tmp_path / "t.db"
    _seed(db)
    rows = database.get_generations(status="success", category="anime", db_path=db)
    assert len(rows) == 1
    assert rows[0]["quote_text"] == "Q1"


def test_get_used_count_missing_tables(tmp_path):
    """Must not crash when called before init (e.g. /config pre-lifespan)."""
    db = tmp_path / "fresh.db"
    assert database.get_used_count(db_path=db) == 0
