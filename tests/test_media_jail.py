"""Unit tests for media serving jail + overlay/upload path validation."""

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import config
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    # Point the app at an isolated output dir.
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "test-token")
    c = TestClient(main.app, raise_server_exceptions=False)
    c.headers.update({"Authorization": "Bearer test-token"})
    return c


def _make_jpg(path, size=(64, 64)):
    Image.new("RGB", size, (10, 20, 30)).save(path, "JPEG")
    return path


def test_serve_media_file(client, tmp_path):
    out = config.OUTPUT_DIR
    _make_jpg(out / "a.jpg")
    r = client.get("/media/a.jpg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/")


def test_serve_media_preview_subdir(client):
    out = config.OUTPUT_DIR
    prev = out / "_previews"
    prev.mkdir()
    _make_jpg(prev / "p.jpg")
    r = client.get("/media/_previews/p.jpg")
    assert r.status_code == 200


def test_serve_media_traversal_blocked(client):
    assert client.get("/media/../main.py").status_code == 404
    assert client.get("/media/%2e%2e/main.py").status_code in (404, 400)
    assert client.get("/media/nope.jpg").status_code == 404


def test_resolve_image_rejects_escape():
    import pytest as _pt
    with _pt.raises(Exception):
        main._resolve_image("/etc/passwd")
    with _pt.raises(Exception):
        main._resolve_image("../../etc/passwd")
    with _pt.raises(Exception):
        main._resolve_image("evil.txt")


def test_preview_overlay_invalid_filename(client):
    r = client.post("/preview-overlay", json={
        "filename": "/etc/passwd",
        "quote_text": "hi",
        "author": "x",
    })
    assert r.status_code in (400, 404)


def test_upload_rejects_outside_jail(client):
    r = client.post("/upload", json={"image_path": "/etc/hostname", "caption": "x"})
    assert r.status_code in (400, 404)
