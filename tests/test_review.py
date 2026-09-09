"""Unit tests for the review-first flow: overlay persistence, font merge, history."""

import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import config
import database
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "review-token")
    monkeypatch.setattr(database, "QUOTES_DB", tmp_path / "r.db")
    database.init_db()
    database.init_generations_table()
    c = TestClient(main.app, raise_server_exceptions=False)
    c.headers.update({"Authorization": "Bearer review-token"})
    return c


def _make_src(name="src.jpg"):
    out = config.OUTPUT_DIR
    p = out / name
    Image.new("RGB", (200, 200), (40, 50, 60)).save(p, "JPEG")
    return name


def test_migration_adds_overlay_column(tmp_path):
    """Old DBs without overlay_json gain it via init (no data loss)."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE generations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " status TEXT NOT NULL, category TEXT, theme TEXT, quote_text TEXT,"
        " author TEXT, art_prompt TEXT, image_filename TEXT,"
        " error_message TEXT,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO generations (status, quote_text) VALUES ('success', 'Q')"
    )
    conn.commit()
    conn.close()
    database.init_generations_table(db)
    rows = database.get_generations(db_path=db)
    assert len(rows) == 1
    assert rows[0]["overlay"] is None


def test_overlay_round_trip(tmp_path):
    db = tmp_path / "rt.db"
    database.init_db(db)
    database.init_generations_table(db)
    overlay = {"quote": "Q.", "font": "Inter", "font_size": "xlarge", "position": "bottom"}
    database.save_generation(status="success", quote_text="Q.", image_filename="a.jpg",
                             overlay=overlay, db_path=db)
    rows = database.get_generations(db_path=db)
    assert rows[0]["overlay"] == overlay


def test_edit_accepts_xlarge_and_short_positions(client):
    name = _make_src()
    for size in ("small", "medium", "large", "xlarge"):
        r = client.post("/preview-overlay", json={
            "filename": name, "quote_text": "Hi.", "author": "A",
            "font_size": size, "position": "bottom",
        })
        assert r.status_code == 200, (size, r.text)
    r = client.post("/preview-overlay", json={
        "filename": name, "quote_text": "Hi.", "author": "A", "position": "top",
    })
    assert r.status_code == 200


def test_edit_preserves_font(client):
    """Generated typography round-trips; only the 5 visible fields change."""
    name = _make_src()
    body = {
        "filename": name, "quote_text": "Edited.", "author": "B",
        "font_size": "large", "text_color": "#FF0000", "position": "top-center",
        "font": "Inter", "font_style": "sans-serif", "font_weight": "bold",
        "text_effect": "glow", "glow_color": "#00FF00", "letter_spacing": 4,
        "x_offset": 5, "y_offset": -3,
    }
    r = client.post("/preview-overlay", json=body)
    assert r.status_code == 200
    # Apply records a history row carrying the merged overlay.
    r = client.post("/apply-overlay", json=body)
    assert r.status_code == 200
    assert r.json()["image_url"].startswith("/media/")
    rows = database.get_generations()
    assert len(rows) == 1
    ov = rows[0]["overlay"]
    assert ov["quote"] == "Edited."
    assert ov["font"] == "Inter"
    assert ov["font_weight"] == "bold"
    assert ov["text_effect"] == "glow"
    assert ov["letter_spacing"] == 4
    assert ov["x_offset"] == 5.0


def test_apply_updates_in_place_single_composition(client):
    """Apply re-renders onto the same file and updates its row — no 2nd file."""
    name = _make_src()
    database.save_generation(status="success", quote_text="Old.", image_filename=name)
    before = {p.name for p in config.OUTPUT_DIR.iterdir()}
    r = client.post("/apply-overlay", json={"filename": name, "quote_text": "New.", "author": "A"})
    assert r.status_code == 200
    assert r.json()["image_url"].startswith(f"/media/{name}")
    after = {p.name for p in config.OUTPUT_DIR.iterdir()}
    # No second composition file may appear.
    assert {f for f in after - before if f.endswith(".jpg")} == set()
    rows = database.get_generations()
    assert len(rows) == 1  # same row updated, not a second row
    assert rows[0]["quote_text"] == "New."
    assert rows[0]["updated_at"]


def test_apply_records_history_visible_in_list(client):
    name = _make_src()
    client.post("/apply-overlay", json={"filename": name, "quote_text": "H.", "author": "A"})
    r = client.get("/history")
    assert r.status_code == 200
    gens = r.json()["generations"]
    assert len(gens) == 1
    assert gens[0]["image_filename"] == name


def test_apply_renders_from_clean_source(client, monkeypatch):
    """Edits render from base art onto the composition — never stacks text."""
    import text_overlay as text_overlay_mod

    seen = {}

    def fake_overlay(src, dst, style):
        seen["src"] = src
        seen["dst"] = dst
        # Copy src -> dst so the file updates without fonts.
        from PIL import Image

        Image.open(src).save(dst, "JPEG")
        return dst

    monkeypatch.setattr(text_overlay_mod, "add_text_overlay", fake_overlay)
    monkeypatch.setattr(main.text_overlay, "add_text_overlay", fake_overlay)

    clean = _make_src("clean.jpg")
    comp = _make_src("comp.jpg")
    database.save_generation(status="success", quote_text="Old.", image_filename=comp,
                             source_filename=clean)
    r = client.post("/apply-overlay", json={"filename": comp, "quote_text": "New."})
    assert r.status_code == 200
    assert seen["src"].endswith("clean.jpg")
    assert seen["dst"].endswith("comp.jpg")


def test_apply_legacy_row_renders_onto_itself(client, monkeypatch):
    """Rows without a stored source fall back to editing the file itself."""
    import text_overlay as text_overlay_mod

    seen = {}

    def fake_overlay(src, dst, style):
        seen["src"] = src
        seen["dst"] = dst
        return dst

    monkeypatch.setattr(text_overlay_mod, "add_text_overlay", fake_overlay)
    monkeypatch.setattr(main.text_overlay, "add_text_overlay", fake_overlay)

    name = _make_src("legacy.jpg")
    r = client.post("/apply-overlay", json={"filename": name, "quote_text": "New."})
    assert r.status_code == 200
    assert seen["src"] == seen["dst"]


def test_upload_modal_contract(client):
    """POST /upload accepts a bare filename from history/result cards."""
    name = _make_src()
    r = client.post("/upload", json={"image_path": name, "caption": "hi"})
    # No IG creds in test env → uploader returns None → status failed (not 4xx).
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
