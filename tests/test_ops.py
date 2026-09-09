"""Unit tests for readiness probe + VRAM wait (no GPU needed)."""

import time

from fastapi.testclient import TestClient

import config
import llama_manager
import main


def test_readyz_ok(tmp_path, monkeypatch):
    out = tmp_path / "output"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "QUOTES_DB", tmp_path / "r.db")
    with TestClient(main.app) as client:
        r = client.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["ready"] is True
        assert body["checks"]["output_dir"] == "ok"
        assert body["checks"]["database"] == "ok"
        assert body["checks"]["disk"] == "ok"


def test_vram_wait_no_tooling_returns_fast(monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
    start = time.time()
    assert llama_manager._wait_for_vram_free() is True
    assert time.time() - start < 10  # grace sleep only, not the old 60s loop


def test_vram_wait_free_gpu(monkeypatch, tmp_path):
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda *_a, **_k: "/usr/bin/nvidia-smi")

    class R:
        stdout = "512\n300\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    assert llama_manager._wait_for_vram_free(timeout=5) is True
