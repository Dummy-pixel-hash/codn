"""Unit tests for the FIFO generation job queue (no network, no GPU)."""

import threading
import time

import pytest
from fastapi.testclient import TestClient

import comfy_manager
import config
import database
import llama_manager
import text_overlay
import trending
import main


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    out = tmp_path / "output"
    out.mkdir()
    db = tmp_path / "t.db"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "job-token")
    monkeypatch.setattr(database, "QUOTES_DB", db)
    database.init_db()
    database.init_generations_table()

    # Fast fake pipeline: no processes, no network.
    monkeypatch.setattr(llama_manager, "is_running", lambda: True)
    monkeypatch.setattr(comfy_manager, "is_running", lambda: True)
    prompts_seen = []

    def fake_art_prompt(proc, prompt):
        prompts_seen.append(prompt)
        return "fake art prompt"

    monkeypatch.setattr(llama_manager, "generate_art_prompt", fake_art_prompt)
    monkeypatch.setattr(main.llama_manager, "generate_art_prompt", fake_art_prompt)
    # Exposed for tests asserting quote-first briefing.
    monkeypatch.setattr(main, "_test_prompts_seen", prompts_seen, raising=False)
    monkeypatch.setattr(
        llama_manager, "pick_quote", lambda category=None, theme=None: []
    )
    monkeypatch.setattr(main.llama_manager, "pick_quote", lambda category=None, theme=None: [])
    monkeypatch.setattr(
        llama_manager,
        "generate_text_overlay",
        lambda proc, img, art="", category="", theme="", max_tokens=512, recalled=None: {
            "quote": "Fake quote.",
            "author": "QA",
            "tag": "ocean-calm",  # sanitised form, as _normalise_overlay yields
        },
    )
    monkeypatch.setattr(trending, "get_trending_terms", lambda: [])
    monkeypatch.setattr(
        comfy_manager, "generate_art", lambda prompt, outdir=config.OUTPUT_DIR, stop=None: str(out / "art.jpg")
    )

    def fake_overlay(src, dst, style):
        from PIL import Image

        Image.new("RGB", (64, 64), (1, 2, 3)).save(dst, "JPEG")
        return dst

    monkeypatch.setattr(text_overlay, "add_text_overlay", fake_overlay)
    # main.py imported text_overlay module object; patching the module attr works.
    monkeypatch.setattr(main.text_overlay, "add_text_overlay", fake_overlay)
    monkeypatch.setattr(main.llama_manager, "is_running", lambda: True)
    monkeypatch.setattr(main.comfy_manager, "is_running", lambda: True)

    with TestClient(main.app) as client:
        client.headers.update({"Authorization": "Bearer job-token"})
        yield client


def _wait_for(client, job_id, want=("done", "error", "cancelled"), timeout=15):
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}")
        assert r.status_code == 200
        last = r.json()
        if last["status"] in want:
            return last
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} never finished: {last}")


def test_generate_enqueues_and_completes(app_client):
    r = app_client.post("/generate", json={"theme": "stoic ocean vibes", "upload": False})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = _wait_for(app_client, job_id)
    assert job["status"] == "done"
    assert job["phase"] == "done"
    assert job["result"]["status"] == "generated"
    assert job["result"]["image_url"].startswith("/media/")
    # Model-derived tag stored as the grouping category.
    assert job["result"]["overlay"]["tag"] == "ocean-calm"
    import database

    rows = database.get_generations()
    assert rows and rows[0]["category"] == "ocean-calm"


def test_generate_validation_still_422(app_client):
    r = app_client.post("/generate", json={"theme": "x" * 500})
    assert r.status_code == 422


def test_job_auth_required(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "API_TOKEN", "job-token")
    with TestClient(main.app) as c:
        assert c.post("/generate", json={}).status_code == 401
        assert c.get("/jobs/nope").status_code == 401


def test_job_not_found(app_client):
    assert app_client.get("/jobs/doesnotexist").status_code == 404
    assert app_client.delete("/jobs/doesnotexist").status_code == 404


def test_cancel_running_job(tmp_path, monkeypatch):
    """Block the fake pipeline, cancel mid-run, expect cancelled status."""
    out = tmp_path / "output"
    out.mkdir()
    db = tmp_path / "t.db"
    monkeypatch.setattr(config, "OUTPUT_DIR", out)
    monkeypatch.setattr(config, "API_TOKEN", "job-token")
    monkeypatch.setattr(database, "QUOTES_DB", db)
    database.init_db()
    database.init_generations_table()

    gate = threading.Event()
    monkeypatch.setattr(llama_manager, "is_running", lambda: True)
    monkeypatch.setattr(comfy_manager, "is_running", lambda: True)

    def slow_prompt(proc, prompt):
        assert gate.wait(timeout=15)
        return "slow art"

    monkeypatch.setattr(llama_manager, "generate_art_prompt", slow_prompt)
    monkeypatch.setattr(main.llama_manager, "is_running", lambda: True)
    monkeypatch.setattr(main.comfy_manager, "is_running", lambda: True)

    with TestClient(main.app) as client:
        client.headers.update({"Authorization": "Bearer job-token"})
        r = client.post("/generate", json={"upload": False})
        job_id = r.json()["job_id"]
        # Wait until the worker picked it up, then cancel.
        deadline = time.time() + 10
        while time.time() < deadline:
            if client.get(f"/jobs/{job_id}").json()["status"] == "running":
                break
            time.sleep(0.1)
        d = client.delete(f"/jobs/{job_id}")
        assert d.status_code == 200
        gate.set()  # let the blocked phase return; worker must observe cancel
        job = _wait_for(client, job_id, want=("cancelled",))
        assert job["status"] == "cancelled"


def test_jobs_list(app_client):
    r = app_client.post("/generate", json={"upload": False})
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    _wait_for(app_client, job_id)
    lst = app_client.get("/jobs")
    assert lst.status_code == 200
    assert any(j["job_id"] == job_id for j in lst.json()["jobs"])


def test_art_prompt_illustrates_picked_quote(app_client, monkeypatch):
    """Quote-first: the pre-picked quote must reach the art-prompt brief."""
    picked = [{"quote": "Picked line here.", "author": "QA", "source": "Wikiquote"}]
    monkeypatch.setattr(llama_manager, "pick_quote", lambda *a, **k: picked)
    monkeypatch.setattr(main.llama_manager, "pick_quote", lambda *a, **k: picked)
    r = app_client.post("/generate", json={"theme": "resilience", "upload": False})
    assert r.status_code == 202
    job = _wait_for(app_client, r.json()["job_id"])
    assert job["status"] == "done"
    assert any("Picked line here." in p for p in main._test_prompts_seen)


def test_art_brief_names_subject_and_signifiers(app_client, monkeypatch):
    """Picked character must steer the art brief (no more random men)."""
    picked = [{"quote": "I am Iron Man.", "character": "Tony Stark", "source": "Iron Man"}]
    monkeypatch.setattr(llama_manager, "pick_quote", lambda *a, **k: picked)
    monkeypatch.setattr(main.llama_manager, "pick_quote", lambda *a, **k: picked)
    r = app_client.post("/generate", json={"theme": "tony stark", "upload": False})
    assert r.status_code == 202
    job = _wait_for(app_client, r.json()["job_id"])
    assert job["status"] == "done"
    briefs = main._test_prompts_seen
    assert briefs, "art prompt should have been requested"
    assert any("Tony Stark" in p and "arc-reactor" in p for p in briefs)
    assert any("Do NOT show the face" in p and "blurred beyond" in p for p in briefs)
