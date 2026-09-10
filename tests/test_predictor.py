"""Predictor tests: KT-smoothed multinomial + cooldown + Hel-as-standalone."""
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
import pytest

from florr_notify.store import Store
from florr_notify.predictor import predict, format_prediction_line, Prediction
from florr_notify.mobs import MOBS, base_mob
from florr_notify.parser import KillEvent


# --- Hel is a standalone mob, not a variant of the base ---

def test_hel_mob_is_its_own_base():
    """Architectural invariant: Hel mobs are standalones. base_mob returns
    themselves, so the predictor for e.g. 'Spider' does NOT include
    Hel Spider, and predict('Hel Spider') just trivially returns Hel Spider."""
    assert base_mob("Hel Spider") == "Hel Spider"
    assert base_mob("Hel Wasp") == "Hel Wasp"
    assert base_mob("Hel Beetle") == "Hel Beetle"
    assert base_mob("Hel Centipede") == "Hel Centipede"
    # And cosmetic variants still map to their base:
    assert base_mob("Mecha Wasp") == "Wasp"
    assert base_mob("Dark Ladybug") == "Ladybug"
    # Standalone base mobs map to themselves:
    assert base_mob("Wasp") == "Wasp"
    assert base_mob("Soldier Termite") == "Soldier Termite"


def test_predict_spider_group_excludes_hel_naturally():
    """predict('Spider') candidates = {Spider, Mecha Spider} only.
    Hel Spider is not included because base_mob('Hel Spider') == 'Hel Spider'."""
    possible = [m for m in MOBS.values() if base_mob(m) == "Spider"]
    assert "Hel Spider" not in possible
    assert set(possible) == {"Spider", "Mecha Spider"}


# --- Fixtures and helpers ---

@pytest.fixture
def store(tmp_path: Path):
    s = Store(tmp_path / "t.db")
    yield s
    s.close()


def _insert_kill(store, mid, mob, ts, region="Sierra (ASIA)"):
    store.upsert_kill(KillEvent(
        message_id=mid, channel_id=1, guild_id=1, mob=mob, rarity="super",
        region=region, image_filename="", image_url="", color=0,
        killers=["x"], observed_at=ts,
        raw_description=f"A Super {mob} has been defeated by x!",
    ))


# --- Core formula: KT-smoothed (alpha = 1/2) frequency estimates ---

def test_predict_cold_start_uniform_prior(store: Store):
    """n=0 -> uniform estimate over possible candidates. With zero data and
    no prior knowledge, symmetric regularisation gives equal estimates."""
    p = predict("Wasp", store)  # {Wasp, Mecha Wasp}, k=2, n=0
    assert p.sample_size == 0
    # KT: each gets 0.5/(0.5*2) = 0.5
    assert p.distribution == pytest.approx({"Wasp": 0.5, "Mecha Wasp": 0.5})
    assert p.top_pct == pytest.approx(0.5)
    assert p.confidence_pct == 0   # zero data -> none of the prior uncertainty resolved


def test_predict_single_variant_with_smoothing(store: Store):
    for i in range(5):
        _insert_kill(store, 1000 + i, "Wasp", f"2026-09-01T10:0{i}:00")
    p = predict("Wasp", store)
    assert p.sample_size == 5
    # KT: Wasp (5+0.5)/(5+1.0) = 5.5/6.0; Mecha Wasp 0.5/6.0
    assert p.distribution["Wasp"] == pytest.approx(5.5 / 6.0)
    assert p.distribution["Mecha Wasp"] == pytest.approx(0.5 / 6.0)
    assert p.top_variant == "Wasp"
    assert p.confidence_pct == 91  # C = 1 - V_post/V_prior of 5/0


