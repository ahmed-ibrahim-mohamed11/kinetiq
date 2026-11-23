"""Persisted rep-segmentation system (Phase 5, P5.1 — closes the core of RISK_REGISTER R-01).

Before this, every evaluation and every inference REFIT the phase model + HSMM decoder at
runtime from the Mendeley BACK TRAIN pose caches (git-ignored, ~165 files) — so a fresh clone
could not run inference at all, and nothing pinned exactly which fitted system was "the model".
The frozen system is now exported ONCE to a small versioned JSON artifact (committed) that holds
the config, the phase-model weights, the decoder parameters and full provenance, plus the
fitted-system fingerprint the baseline manifest already records. `load_artifact` re-verifies
that fingerprint, so a corrupted or hand-edited artifact fails loudly instead of silently
producing different reps.

Fingerprint = sha256 of {model weights, decoder parameters (minus its descriptive `stats`), cfg},
floats rounded to 6 decimals (robust to BLAS ULP noise across machines) — the same definition
`ai/scripts/baseline_lib.py::system_fingerprint` used for the Phase 0 manifest (it now imports
this one, so the two can never diverge).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional

from ai.core.config import Cfg
from ai.core.decoder import Decoder
from ai.core.phase_model import PhaseModel

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ARTIFACT_DIR = os.path.join(REPO_ROOT, "ai", "models", "rep_segmentation")
DEFAULT_ARTIFACT = os.path.join(ARTIFACT_DIR, "mblite_rowing_v1.json")
ARTIFACT_SCHEMA = "gym-ai/rep-segmentation-artifact/v1"


def _round(x, nd=6):
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _round(v, nd) for k, v in sorted(x.items())}
    if isinstance(x, (list, tuple)):
        return [_round(v, nd) for v in x]
    return x


def fingerprint(cfg: Cfg, model: PhaseModel, dec: Decoder) -> str:
    d = dict(model=model.to_json(),
             decoder={k: v for k, v in dec.to_json().items() if k != "stats"},
             cfg=cfg.__dict__)
    return hashlib.sha256(json.dumps(_round(d), sort_keys=True).encode()).hexdigest()


@dataclass
class LoadedSystem:
    """A ready-to-run rep-segmentation system (duck-type compatible with the evaluation
    harness's RepSystem and with ai.core.live.LiveStreamer: exposes .cfg, .model, .dec)."""
    cfg: Cfg
    model: PhaseModel
    dec: Decoder
    fingerprint: str
    version: str
    meta: dict = field(default_factory=dict)
    name: str = "artifact"

    def predict(self, v: dict) -> dict:
        from ai.core.guards import safe_predict_video
        return safe_predict_video(v, self.model, self.dec, self.cfg)


def export_artifact(cfg: Cfg, model: PhaseModel, dec: Decoder, path: str,
                    artifact_version: str, provenance: dict) -> dict:
    d = dict(schema=ARTIFACT_SCHEMA, artifact_version=artifact_version,
             created_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
             fitted_system_sha256=fingerprint(cfg, model, dec),
             cfg=cfg.__dict__, model=model.to_json(), decoder=dec.to_json(),
             provenance=provenance)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(d, f, indent=1, default=float)
    return d


def load_artifact(path: Optional[str] = None, verify: bool = True) -> LoadedSystem:
    path = path or DEFAULT_ARTIFACT
    if not os.path.exists(path):
        raise FileNotFoundError(f"rep-segmentation artifact not found: {path} "
                                f"(export with ai/scripts/export_model_artifact.py)")
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("schema") != ARTIFACT_SCHEMA:
        raise ValueError(f"{path}: unsupported artifact schema {d.get('schema')!r}, "
                         f"expected {ARTIFACT_SCHEMA!r}")
    cfg = Cfg(**d["cfg"])
    model = PhaseModel.from_json(d["model"])
    dec = Decoder.from_json(d["decoder"])
    fp = fingerprint(cfg, model, dec)
    if verify and fp != d["fitted_system_sha256"]:
        raise ValueError(f"{path}: fingerprint mismatch (stored {d['fitted_system_sha256'][:12]}, "
                         f"recomputed {fp[:12]}) — artifact corrupted or edited")
    return LoadedSystem(cfg=cfg, model=model, dec=dec, fingerprint=fp,
                        version=d["artifact_version"], meta=d.get("provenance", {}))
