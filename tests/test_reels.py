"""Unit tests for the reels upload flow (no network — requests mocked)."""

import pytest

import instagram_uploader


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or "{}"
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        return self._payload


@pytest.fixture
def img(tmp_path):
    from PIL import Image

    p = tmp_path / "reel.jpg"
    Image.new("RGB", (64, 64), (5, 6, 7)).save(p, "JPEG")
    return str(p)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setattr(instagram_uploader, "INSTAGRAM_ACCESS_TOKEN", "tok")
    monkeypatch.setattr(instagram_uploader, "INSTAGRAM_BUSINESS_ID", "123")
    monkeypatch.setattr(
        instagram_uploader, "_public_image_url", lambda p: "https://cdn/x.jpg"
    )
    monkeypatch.setattr(instagram_uploader, "_check_public_image", lambda u: True)
    monkeypatch.setattr(instagram_uploader, "_wait_for_container", lambda cid, timeout=120: True)


def test_reel_success_no_leak(img, creds, monkeypatch):
    """Happy path: container + publish, all requests carry timeouts."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        assert "timeout" in kwargs, "every IG request must have a timeout"
        if url.endswith("/media"):
            return _Resp(200, {"id": "container-1"})
        return _Resp(200, {"id": "media-1"})

    monkeypatch.setattr(instagram_uploader.requests, "post", fake_post)
    out = instagram_uploader.upload_reel(img, "cap", "music-9")
    assert out == {"id": "media-1"}
    assert len(calls) == 2


def test_reel_falls_back_to_post_on_rejection(img, creds, monkeypatch):
    """REELS rejected → single fallback to image post, no recursion."""
    seen = []

    def fake_post(url, **kwargs):
        seen.append(url)
        if url.endswith("/media"):
            # First call (REELS attempt) fails; fallback post succeeds.
            if not any("media_publish" in u for u in seen[:-1]) and len(
                [u for u in seen if u.endswith("/media")] ) == 1:
                return _Resp(400, {"error": {"message": "bad"}}, text='{"error":{}}')
            return _Resp(200, {"id": "container-2"})
        return _Resp(200, {"id": "media-2"})

    monkeypatch.setattr(instagram_uploader.requests, "post", fake_post)
    out = instagram_uploader.upload_reel(img, "cap")
    assert out == {"id": "media-2"}
    # One REELS attempt + one fallback container + one publish.
    assert len(seen) == 3


def test_reel_missing_creds_returns_none(img, monkeypatch):
    monkeypatch.setattr(instagram_uploader, "INSTAGRAM_ACCESS_TOKEN", "")
    assert instagram_uploader.upload_reel(img, "cap") is None
