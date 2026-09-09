"""Unit tests for llama/comfy start semantics (no network, no processes)."""

import comfy_manager
import llama_manager


def test_start_llama_already_running_returns_none(monkeypatch):
    """Must return None (not True) so callers never store a bool as a proc handle."""
    monkeypatch.setattr(llama_manager, "is_running", lambda: True)
    assert llama_manager.start_llama() is None


def test_start_comfyui_already_running_returns_none(monkeypatch):
    monkeypatch.setattr(comfy_manager, "is_running", lambda: True)
    assert comfy_manager.start_comfyui() is None


def test_stop_llama_none_is_noop():
    """Stopping with no owned handle must not raise."""
    llama_manager.stop_llama(None, wait_for_vram=False)
