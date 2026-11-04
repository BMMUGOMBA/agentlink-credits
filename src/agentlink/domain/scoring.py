"""Pure rating engine: readings + ruleset in, score/tier/confidence/explanation out.

No I/O here. The function is deterministic, so replaying the same readings with the same
ruleset version always produces an identical result (the basis of idempotency).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agentlink.domain.models import RatingStatus, ReadingIn, ReadingKind, Tier
from agentlink.domain.rules import RuleSet, Thresholds


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile, p in (0, 100]."""
    if not values:
        raise ValueError("percentile of empty sequence")
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def norm_high(value: float, target: float, worst: float) -> float:
    """Higher is better: 100 at/above target, 0 at/below worst."""
    return max(0.0, min(1.0, (value - worst) / (target - worst))) * 100


def norm_low(value: float, target: float, worst: float) -> float:
    """Lower is better: 100 at/below target, 0 at/above worst."""
    return max(0.0, min(1.0, (worst - value) / (worst - target))) * 100


@dataclass(frozen=True)
class Metrics:
    n_probes: int
    n_reachable: int
    n_transactions: int
    availability_pct: float
    txn_success_pct: float
    txn_timeout_pct: float
    latency_median_ms: float
    latency_p95_ms: float
    packet_loss_pct: float
    jitter_ms: float
    throughput_kbps: float

    def as_dict(self) -> dict[str, float | int]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class RatingResult:
    score: float
    tier: Tier | None
    confidence: float
    status: RatingStatus
    metrics: Metrics
    components: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def explanation(self, ruleset_version: str) -> dict[str, Any]:
        return {
            "ruleset_version": ruleset_version,
            "components": self.components,
            "notes": self.notes,
            "confidence": self.confidence,
        }


def input_hash(reading_ids: Sequence[str], ruleset_version: str) -> str:
    """Fingerprint of everything that determines a rating. Part of the idempotency key."""
    h = hashlib.sha256()
    h.update(ruleset_version.encode())
    for rid in sorted(reading_ids):
        h.update(b"|")
        h.update(rid.encode())
    return h.hexdigest()[:32]


def compute_metrics(readings: Sequence[ReadingIn]) -> Metrics:
    probes = [r for r in readings if r.kind is ReadingKind.PROBE]
    txns = [r for r in readings if r.kind is ReadingKind.TRANSACTION]
    reachable = [p for p in probes if p.reachable]

    availability = 100 * len(reachable) / len(probes) if probes else 0.0
    n_ok = sum(1 for t in txns if t.success)
    n_timeout = sum(1 for t in txns if t.timed_out)
    success = 100 * n_ok / len(txns) if txns else 0.0
    timeout = 100 * n_timeout / len(txns) if txns else 0.0

    if reachable:
        lat = [p.latency_ms for p in reachable if p.latency_ms is not None]
        median = percentile(lat, 50)
        p95 = percentile(lat, 95)
        loss = sum(p.packet_loss_pct or 0 for p in reachable) / len(reachable)
        jitter = sum(p.jitter_ms or 0 for p in reachable) / len(reachable)
        thr = sum(p.throughput_kbps or 0 for p in reachable) / len(reachable)
    else:  # nothing reachable: worst-case finite values so the components score 0
        median = p95 = jitter = 60_000.0
        loss = 100.0
        thr = 0.0

    return Metrics(
        n_probes=len(probes),
        n_reachable=len(reachable),
        n_transactions=len(txns),
        availability_pct=availability,
        txn_success_pct=success,
        txn_timeout_pct=timeout,
        latency_median_ms=median,
        latency_p95_ms=p95,
        packet_loss_pct=loss,
        jitter_ms=jitter,
        throughput_kbps=thr,
    )


def tier_for(score: float, rules: RuleSet) -> Tier:
    if score >= rules.tier_a_min:
        return Tier.A
    if score >= rules.tier_b_min:
        return Tier.B
    if score >= rules.tier_c_min:
        return Tier.C
    return Tier.D


