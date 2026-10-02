"""Ridge phase model (shared L/R weights) + arm fusion. Verbatim from rep_segmentation_v2_hsmm.py."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from ai.core.config import Cfg
from ai.core.features import get_design


@dataclass
class PhaseModel:
    mu: np.ndarray
    sd: np.ndarray
    w: np.ndarray
    b: float
    min_rep_s: float          # shortest annotated rep in the TRAINING set (used for blip rejection)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mu) / self.sd) @ self.w + self.b

    def to_json(self) -> dict:
        return dict(mu=self.mu.tolist(), sd=self.sd.tolist(), w=self.w.tolist(), b=self.b, min_rep_s=self.min_rep_s)

    @staticmethod
    def from_json(d: dict) -> "PhaseModel":
        return PhaseModel(np.array(d["mu"]), np.array(d["sd"]), np.array(d["w"]), d["b"], d["min_rep_s"])


def fit_model(train: List[dict], cfg: Cfg, cache: Optional[dict]) -> PhaseModel:
    Xs, ys = [], []
    for v in train:
        d = get_design(v, cfg, cache)
        for a in "LR":                                        # shared weights across both arms
            Xs.append(d[a])
            ys.append(v["y"])
    X, y = np.vstack(Xs), np.concatenate(ys)
    mu, sd = X.mean(0), X.std(0) + 1e-6
    Z, yc = (X - mu) / sd, y.mean()
    n = len(Z)
    w = np.linalg.solve(Z.T @ Z + cfg.alpha * n * np.eye(Z.shape[1]), Z.T @ (y - yc))
    min_rep_s = min((r["end"] - r["start"]) / v["fps"] for v in train for r in v["gt"])
    return PhaseModel(mu, sd, w, float(yc), float(min_rep_s))


def fuse(phi_L, phi_R, valid_L, valid_R) -> np.ndarray:
    """'Either arm is pulling'. Low-confidence arm frames are ignored; if both are bad, fall back."""
    f = np.fmax(np.where(valid_L, phi_L, np.nan), np.where(valid_R, phi_R, np.nan))
    bad = np.isnan(f)
    f[bad] = np.maximum(phi_L, phi_R)[bad]
    return f


def predict_phase(v: dict, model: PhaseModel, cfg: Cfg, cache: Optional[dict]):
    d = get_design(v, cfg, cache)
    phi = {a: model.predict(d[a]) for a in "LR"}
    return phi, fuse(phi["L"], phi["R"], v["valid"]["L"], v["valid"]["R"]), d