def test_predict_mixed_with_confidence(store: Store):
    for i in range(11):
        _insert_kill(store, 2000 + i, "Mecha Wasp", f"2026-09-02T10:0{i%10}:00")
    for i in range(4):
        _insert_kill(store, 3000 + i, "Wasp", f"2026-09-02T11:0{i}:00")
    p = predict("Wasp", store)
    assert p.sample_size == 15
    assert p.top_variant == "Mecha Wasp"
    # KT: (11+0.5)/(15+1.0) = 11.5/16.0
    assert p.top_pct == pytest.approx(11.5 / 16.0)
    assert p.confidence_pct == 90  # C of 11/4
    assert p.distribution["Mecha Wasp"] > p.distribution["Wasp"]


def test_predict_small_sample_confidence_is_estimate_trust(store: Store):
    """2 Mecha Wasp (m=2, N=2). Point estimate (KT): 2.5/3.0 = 83%.
    Confidence is the fraction of prior uncertainty resolved: 2-0 is
    fairly skewed but the sample is tiny -> moderate (72%)."""
    _insert_kill(store, 8001, "Mecha Wasp", "2026-09-06T10:00:00")
    _insert_kill(store, 8002, "Mecha Wasp", "2026-09-06T10:01:00")
    p = predict("Wasp", store)
    assert p.top_variant == "Mecha Wasp"
    assert p.top_pct == pytest.approx(2.5 / 3.0)

    assert p.distribution["Wasp"] == pytest.approx(0.5 / 3.0)
    assert p.confidence_pct == 72  # C of 2/0 (evidence resolved ~72% of prior variance)
    assert p.sample_size == 2


def test_predict_confidence_grows_with_data(store: Store):
    """The user's requirement: little data -> low confidence, more data ->
    higher confidence. A mildly-split distribution firms up as observations
    accumulate (variance-reduction semantics)."""
    for i in range(3):
        _insert_kill(store, 1000 + i, "Wasp", f"2026-09-01T10:0{i}:00")
    for i in range(2):
        _insert_kill(store, 1100 + i, "Mecha Wasp", f"2026-09-01T11:0{i}:00")
    p_weak = predict("Wasp", store)   # 3/2 split -> ranking far from settled
    for i in range(17):
        _insert_kill(store, 2000 + i, "Wasp", f"2026-09-02T10:{i:02d}:00")
    p_strong = predict("Wasp", store)  # 20/2 -> almost fully resolved
    assert p_weak.confidence_pct == 72
    assert p_strong.confidence_pct == 97
    assert p_strong.confidence_pct > p_weak.confidence_pct


def test_predict_kt_gives_unseen_variant_nonzero_estimate(store: Store):
    """Unseen variant gets a nonzero estimate (the user's requirement:
    'not zero'). With n=1 and k=2, KT gives 75/25 -- honest about how
    little one observation tells us."""
    _insert_kill(store, 9001, "Mecha Spider", "2026-09-07T10:00:00")
    p = predict("Spider", store)
    assert p.top_variant == "Mecha Spider"
    assert p.distribution["Mecha Spider"] == pytest.approx(1.5 / 2.0)
    assert p.distribution["Spider"] == pytest.approx(0.5 / 2.0)
    assert p.distribution["Spider"] > 0


# --- k=1 (standalone) -> conf is naturally 100% ---

def test_predict_standalone_mob_gives_100_percent_confidence(store: Store):
    """Worker Ant is a standalone base (no variants). With n=4 observations
    and k=1, the estimate is 1.0 and conf is 100% -- the formula gives this
    naturally; no ad-hoc special case."""
    for i in range(4):
        _insert_kill(store, 1100 + i, "Worker Ant", f"2026-09-08T10:0{i}:00")
    p = predict("Worker Ant", store)
    assert p.candidate_count == 1
    assert p.top_variant == "Worker Ant"
    assert p.top_pct == 1.0
    assert p.confidence_pct == 100
    assert p.distribution == {"Worker Ant": 1.0}


# --- Hel (standalone) predictor: trivially 100% ---

