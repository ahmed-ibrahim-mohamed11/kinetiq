"""Phase 2.5C pose pipelines. Every YOLO-based lifter consumes the SAME tracked 2D COCO
keypoints (ai/outputs/pose_bench/tracked2d) and returns (T,17,3) 3D in H36M-17 order, pelvis
zeroed per frame, plus (T,17) confidence. Each follows its repo's OFFICIAL in-the-wild recipe
(documented per class); differences between recipes are part of the pipeline under test.

MediaPipe is an end-to-end baseline: it runs its own detector/tracker on the raw video and its
33 world landmarks (meters, mid-hip origin) are mapped to H36M-17. Only the joints the downstream
interface needs are semantically meaningful; the mapping is documented in MediaPipeLifter.

NaN (tracking-lost) frames are linearly interpolated in 2D with confidence 0 before lifting —
identical policy for every pipeline (matches the production extractor's behaviour).
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
TP = os.path.join(REPO_ROOT, "third_party")
for p in (REPO_ROOT, os.path.join(REPO_ROOT, "motionbert")):
    if p not in sys.path:
        sys.path.insert(0, p)

import torch  # noqa: E402

from ai.core.pose import coco2h36m  # noqa: E402

H36M_LEFT = [4, 5, 6, 11, 12, 13]
H36M_RIGHT = [1, 2, 3, 14, 15, 16]


def interp_nan_2d(k: np.ndarray) -> np.ndarray:
    """(T,17,3) with NaN rows -> linear interp on x,y; conf 0 on filled frames."""
    k = k.copy()
    valid = np.isfinite(k[:, 0, 0])
    if valid.all() or valid.sum() < 2:
        return np.nan_to_num(k)
    idx = np.arange(len(k))
    for j in range(17):
        for c in range(2):
            k[~valid, j, c] = np.interp(idx[~valid], idx[valid], k[valid, j, c])
    k[~valid, :, 2] = 0.0
    return k


def flip_h36m(x: torch.Tensor) -> torch.Tensor:
    out = x.clone()
    out[..., 0] *= -1
    out[..., H36M_LEFT + H36M_RIGHT, :] = out[..., H36M_RIGHT + H36M_LEFT, :]
    return out


def norm_screen(xy: np.ndarray, w: float, h: float) -> np.ndarray:
    """VideoPose3D-style: x/w*2-1, y scaled by the same factor (aspect preserved)."""
    out = xy.copy()
    out[..., :2] = xy[..., :2] / w * 2 - np.array([1, h / w])
    return out


class BaseLifter:
    name = "base"
    ckpt_path = None

    def model_size_mb(self) -> float:
        return os.path.getsize(self.ckpt_path) / 1e6 if self.ckpt_path else 0.0

    def lift(self, coco: np.ndarray, wh: tuple) -> tuple[np.ndarray, np.ndarray]:
        raise NotImplementedError


class MotionBertLifter(BaseLifter):
    """Production recipe (frozen baseline's stage, via ai.core): coco2h36m -> crop_scale[1,1]
    over the whole video -> DSTformer 243-chunks (plain tail) -> flip TTA -> root-relative."""
    name = "motionbert"

    def __init__(self):
        from ai.core.lifting import DEFAULT_CHECKPOINT, load_motionbert
        self.ckpt_path = DEFAULT_CHECKPOINT
        self.model = load_motionbert(self.ckpt_path, "cpu")

    def lift(self, coco, wh):
        from ai.core.lifting import crop_scale, lift_3d
        h36m = coco2h36m(interp_nan_2d(coco))
        conf = h36m[:, :, 2].copy()
        norm = crop_scale(h36m, scale_range=[1, 1]).astype(np.float32)
        return lift_3d(self.model, norm, 243, "cpu"), conf


class MotionBertLiteLifter(BaseLifter):
    """Official MB-Lite 'global' checkpoint (FT_MB_lite_MB_ft_h36m_global_lite): same in-the-wild
    recipe as MotionBERT (infer_wild.py) with the lite architecture from its yaml; output pelvis
    is zeroed by us (the 'global' head is not root-relative)."""
    name = "motionbert_lite"

    def __init__(self):
        import yaml
        from lib.model.DSTformer import DSTformer
        cfgp = os.path.join(REPO_ROOT, "motionbert", "configs", "pose3d", "MB_ft_h36m_global_lite.yaml")
        c = yaml.safe_load(open(cfgp))
        self.ckpt_path = os.path.join(REPO_ROOT, "ai", "models", "motionbert",
                                      "FT_MB_lite_MB_ft_h36m_global_lite", "best_epoch.bin")
        model = DSTformer(dim_in=3, dim_out=3, dim_feat=c["dim_feat"], dim_rep=c["dim_rep"],
                          depth=c["depth"], num_heads=c["num_heads"], mlp_ratio=c["mlp_ratio"],
                          norm_layer=lambda d: torch.nn.LayerNorm(d, eps=1e-6),
                          maxlen=c["maxlen"], num_joints=c["num_joints"])
        ckpt = torch.load(self.ckpt_path, map_location="cpu", weights_only=True)
        state = {k[len("module."):] if k.startswith("module.") else k: v
                 for k, v in ckpt["model_pos"].items()}
        model.load_state_dict(state, strict=True)
        self.model = model.eval()
        self.no_conf = bool(c.get("no_conf", False))

    def lift(self, coco, wh):
        from ai.core.lifting import crop_scale
        h36m = coco2h36m(interp_nan_2d(coco))
        conf = h36m[:, :, 2].copy()
        norm = crop_scale(h36m, scale_range=[1, 1]).astype(np.float32)
        if self.no_conf:
            norm = norm[..., :2]
        out = []
        with torch.no_grad():
            for st in range(0, len(norm), 243):
                clip = torch.from_numpy(norm[st:st + 243]).float().unsqueeze(0)
                pred = (self.model(clip) + flip_h36m(self.model(flip_h36m(clip)))) / 2.0
                out.append(pred.squeeze(0).numpy())
        p = np.concatenate(out).astype(np.float32)
        return p - p[:, :1], conf                     # zero pelvis (global head)


class MotionAGFormerLifter(BaseLifter):
    """Official demo recipe (third_party/MotionAGFormer/demo/vis.py): H36M-17 2D normalised by
    normalize_screen_coordinates(w,h) + conf channel, 243-frame clips (tail resampled to 243 and
    de-duplicated), flip TTA, pelvis zeroed. Base model, configs/h36m/MotionAGFormer-base.yaml."""
    name = "motionagformer"

    def __init__(self):
        import yaml
        sys.path.insert(0, os.path.join(TP, "MotionAGFormer"))
        from model.MotionAGFormer import MotionAGFormer
        c = yaml.safe_load(open(os.path.join(TP, "MotionAGFormer", "configs", "h36m",
                                             "MotionAGFormer-base.yaml")))
        self.ckpt_path = os.path.join(REPO_ROOT, "ai", "models", "motionagformer",
                                      "motionagformer-b-h36m.pth.tr")
        model = MotionAGFormer(n_layers=c["n_layers"], dim_in=c["dim_in"], dim_feat=c["dim_feat"],
                               dim_rep=c["dim_rep"], n_frames=c["n_frames"],
                               neighbour_num=c.get("neighbour_num", 2),
                               use_adaptive_fusion=c.get("use_adaptive_fusion", True),
                               use_tcn=c.get("use_tcn", False), graph_only=c.get("graph_only", False))
        ckpt = torch.load(self.ckpt_path, map_location="cpu", weights_only=False)
        state = ckpt["model"] if "model" in ckpt else ckpt
        state = {k.replace("module.", ""): v for k, v in state.items()}
        model.load_state_dict(state, strict=True)
        self.model = model.eval()

    def lift(self, coco, wh):
        w, h = wh
        h36m = coco2h36m(interp_nan_2d(coco))
        conf = h36m[:, :, 2].copy()
        kp = norm_screen(h36m.astype(np.float32), w, h)
        T = len(kp)
        out = np.zeros((T, 17, 3), dtype=np.float32)
        with torch.no_grad():
            for st in range(0, T, 243):
                seg = kp[st:st + 243]
                L = len(seg)
                if L < 243:                            # official tail handling: resample + dedup
                    idx = np.clip(np.floor(np.linspace(0, L, num=243, endpoint=False)), 0, L - 1).astype(int)
                    clipk = seg[idx]
                    keep = np.unique(idx, return_index=True)[1]
                else:
                    clipk, keep = seg, None
                x = torch.from_numpy(clipk).float().unsqueeze(0)
                pred = (self.model(x) + flip_h36m(self.model(flip_h36m(x)))) / 2.0
                pred = pred.squeeze(0).numpy()
                out[st:st + L] = pred[keep] if keep is not None else pred
        return out - out[:, :1], conf


class PoseFormerV2Lifter(BaseLifter):
    """Official demo recipe (third_party/PoseFormerV2/demo/vis.py): H36M-17 (x,y) normalised,
    NO conf channel, per-frame 243-window (edge-padded) predicting the centre frame, flip TTA.
    Checkpoint 27_243_45.2.bin (243-frame sequence, 27 kept frames/coeffs). Windows are batched
    for CPU throughput; numerics are unchanged."""
    name = "poseformerv2"

    def __init__(self, batch: int = 16):
        sys.path.insert(0, os.path.join(TP, "PoseFormerV2"))
        from common.model_poseformer import PoseTransformerV2
        import argparse
        a = argparse.Namespace(embed_dim_ratio=32, depth=4, frames=243,
                               number_of_kept_frames=27, number_of_kept_coeffs=27)
        self.ckpt_path = os.path.join(REPO_ROOT, "ai", "models", "poseformerv2", "27_243_45.2.bin")
        model = PoseTransformerV2(num_frame=243, num_joints=17, in_chans=2, args=a)
        ckpt = torch.load(self.ckpt_path, map_location="cpu", weights_only=False)
        state = {k.replace("module.", ""): v for k, v in ckpt["model_pos"].items()}
        model.load_state_dict(state, strict=True)
        self.model = model.eval()
        self.batch = batch

    def lift(self, coco, wh):
        w, h = wh
        h36m = coco2h36m(interp_nan_2d(coco))
        conf = h36m[:, :, 2].copy()
        kp = norm_screen(h36m[..., :2].astype(np.float32), w, h)
        T, pad = len(kp), 121
        padded = np.pad(kp, ((pad, pad), (0, 0), (0, 0)), "edge")
        wins = np.stack([padded[i:i + 243] for i in range(T)])
        out = np.zeros((T, 17, 3), dtype=np.float32)
        with torch.no_grad():
            for st in range(0, T, self.batch):
                x = torch.from_numpy(wins[st:st + self.batch]).float()
                pred = (self.model(x) + flip_h36m(self.model(flip_h36m(x)))) / 2.0
                out[st:st + len(x)] = pred[:, 0].numpy()     # centre-frame prediction
        return out - out[:, :1], conf


class VideoPose3DLifter(BaseLifter):
    """Official recipe (third_party/VideoPose3D): COCO-17 (x,y) normalised screen coordinates,
    TemporalModel 1024ch, filter widths 3^5 (receptive field 243), edge padding 121, flip TTA.
    Checkpoint pretrained_h36m_detectron_coco.bin (COCO-keypoint input head, H36M-17 output)."""
    name = "videopose3d"

    def __init__(self):
        sys.path.insert(0, os.path.join(TP, "VideoPose3D"))
        from common.model import TemporalModel
        self.ckpt_path = os.path.join(REPO_ROOT, "ai", "models", "videopose3d",
                                      "pretrained_h36m_detectron_coco.bin")
        model = TemporalModel(17, 2, 17, filter_widths=[3, 3, 3, 3, 3], causal=False,
                              dropout=0.25, channels=1024, dense=False)
        ckpt = torch.load(self.ckpt_path, map_location="cpu", weights_only=False)
        model.load_state_dict(ckpt["model_pos"])
        self.model = model.eval()
        # COCO left/right joint sets for input flip; output is H36M
        self.coco_left = [1, 3, 5, 7, 9, 11, 13, 15]
        self.coco_right = [2, 4, 6, 8, 10, 12, 14, 16]

    def lift(self, coco, wh):
        w, h = wh
        k = interp_nan_2d(coco)
        conf = coco2h36m(k)[:, :, 2].copy()
        kp = norm_screen(k[..., :2].astype(np.float32), w, h)
        pad = 121
        padded = np.pad(kp, ((pad, pad), (0, 0), (0, 0)), "edge")
        x = torch.from_numpy(padded).float().unsqueeze(0)
        xf = x.clone()
        xf[..., 0] *= -1
        xf[:, :, self.coco_left + self.coco_right] = xf[:, :, self.coco_right + self.coco_left]
        with torch.no_grad():
            p1 = self.model(x)
            p2 = flip_h36m(self.model(xf))
            pred = ((p1 + p2) / 2.0).squeeze(0).numpy().astype(np.float32)
        return pred - pred[:, :1], conf


class MediaPipeLifter(BaseLifter):
    """End-to-end baseline: MediaPipe Pose Landmarker (tasks API, pose_landmarker_full.task,
    running_mode=VIDEO so its internal TRACKER provides temporal identity; num_poses=1 selects
    the most prominent person - our benchmark tracker is NOT applicable, documented difference).
    Representation: 33 world landmarks (metres, mid-hip origin) mapped to H36M-17:
      pelvis=mid-hip, thorax=mid-shoulder, spine=mid(pelvis,thorax), nose=0, head=mid-eyes,
      limbs direct. Confidence = landmark visibility. Pelvis re-zeroed per frame."""
    name = "mediapipe"
    _MAP = dict(pelvis=(23, 24), rhip=24, rknee=26, rank=28, lhip=23, lknee=25, lank=27,
                nose=0, head=(2, 5), lsho=11, lelb=13, lwri=15, rsho=12, relb=14, rwri=16)

    def __init__(self):
        self.ckpt_path = os.path.join(REPO_ROOT, "ai", "models", "mediapipe",
                                      "pose_landmarker_full.task")

    def lift_video(self, video_path: str):
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        opts = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=self.ckpt_path),
            running_mode=vision.RunningMode.VIDEO, num_poses=1)
        lmk = vision.PoseLandmarker.create_from_options(opts)
        cap = cv2.VideoCapture(video_path)
        out, conf = [], []
        t_ms = 0
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            img = mp.Image(image_format=mp.ImageFormat.SRGB,
                           data=cv2.cvtColor(fr, cv2.COLOR_BGR2RGB))
            res = lmk.detect_for_video(img, t_ms)
            t_ms += 67                                   # 15 fps
            if not res.pose_world_landmarks:
                out.append(np.full((17, 3), np.nan, dtype=np.float32))
                conf.append(np.zeros(17, dtype=np.float32))
                continue
            lm = res.pose_world_landmarks[0]
            P = np.array([[p.x, p.y, p.z] for p in lm], dtype=np.float32)
            V = np.array([p.visibility for p in lm], dtype=np.float32)
            h = np.zeros((17, 3), dtype=np.float32)
            c = np.zeros(17, dtype=np.float32)
            m = self._MAP
            def pick(spec):
                if isinstance(spec, tuple):
                    return P[list(spec)].mean(0), V[list(spec)].mean()
                return P[spec], V[spec]
            order = [m["pelvis"], m["rhip"], m["rknee"], m["rank"], m["lhip"], m["lknee"], m["lank"],
                     None, None, m["nose"], m["head"], m["lsho"], m["lelb"], m["lwri"],
                     m["rsho"], m["relb"], m["rwri"]]
            for j, spec in enumerate(order):
                if spec is None:
                    continue
                h[j], c[j] = pick(spec)
            h[8], c[8] = (h[11] + h[14]) / 2, (c[11] + c[14]) / 2      # thorax
            h[7], c[7] = (h[0] + h[8]) / 2, (c[0] + c[8]) / 2          # spine
            # MediaPipe world: x right, y DOWN, z toward camera -> flip y up, keep handedness
            h[:, 1] *= -1
            out.append(h)
            conf.append(c)
        cap.release()
        lmk.close()
        P = np.stack(out)
        valid = np.isfinite(P[:, 0, 0])
        if valid.any() and not valid.all() and valid.sum() >= 2:
            idx = np.arange(len(P))
            flat = P.reshape(len(P), -1)
            for col in range(flat.shape[1]):
                flat[~valid, col] = np.interp(idx[~valid], idx[valid], flat[valid, col])
            P = flat.reshape(len(P), 17, 3)
        P = np.nan_to_num(P)
        return (P - P[:, :1]).astype(np.float32), np.stack(conf)


LIFTERS = {
    "motionbert": MotionBertLifter,
    "motionbert_lite": MotionBertLiteLifter,
    "motionagformer": MotionAGFormerLifter,
    "poseformerv2": PoseFormerV2Lifter,
    "videopose3d": VideoPose3DLifter,
    "mediapipe": MediaPipeLifter,
}
