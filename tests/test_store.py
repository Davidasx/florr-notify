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
           ts="2026-08-09T10:00:00", region="Romeo (EU)", summoned=False):
    return SpawnEvent(
        message_id=mid, channel_id=CID, guild_id=GID,
        mob=mob, rarity=rarity, region=region,
        image_filename=fname, image_url="https://example/" + fname,
        color=0x2BFFA4, observed_at=ts,
        raw_description=(f"A Super {mob} has been summoned!" if summoned
                         else f"A Super {mob} has spawned!"),
        summoned=summoned,
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



def test_summoned_flag_survives_the_kill_overwrite(store: Store):
    """raw_description is overwritten by the kill edit, so the summoned
    marker needs its own column -- otherwise the trace is lost the moment
    the mob dies (which is exactly the analysis case that needs it)."""
    assert store.upsert_spawn(_spawn(mid=MID + 7, summoned=True)) is True
    assert store.recent(1)[0]["summoned"] == 1
    assert store.upsert_kill(_kill(mid=MID + 7)) is True
    r = store.recent(1)[0]
    assert r["summoned"] == 1          # still marked after the kill
    assert "summoned" not in (r["raw_description"] or "")
    # a natural spawn stays 0 (recent() sorts by kill/spawn time, so look at
    # both rows rather than assuming order)
    assert store.upsert_spawn(_spawn(mid=MID + 8)) is True
    assert sorted(x["summoned"] for x in store.recent(2)) == [0, 1]

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


def test_expired_alive_tally_and_sweep_ordering(tmp_path: Path):
    """Retention: rows older than 24h with no kill broadcast are deleted, but
    (a) Store() must NOT prune on open -- the collector prunes only AFTER its
    alive-check, so a kill that happened while we were down is still
    recoverable from the message; and (b) each pruned row is tallied per mob
    so "which mobs outlive the window" stays answerable. A killed mob is
    never tallied."""
    db = tmp_path / "t.db"
    old = "2026-01-01T00:00:00"          # far outside the 24h window
    s = Store(db)
    for mid, mob in ((MID + 20, "Moth"), (MID + 21, "Moth"), (MID + 22, "Hornet"),
                     (MID + 23, "Moth")):
        assert s.upsert_spawn(
            _spawn(mid=mid, mob=mob, ts=old, fname=f"petal-{mob.lower()}-super.png")
        ) is True
    assert s.upsert_kill(_kill(mid=MID + 23, mob="Moth",
                               fname="petal-moth-super-x.png")) is True
    s.close()

    s = Store(db)                        # reopening must NOT prune
    assert s.count() == 4, "Store() must not run the retention sweep"

    s.expire_stale_alive()
    assert s.expired_alive_counts() == [("Moth", 2), ("Hornet", 1)]
    assert s.count() == 1                # only the killed Moth survived

    # tallies accumulate across sweeps
    assert s.upsert_spawn(
        _spawn(mid=MID + 24, mob="Moth", ts=old, fname="petal-moth-super.png")
    ) is True
    s.expire_stale_alive()
    assert s.expired_alive_counts() == [("Moth", 3), ("Hornet", 1)]
    s.close()

def test_count(store: Store):
    assert store.count() == 0
    store.upsert_spawn(_spawn())
    assert store.count() == 1
