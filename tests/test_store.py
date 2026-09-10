"""Store tests: spawn/kill lifecycle, rarity field, backfill, dedupe."""
from __future__ import annotations
import json
from pathlib import Path
import pytest

from florr_notify.parser import SpawnEvent, KillEvent
from florr_notify.store import Store

MID = 1234567890
CID = 1349166028126556160
GID = 668937237010055189


def _killers_to_raw(mob: str, killers: tuple[str, ...]) -> str:
    if len(killers) == 1:
        s = killers[0]
    elif len(killers) == 2:
        s = f"{killers[0]} and {killers[1]}"
    else:
        s = ", ".join(killers[:-1]) + " and " + killers[-1]
    return f"A Super {mob} has been defeated by {s}!"


def _spawn(mid=MID, mob="Wasp", rarity="super", fname="petal-wasp-super.png",
           ts="2026-08-09T10:00:00", region="Romeo (EU)"):
    return SpawnEvent(
        message_id=mid, channel_id=CID, guild_id=GID,
        mob=mob, rarity=rarity, region=region,
        image_filename=fname, image_url="https://example/" + fname,
        color=0x2BFFA4, observed_at=ts,
        raw_description=f"A Super {mob} has spawned!",
    )


def _kill(mid=MID, mob="Wasp", rarity="super", fname="petal-wasp-super-x.png",
          killers=("X", "Y"), ts="2026-08-09T10:05:00", region="Romeo (EU)"):
    return KillEvent(
        message_id=mid, channel_id=CID, guild_id=GID,
        mob=mob, rarity=rarity, region=region,
        image_filename=fname, image_url="https://example/" + fname,
        color=0x2BFFA4, killers=list(killers), observed_at=ts,
        raw_description=_killers_to_raw(mob, killers),
    )


@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def test_spawn_then_kill(store: Store):
    assert store.upsert_spawn(_spawn()) is True
    assert store.upsert_kill(_kill()) is True
    r = store.recent(1)[0]
    assert r["mob"] == "Wasp" and r["rarity"] == "super"
    assert r["edit_count"] == 1
    assert json.loads(r["killers_json"]) == ["X", "Y"]


def test_unique_tier_persisted(store: Store):
    assert store.upsert_spawn(_spawn(mid=MID+1, mob="Ghost", rarity="unique",
                                       fname="petal-ghost-unique.png")) is True
    r = store.recent(1)[0]
    assert r["rarity"] == "unique" and r["mob"] == "Ghost"


def test_eternal_tier_persisted(store: Store):
    assert store.upsert_spawn(_spawn(mid=MID+2, mob="Mecha Flower", rarity="eternal",
                                       fname="petal-mecha_flower-eternal.png")) is True
    assert store.upsert_kill(_kill(mid=MID+2, mob="Mecha Flower", rarity="eternal",
                                     fname="petal-mecha_flower-eternal-x.png",
                                     killers=("gonee_",))) is True
    r = store.recent(1)[0]
    assert r["rarity"] == "eternal"
    assert r["kill_filename"] == "petal-mecha_flower-eternal-x.png"


def test_image_url_not_stored(store: Store):
    """image_url must NOT be persisted (per user request)."""
    store.upsert_spawn(_spawn())
    r = store.recent(1)[0]
    assert "image_url" not in r.keys()


def test_kill_dedupe(store: Store):
    store.upsert_spawn(_spawn())
    assert store.upsert_kill(_kill()) is True
    assert store.upsert_kill(_kill()) is False
    assert store.recent(1)[0]["edit_count"] == 1


def test_count(store: Store):
    assert store.count() == 0
    store.upsert_spawn(_spawn())
    assert store.count() == 1
