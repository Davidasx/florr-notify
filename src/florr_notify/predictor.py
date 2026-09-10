"""Variant predictor with 30-min cooldown awareness and KT smoothing.

Usage:
    predictor.predict("Wasp", store, server="Sierra (ASIA)", now=...) -> Prediction

Algorithm:
  1. Candidate set: all mobs whose base_mob() matches the requested base,
     i.e. the cosmetic variants (Mecha/Dark/Shiny/...) of that base. Hel
     mobs are NOT included because we treat them as standalone mobs (see
     mobs.MOBS docstring), so base_mob("Hel Spider") == "Hel Spider" and
     it doesn't match "Spider".
  2. Optional 30-min cooldown: for the given server, exclude any
     candidate whose last kill was within 30 minutes of `now`.
  3. KT smoothing so unseen variants get a nonzero estimate instead of 0%
     (cold-start friendly).
  4. Renormalize and return the distribution.

Point estimates -- honest framing (the prior is UNKNOWN):

The true variant distribution is whatever florr.io's spawn logic does; we
have no knowledge of it and cannot justify any particular prior -- in
particular not a uniform one. We therefore do NOT present the distribution
as Bayesian posteriors. We use the Krichevsky-Trofimov (KT) estimator:
additive smoothing with alpha = 1/2, equivalently the Jeffreys prior for
the multinomial:

    p_i = (n_i + 1/2) / (N + k/2)

Why KT: among all sequential probability assigners, KT minimises the
worst-case log-loss regret against ANY true distribution. The outputs
are regularised frequency estimates; alpha is a plain hyperparameter.

Confidence -- fraction of prior uncertainty resolved by the data:

confidence_pct measures how much of the initial (zero-data) uncertainty
about the whole distribution has been eliminated by the observations.
For a Dirichlet posterior with candidate count m, observation count N
and alpha_i = n_i + alpha, the total variance of the probability vector is

    V_post = (kappa^2 - sum_i alpha_i^2) / (kappa^2 * (kappa + 1))

with kappa = N + alpha*m (total concentration). The zero-data baseline is

    V_prior = (m - 1) / (m * (alpha*m + 1))

and the confidence is the uncertainty-elimination ratio

    C = 1 - V_post / V_prior

Properties (all emergent from the formula, none hard-coded):
  - m = 1 (single candidate: a standalone mob, or every other variant
    excluded by cooldown) -> exactly 100%: V_prior = 0, there is nothing
    to be uncertain about.
  - zero data -> 0%: the observations have eliminated none of the prior
    uncertainty.
  - converges like O(1/N), so it keeps discriminating across sample
    sizes instead of saturating after a few observations.
  - distinct from top_pct: top_pct answers "how likely is the NEXT spawn
    to be the top variant" (the estimated frequency); confidence answers
    "how trustworthy is that estimate given how much evidence we have".
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .mobs import MOBS, base_mob as _base_mob

if TYPE_CHECKING:
    from .store import Store

COOLDOWN = timedelta(minutes=30)

# Krichevsky-Trofimov smoothing strength for the point estimates.
SMOOTHING_ALPHA: float = 0.5



@dataclass(frozen=True)
class Prediction:
    base_mob: str
    sample_size: int                       # kill observations in the allowed set
    distribution: dict[str, float]         # allowed variant -> KT estimate
    top_variant: str | None
    top_pct: float                         # KT-regularised frequency of top
    confidence_pct: int                    # 0-100; fraction of prior uncertainty resolved
    candidate_count: int = 0               # |distribution|
    excluded: tuple[str, ...] = ()         # variants excluded by cooldown
    cooldown_minutes: dict[str, float] = field(default_factory=dict)
    # ^ mob -> minutes remaining on cooldown (< 30)
    counts: dict[str, int] = field(default_factory=dict)
    # ^ raw observation counts per allowed variant (0 for unseen ones);
    #   the smoothed distribution is derived from these + alpha

    def __str__(self) -> str:
        if not self.distribution:
            if self.excluded:
                return (
                    f"All {len(self.excluded)} possible variant(s) for {self.base_mob} "
                    f"are on cooldown on this server right now "
                    f"({', '.join(self.excluded)}). No prediction."
                )
            return f"No observations for {self.base_mob!r} yet."
        lines = [
            f"Prediction for {self.base_mob} (n={self.sample_size} kill observations):",
        ]
        for v, p in sorted(self.distribution.items(), key=lambda kv: (-kv[1], kv[0])):
            c = self.counts.get(v, 0)
            lines.append(f"  {v:<22} {p*100:5.1f}%   ({c}/{self.sample_size} obs)")
        if self.excluded:
            parts = [f"{v} ({self.cooldown_minutes.get(v, 0):.0f}m left)"
                     for v in self.excluded]
            lines.append(f"  [excluded (cooldown): {', '.join(parts)}]")
        # Uniform output: the confidence value is the plain formula
        # int(round(top_pct * 100)) -- for k=1 it is naturally 100% because
        # the single candidate carries all the mass. No display special case.
        lines.append(
            f"Top: {self.top_variant}  |  Confidence: {self.confidence_pct}%"
        )
        return "\n".join(lines)


def _possible_variants(base_mob: str) -> list[str]:
    """All mobs in MOBS whose base_mob() == `base_mob`.

    Because Hel mobs are standalone (base_mob("Hel Spider") == "Hel Spider"),
    predict("Spider", ...) naturally does NOT include Hel Spider.
    """
    b = base_mob.strip().lower()
    return [
        m for m in MOBS.values()
        if _base_mob(m).strip().lower() == b
    ]


def _cooldown_excluded(
    store: "Store", candidates: list[str], server: str | None, now: datetime,
) -> tuple[frozenset[str], dict[str, float]]:
    """For each candidate, check the most recent kill on `server`; if
    within COOLDOWN, mark excluded. Returns (excluded_set, minutes_remaining)."""

    def _aware(dt: datetime) -> datetime:
        # Normalize naive datetimes to UTC so subtraction always works
        # (stored killed_at strings carry '+00:00'; a caller may pass a
        # naive `now`).
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)

    now = _aware(now)
    if not server:
        return frozenset(), {}
    excluded: set[str] = set()
    minutes_left: dict[str, float] = {}
    now_iso = now.isoformat()
    for mob in candidates:
        row = store.conn.execute(
            "SELECT killed_at FROM incidents "
            "WHERE mob = ? AND region = ? AND killed_at IS NOT NULL "
            "AND killed_at <= ? "
            "ORDER BY killed_at DESC LIMIT 1",
            (mob, server, now_iso),
        ).fetchone()
        if not row or not row["killed_at"]:
            continue
        try:
            killed_at = _aware(datetime.fromisoformat(row["killed_at"]))
        except (TypeError, ValueError):
            continue
        age = now - killed_at
        # age is guaranteed >= 0 (SQL filtered killed_at <= now).
        if age < COOLDOWN:
            excluded.add(mob)
            minutes_left[mob] = max(0.0, (COOLDOWN - age).total_seconds() / 60)
    return frozenset(excluded), minutes_left


def _variance_confidence(counts: dict[str, int], alpha: float) -> float:
    """Fraction of the zero-data uncertainty resolved by the observations:
    C = 1 - V_post / V_prior under the Dirichlet(counts + alpha) posterior
    (closed form, fully deterministic -- no Monte Carlo).

    m = 1 (single candidate) naturally returns 1.0: V_prior = 0, so there
    is no uncertainty to begin with -- no special case needed."""
    m = len(counts)
    if m == 0:
        return 0.0
    if m == 1:
        return 1.0          # V_prior = 0: nothing to be uncertain about
    alphas = [counts[mob] + alpha for mob in counts]
    kappa = sum(alphas)                 # = N + alpha*m
    v_post = (kappa * kappa - sum(a * a for a in alphas)) / (
        kappa * kappa * (kappa + 1)
    )
    v_prior = (m - 1) / (m * (alpha * m + 1))
    return 1.0 - v_post / v_prior


def predict(
    base_mob: str,
    store: "Store",
    *,
    server: str | None = None,
    now: datetime | None = None,
    alpha: float = SMOOTHING_ALPHA,
) -> Prediction:
    """Variant prediction for `base_mob`.

    server/now enable cooldown exclusion. Without them, no cooldown is
    applied (useful for the CLI / offline analysis).

    alpha is the KT smoothing strength (default 1/2) used for the point
    estimates; confidence is the fraction of prior uncertainty resolved by
    the data (see module docstring).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    possible = _possible_variants(base_mob)
    excluded, minutes_left = _cooldown_excluded(store, possible, server, now)
    allowed = [m for m in possible if m not in excluded]

    observed: dict[str, int] = {}
    rows = store.conn.execute(
        "SELECT mob FROM incidents "
        "WHERE killed_at IS NOT NULL AND mob IS NOT NULL"
    ).fetchall()
    b = base_mob.strip().lower()
    for r in rows:
        mob = r["mob"]
        if not mob:
            continue
        if _base_mob(mob).strip().lower() != b:
            continue
        if mob not in allowed:
            continue
        observed[mob] = observed.get(mob, 0) + 1
    n = sum(observed.values())

    if not allowed:
        return Prediction(
            base_mob=base_mob,
            sample_size=n,
            distribution={},
            top_variant=None,
            top_pct=0.0,
            confidence_pct=0,
            candidate_count=0,
            excluded=tuple(sorted(excluded)),
            cooldown_minutes=minutes_left,
            counts={},
        )

    # KT smoothing: add alpha=1/2 to each allowed candidate (point estimates).
    dist_raw = {m: observed.get(m, 0) + alpha for m in allowed}
    total = sum(dist_raw.values())
    distribution = {m: v / total for m, v in dist_raw.items()}

    top_variant = max(distribution, key=distribution.get)
    top_pct = distribution[top_variant]

    allowed_counts = {m: observed.get(m, 0) for m in allowed}

    # Confidence = fraction of the zero-data uncertainty resolved by the
    # observations (closed form). m=1 naturally yields 100% (V_prior = 0)
    # and zero data naturally yields 0% -- no special cases.
    confidence_pct = int(round(
        _variance_confidence(allowed_counts, alpha) * 100
    ))

    return Prediction(
        base_mob=base_mob,
        sample_size=n,
        distribution=distribution,
        top_variant=top_variant,
        top_pct=top_pct,
        confidence_pct=confidence_pct,
        candidate_count=len(allowed),
        excluded=tuple(sorted(excluded)),
        cooldown_minutes=minutes_left,
        counts=dict(allowed_counts),
    )


def format_prediction_line(pred: Prediction) -> str | None:
    """Return a short human-readable prediction line for notifications, or
    None if there's nothing useful to show.

    A true standalone mob (single possible candidate, nothing excluded --
    e.g. Worker Ant, Hel Spider) is skipped: predicting "it is itself"
    carries no information. A cooldown lock-in (all other variants
    excluded, exactly one remains) is the MOST informative case and must
    still be shown."""
    if not pred.distribution or pred.top_variant is None:
        return None
    if pred.candidate_count <= 1 and not pred.excluded:
        return None
    parts = [f"{v} {p*100:.0f}%" for v, p in
             sorted(pred.distribution.items(), key=lambda kv: (-kv[1], kv[0]))]
    line = "predicted: " + ", ".join(parts) + f" (conf {pred.confidence_pct}%)"
    if pred.excluded:
        exc = ", ".join(f"{v} {pred.cooldown_minutes.get(v, 0):.0f}m"
                       for v in pred.excluded)
        line += f"  ·  cooldown: {exc}"
    return line
