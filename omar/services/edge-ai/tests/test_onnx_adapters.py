"""
Exercises the ONNX adapters end-to-end through ONNX Runtime with SYNTHETIC models.

The models are built here with the `onnx` package: they return fixed tensors (plus a
zero-weighted term from the input so the graph consumes it). This validates
pre-/post-processing, letterboxing, NMS, un-letterboxing, pose→box matching,
normalisation and L2 output handling without downloading any weights.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
cv2 = pytest.importorskip("cv2")

from onnx import TensorProto, helper, numpy_helper  # noqa: E402


def _constant_model(path: Path, input_shape: list[int], output: np.ndarray, in_name: str = "images") -> None:
    """y = const + 0 * reduce_mean(x)  — output is independent of x but x is consumed."""
    x = helper.make_tensor_value_info(in_name, TensorProto.FLOAT, input_shape)
    y = helper.make_tensor_value_info("output0", TensorProto.FLOAT, list(output.shape))
    const = numpy_helper.from_array(output.astype(np.float32), "const")
    zero = numpy_helper.from_array(np.array(0.0, dtype=np.float32), "zero")
    nodes = [
        helper.make_node("ReduceMean", [in_name], ["m"], keepdims=0),
        helper.make_node("Mul", ["m", "zero"], ["z"]),
        helper.make_node("Add", ["const", "z"], ["output0"]),
    ]
    graph = helper.make_graph(nodes, "synthetic", [x], [y], initializer=[const, zero])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def _frame(w: int = 1280, h: int = 720) -> np.ndarray:
    return np.full((h, w, 3), 90, dtype=np.uint8)


def test_detector_adapter_decodes_letterboxed_prediction(tmp_path: Path):
    from edge_ai.detection.onnx_detector import OnnxPersonDetector, letterbox

    frame = _frame()
    # Ground-truth person box in image pixels → express it in letterboxed 640×640 coordinates.
    gt = (300.0, 100.0, 420.0, 420.0)
    _, scale, (px, py) = letterbox(frame, (640, 640))
    cx = (gt[0] + gt[2]) / 2 * scale + px
    cy = (gt[1] + gt[3]) / 2 * scale + py
    w = (gt[2] - gt[0]) * scale
    h = (gt[3] - gt[1]) * scale
    n = 50
    pred = np.zeros((1, 84, n), dtype=np.float32)
    pred[0, :4, 0] = [cx, cy, w, h]
    pred[0, 4, 0] = 0.92  # person score
    pred[0, :4, 1] = [cx + 2, cy + 2, w, h]  # near-duplicate → suppressed by NMS
    pred[0, 4, 1] = 0.80
    pred[0, :4, 2] = [50, 50, 20, 20]
    pred[0, 5, 2] = 0.95  # a bicycle (class 1), ignored
    model = tmp_path / "det.onnx"
    _constant_model(model, [1, 3, 640, 640], pred)

    det = OnnxPersonDetector(str(model), providers=["CPUExecutionProvider"])
    out = det.detect(frame)
    assert len(out) == 1
    assert out[0].score == pytest.approx(0.92, abs=1e-5)
    for a, b in zip(out[0].box, gt, strict=False):
        assert abs(a - b) < 1.5


def test_pose_adapter_matches_keypoints_to_boxes(tmp_path: Path):
    from edge_ai.detection.onnx_detector import letterbox
    from edge_ai.pose.onnx_pose import YoloPoseOnnx

    frame = _frame()
    gt = (500.0, 150.0, 640.0, 560.0)
    _, scale, (px, py) = letterbox(frame, (640, 640))
    cx = (gt[0] + gt[2]) / 2 * scale + px
    cy = (gt[1] + gt[3]) / 2 * scale + py
    w = (gt[2] - gt[0]) * scale
    h = (gt[3] - gt[1]) * scale
    pred = np.zeros((1, 56, 20), dtype=np.float32)
    pred[0, :4, 0] = [cx, cy, w, h]
    pred[0, 4, 0] = 0.9
    # knee (13) at image (560, 420), hip (11) at (560, 300), ankle (15) at (560, 540): straight leg → 180°
    for idx, (ix, iy) in {11: (560, 300), 13: (560, 420), 15: (560, 540)}.items():
        pred[0, 5 + idx * 3 : 5 + idx * 3 + 3, 0] = [ix * scale + px, iy * scale + py, 0.95]
    model = tmp_path / "pose.onnx"
    _constant_model(model, [1, 3, 640, 640], pred)

    est = YoloPoseOnnx(str(model), providers=["CPUExecutionProvider"])
    poses = est.estimate(frame, [gt, (10.0, 10.0, 60.0, 120.0)])
    assert poses[1] is None  # no person there
    pose = poses[0]
    assert pose is not None
    knee = pose.kp("left_knee")
    assert abs(knee.x - 560) < 1.0 and abs(knee.y - 420) < 1.0 and knee.score == pytest.approx(0.95, abs=1e-5)
    from edge_ai.pose.angles import knee_angle_mean

    angle, conf = knee_angle_mean(pose)
    assert abs(angle - 180.0) < 0.5 and conf == pytest.approx(0.95, abs=1e-5)


def test_reid_embedder_returns_unit_vector(tmp_path: Path):
    from edge_ai.fusion.onnx_reid import OnnxReidEmbedder

    vec = np.arange(1, 513, dtype=np.float32)[None]
    model = tmp_path / "reid.onnx"
    _constant_model(model, [1, 3, 256, 128], vec)
    emb = OnnxReidEmbedder(str(model), providers=["CPUExecutionProvider"])
    out = emb.embed(_frame(), (100, 100, 200, 400))
    assert out is not None and len(out) == 512
    assert abs(float(np.linalg.norm(out)) - 1.0) < 1e-5
    assert emb.embed(_frame(), (-50, -50, -10, -10)) is None  # box outside the image


def test_face_embedder_and_haar_detector(tmp_path: Path):
    from edge_ai.identity.face_pipeline import HaarFaceDetector, NoLivenessDetector, OnnxFaceEmbedder

    vec = np.ones((1, 512), dtype=np.float32)
    model = tmp_path / "face.onnx"
    _constant_model(model, [1, 3, 112, 112], vec)
    fe = OnnxFaceEmbedder(str(model), providers=["CPUExecutionProvider"])
    out = fe.embed(_frame(200, 200))
    assert len(out) == 512 and abs(float(np.linalg.norm(out)) - 1.0) < 1e-5
    det = HaarFaceDetector()
    assert det.detect(_frame(300, 300)) == []  # a flat grey image has no face
    assert NoLivenessDetector().score(_frame(), None) is None  # type: ignore[arg-type]