def test_predict_hel_mob_is_standalone(store: Store):
    """predict('Hel Spider') has candidate set {Hel Spider} only -- k=1,
    100% Hel Spider. Correct: a Hel Spider spawn is known to be Hel Spider."""
    for i in range(3):
        _insert_kill(store, 2000 + i, "Hel Spider", f"2026-09-09T10:0{i}:00")
    p = predict("Hel Spider", store)
    assert p.candidate_count == 1
    assert p.top_variant == "Hel Spider"
    assert p.top_pct == 1.0
    assert p.confidence_pct == 100
    # The Spider group does NOT include Hel Spider:
    p_spider = predict("Spider", store)
    assert "Hel Spider" not in p_spider.distribution
    assert "Hel Spider" not in p_spider.excluded  # not a candidate at all


# --- Cooldown ---

def test_predict_cooldown_excludes_recently_killed_variant(store: Store):
    kill_time = "2026-09-09T10:00:00"
    _insert_kill(store, 7001, "Mecha Spider", kill_time, region="Sierra (ASIA)")
    now = datetime.fromisoformat(kill_time) + timedelta(minutes=5)
    p = predict("Spider", store, server="Sierra (ASIA)", now=now)
    assert "Mecha Spider" in p.excluded
    assert p.cooldown_minutes["Mecha Spider"] == pytest.approx(25.0, abs=0.1)
    # Allowed: just base Spider -> k=1 -> conf 100%
    assert p.top_variant == "Spider"
    assert p.top_pct == 1.0
    assert p.confidence_pct == 100
    assert p.candidate_count == 1


def test_predict_cooldown_per_server(store: Store):
    kill_time = "2026-09-09T10:00:00"
    _insert_kill(store, 7101, "Mecha Spider", kill_time, region="Sierra (ASIA)")
    now = datetime.fromisoformat(kill_time) + timedelta(minutes=5)
    p_sierra = predict("Spider", store, server="Sierra (ASIA)", now=now)
    assert "Mecha Spider" in p_sierra.excluded
    p_romeo = predict("Spider", store, server="Romeo (EU)", now=now)
    assert "Mecha Spider" not in p_romeo.excluded
    assert "Mecha Spider" in p_romeo.distribution


def test_predict_cooldown_expires_after_30_min(store: Store):
    kill_time = "2026-09-09T10:00:00"
    _insert_kill(store, 7201, "Mecha Spider", kill_time, region="Sierra (ASIA)")
    now = datetime.fromisoformat(kill_time) + timedelta(minutes=31)
    p = predict("Spider", store, server="Sierra (ASIA)", now=now)
    assert "Mecha Spider" not in p.excluded
    assert "Mecha Spider" in p.distribution


# --- format_prediction_line (used by collector for notifications) ---

def test_format_prediction_line_returns_none_for_k1():
    pred = Prediction(
        base_mob="Worker Ant", sample_size=4,
        distribution={"Worker Ant": 1.0}, top_variant="Worker Ant",
        top_pct=1.0, confidence_pct=100, candidate_count=1,
    )
    assert format_prediction_line(pred) is None


def test_format_prediction_line_sorted_descending():
    pred = Prediction(
        base_mob="Wasp", sample_size=15,
        distribution={"Mecha Wasp": 0.73, "Wasp": 0.27},
        top_variant="Mecha Wasp", top_pct=0.73, confidence_pct=73,
        candidate_count=2,
        excluded=(), cooldown_minutes={},
    )
    line = format_prediction_line(pred)
    assert line is not None
    assert line.index("Mecha Wasp") < line.index("Wasp")
    assert "73%" in line and "conf 73%" in line
    assert "cooldown" not in line  # no cooldown here


def test_format_prediction_line_shows_cooldown_lockin():
    pred = Prediction(
        base_mob="Spider", sample_size=3,
        distribution={"Spider": 1.0}, top_variant="Spider",
        top_pct=1.0, confidence_pct=100, candidate_count=1,
        excluded=("Mecha Spider",), cooldown_minutes={"Mecha Spider": 18.0},
    )
    # Lock-in via cooldown: the prediction MUST still be shown -- the only
    # candidate is informative (this regression: the rare Shiny Leafbug was
    # missed because a full lock-in suppressed the whole line).
    line = format_prediction_line(pred)
    assert line is not None
    assert "Spider 100%" in line
    assert "Mecha Spider 18m" in line
