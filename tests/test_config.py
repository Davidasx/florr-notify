"""Tests for config loading: whitelist semantics + silent mode."""
from __future__ import annotations
from pathlib import Path

from florr_notify.config import load_config


BASE_TOML = """
[discord]
token = "x"
game_channel_id = 1349166028126556160
guild_id = 668937237010055189

[storage]
db_path = "./data/t.db"

[notify]
desktop = true

[logging]
level = "INFO"
"""


def _write(tmp_path: Path, extra: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(BASE_TOML + extra)
    return p


def test_no_whitelist_section_means_no_filtering(tmp_path: Path):
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.whitelist.spawn is None
    assert cfg.whitelist.kill is None
    assert cfg.whitelist.allows_spawn("Mecha Wasp")
    assert cfg.whitelist.allows_kill("Anything")


def test_silent_defaults_to_false(tmp_path: Path):
    cfg = load_config(_write(tmp_path, ""))
    assert cfg.notify.silent is False
    assert cfg.notify.desktop is True


def test_silent_flag_parsed(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text("""
        [discord]
        token = "x"
        game_channel_id = 1

        [storage]
        db_path = "./data/t.db"

        [notify]
        desktop = true
        silent = true
    """)
    cfg = load_config(p)
    assert cfg.notify.silent is True


def test_whitelist_present_empty_blocks_that_kind(tmp_path: Path):
    cfg = load_config(_write(tmp_path, '\n[whitelist]\nkill = []\n'))
    # kill list present and empty -> nothing announced for kills
    assert cfg.whitelist.kill == ()
    assert cfg.whitelist.allows_kill("Mecha Wasp") is False
    # spawn key absent -> no filtering for spawns
    assert cfg.whitelist.spawn is None
    assert cfg.whitelist.allows_spawn("Mecha Wasp") is True


def test_spawn_whitelist_ignores_variant_entries(tmp_path: Path):
    """The core asymmetry: at spawn time a cosmetic variant is NOT visible
    (the spawn image is the base mob's), so variant entries in the spawn
    whitelist can never match and are ignored. Base entries match the
    spawn's base mob -- so 'Wasp' reports ALL wasp spawns."""
    cfg = load_config(
        _write(tmp_path, '\n[whitelist]\nspawn = ["Mecha Wasp", "Wasp"]\n')
    )
    # 'Mecha Wasp' entry ignored; 'Wasp' entry matches the spawn's base.
    assert cfg.whitelist.ignored_spawn_entries() == ["Mecha Wasp"]
    assert cfg.whitelist.allows_spawn("Wasp") is True        # base wasp spawn
    assert cfg.whitelist.allows_spawn("Mecha Wasp") is True  # spawn shows as Wasp
    assert cfg.whitelist.allows_spawn("Beetle") is False
    # Standalone mobs match by their own name:
    assert cfg.whitelist.allows_spawn("Worker Ant") is False
    assert cfg.whitelist.allows_spawn("Hel Spider") is False


def test_spawn_whitelist_matches_hel_and_standalone(tmp_path: Path):
    """Hel mobs are visible at spawn (filename carries the variant), and
    standalone mobs are precise -- both match by their own name."""
    cfg = load_config(
        _write(tmp_path, '\n[whitelist]\nspawn = ["Hel Spider", "Worker Ant"]\n')
    )
    assert cfg.whitelist.ignored_spawn_entries() == []
    assert cfg.whitelist.allows_spawn("Hel Spider") is True
    assert cfg.whitelist.allows_spawn("Worker Ant") is True
    assert cfg.whitelist.allows_spawn("Wasp") is False


def test_kill_whitelist_exact_match(tmp_path: Path):
    """The kill embed names the precise mob: 'Mecha Wasp' matches only
    Mecha Wasp kills; 'Wasp' matches only base-Wasp kills."""
    cfg = load_config(
        _write(tmp_path, '\n[whitelist]\nkill = ["Mecha Wasp"]\n')
    )
    assert cfg.whitelist.allows_kill("Mecha Wasp") is True
    assert cfg.whitelist.allows_kill("Wasp") is False
    assert cfg.whitelist.allows_kill("Dark Ladybug") is False


def test_variant_spawn_entries_flagged_for_warning(tmp_path: Path):
    """ignored_spawn_entries() drives the startup warning so users know
    their variant entry has no effect on spawns."""
    cfg = load_config(
        _write(tmp_path, '\n[whitelist]\nspawn = ["Mecha Wasp"]\n')
    )
    assert cfg.whitelist.ignored_spawn_entries() == ["Mecha Wasp"]
