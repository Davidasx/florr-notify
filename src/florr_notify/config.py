"""Configuration loader for florr-notify."""
from __future__ import annotations
import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscordConfig:
    token: str
    game_channel_id: int
    guild_id: int | None


@dataclass(frozen=True)
class StorageConfig:
    db_path: Path


@dataclass(frozen=True)
class NotifyConfig:
    desktop: bool
    silent: bool = False   # true -> no desktop notifications (still stores + logs)


@dataclass(frozen=True)
class WhitelistConfig:
    """Per-kind announcement whitelists.

    None  = key absent from config -> no filtering (report everything).
    tuple = key present (possibly empty) -> ONLY matching mobs announced.

    Semantics differ per kind (see allows_spawn / allows_kill):
    - spawn: at spawn time the broadcast only shows the BASE mob (a cosmetic
      variant is indistinguishable until the kill). Matching is done on the
      base mob; entries that are themselves cosmetic variants (keys of
      BASE_OF) can never match a spawn and are ignored -- see
      ignored_spawn_entries().
    - kill: the kill embed names the precise mob, so exact matching is used:
      'Mecha Wasp' matches only Mecha Wasp kills; 'Wasp' only base Wasp.
    """

    spawn: tuple[str, ...] | None
    kill: tuple[str, ...] | None

    def ignored_spawn_entries(self) -> list[str]:
        """Whitelist entries that are cosmetic variants and therefore can
        never match a spawn (the variant is not visible at spawn time)."""
        from .mobs import BASE_OF
        return [w for w in (self.spawn or ()) if w in BASE_OF]

    def allows_spawn(self, mob: str) -> bool:
        if self.spawn is None:
            return True
        from .mobs import BASE_OF, base_mob
        # A spawn's mob name is the base mob for standard variants (the
        # spawn image is the base's), and already-precise for standalone
        # mobs (Hel *, Worker Ant, ...): base_mob() maps all of these
        # correctly.
        base = base_mob(mob).strip().lower()
        return any(
            base == w.strip().lower()
            for w in self.spawn
            if w not in BASE_OF          # cosmetic-variant entries are ignored
        )

    def allows_kill(self, mob: str) -> bool:
        if self.kill is None:
            return True
        m = mob.strip().lower()
        return any(m == w.strip().lower() for w in self.kill)


@dataclass(frozen=True)
class Config:
    discord: DiscordConfig
    storage: StorageConfig
    notify: NotifyConfig
    whitelist: WhitelistConfig
    log_level: str


def load_config(path: Path) -> Config:
    data = tomllib.loads(Path(path).read_text())
    d = data["discord"]
    notify = data.get("notify", {})
    wl = data.get("whitelist", {})
    cfg = Config(
        discord=DiscordConfig(
            token=d["token"],
            game_channel_id=int(d["game_channel_id"]),
            guild_id=(int(d["guild_id"]) if d.get("guild_id") is not None else None),
        ),
        storage=StorageConfig(db_path=Path(data["storage"]["db_path"])),
        notify=NotifyConfig(
            desktop=bool(notify.get("desktop", True)),
            silent=bool(notify.get("silent", False)),
        ),
        whitelist=WhitelistConfig(
            spawn=tuple(wl["spawn"]) if "spawn" in wl else None,
            kill=tuple(wl["kill"]) if "kill" in wl else None,
        ),
        log_level=str(data.get("logging", {}).get("level", "INFO")),
    )
    for entry in cfg.whitelist.ignored_spawn_entries():
        log.warning(
            "spawn whitelist entry %r is a cosmetic variant and will be ignored "
            "for spawns (the variant is only revealed at kill time)",
            entry,
        )
    return cfg
