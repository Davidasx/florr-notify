"""Tests for the `alive [query]` CLI filter semantics."""
from __future__ import annotations

from types import SimpleNamespace

from florr_notify.__main__ import _filter_alive_rows
from florr_notify.config import WhitelistConfig


def _row(mob: str, region: str) -> dict:
    return {"mob": mob, "region": region, "rarity": "super"}


# Beetle is whitelisted; Leafbug is not.
CFG = SimpleNamespace(
    whitelist=WhitelistConfig(spawn=("Beetle",), kill=None),
)
NO_WL = SimpleNamespace(whitelist=WhitelistConfig(spawn=None, kill=None))

ROWS = [
    _row("Leafbug", "Sierra (ASIA)"),
    _row("Soldier Ant", "Juliett (US)"),
    _row("Beetle", "Romeo (EU)"),
    _row("Beetle", "Sierra (ASIA)"),
]


def test_no_query_returns_all():
    rows, note = _filter_alive_rows(ROWS, None, None)
    assert len(rows) == 4 and note == ""
    rows, note = _filter_alive_rows(ROWS, "", None)
    assert len(rows) == 4 and note == ""


def test_server_shorthand_filters_by_region():
    for q, want_region in [("asia", "Sierra (ASIA)"), ("EU", "Romeo (EU)"),
                           ("us", "Juliett (US)")]:
        rows, note = _filter_alive_rows(ROWS, q, None)
        assert all(r["region"] == want_region for r in rows)
        assert note.startswith("region ")


def test_mob_query_is_case_insensitive_exact_base_match():
    rows, note = _filter_alive_rows(ROWS, "leafbug", None)
    assert [r["mob"] for r in rows] == ["Leafbug"]
    assert "leafbug" in note
    rows, _ = _filter_alive_rows(ROWS, "SOLDIER ANT", None)
    assert [r["mob"] for r in rows] == ["Soldier Ant"]


def test_variant_query_matches_nothing():
    """Deliberate semantics: variant names match nothing. The user queries
    the base mob and reads the prediction line (which shows a cooldown
    lock-in such as 'Shiny Leafbug 100%')."""
    rows, note = _filter_alive_rows(ROWS, "shiny leafbug", None)
    assert rows == []
    assert "mob 'shiny leafbug'" in note


def test_unknown_query_matches_nothing():
    rows, _ = _filter_alive_rows(ROWS, "dragon", None)
    assert rows == []


def test_empty_rowset_still_classifies_query():
    rows, note = _filter_alive_rows([], "asia", None)
    assert rows == [] and note.startswith("region ")
    rows, note = _filter_alive_rows([], "leafbug", None)
    assert rows == [] and note.startswith("mob ")


def test_whitelist_filter_keeps_only_whitelisted_bases():
    rows, note = _filter_alive_rows(ROWS, "whitelist", CFG)
    assert [r["mob"] for r in rows] == ["Beetle", "Beetle"]
    assert note == "whitelist"


def test_whitelist_filter_is_case_insensitive():
    rows, _ = _filter_alive_rows(ROWS, "Whitelist", CFG)
    assert len(rows) == 2


def test_whitelist_filter_exempt_unique_and_eternal():
    """Notification semantics: the whitelist gates Super only; Unique /
    Eternal rows always show even when their base is not whitelisted."""
    rows = ROWS + [_row("Leafbug", "Sierra (ASIA)")]
    rows[-1]["rarity"] = "eternal"
    rows, _ = _filter_alive_rows(rows, "whitelist", CFG)
    assert [(r["mob"], r["rarity"]) for r in rows] == [
        ("Beetle", "super"), ("Beetle", "super"), ("Leafbug", "eternal"),
    ]


def test_whitelist_filter_without_configured_whitelist():
    rows, note = _filter_alive_rows(ROWS, "whitelist", NO_WL)
    assert len(rows) == 4
    assert "none configured" in note
