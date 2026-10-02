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
    return out


def load_model(path: str):
    d = json.load(open(path))
    return Cfg(**d["cfg"]), PhaseModel.from_json(d["model"]), Decoder.from_json(d["decoder"])


def segment_video(pose3d: np.ndarray, fps: float, model_path: str, conf: Optional[np.ndarray] = None) -> List[dict]:
    """Drop-in call for the live pipeline: MotionBERT (T,17,3) -> list of reps with per-arm timing."""
    cfg, model, dec = load_model(model_path)
    v = prepare_video("new", pose3d, fps, conf, cfg.conf_thr)
    return predict_video(v, model, dec, cfg, None)["reps"]
