"""Annotation / saved-model loading + the live entry point. Verbatim from rep_segmentation_v2_hsmm.py."""
from __future__ import annotations

import json
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ai.core.config import Cfg
from ai.core.decoder import Decoder, predict_video
from ai.core.features import prepare_video
from ai.core.phase_model import PhaseModel


def load_annotations(path: str) -> Dict[str, List[dict]]:
    df = pd.read_csv(path)[["video", "rep", "start_frame", "pull_frame", "end_frame"]]  # 'label' dropped on purpose
    df["video"] = df["video"].astype(str).str.replace(r"\.[A-Za-z0-9]+$", "", regex=True)
    out = {}
    for name, g in df.sort_values(["video", "rep"]).groupby("video"):
        reps = [dict(start=int(r.start_frame), pull=int(r.pull_frame), end=int(r.end_frame)) for r in g.itertuples()]
        for r in reps:
            if not r["start"] < r["pull"] < r["end"]:
                raise ValueError(f"{name}: annotation not ordered start<pull<end: {r}")
        out[name] = reps
    if not out:
        # Phase 3 (P3.7, clear failure behaviour): an empty annotations file previously returned
        # {} silently -- indistinguishable from "legitimately zero videos" to any caller. Fail
        # loudly instead: a caller that gets zero annotations almost always has a path/schema bug.
        raise ValueError(f"{path}: no annotations found (file has 0 rows or no video column "
                         f"matched) -- refusing to silently return an empty result")
    return out


def load_model(path: Optional[str] = None):
    """(cfg, phase model, decoder). Phase 5: defaults to the committed, fingerprint-verified
    artifact (ai/models/rep_segmentation/, ai.core.model_artifact); a legacy --save_model JSON
    without the artifact schema is still read as before."""
    from ai.core.model_artifact import ARTIFACT_SCHEMA, load_artifact
    if path is None:
        s = load_artifact()
        return s.cfg, s.model, s.dec
    d = json.load(open(path))
    if d.get("schema") == ARTIFACT_SCHEMA:
        s = load_artifact(path)
        return s.cfg, s.model, s.dec
    return Cfg(**d["cfg"]), PhaseModel.from_json(d["model"]), Decoder.from_json(d["decoder"])


def segment_video(pose3d: np.ndarray, fps: float, model_path: Optional[str] = None,
                  conf: Optional[np.ndarray] = None) -> List[dict]:
    """Pose-level entry point: (T,17,3) MotionBERT-Lite pose -> list of reps with per-arm timing.
    (R-01: this was dead code until Phase 5 — no saved model existed. For video -> validated result
    use ai.core.pipeline.analyze_video, which adds detection, tracking, lifting and the guards.)"""
    cfg, model, dec = load_model(model_path)
    v = prepare_video("new", pose3d, fps, conf, cfg.conf_thr)
    return predict_video(v, model, dec, cfg, None)["reps"]
