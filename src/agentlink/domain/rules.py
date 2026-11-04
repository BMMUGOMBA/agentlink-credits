"""Versioned rulesets: weights, thresholds, tier cut-offs, coverage and credit rules."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Thresholds(BaseModel):
     """Per-area-type thresholds. 'target' scores 100, 'worst' scores 0, in between is linear."""

     availabilty_target_pct: float
     availabilty_worst_pct: float
     availibilty_floor_pct: float #below this, the reading is capped (hard floor)
     txn_success_target_pct: float
     txn_success_worst_pct: float
     txn_success_floor_pct: float #
     latency_median_target_ms: float
     latency_median_worst_ms: float
     latency_p95_target_ms: float
     latency_p95_worst_ms: float
     loss_target_pct: float
     loss_worst_pct: float
     jitter_target_ms: float
     jitter_worst_ms: float
     throughput_target_kbps: float
     throughput_worst_kbps: float


class Weights(BaseModel):
    availability: float = 0.35
    transactions: float = 0.25
    latency: float = 0.20
    network_quality: float = 0.15  # packet loss and jitter
    throughput: float = 0.05

    @model_validator(mode="after")
    def _sum_to_one(self) -> Weights:
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1.0, got {total}")
        return self


class RuleSet(BaseModel):
    version: str
    weights: Weights = Field(default_factory=Weights)
    urban: Thresholds
    rural: Thresholds

    # sub-weights inside composite components
    latency_median_share: float = 0.5  # remainder goes to p95
    loss_share: float = 0.6  # remainder goes to jitter

    # tiers: score >= cutoff
    tier_a_min: float = 90.0
    tier_b_min: float = 75.0
    tier_c_min: float = 60.0
    floor_cap_score: float = 59.0  # the highest score a rating can have when a hard floor trips

    # coverage and confidence
    expected_probes_per_window: int = 168  # hourly probes for a week
    expected_transactions_per_window: int = 50
    min_probe_coverage: float = 0.5
    min_transactions: int = 10

    # verification
    divergence_tolerance_pct: float = 15.0  # |probe availability - transaction success|

    # credits per verified tier (integer credit units)
    credits_by_tier: dict[str, int] = Field(default_factory=lambda: {"A": 100, "B": 50, "C": 0, "D": 0})

    def thresholds_for(self, area_type: str) -> Thresholds:
        return self.urban if area_type == "urban" else self.rural


DEFAULT_RULESET = RuleSet(
    version="v1",
    urban=Thresholds(
        availability_target_pct=97,
        availability_worst_pct=80,
        availability_floor_pct=90,
        txn_success_target_pct=98,
        txn_success_worst_pct=85,
        txn_success_floor_pct=90,
        latency_median_target_ms=300,
        latency_median_worst_ms=1500,
        latency_p95_target_ms=800,
        latency_p95_worst_ms=4000,
        loss_target_pct=1,
        loss_worst_pct=10,
        jitter_target_ms=30,
        jitter_worst_ms=200,
        throughput_target_kbps=1000,
        throughput_worst_kbps=100,
    ),
    rural=Thresholds(
        availability_target_pct=90,
        availability_worst_pct=60,
        availability_floor_pct=75,
        txn_success_target_pct=95,
        txn_success_worst_pct=75,
        txn_success_floor_pct=80,
        latency_median_target_ms=800,
        latency_median_worst_ms=3000,
        latency_p95_target_ms=2000,
        latency_p95_worst_ms=8000,
        loss_target_pct=3,
        loss_worst_pct=20,
        jitter_target_ms=80,
        jitter_worst_ms=400,
        throughput_target_kbps=500,
        throughput_worst_kbps=50,
    ),
)


