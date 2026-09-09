"""Unit tests for the fail-closed bearer-token guard (no network)."""

import pytest
from fastapi.testclient import TestClient

import config
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "secret-token")
    return TestClient(main.app, raise_server_exceptions=False)


def test_public_routes_need_no_token(client):
    assert client.get("/health").status_code == 200
    assert client.get("/robots.txt").status_code == 200


def test_guarded_routes_reject_missing_token(client):
    assert client.get("/config").status_code == 401
    assert client.get("/quotes").status_code == 401
    assert client.get("/history").status_code == 401
    assert client.get("/comfy/status").status_code == 401
    assert client.post("/generate", json={}).status_code == 401
    assert client.post("/upload", json={}).status_code == 401
    assert client.post(
        "/preview-overlay",
        json={"filename": "x.jpg", "quote_text": "q", "author": "a"},
    ).status_code == 401


def test_wrong_token_forbidden(client):
    h = {"Authorization": "Bearer wrong"}
    assert client.get("/config", headers=h).status_code == 403
    assert client.post("/generate", json={}, headers=h).status_code == 403


def test_correct_token_allowed(client):
    h = {"Authorization": "Bearer secret-token"}
    # /config may return empty account, but auth must pass (not 401/403).
    assert client.get("/config", headers=h).status_code in (200, 500)


def test_unconfigured_token_returns_503(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "")
    c = TestClient(main.app, raise_server_exceptions=False)
    assert c.get("/config").status_code == 503
    assert c.post("/generate", json={}).status_code == 503
    # Public routes still work with no token configured.
    assert c.get("/health").status_code == 200


def _auth():
    return {"Authorization": "Bearer secret-token"}


def test_request_id_header_present(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID")


def test_generate_rejects_oversized_theme(client):
    r = client.post("/generate", json={"theme": "x" * 500}, headers=_auth())
    assert r.status_code == 422


def test_preview_rejects_invalid_position(client):
    r = client.post("/preview-overlay", json={
        "filename": "x.jpg", "quote_text": "q", "author": "a",
        "position": "somewhere-weird",
    }, headers=_auth())
    assert r.status_code == 422


def test_preview_rejects_oversized_quote(client):
    r = client.post("/preview-overlay", json={
        "filename": "x.jpg", "quote_text": "q" * 5000, "author": "a",
    }, headers=_auth())
    assert r.status_code == 422


def test_api_tunnel_public_no_token(client):
    r = client.get("/api/tunnel")
    assert r.status_code == 200
    body = r.json()
    assert "url" in body and "engine" in body


def test_add_quote_requires_token(client):
    r = client.post("/api/quotes", json={"quote_text": "q", "author": "a"})
    assert r.status_code == 401


def test_add_quote_validation(client):
    assert client.post("/api/quotes", json={}, headers=_auth()).status_code == 422
    assert client.post(
        "/api/quotes", json={"quote_text": "   "}, headers=_auth()
    ).status_code == 400


def test_add_quote_saved(tmp_path, monkeypatch):
    import database
    db = tmp_path / "q.db"
    database.init_db(db)
    database.init_generations_table(db)
    monkeypatch.setattr(database, "QUOTES_DB", db)
    # Route handlers reference database module attrs at call time for
    # save_quote/save_generation, but get_* read QUOTES_DB global — patch it.
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "secret-token")
    c = TestClient(main.app, raise_server_exceptions=False)
    r = c.post("/api/quotes",
               json={"quote_text": "A saved line.", "author": "Me", "category": "general"},
               headers=_auth())
    assert r.status_code == 200
    assert r.json()["status"] == "saved"
    rows = database.get_generations(db_path=db)
    assert len(rows) == 1 and rows[0]["quote_text"] == "A saved line."
