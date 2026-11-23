"""Squat Assessment System — Task 3: The 5 Squat Judging Models + Temperature Scaling Calibration.

Implements all 5 Squat Judging Candidates from KINETIQ_A3_Model_Architecture.pdf (Boxes 06-10)
plus Supporting Algorithm #3 (Temperature Scaling Confidence Calibration & Uncertainty Abstention):

  06. `RulesJudge`          — Explainable phase-aware biomechanical threshold rules.
  07. `RandomForestJudge`   — Classical ML ensemble classifier (Random Forest / Gradient Boosting) on
                              14 engineered per-rep motion features.
  08. `TemporalCNNJudge`    — PyTorch 1D-CNN + BiGRU temporal classifier on resampled `(L=32, C=8)`
                              per-rep angle & trajectory time-series.
  09. `AngleGraphJudge`     — GYMetricPose-style Angle Graph Neural Network (GNN over the 8-node
                              lower/upper body joint-angle graph with message passing).
  10. `SmallSTGCNJudge`     — Lightweight Spatio-Temporal Graph Convolutional Network (ST-GCN)
                              operating jointly on the 17-joint H36M skeleton graph across time.

Supporting Algorithm:
  `TemperatureScaler`       — Post-hoc temperature scaling calibration (`logits / T`) learned by
                              minimizing negative log-likelihood (NLL), reducing Expected Calibration
                              Error (ECE) and abstaining as `"uncertain"` when calibrated confidence
                              is below `abstain_threshold` (FR6, FR9).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from scipy.optimize import minimize

from ai.core.squat_alarm import ERROR_SPEC, SquatThresholds
from ai.core.squat_features import (
    L_ANK,
    L_HIP,
    L_KNEE,
    L_SHO,
    PELVIS,
    R_ANK,
    R_HIP,
    R_KNEE,
    R_SHO,
    SPINE,
    THORAX,
    summarize_squat_rep_features,
)

FEATURE_KEYS: Tuple[str, ...] = (
    "duration_s",
    "descent_s",
    "ascent_s",
    "min_knee_angle_deg",
    "min_hip_angle_deg",
    "min_ankle_angle_deg",
    "bottom_depth_rel",
    "max_trunk_lean_deg",
    "min_valgus_ratio",
    "ascent_valgus_ratio",
    "max_forward_knee",
    "max_lr_knee_diff_deg",
    "max_lr_depth_asym",
    "max_heel_lift",
)

# H36M-17 skeletal graph edges for ST-GCN (Model 10)
H36M_EDGES: List[Tuple[int, int]] = [
    (0, 1), (1, 2), (2, 3),        # Pelvis -> R_hip -> R_knee -> R_ank
    (0, 4), (4, 5), (5, 6),        # Pelvis -> L_hip -> L_knee -> L_ank
    (0, 7), (7, 8), (8, 9), (9, 10),  # Spine -> Thorax -> Nose -> Head
    (8, 11), (11, 12), (12, 13),   # Thorax -> L_sho -> L_elb -> L_wri
    (8, 14), (14, 15), (15, 16),   # Thorax -> R_sho -> R_elb -> R_wri
    (1, 4), (2, 5), (3, 6),        # Cross-lateral hip, knee, ankle links (for valgus/asymmetry)
]

# 8-node Angle Graph adjacency for GYMetricPose-style GNN (Model 09)
# Nodes: 0:L_knee, 1:R_knee, 2:L_hip, 3:R_hip, 4:L_ank, 5:R_ank, 6:trunk, 7:valgus_ratio
ANGLE_GRAPH_EDGES: List[Tuple[int, int]] = [
    (0, 2), (0, 4), (0, 1), (0, 7),
    (1, 3), (1, 5), (1, 7),
    (2, 3), (2, 6), (3, 6),
    (4, 5), (4, 7), (5, 7),
]


@dataclass
class RepJudgment:
    rep_index: int
    model_name: str
    verdict: Literal["correct", "incorrect", "uncertain"]
    p_incorrect: float
    confidence: float
    calibrated: bool
    primary_error: Optional[str]
    detected_errors: List[str] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# -----------------------------------------------------------------------------
# Temperature Scaling & Expected Calibration Error (ECE)
# -----------------------------------------------------------------------------
class TemperatureScaler:
    """Single-parameter post-hoc probability calibration (`p = sigmoid(logit / T)`)."""

    def __init__(self, temperature: float = 1.0):
        self.temperature = float(max(temperature, 0.05))

    def fit(self, logits: np.ndarray, y_true: np.ndarray) -> "TemperatureScaler":
        logits = np.asarray(logits, dtype=np.float64).ravel()
        y = np.asarray(y_true, dtype=np.float64).ravel()
        if len(logits) < 4 or len(np.unique(y)) < 2:
            self.temperature = 1.0
            return self

        def nll(t_arr):
            T = max(float(t_arr[0]), 0.05)
            z = np.clip(logits / T, -30.0, 30.0)
            p = 1.0 / (1.0 + np.exp(-z))
            p = np.clip(p, 1e-7, 1.0 - 1e-7)
            return -float(np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))

        res = minimize(nll, x0=[1.0], bounds=[(0.1, 10.0)], method="L-BFGS-B")
        self.temperature = float(res.x[0]) if res.success else 1.0
        return self

    def calibrate_probs(self, raw_probs: np.ndarray) -> np.ndarray:
        p = np.clip(np.asarray(raw_probs, dtype=np.float64), 1e-6, 1.0 - 1e-6)
        logits = np.log(p / (1.0 - p))
        z = np.clip(logits / self.temperature, -30.0, 30.0)
        return 1.0 / (1.0 + np.exp(-z))


def expected_calibration_error(probs: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> float:
    """Compute Expected Calibration Error (ECE) over binary predictions."""
    probs = np.asarray(probs, dtype=np.float64).ravel()
    y = np.asarray(y_true, dtype=np.float64).ravel()
    if len(probs) == 0:
        return 0.0
    conf = np.maximum(probs, 1.0 - probs)
    pred = (probs >= 0.5).astype(np.float64)
    acc = (pred == y).astype(np.float64)
    bins = np.linspace(0.5, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (conf >= bins[i]) & (conf <= bins[i + 1] if i == n_bins - 1 else conf < bins[i + 1])
        if np.any(mask):
            ece += (np.sum(mask) / len(probs)) * abs(float(np.mean(acc[mask]) - np.mean(conf[mask])))
    return round(float(ece), 4)


# -----------------------------------------------------------------------------
# Helper: Extract Fixed-Length Time Series & Feature Vector per Rep
# -----------------------------------------------------------------------------
def rep_feature_vector(rep_summary: dict) -> np.ndarray:
    return np.array([float(rep_summary.get(k, 0.0)) for k in FEATURE_KEYS], dtype=np.float32)


def resample_rep_timeseries(v: dict, start: int, end: int, length: int = 32) -> np.ndarray:
    """Resample 8 biomechanical angle/ratio signals inside `[start, end]` to `(length, 8)`."""
    s = max(0, int(start))
    e = min(int(v["T"]) - 1, max(s + 1, int(end)))
    channels = np.column_stack(
        [
            (180.0 - v["knee_angle_l"][s : e + 1]) / 90.0,
            (180.0 - v["knee_angle_r"][s : e + 1]) / 90.0,
            (180.0 - v["hip_angle_l"][s : e + 1]) / 90.0,
            (180.0 - v["hip_angle_r"][s : e + 1]) / 90.0,
            v["ankle_angle_l"][s : e + 1] / 45.0,
            v["ankle_angle_r"][s : e + 1] / 45.0,
            v["trunk_lean_deg"][s : e + 1] / 45.0,
            v["knee_valgus_ratio"][s : e + 1],
        ]
    )
    src_x = np.linspace(0.0, 1.0, len(channels))
    dst_x = np.linspace(0.0, 1.0, length)
    out = np.zeros((length, channels.shape[1]), dtype=np.float32)
    for c in range(channels.shape[1]):
        out[:, c] = np.interp(dst_x, src_x, channels[:, c])
    return out


def resample_rep_skeleton_3d(v: dict, start: int, end: int, length: int = 32) -> np.ndarray:
    """Resample 3D pose `(T_rep, 17, 3)` inside `[start, end]` to `(length, 17, 3)` for ST-GCN."""
    s = max(0, int(start))
    e = min(int(v["T"]) - 1, max(s + 1, int(end)))
    P = v["pose3d_smoothed"][s : e + 1] / max(float(v["torso_len"]), 1e-6)
    src_x = np.linspace(0.0, 1.0, len(P))
    dst_x = np.linspace(0.0, 1.0, length)
    out = np.zeros((length, 17, 3), dtype=np.float32)
    for j in range(17):
        for d in range(3):
            out[:, j, d] = np.interp(dst_x, src_x, P[:, j, d])
    return out


def diagnose_rep_errors(
    rep_summary: dict,
    observable_errors: Dict[str, bool],
    thr: Optional[SquatThresholds] = None,
) -> Tuple[List[str], Dict[str, float]]:
    """Named error diagnosis + per-error severity ratios (> 1.0 means threshold violated)."""
    t = thr or SquatThresholds()
    ratios: Dict[str, float] = {}
    errors: List[str] = []

    # 1. Knees caving inward (KIE)
    if observable_errors.get("knees_caving_inward", True):
        r_kie = t.valgus_ratio_min / max(float(rep_summary["min_valgus_ratio"]), 0.1)
        ratios["knees_caving_inward"] = round(r_kie, 3)
        if r_kie > 1.0:
            errors.append("knees_caving_inward")

    # 2. Excessive forward knee travel (KFE)
    if observable_errors.get("knees_forward_error", True):
        r_kfe = float(rep_summary["max_forward_knee"]) / max(t.forward_knee_max, 0.1)
        ratios["knees_forward_error"] = round(r_kfe, 3)
        if r_kfe > 1.0:
            errors.append("knees_forward_error")

    # 3. Insufficient squat depth (SS)
    if observable_errors.get("shallow_squat", True):
        r_ang = float(rep_summary["min_knee_angle_deg"]) / max(t.shallow_knee_angle_max, 1.0)
        r_dep = (float(rep_summary["bottom_depth_rel"]) + 0.5) / (t.shallow_depth_rel_max + 0.5)
        r_ss = max(r_ang, r_dep) if (r_ang > 1.0 and r_dep > 1.0) else min(r_ang, r_dep)
        ratios["shallow_squat"] = round(r_ss, 3)
        if r_ss > 1.0:
            errors.append("shallow_squat")

    # 4. Excessive trunk lean (ETL)
    if observable_errors.get("excessive_trunk_lean", True):
        r_etl = float(rep_summary["max_trunk_lean_deg"]) / max(t.trunk_lean_max_deg, 1.0)
        ratios["excessive_trunk_lean"] = round(r_etl, 3)
        if r_etl > 1.0:
            errors.append("excessive_trunk_lean")

    # 5. Heel lift (HL)
    if observable_errors.get("heel_lift", True):
        r_hl = float(rep_summary["max_heel_lift"]) / max(t.heel_lift_max_ratio, 0.01)
        ratios["heel_lift"] = round(r_hl, 3)
        if r_hl > 1.0:
            errors.append("heel_lift")

    # 6. Left/Right asymmetry (ASYM)
    if observable_errors.get("lr_asymmetry", True):
        r_asym = float(rep_summary["max_lr_knee_diff_deg"]) / max(t.lr_knee_diff_max_deg, 1.0)
        ratios["lr_asymmetry"] = round(r_asym, 3)
        if r_asym > 1.0:
            errors.append("lr_asymmetry")

    return errors, ratios


# -----------------------------------------------------------------------------
# Model 06: Explainable Rules / Thresholds Judge
# -----------------------------------------------------------------------------
class RulesJudge:
    name = "06_Rules_Thresholds"

    def __init__(self, thresholds: Optional[SquatThresholds] = None, abstain_conf: float = 0.55):
        self.thr = thresholds or SquatThresholds()
        self.abstain_conf = float(abstain_conf)
        self.scaler = TemperatureScaler(1.0)
        self.bias: float = 1.0

    def fit(self, X_feat: np.ndarray, y: np.ndarray) -> "RulesJudge":
        """Calibrate rule thresholds from good-form training reps (Proposal Gap 11) + fit temperature."""
        X_clean = np.nan_to_num(X_feat, nan=0.0)
        good = X_clean[y == 0]
        if len(good) >= 3:
            idx_valgus = FEATURE_KEYS.index("min_valgus_ratio")
            idx_kfe = FEATURE_KEYS.index("max_forward_knee")
            idx_knee = FEATURE_KEYS.index("min_knee_angle_deg")
            idx_depth = FEATURE_KEYS.index("bottom_depth_rel")
            idx_trunk = FEATURE_KEYS.index("max_trunk_lean_deg")
            idx_asym = FEATURE_KEYS.index("max_lr_knee_diff_deg")
            # Set thresholds using robust good-form percentiles (p15 / p85)
            self.thr.valgus_ratio_min = float(np.clip(np.percentile(good[:, idx_valgus], 15) - 0.03, 0.72, 0.90))
            self.thr.forward_knee_max = float(np.clip(np.percentile(good[:, idx_kfe], 85) + 0.04, 0.45, 0.65))
            self.thr.shallow_knee_angle_max = float(np.clip(np.percentile(good[:, idx_knee], 85) + 4.0, 98.0, 118.0))
            self.thr.shallow_depth_rel_max = float(np.clip(np.percentile(good[:, idx_depth], 85) + 0.04, 0.15, 0.46))
            self.thr.trunk_lean_max_deg = float(np.clip(np.percentile(good[:, idx_trunk], 85) + 4.0, 28.0, 48.0))
            self.thr.lr_knee_diff_max_deg = float(np.clip(np.percentile(good[:, idx_asym], 85) + 2.0, 14.0, 24.0))
        ratios = self._max_violation_ratio(X_clean)
        if len(np.unique(y)) >= 2:
            # Find optimal decision boundary around 1.0 on training set
            best_b, best_acc = 1.0, -1.0
            for b in np.linspace(0.85, 1.15, 31):
                acc = float(np.mean((ratios >= b) == (y == 1)))
                if acc > best_acc:
                    best_acc, best_b = acc, float(b)
            self.bias = best_b
        probs = self.predict_proba_features(X_clean)
        logits = np.log(np.clip(probs, 1e-5, 1 - 1e-5) / (1 - np.clip(probs, 1e-5, 1 - 1e-5)))
        self.scaler.fit(logits, y)
        return self

    def _max_violation_ratio(self, X_feat: np.ndarray) -> np.ndarray:
        X_clean = np.nan_to_num(X_feat, nan=0.0)
        idx_valgus = FEATURE_KEYS.index("min_valgus_ratio")
        idx_kfe = FEATURE_KEYS.index("max_forward_knee")
        idx_knee = FEATURE_KEYS.index("min_knee_angle_deg")
        idx_depth = FEATURE_KEYS.index("bottom_depth_rel")
        idx_trunk = FEATURE_KEYS.index("max_trunk_lean_deg")
        idx_asym = FEATURE_KEYS.index("max_lr_knee_diff_deg")

        r_kie = self.thr.valgus_ratio_min / np.maximum(X_clean[:, idx_valgus], 0.2)
        r_kfe = X_clean[:, idx_kfe] / max(self.thr.forward_knee_max, 0.1)
        r_ss = np.maximum(
            X_clean[:, idx_knee] / self.thr.shallow_knee_angle_max,
            (X_clean[:, idx_depth] + 0.5) / (self.thr.shallow_depth_rel_max + 0.5),
        )
        r_etl = X_clean[:, idx_trunk] / self.thr.trunk_lean_max_deg
        r_asym = X_clean[:, idx_asym] / self.thr.lr_knee_diff_max_deg
        return np.maximum.reduce([r_kie, r_kfe, r_ss, r_etl, r_asym])

    def predict_proba_features(self, X_feat: np.ndarray) -> np.ndarray:
        max_ratio = self._max_violation_ratio(X_feat)
        raw_p = 1.0 / (1.0 + np.exp(-6.0 * (max_ratio - self.bias)))
        return self.scaler.calibrate_probs(raw_p)


# -----------------------------------------------------------------------------
# Model 07: Classical ML Ensemble (Random Forest / Gradient Boosted Trees)
# -----------------------------------------------------------------------------
class RandomForestJudge:
    name = "07_RF_XGBoost_Ensemble"

    def __init__(self, n_trees: int = 60, max_depth: int = 4, seed: int = 42):
        self.n_trees = int(n_trees)
        self.max_depth = int(max_depth)
        self.seed = int(seed)
        self.trees: List[dict] = []
        self.scaler = TemperatureScaler(1.0)
        self.mu: Optional[np.ndarray] = None
        self.sd: Optional[np.ndarray] = None

    def _fit_stump_tree(self, X: np.ndarray, y: np.ndarray, rng: np.random.Generator, depth: int) -> dict:
        if depth >= self.max_depth or len(y) < 6 or len(np.unique(y)) == 1:
            return {"leaf": True, "prob": float(np.mean(y))}
        n_feat = X.shape[1]
        cand_feats = rng.choice(n_feat, size=max(3, int(np.sqrt(n_feat))), replace=False)
        best_gain, best_f, best_thr = -1.0, int(cand_feats[0]), float(np.median(X[:, cand_feats[0]]))
        base_var = float(np.var(y)) * len(y)
        for f in cand_feats:
            vals = X[:, f]
            for q in (20, 35, 50, 65, 80):
                thr = float(np.percentile(vals, q))
                left = y[vals <= thr]
                right = y[vals > thr]
                if len(left) < 2 or len(right) < 2:
                    continue
                gain = base_var - (float(np.var(left)) * len(left) + float(np.var(right)) * len(right))
                if gain > best_gain:
                    best_gain, best_f, best_thr = gain, int(f), thr
        if best_gain <= 1e-6:
            return {"leaf": True, "prob": float(np.mean(y))}
        mask = X[:, best_f] <= best_thr
        return {
            "leaf": False,
            "feat": best_f,
            "thr": best_thr,
            "left": self._fit_stump_tree(X[mask], y[mask], rng, depth + 1),
            "right": self._fit_stump_tree(X[~mask], y[~mask], rng, depth + 1),
        }

    def _pred_tree(self, node: dict, x: np.ndarray) -> float:
        if node["leaf"]:
            return node["prob"]
        if x[node["feat"]] <= node["thr"]:
            return self._pred_tree(node["left"], x)
        return self._pred_tree(node["right"], x)

    def fit(self, X_feat: np.ndarray, y: np.ndarray) -> "RandomForestJudge":
        rng = np.random.default_rng(self.seed)
        self.mu = X_feat.mean(axis=0)
        self.sd = X_feat.std(axis=0) + 1e-6
        Z = (X_feat - self.mu) / self.sd
        self.trees = []
        n = len(y)
        for _ in range(self.n_trees):
            idx = rng.choice(n, size=n, replace=True)
            self.trees.append(self._fit_stump_tree(Z[idx], y[idx], rng, 0))
        raw_p = self._raw_probs(Z)
        logits = np.log(np.clip(raw_p, 1e-5, 1 - 1e-5) / (1 - np.clip(raw_p, 1e-5, 1 - 1e-5)))
        self.scaler.fit(logits, y)
        return self

    def _raw_probs(self, Z: np.ndarray) -> np.ndarray:
        out = np.zeros(len(Z), dtype=np.float64)
        for i, row in enumerate(Z):
            out[i] = float(np.mean([self._pred_tree(t, row) for t in self.trees]))
        return np.clip(out, 0.02, 0.98)

    def predict_proba_features(self, X_feat: np.ndarray) -> np.ndarray:
        if not self.trees or self.mu is None or self.sd is None:
            return np.full(len(X_feat), 0.5)
        Z = (X_feat - self.mu) / self.sd
        return self.scaler.calibrate_probs(self._raw_probs(Z))


# -----------------------------------------------------------------------------
# Model 08: 1D-CNN + BiGRU Temporal Classifier (PyTorch)
# -----------------------------------------------------------------------------
class _TemporalCNNNet(nn.Module):
    def __init__(self, in_channels: int = 8, hidden: int = 24):
        super().__init__()
        self.conv1 = nn.Conv1d(in_channels, hidden, kernel_size=3, padding=1)
        self.conv2 = nn.Conv1d(hidden, hidden, kernel_size=3, padding=1)
        self.gru = nn.GRU(hidden, hidden // 2, batch_first=True, bidirectional=True)
        self.head = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, L, C) -> conv expects (B, C, L)
        h = F.relu(self.conv1(x.transpose(1, 2)))
        h = F.relu(self.conv2(h)).transpose(1, 2)
        out, _ = self.gru(h)
        pooled = out.mean(dim=1)
        return self.head(pooled).squeeze(-1)


class TemporalCNNJudge:
    name = "08_1DCNN_BiGRU_Temporal"

    def __init__(self, epochs: int = 60, lr: float = 1e-2, seed: int = 42):
        self.epochs = int(epochs)
        self.lr = float(lr)
        self.seed = int(seed)
        torch.manual_seed(self.seed)
        self.net = _TemporalCNNNet()
        self.scaler = TemperatureScaler(1.0)
        self.mu: Optional[np.ndarray] = None
        self.sd: Optional[np.ndarray] = None

    def fit(self, X_seq: np.ndarray, y: np.ndarray) -> "TemporalCNNJudge":
        torch.manual_seed(self.seed)
        self.mu = X_seq.mean(axis=(0, 1), keepdims=True)
        self.sd = X_seq.std(axis=(0, 1), keepdims=True) + 1e-5
        Z = (X_seq - self.mu) / self.sd
        self.net = _TemporalCNNNet(in_channels=Z.shape[2])
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=1e-3)
        xt = torch.from_numpy(Z).float()
        yt = torch.from_numpy(y).float()
        self.net.train()
        for _ in range(self.epochs):
            opt.zero_grad()
            logits = self.net(xt)
            loss = F.binary_cross_entropy_with_logits(logits, yt)
            loss.backward()
            opt.step()
        self.net.eval()
        with torch.no_grad():
            logits_np = self.net(xt).cpu().numpy()
        self.scaler.fit(logits_np, y)
        return self

    def predict_proba_seq(self, X_seq: np.ndarray) -> np.ndarray:
        self.net.eval()
        Z = (X_seq - self.mu) / self.sd if (self.mu is not None and self.sd is not None) else X_seq
        with torch.no_grad():
            logits = self.net(torch.from_numpy(Z).float()).cpu().numpy()
        raw_p = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
        return self.scaler.calibrate_probs(raw_p)


# -----------------------------------------------------------------------------
# Model 09: Angle Graph Network (GYMetricPose-style GNN, IEEE CBMS 2024)
# -----------------------------------------------------------------------------
class _AngleGraphNet(nn.Module):
    def __init__(self, n_nodes: int = 8, t_len: int = 32, hidden: int = 24):
        super().__init__()
        adj = torch.eye(n_nodes)
        for i, j in ANGLE_GRAPH_EDGES:
            adj[i, j] = 1.0
            adj[j, i] = 1.0
        deg = adj.sum(dim=1, keepdim=True).clamp(min=1.0)
        self.register_buffer("A", adj / deg)
        self.node_proj = nn.Linear(t_len, hidden)
        self.gcn1 = nn.Linear(hidden, hidden)
        self.gcn2 = nn.Linear(hidden, hidden)
        self.head = nn.Linear(hidden * n_nodes, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, L, N_nodes) -> (B, N_nodes, L)
        h = F.relu(self.node_proj(x.transpose(1, 2)))
        h = F.relu(self.gcn1(torch.matmul(self.A, h)) + h)
        h = F.relu(self.gcn2(torch.matmul(self.A, h)) + h)
        return self.head(h.reshape(h.size(0), -1)).squeeze(-1)


class AngleGraphJudge:
    name = "09_AngleGraph_GNN"

    def __init__(self, epochs: int = 60, lr: float = 1e-2, seed: int = 42):
        self.epochs = int(epochs)
        self.lr = float(lr)
        self.seed = int(seed)
        self.net = _AngleGraphNet()
        self.scaler = TemperatureScaler(1.0)
        self.mu: Optional[np.ndarray] = None
        self.sd: Optional[np.ndarray] = None

    def fit(self, X_seq: np.ndarray, y: np.ndarray) -> "AngleGraphJudge":
        torch.manual_seed(self.seed)
        self.mu = X_seq.mean(axis=(0, 1), keepdims=True)
        self.sd = X_seq.std(axis=(0, 1), keepdims=True) + 1e-5
        Z = (X_seq - self.mu) / self.sd
        self.net = _AngleGraphNet(n_nodes=Z.shape[2], t_len=Z.shape[1])
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=1e-3)
        xt = torch.from_numpy(Z).float()
        yt = torch.from_numpy(y).float()
        self.net.train()
        for _ in range(self.epochs):
            opt.zero_grad()
            logits = self.net(xt)
            loss = F.binary_cross_entropy_with_logits(logits, yt)
            loss.backward()
            opt.step()
        self.net.eval()
        with torch.no_grad():
            logits_np = self.net(xt).cpu().numpy()
        self.scaler.fit(logits_np, y)
        return self

    def predict_proba_seq(self, X_seq: np.ndarray) -> np.ndarray:
        self.net.eval()
        Z = (X_seq - self.mu) / self.sd if (self.mu is not None and self.sd is not None) else X_seq
        with torch.no_grad():
            logits = self.net(torch.from_numpy(Z).float()).cpu().numpy()
        raw_p = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
        return self.scaler.calibrate_probs(raw_p)


# -----------------------------------------------------------------------------
# Model 10: Small ST-GCN (Spatio-Temporal Graph Convolutional Network on 17 3D Joints)
# -----------------------------------------------------------------------------
class _SmallSTGCNNet(nn.Module):
    def __init__(self, n_joints: int = 17, in_dim: int = 3, hidden: int = 24):
        super().__init__()
        adj = torch.eye(n_joints)
        for i, j in H36M_EDGES:
            adj[i, j] = 1.0
            adj[j, i] = 1.0
        deg = adj.sum(dim=1, keepdim=True).clamp(min=1.0)
        self.register_buffer("A", adj / deg)
        self.spatial1 = nn.Linear(in_dim, hidden)
        self.temp1 = nn.Conv1d(hidden, hidden, kernel_size=3, padding=1)
        self.spatial2 = nn.Linear(hidden, hidden)
        self.head = nn.Linear(hidden * 2, 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, T, V=17, C=3)
        B, T, V, C = x.shape
        h = torch.einsum("ij,btjc->btic", self.A, x)
        h = F.relu(self.spatial1(h))  # (B, T, V, H)
        # Temporal conv per joint
        h_t = h.permute(0, 2, 3, 1).reshape(B * V, -1, T)
        h_t = F.relu(self.temp1(h_t)).reshape(B, V, -1, T).permute(0, 3, 1, 2) + h
        h2 = torch.einsum("ij,btjc->btic", self.A, h_t)
        h2 = F.relu(self.spatial2(h2) + h_t)
        mean_pool = h2.mean(dim=(1, 2))
        max_pool = h2.amax(dim=(1, 2))
        pooled = torch.cat([mean_pool, max_pool], dim=-1)
        return self.head(pooled).squeeze(-1)


class SmallSTGCNJudge:
    name = "10_Small_STGCN"

    def __init__(self, epochs: int = 70, lr: float = 1e-2, seed: int = 42):
        self.epochs = int(epochs)
        self.lr = float(lr)
        self.seed = int(seed)
        self.net = _SmallSTGCNNet()
        self.scaler = TemperatureScaler(1.0)
        self.mu: Optional[np.ndarray] = None
        self.sd: Optional[np.ndarray] = None

    def fit(self, X_skel: np.ndarray, y: np.ndarray) -> "SmallSTGCNJudge":
        torch.manual_seed(self.seed)
        self.mu = X_skel.mean(axis=(0, 1), keepdims=True)
        self.sd = X_skel.std(axis=(0, 1), keepdims=True) + 1e-5
        Z = (X_skel - self.mu) / self.sd
        self.net = _SmallSTGCNNet()
        opt = torch.optim.AdamW(self.net.parameters(), lr=self.lr, weight_decay=1e-3)
        xt = torch.from_numpy(Z).float()
        yt = torch.from_numpy(y).float()
        self.net.train()
        for _ in range(self.epochs):
            opt.zero_grad()
            logits = self.net(xt)
            loss = F.binary_cross_entropy_with_logits(logits, yt)
            loss.backward()
            opt.step()
        self.net.eval()
        with torch.no_grad():
            logits_np = self.net(xt).cpu().numpy()
        self.scaler.fit(logits_np, y)
        return self

    def predict_proba_skel(self, X_skel: np.ndarray) -> np.ndarray:
        self.net.eval()
        Z = (X_skel - self.mu) / self.sd if (self.mu is not None and self.sd is not None) else X_skel
        with torch.no_grad():
            logits = self.net(torch.from_numpy(Z).float()).cpu().numpy()
        raw_p = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
        return self.scaler.calibrate_probs(raw_p)


# -----------------------------------------------------------------------------
# Unified Squat Judging Suite (Runs Any or All 5 Models + Error Naming + Abstention)
# -----------------------------------------------------------------------------
class SquatJudgeSuite:
    """Houses all 5 Squat Judging Models (06..10) + Temperature Calibration + Error Naming."""

    def __init__(self, thresholds: Optional[SquatThresholds] = None, abstain_conf: float = 0.56):
        self.thr = thresholds or SquatThresholds()
        self.abstain_conf = float(abstain_conf)
        self.rules = RulesJudge(self.thr, abstain_conf=abstain_conf)
        self.rf = RandomForestJudge()
        self.tcnn = TemporalCNNJudge()
        self.agnn = AngleGraphJudge()
        self.stgcn = SmallSTGCNJudge()
        self.is_fitted = False

    def fit_all(
        self,
        X_feat: np.ndarray,
        X_seq: np.ndarray,
        X_skel: np.ndarray,
        y: np.ndarray,
    ) -> "SquatJudgeSuite":
        self.rules.fit(X_feat, y)
        self.rf.fit(X_feat, y)
        self.tcnn.fit(X_seq, y)
        self.agnn.fit(X_seq, y)
        self.stgcn.fit(X_skel, y)
        self.is_fitted = True
        return self

    def judge_rep(
        self,
        v: dict,
        rep: dict,
        model_choice: str = "ensemble",
    ) -> RepJudgment:
        """Judge a single segmented Squat repetition and return verdict, named errors, and calibrated confidence."""
        s, b, e = int(rep["start"]), int(rep.get("bottom", rep.get("pull"))), int(rep["end"])
        summary = summarize_squat_rep_features(v, s, b, e)
        obs = v["view_info"].observable_errors
        errors, ratios = diagnose_rep_errors(summary, obs, self.thr)

        # Low keypoint visibility inside the rep -> abstain as "uncertain" (FR9)
        if summary["frac_valid"] < 0.60:
            return RepJudgment(
                rep_index=int(rep["index"]),
                model_name=model_choice,
                verdict="uncertain",
                p_incorrect=0.5,
                confidence=round(summary["frac_valid"], 3),
                calibrated=True,
                primary_error=None,
                detected_errors=[],
                explanation="Uncertain — key lower-body joints were occluded during this repetition.",
            )

        x_f = rep_feature_vector(summary)[None, :]
        p_rules = float(self.rules.predict_proba_features(x_f)[0])

        if self.is_fitted:
            x_seq = resample_rep_timeseries(v, s, e)[None, ...]
            x_skel = resample_rep_skeleton_3d(v, s, e)[None, ...]
            p_rf = float(self.rf.predict_proba_features(x_f)[0])
            p_tcnn = float(self.tcnn.predict_proba_seq(x_seq)[0])
            p_agnn = float(self.agnn.predict_proba_seq(x_seq)[0])
            p_stgcn = float(self.stgcn.predict_proba_skel(x_skel)[0])
            model_probs = {
                "06_Rules_Thresholds": p_rules,
                "07_RF_XGBoost_Ensemble": p_rf,
                "08_1DCNN_BiGRU_Temporal": p_tcnn,
                "09_AngleGraph_GNN": p_agnn,
                "10_Small_STGCN": p_stgcn,
                "ensemble": float(0.25 * p_rules + 0.25 * p_rf + 0.20 * p_tcnn + 0.15 * p_agnn + 0.15 * p_stgcn),
            }
            p_inc = model_probs.get(model_choice, model_probs["ensemble"])
        else:
            p_inc = p_rules

        # Blend rule-triggered error evidence so named biomechanical violations always agree with verdict
        if errors and p_inc < 0.55:
            p_inc = max(p_inc, 0.68)
        elif not errors and p_inc >= 0.50 and ratios:
            # If ML classifier flagged incorrect, attribute to the highest-ratio observable indicator
            top_err = max(ratios.items(), key=lambda kv: kv[1])[0]
            if ratios[top_err] >= 0.88:
                errors = [top_err]

        conf = float(max(p_inc, 1.0 - p_inc)) * float(min(1.0, summary["frac_valid"] + 0.1))
        if conf < self.abstain_conf:
            verdict: Literal["correct", "incorrect", "uncertain"] = "uncertain"
            explanation = "Uncertain — movement sits near the decision boundary."
        elif p_inc >= 0.50 or len(errors) > 0:
            verdict = "incorrect"
            primary = errors[0] if errors else max(ratios.items(), key=lambda kv: kv[1])[0]
            if primary not in errors:
                errors.append(primary)
            explanation = ERROR_SPEC[primary]["message"]
        else:
            verdict = "correct"
            explanation = "Good squat form — depth, knee tracking, and posture within target range."

        return RepJudgment(
            rep_index=int(rep["index"]),
            model_name=model_choice,
            verdict=verdict,
            p_incorrect=round(p_inc, 4),
            confidence=round(conf, 4),
            calibrated=True,
            primary_error=errors[0] if errors else None,
            detected_errors=errors,
            explanation=explanation,
        )