def _component_scores(m: Metrics, t: Thresholds, rules: RuleSet) -> dict[str, tuple[float, str]]:
    ms = rules.latency_median_share
    ls = rules.loss_share
    lat = ms * norm_low(m.latency_median_ms, t.latency_median_target_ms, t.latency_median_worst_ms)
    lat += (1 - ms) * norm_low(m.latency_p95_ms, t.latency_p95_target_ms, t.latency_p95_worst_ms)
    net = ls * norm_low(m.packet_loss_pct, t.loss_target_pct, t.loss_worst_pct)
    net += (1 - ls) * norm_low(m.jitter_ms, t.jitter_target_ms, t.jitter_worst_ms)
    return {
        "availability": (
            norm_high(m.availability_pct, t.availability_target_pct, t.availability_worst_pct),
            f"{m.availability_pct:.1f}% of probes reachable",
        ),
        "transactions": (
            norm_high(m.txn_success_pct, t.txn_success_target_pct, t.txn_success_worst_pct),
            f"{m.txn_success_pct:.1f}% success, {m.txn_timeout_pct:.1f}% timeouts",
        ),
        "latency": (
            lat,
            f"median {m.latency_median_ms:.0f} ms, p95 {m.latency_p95_ms:.0f} ms",
        ),
        "network_quality": (
            net,
            f"{m.packet_loss_pct:.2f}% loss, {m.jitter_ms:.0f} ms jitter",
        ),
        "throughput": (
            norm_high(m.throughput_kbps, t.throughput_target_kbps, t.throughput_worst_kbps),
            f"{m.throughput_kbps:.0f} kbps average",
        ),
    }


def score_window(readings: Sequence[ReadingIn], area_type: str, rules: RuleSet) -> RatingResult:
    """Compute the rating for one (outlet, provider, window)."""
    m = compute_metrics(readings)
    t = rules.thresholds_for(area_type)
    notes: list[str] = [f"area type '{area_type}' thresholds applied"]

    probe_cov = min(1.0, m.n_probes / rules.expected_probes_per_window)
    txn_cov = min(1.0, m.n_transactions / rules.expected_transactions_per_window)
    confidence = round(0.7 * probe_cov + 0.3 * txn_cov, 3)

    weights = rules.weights.model_dump()
    comps = _component_scores(m, t, rules)
    composite = sum(weights[name] * comps[name][0] for name in weights)
    components = [
        {
            "name": name,
            "observed": detail,
            "normalized": round(norm, 2),
            "weight": weights[name],
            "contribution": round(weights[name] * norm, 2),
        }
        for name, (norm, detail) in comps.items()
    ]

    if probe_cov < rules.min_probe_coverage or m.n_transactions < rules.min_transactions:
        notes.append(
            f"insufficient data: {m.n_probes} probes (need "
            f"{math.ceil(rules.min_probe_coverage * rules.expected_probes_per_window)}), "
            f"{m.n_transactions} transactions (need {rules.min_transactions})"
        )
        return RatingResult(
            score=round(composite, 2),
            tier=None,
            confidence=confidence,
            status=RatingStatus.INSUFFICIENT_DATA,
            metrics=m,
            components=components,
            notes=notes,
        )

    score = composite
    if m.availability_pct < t.availability_floor_pct:
        score = min(score, rules.floor_cap_score)
        notes.append(
            f"hard floor: availability {m.availability_pct:.1f}% is below "
            f"{t.availability_floor_pct}%, score capped at {rules.floor_cap_score}"
        )
    if m.txn_success_pct < t.txn_success_floor_pct:
        score = min(score, rules.floor_cap_score)
        notes.append(
            f"hard floor: transaction success {m.txn_success_pct:.1f}% is below "
            f"{t.txn_success_floor_pct}%, score capped at {rules.floor_cap_score}"
        )
    score = round(max(0.0, min(100.0, score)), 2)
    tier = tier_for(score, rules)
    notes.append(f"tier {tier.value} (A>={rules.tier_a_min}, B>={rules.tier_b_min}, "
                 f"C>={rules.tier_c_min})")
    return RatingResult(
        score=score,
        tier=tier,
        confidence=confidence,
        status=RatingStatus.COMPUTED,
        metrics=m,
        components=components,
        notes=notes,
    )


def diverges(m_probe_availability: float, m_txn_success: float, rules: RuleSet) -> bool:
    """Probe and transaction evidence disagree by more than the tolerance."""
    return abs(m_probe_availability - m_txn_success) > rules.divergence_tolerance_pct
