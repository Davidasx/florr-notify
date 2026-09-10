"""Tests for the notifier's icon download + content validation.

The Discord CDN occasionally returns non-image payloads (HTML error pages on
403/expired/rate-limit). Without validation, those get cached and show up as
"broken image" icons in the desktop notification. These tests pin the fix.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch


from florr_notify.notify import (
    DEFAULT_ICON,
    Notifier,
    _is_valid_image_bytes,
    _is_valid_image_file,
    _sync_download,
)
from florr_notify.config import NotifyConfig


# --- magic-byte helper ---

def test_is_valid_image_bytes_png():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert _is_valid_image_bytes(png)


def test_is_valid_image_bytes_webp():
    webp = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBPVP8 " + b"\x00" * 100
    assert _is_valid_image_bytes(webp)


def test_is_valid_image_bytes_rejects_html():
    assert not _is_valid_image_bytes(b"<html>403 Forbidden</html>")


def test_is_valid_image_bytes_rejects_empty():
    assert not _is_valid_image_bytes(b"")
    assert not _is_valid_image_bytes(b"\x89PNG")  # truncated


# --- _sync_download (with mocked urlopen) ---

URL = "https://cdn.discordapp.com/attachments/X/petal-wasp-super.png"


def _urlopen_mock(body: bytes, content_type: str = "image/png"):
    """Build a (urlopen_mock, resp_mock) pair.

    `with urlopen(req) as resp:` must bind `resp` to our configured response
    (with .read() and .headers), so we wire `__enter__.return_value` to the
    same resp MagicMock. The outer `urlopen_mock` is what we hand to `patch`.
    """
    resp = MagicMock()
    resp.read.return_value = body
    # Use a real dict for headers (it already has .get); no attribute tricks.
    resp.headers = {"Content-Type": content_type}
    urlopen = MagicMock()
    urlopen.__enter__.return_value = resp
    urlopen.__exit__.return_value = False
    return urlopen, resp


def test_sync_download_accepts_png(tmp_path: Path):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    dest = tmp_path / "ok.png"
    urlopen, _ = _urlopen_mock(png)
    with patch("urllib.request.urlopen", return_value=urlopen):
        assert _sync_download(URL, dest) is True
    assert dest.exists() and dest.read_bytes() == png


def test_sync_download_accepts_webp(tmp_path: Path):
    webp = b"RIFF" + b"\x24\x00\x00\x00" + b"WEBPVP8 " + b"\x00" * 100
    dest = tmp_path / "ok.webp"
    urlopen, _ = _urlopen_mock(webp, "image/webp")
    with patch("urllib.request.urlopen", return_value=urlopen):
        assert _sync_download(URL, dest) is True
    assert dest.exists()


def test_sync_download_rejects_html_garbage(tmp_path: Path):
    """THE BUG: CDN returns HTML on 403; old code saved it; notification
    daemon showed it as a broken image. We must refuse to cache non-images."""
    html = b"<!DOCTYPE html><html><body>403 Forbidden</body></html>"
    dest = tmp_path / "bad.png"
    urlopen, _ = _urlopen_mock(html, "text/html")
    with patch("urllib.request.urlopen", return_value=urlopen):
        assert _sync_download(URL, dest) is False
    assert not dest.exists()


def test_sync_download_rejects_wrong_magic_bytes(tmp_path: Path):
    """Content-Type says image but body is not an image (e.g. truncated PNG
    or a renamed text file)."""
    bad = b"this is not an image at all, despite the header"
    dest = tmp_path / "bad2.png"
    urlopen, _ = _urlopen_mock(bad, "image/png")
    with patch("urllib.request.urlopen", return_value=urlopen):
        assert _sync_download(URL, dest) is False
    assert not dest.exists()


def test_sync_download_rejects_empty_body(tmp_path: Path):
    dest = tmp_path / "empty.png"
    urlopen, _ = _urlopen_mock(b"")
    with patch("urllib.request.urlopen", return_value=urlopen):
        assert _sync_download(URL, dest) is False
    assert not dest.exists()


def test_sync_download_rejects_network_error(tmp_path: Path):
    dest = tmp_path / "net.png"
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert _sync_download(URL, dest) is False
    assert not dest.exists()


# --- _is_valid_image_file (cache validation) ---

def test_is_valid_image_file_accepts_real_png(tmp_path: Path):
    p = tmp_path / "real.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
    assert _is_valid_image_file(p) is True


def test_is_valid_image_file_rejects_garbage(tmp_path: Path):
    p = tmp_path / "garbage.png"
    p.write_bytes(b"<html>old bad download</html>")
    assert _is_valid_image_file(p) is False


def test_is_valid_image_file_missing(tmp_path: Path):
    p = tmp_path / "nope.png"
    assert _is_valid_image_file(p) is False


# --- Notifier._icon end-to-end (asyncio, mocked download) ---

async def test_notifier_icon_falls_back_when_download_fails(tmp_path, monkeypatch):
    """End-to-end: bad download -> fallback to DEFAULT_ICON, nothing cached."""
    monkeypatch.setattr("florr_notify.notify.ICON_CACHE_DIR", tmp_path)
    notifier = Notifier(NotifyConfig(desktop=True))
    urlopen, _ = _urlopen_mock(b"<html>403</html>", "text/html")
    with patch("urllib.request.urlopen", return_value=urlopen):
        icon = await notifier._icon(URL, "petal-wasp-super.png")
    assert icon == DEFAULT_ICON
    assert not (tmp_path / "petal-wasp-super.png").exists()


async def test_notifier_icon_caches_valid_and_reuses(tmp_path, monkeypatch):
    """Good download -> cached, then reused on second call (no re-download)."""
    monkeypatch.setattr("florr_notify.notify.ICON_CACHE_DIR", tmp_path)
    notifier = Notifier(NotifyConfig(desktop=True))
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    call_count = {"n": 0}

    def fake_urlopen(req, timeout=10):
        call_count["n"] += 1
        u, _ = _urlopen_mock(png)
        return u

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        icon1 = await notifier._icon(URL, "petal-wasp-super.png")
        icon2 = await notifier._icon(URL, "petal-wasp-super.png")

    assert call_count["n"] == 1
    assert icon1 == icon2
    assert icon1.endswith("petal-wasp-super.png")


async def test_notifier_icon_evicts_bad_cache(tmp_path, monkeypatch):
    """A previously-cached bad file (e.g. from a pre-fix run) is detected
    and removed; a fresh download replaces it."""
    monkeypatch.setattr("florr_notify.notify.ICON_CACHE_DIR", tmp_path)
    notifier = Notifier(NotifyConfig(desktop=True))
    bad = tmp_path / "petal-wasp-super.png"
    bad.write_bytes(b"<html>stale garbage</html>")  # pre-existing bad cache

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    urlopen, _ = _urlopen_mock(png)
    with patch("urllib.request.urlopen", return_value=urlopen):
        icon = await notifier._icon(URL, "petal-wasp-super.png")
    assert icon.endswith("petal-wasp-super.png")
    assert _is_valid_image_file(bad)


def test_cleanup_existing_bad_caches_in_data_icons(tmp_path):
    """Smoke test: walk ICON_CACHE_DIR and delete any file whose magic bytes
    don't match an image format. The user can run a one-shot CLI to clean
    up after the bug."""
    good_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    (tmp_path / "good.png").write_bytes(good_png)
    (tmp_path / "bad1.png").write_bytes(b"<html>stale</html>")
    (tmp_path / "bad2.png").write_bytes(b"\x00\x00\x00\x00not an image")

    for p in tmp_path.iterdir():
        if p.is_file() and not _is_valid_image_file(p):
            p.unlink()

    survivors = sorted(p.name for p in tmp_path.iterdir())
    assert survivors == ["good.png"]
