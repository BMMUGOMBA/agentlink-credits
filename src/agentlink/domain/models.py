from __future__ import annotations

from datetime import UTC , datetime, timedelta
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator, model_validator


class AreaType(StrEnum):
    URBAN = "urban"
    RURAL = "rural"


class ReadingKind(StrEnum):
    PROBE = "probe"
    TRANSACTION = "transaction"


class ReadingStatus(StrEnum):
    RECEIVED = "received"
    VALIDATED = "validated"
    REJECTED = "rejected"


class RatinStatus(StrEnum):
    COMPUTED = "computed"
    VERIFIED = "verified"
    DISPUTED = "disputed"
    INSUFFICIENT_DATA = "insufficient_data"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class Tier(StrEnum):
    A = "A"
    B = "B"
    C  = "C"
    D = "D"


class ReadingIn(BaseModel):
    """One measurement reading from a probe or transaction outcome"""

    reading_id: str = Field(min_length=8, max_length=64)
    outlet_id: str = Field(min_length=1, max_length=64)
    provider_id: str = Field(min_length=1, max_length=64)
    ts: datetime
    kind: ReadingKind

    # probe fields (all required when the probe reaches target)
    reachable: bool | None = None
    latency_ms: float | None = Field(default=None, ge=0, le=60_000)
    packet_loss_pct: float | None = Field(default=None, ge=0, le=100)
    jitter_ms: float | None = Field(default=None, ge=0, le=60_000)
    throughput_kbps: float | None = Field(default=None, ge=0, le=1_000_000)

    #transaction fields 
    success: bool | None = None
    timed_out: bool | None = None

    @field_validator("ts")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ts must be timezone offset")
        return v.astimezone(UTC())

    @model_validator(mode="after")
    def _kind_fields(self) -> ReadingIn:
        if self.kind is ReadingKind.PROBE:
            if self.reachable is None:
                raise ValueError("probe readings require 'reachable'")
            if self.reachable:
                missing = [
                    f
                    for f in ("latency_ms", "packet_loss_pct", "jitter_ms", "throughput_kbps")
                    if getattr(self, f) is None
                ]
                if missing:
                    raise ValueError(f"reachable probes require {', '.join(missing)}")
        else:
            if self.success is None:
                raise ValueError("transaction readings require 'success'")
            if self.success and self.timed_out:
                raise ValueError("a successful transaction cannot be timed out")
        return self


class OutletIn(BaseModel):
    outlet_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    country: str = Field(min_length=2, max_length=2)
    area_type: AreaType


class ProviderIn(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)


class AssignmentIn(BaseModel):
    outlet_id: str
    provider_id: str


def window_start(ts: datetime) -> datetime:
    """Weekly windows start Monday 00:00 UTC."""
    ts = ts.astimezone(UTC)
    day = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday())


def window_end(start: datetime) -> datetime:
    return start + timedelta(days=7)
