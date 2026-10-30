"""
Validated repetition state machine.

States: START (the profile's start_state, e.g. arms extended at the top of a lat
pulldown), MOVING_OUT (left the start band but not yet in the opposite band),
OPPOSITE (reached the opposite band), MOVING_BACK (returning).

A rep is counted only when the signal:
  1. leaves the start band,
  2. crosses fully into the opposite band (hysteresis — jitter near one threshold
     never counts),
  3. returns fully into the start band,
  4. and the whole cycle took between min_rep_seconds and max_rep_seconds.

Cycles that are too fast (noise) or too slow (someone adjusting the seat) are
rejected and reported as `rejected_cycles`, which the pipeline uses to lower the
set confidence. NaN samples (joints not visible) freeze the state and, if they
persist longer than `max_rep_seconds`, reset it — we never count what we did
not see.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from .profiles import RepProfile


class RepState(str, Enum):
    START = "start"
    MOVING_OUT = "moving_out"
    OPPOSITE = "opposite"
    MOVING_BACK = "moving_back"
    UNKNOWN = "unknown"  # before the first confident sample


@dataclass(frozen=True)
class RepEvent:
    rep_index: int
    started_at: float
    completed_at: float
    duration_s: float
    confidence: float
    peak_value: float


@dataclass
class RepCounter:
    profile: RepProfile
    state: RepState = RepState.UNKNOWN
    count: int = 0
    rejected_cycles: int = 0
    _smoothed: float = field(default=math.nan, init=False)
    _cycle_start: float | None = field(default=None, init=False)
    _cycle_peak: float = field(default=math.nan, init=False)
    _cycle_min_conf: float = field(default=1.0, init=False)
    _last_sample_t: float | None = field(default=None, init=False)
    _last_valid_t: float | None = field(default=None, init=False)
    last_rep_at: float | None = field(default=None, init=False)

    # ── band helpers ─────────────────────────────────────────────────────────
    def _in_top(self, v: float) -> bool:
        return v >= float(self.profile.top_threshold)  # type: ignore[arg-type]

    def _in_bottom(self, v: float) -> bool:
        return v <= float(self.profile.bottom_threshold)  # type: ignore[arg-type]

    def _in_start(self, v: float) -> bool:
        return self._in_top(v) if self.profile.start_state == "top" else self._in_bottom(v)

    def _in_opposite(self, v: float) -> bool:
        return self._in_bottom(v) if self.profile.start_state == "top" else self._in_top(v)

    def _reset_cycle(self) -> None:
        self._cycle_start = None
        self._cycle_peak = math.nan
        self._cycle_min_conf = 1.0

    # ── main entry point ─────────────────────────────────────────────────────
    def update(self, value: float, confidence: float, t: float) -> RepEvent | None:
        """Feed one sample. Returns a RepEvent when a valid rep completes."""
        if not self.profile.count_reps or self.profile.occupancy_only:
            return None
        self._last_sample_t = t

        if math.isnan(value) or confidence < self.profile.min_signal_confidence:
            # Lost sight of the joints. Hold state briefly; abandon the cycle if it drags on.
            if self._last_valid_t is not None and t - self._last_valid_t > self.profile.max_rep_seconds:
                if self._cycle_start is not None:
                    self.rejected_cycles += 1
                self._reset_cycle()
                self.state = RepState.UNKNOWN
                self._smoothed = math.nan
            return None

        prev_t = self._last_valid_t
        self._last_valid_t = t
        tau = self.profile.smoothing_tau_seconds
        if math.isnan(self._smoothed) or tau == 0 or prev_t is None:
            self._smoothed = value
        else:
            dt = max(0.0, t - prev_t)
            a = 1.0 - math.exp(-dt / tau)
            self._smoothed = a * value + (1 - a) * self._smoothed
        v = self._smoothed

        # A cycle that drags on longer than any plausible rep (seat adjustment, chatting mid-machine)
        # is abandoned: we wait for a clean return to the start band before counting again.
        if self._cycle_start is not None and t - self._cycle_start > self.profile.max_rep_seconds:
            self.rejected_cycles += 1
            self._reset_cycle()
            self.state = RepState.UNKNOWN

        if self.state == RepState.UNKNOWN:
            if self._in_start(v):
                self.state = RepState.START
            # If we first see the person mid-rep or at the opposite end, wait for them to return
            # to the start band; we never count a rep we only half observed.
            return None

        if self.state == RepState.START:
            if not self._in_start(v):
                self.state = RepState.MOVING_OUT
                self._cycle_start = t
                self._cycle_peak = v
                self._cycle_min_conf = confidence
            return None

        # track peak + confidence through the cycle
        if self.profile.start_state == "top":
            self._cycle_peak = v if math.isnan(self._cycle_peak) else min(self._cycle_peak, v)
        else:
            self._cycle_peak = v if math.isnan(self._cycle_peak) else max(self._cycle_peak, v)
        self._cycle_min_conf = min(self._cycle_min_conf, confidence)

        if self.state == RepState.MOVING_OUT:
            if self._in_opposite(v):
                self.state = RepState.OPPOSITE
            elif self._in_start(v):
                # Bounced back without reaching the opposite band → partial movement, not a rep.
                self.state = RepState.START
                self.rejected_cycles += 1
                self._reset_cycle()
            return None

        if self.state == RepState.OPPOSITE:
            if not self._in_opposite(v):
                self.state = RepState.MOVING_BACK
            return None

        if self.state == RepState.MOVING_BACK:
            if self._in_opposite(v):
                self.state = RepState.OPPOSITE  # dipped again; still the same cycle
            elif self._in_start(v):
                return self._complete_cycle(t)
            return None
        return None

    def _complete_cycle(self, t: float) -> RepEvent | None:
        start = self._cycle_start if self._cycle_start is not None else t
        duration = t - start
        self.state = RepState.START
        valid = self.profile.min_rep_seconds <= duration <= self.profile.max_rep_seconds
        if not valid:
            self.rejected_cycles += 1
            self._reset_cycle()
            return None
        self.count += 1
        self.last_rep_at = t
        # Confidence blends joint visibility with how cleanly the cycle fit the plausibility window.
        mid = (self.profile.min_rep_seconds + self.profile.max_rep_seconds) / 2
        span = (self.profile.max_rep_seconds - self.profile.min_rep_seconds) / 2
        timing_conf = 1.0 - min(1.0, abs(duration - mid) / span) * 0.3
        conf = round(max(0.0, min(1.0, self._cycle_min_conf * timing_conf)), 4)
        ev = RepEvent(self.count, start, t, round(duration, 3), conf, self._cycle_peak)
        self._reset_cycle()
        return ev

    def idle_seconds(self, now: float) -> float:
        """Seconds since the last completed rep (or since the first sample when none yet)."""
        ref = self.last_rep_at if self.last_rep_at is not None else self._last_valid_t
        return math.inf if ref is None else now - ref

    def reset(self) -> None:
        self.state = RepState.UNKNOWN
        self.count = 0
        self.rejected_cycles = 0
        self._smoothed = math.nan
        self._reset_cycle()
        self._last_valid_t = None
        self.last_rep_at = None
