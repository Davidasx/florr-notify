"""Async desktop notification dispatcher with mob-icon download/cache.

The mob thumbnail is downloaded from the Discord CDN on first use and cached
locally at ./data/icons/<filename>, then passed to `notify-send -i` so the
notification shows the actual mob picture instead of a generic icon.

Discord's CDN (cdn.discordapp.com) requires a browser-like User-Agent
(otherwise 403). Payloads are validated by magic bytes (PNG/WebP) before
caching, and cached files are re-validated on use, so a bad file from an
older buggy run is transparently removed.

Icon paths are ALWAYS absolute: the notification daemon runs as a separate
D-Bus process and resolves relative paths against its own CWD.

Silent mode (config [notify] silent = true) suppresses all desktop
notifications and icon downloads; events are still stored and logged.
"""
from __future__ import annotations
import asyncio
import logging
import subprocess
import urllib.request
from pathlib import Path

from .config import NotifyConfig
from .parser import SpawnEvent, KillEvent

log = logging.getLogger(__name__)

DEFAULT_ICON = "dialog-information"
ICON_CACHE_DIR = Path("./data/icons")
_DOWNLOAD_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Magic-byte signatures of the two image formats Discord's CDN serves us.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_WEBP_MAGIC_HEAD = b"RIFF"
_WEBP_MAGIC_TAIL = b"WEBP"


def _is_valid_image_bytes(data: bytes) -> bool:
    if len(data) < 12:
        return False
    if data[:8] == _PNG_MAGIC:
        return True
    if data[:4] == _WEBP_MAGIC_HEAD and data[8:12] == _WEBP_MAGIC_TAIL:
        return True
    return False


def _is_valid_image_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(12)
        return _is_valid_image_bytes(head)
    except OSError:
        return False


def _sync_download(url: str, dest: Path) -> bool:
    """Blocking download with content validation. Run via asyncio.to_thread."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": _DOWNLOAD_UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
        if not data:
            log.warning("icon download returned empty body: %s", url)
            return False
        if not _is_valid_image_bytes(data):
            log.warning(
                "icon download failed content check (not a PNG/WebP): "
                "%s (%d bytes, head=%r)",
                url, len(data), data[:16],
            )
            return False
        dest.write_bytes(data)
        log.info("icon cached: %s (%d bytes)", dest, len(data))
        return True
    except Exception as e:
        log.warning("icon download failed for %s: %s", url, e)
        return False


class Notifier:
    """Async wrapper over `notify-send` with per-(mob,rarity,kind) cooldown."""

    def __init__(self, cfg: NotifyConfig):
        self.cfg = cfg
    async def _icon(self, image_url: str, filename: str) -> str:
        """Return a local ABSOLUTE file path for the icon, or DEFAULT_ICON."""
        if not self.cfg.desktop or not filename:
            return DEFAULT_ICON
        dest = ICON_CACHE_DIR / filename
        if dest.exists() and dest.stat().st_size > 0:
            if _is_valid_image_file(dest):
                return str(dest.resolve())
            log.warning("cached icon is not a valid image, removing: %s", dest)
            try:
                dest.unlink()
            except OSError:
                pass
        if not image_url:
            return DEFAULT_ICON
        ok = await asyncio.to_thread(_sync_download, image_url, dest)
        if not ok:
            try:
                if dest.exists() and dest.stat().st_size == 0:
                    dest.unlink()
            except OSError:
                pass
            return DEFAULT_ICON
        return str(dest.resolve())

    async def _fire(
        self, mob: str, rarity: str, kind: str,
        title: str, body: str, icon: str,
    ) -> None:
        if not self.cfg.desktop:
            return
        try:
            await asyncio.to_thread(
                subprocess.run,
                [
                    "notify-send",
                    "-u", "normal",
                    "-a", "florr-notify",
                    "-c", "game",
                    "-i", icon,
                    title,
                    body,
                ],
                check=False,
                timeout=5,
            )
        except FileNotFoundError:
            log.warning("notify-send not installed; disabling desktop notifications")
            self.cfg.desktop = False
        except Exception as ex:  # pragma: no cover
            log.warning("notify-send failed: %s", ex)

    async def spawn(
        self, e: SpawnEvent, *, prediction: str | None = None,
    ) -> None:
        if self.cfg.silent:
            return
        icon = await self._icon(e.image_url, e.image_filename)
        body = e.region
        if prediction:
            body += f"  ·  {prediction}"
        await self._fire(
            e.mob, e.rarity, "spawn",
            title=f"{e.rarity.capitalize()} {e.mob} spawned",
            body=body, icon=icon,
        )

    async def kill(self, e: KillEvent) -> None:
        if self.cfg.silent:
            return
        icon = await self._icon(e.image_url, e.image_filename)
        killers = ", ".join(e.killers) if e.killers else "?"
        body = f"{e.region}  ·  by {killers}"
        await self._fire(
            e.mob, e.rarity, "kill",
            title=f"{e.rarity.capitalize()} {e.mob} defeated",
            body=body, icon=icon,
        )
