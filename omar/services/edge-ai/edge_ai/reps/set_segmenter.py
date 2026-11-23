"""
Set segmentation on top of the rep stream.

* A set STARTS at the first rep after idle (or at the first rep of the activity).
* A set ENDS when no rep completes for `idle_seconds` (SET_END_IDLE_SECONDS) or
  when the activity ends (forced close → verified=false, we did not see the end).
* rest_before_seconds = this set's first rep `started_at` minus the previous
  set's last rep `completed_at`, reported only when >= MIN_REST_SECONDS
  (shorter gaps are treated as pauses inside the same set and never split it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from edge_ai.constants import MIN_REST_SECONDS, SET_END_IDLE_SECONDS

from .state_machine import RepEvent


@dataclass(frozen=True)
class SetEvent:
    kind: Literal["set_started", "rep", "set_completed"]
    set_index: int
    set_local_id: str
    t: float
    rep: RepEvent | None = None
    rep_count: int = 0
    rest_before_seconds: float | None = None
    confidence: float = 0.0
    verified: bool = True


@dataclass
class SetSegmenter:
    activity_local_id: str
    idle_seconds: float = SET_END_IDLE_SECONDS
    min_rest_seconds: float = MIN_REST_SECONDS
    tracking_verified: bool = True  # flipped by the pipeline when tracking confidence is low

    set_index: int = 0
    _open: bool = field(default=False, init=False)
    _reps: list[RepEvent] = field(default_factory=list, init=False)
    _set_started_at: float | None = field(default=None, init=False)
    _last_rep_at: float | None = field(default=None, init=False)
    _last_set_ended_at: float | None = field(default=None, init=False)
    _unverified_seen: bool = field(default=False, init=False)

    @property
    def _set_index(self) -> int:
        return self.set_index

    @property
    def current_set_local_id(self) -> str:
        return f"{self.activity_local_id}:s{self.set_index}"

    def on_rep(self, rep: RepEvent) -> list[SetEvent]:
        out: list[SetEvent] = []
        if not self._open:
            self.set_index += 1
            self._open = True
            self._reps = []
            self._set_started_at = rep.started_at
            self._unverified_seen = not self.tracking_verified
            rest = None
            if self._last_set_ended_at is not None:
                gap = rep.started_at - self._last_set_ended_at
                rest = round(gap, 2) if gap >= self.min_rest_seconds else None
            out.append(
                SetEvent(
                    "set_started", self.set_index, self.current_set_local_id, rep.started_at, rest_before_seconds=rest
                )
            )
        if not self.tracking_verified:
            self._unverified_seen = True
        self._reps.append(rep)
        self._last_rep_at = rep.completed_at
        out.append(
            SetEvent(
                "rep", self.set_index, self.current_set_local_id, rep.completed_at, rep=rep, rep_count=len(self._reps)
            )
        )
        return out

    def tick(self, now: float) -> SetEvent | None:
        """Call periodically; closes the set after idle. The set's end time is the last rep's
        completion — the idle window is detection delay, not exercise."""
        if self._open and self._last_rep_at is not None and now - self._last_rep_at >= self.idle_seconds:
            return self._close(self._last_rep_at, forced=False)
        return None

    def close(self, now: float) -> SetEvent | None:
        """Force-close (activity ended / track lost). Marks the set unverified."""
        if self._open:
            return self._close(now, forced=True)
        return None

    def _close(self, t: float, forced: bool) -> SetEvent:
        n = len(self._reps)
        conf = round(sum(r.confidence for r in self._reps) / n, 4) if n else 0.0
        verified = (not forced) and (not self._unverified_seen) and n > 0
        ev = SetEvent(
            "set_completed",
            self.set_index,
            self.current_set_local_id,
            t,
            rep_count=n,
            confidence=conf,
            verified=verified,
        )
        self._open = False
        self._last_set_ended_at = t
        self._reps = []
        return ev

    @property
    def is_open(self) -> bool:
        return self._open
