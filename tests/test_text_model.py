"""Unit tests for the text-model switch API (no GPU, no .env writes outside tmp)."""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path

import config
import llama_manager
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "model-token")
    # Isolate .env persistence + model path globals (auto-restored).
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "LLAMA_MODEL_PATH", Path("/orig/model.gguf"))
    monkeypatch.setattr(llama_manager, "LLAMA_MODEL_PATH", Path("/orig/model.gguf"))
    c = TestClient(main.app, raise_server_exceptions=False)
    c.headers.update({"Authorization": "Bearer model-token"})
    return c


def test_get_text_model(client):
    r = client.get("/api/text-model")
    assert r.status_code == 200
    assert r.json()["path"] == "/orig/model.gguf"
    assert r.json()["exists"] is False


def test_text_model_requires_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "model-token")
    with TestClient(main.app) as c:
        assert c.get("/api/text-model").status_code == 401
        assert c.post("/api/text-model", json={"path": "/x.gguf"}).status_code == 401


def test_set_text_model_rejects_missing(client):
    r = client.post("/api/text-model", json={"path": "/nonexistent/model.gguf"})
    assert r.status_code == 400


def test_set_text_model_rejects_non_gguf(client, tmp_path):
    f = tmp_path / "model.bin"
    f.write_text("x")
    r = client.post("/api/text-model", json={"path": str(f)})
    assert r.status_code == 400


def test_set_text_model_switches_and_persists(client, tmp_path):
    f = tmp_path / "Other-7B-Q4.gguf"
    f.write_text("fake")
    r = client.post("/api/text-model", json={"path": str(f)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(f.resolve())
    assert body["persisted"] is True
    # Both module refs updated (llama_manager binds at import).
    assert str(config.LLAMA_MODEL_PATH) == str(f.resolve())
    assert str(llama_manager.LLAMA_MODEL_PATH) == str(f.resolve())
    # .env got the new value.
    env_text = (tmp_path / ".env").read_text()
    assert f"LLAMA_MODEL_PATH={f.resolve()}" in env_text
    # GET reflects the switch.
    assert client.get("/api/text-model").json()["path"] == str(f.resolve())
