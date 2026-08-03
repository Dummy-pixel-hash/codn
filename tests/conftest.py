"""Shared fixtures and path setup for the codn test suite."""

import sys
from pathlib import Path

import pytest
import database

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolate_quote_db(monkeypatch):
    """Keep tests independent of the live quotes.db state."""
    monkeypatch.setattr(database, "get_used_quote_keys", lambda *a, **k: set())
    monkeypatch.setattr(database, "save_quote", lambda *a, **k: None)


@pytest.fixture
def base_image(tmp_path):
    """Create a small solid-color RGB image for renderer tests."""
    from PIL import Image

    img_path = tmp_path / "base.png"
    Image.new("RGB", (400, 400), (30, 40, 60)).save(img_path)
    return str(img_path)
