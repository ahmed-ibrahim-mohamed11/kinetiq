"""Canonical result-row schema for all evaluation runners (Phase A root-cause fix).

The Phase 2.7/2.8 crashes ("dict() got multiple values for keyword argument 'rule'") shared one
architectural cause: metric functions returned METADATA (rule, counts context) mixed into the same
flat dict as METRICS, and every runner then rebuilt rows via `dict(meta_kwarg=..., **metrics)` -
a pattern that explodes exactly when the metric dict grows a key the caller also passes. The fix
is structural, not a line patch:

  * metric functions return METRICS ONLY (enforced: no reserved metadata keys in their output);
  * rows are built exclusively through make_row(meta, metrics), which raises on ANY key overlap
    and on metrics that smuggle reserved metadata keys - collisions become loud test failures
    instead of silent overwrites or TypeErrors deep in a 40-minute run.

Regression coverage: ai/tests/test_result_schema.py reconstructs the original failure pattern as
a fixture (per policy: no broken code is reintroduced into runners).
"""
from __future__ import annotations

# Metadata keys owned by runners. A metrics producer must never emit these.
RESERVED_META = frozenset({
    "video_id", "video", "subject", "subject_id", "view", "camera_view", "form", "form_label",
    "exercise", "rule", "pipeline", "tag", "split", "tempo", "protocol",
})


class ResultSchemaError(ValueError):
    pass


def check_metrics(metrics: dict) -> dict:
    bad = RESERVED_META & set(metrics)
    if bad:
        raise ResultSchemaError(
            f"metrics dict carries reserved metadata keys {sorted(bad)}; "
            f"metadata belongs to the runner's meta dict (see result_schema.py)")
    return metrics


def make_row(meta: dict, metrics: dict) -> dict:
    """The ONLY sanctioned way to build a result row: meta and metrics must be disjoint."""
    check_metrics(metrics)
    overlap = set(meta) & set(metrics)
    if overlap:
        raise ResultSchemaError(f"meta/metrics key collision: {sorted(overlap)}")
    return {**meta, **metrics}
