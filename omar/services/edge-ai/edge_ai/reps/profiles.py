"""Exercise rep profiles — parsed from exercise_catalog.rep_profile (see supabase/seed.sql)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RepProfile(BaseModel):
    version: int = 1
    signal: str
    unit: str = "deg"
    count_reps: bool = True
    occupancy_only: bool = False
    # Hysteresis band: a rep = leaving start state, crossing to the opposite state, and returning.
    top_threshold: float | None = None
    bottom_threshold: float | None = None
    start_state: Literal["top", "bottom"] = "top"
    min_rep_seconds: float = Field(default=0.6, gt=0)
    max_rep_seconds: float = Field(default=8.0, gt=0)
    # Time-constant (seconds) of the exponential smoother on the raw signal; 0 = no smoothing.
    # Time-aware so behaviour is identical at 5, 10 or 30 Hz. Kept small (80 ms) because the
    # hysteresis bands already reject jitter and heavier smoothing attenuates fast reps.
    smoothing_tau_seconds: float = Field(default=0.08, ge=0, le=2.0)
    # Minimum signal confidence (keypoint score) to accept a sample.
    min_signal_confidence: float = Field(default=0.3, ge=0, le=1)

    @model_validator(mode="after")
    def _check(self) -> RepProfile:
        if self.count_reps and not self.occupancy_only:
            if self.top_threshold is None or self.bottom_threshold is None:
                raise ValueError("rep-counting profiles need top_threshold and bottom_threshold")
            if self.top_threshold <= self.bottom_threshold:
                raise ValueError("top_threshold must be greater than bottom_threshold")
            if self.max_rep_seconds <= self.min_rep_seconds:
                raise ValueError("max_rep_seconds must exceed min_rep_seconds")
        return self

    @classmethod
    def from_catalog(cls, rep_profile_json: dict[str, Any]) -> RepProfile:
        return cls.model_validate(rep_profile_json)
