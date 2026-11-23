"""
YOLOX (raw head) detector layout and RTMPose (SimCC) top-down adapter, through ONNX Runtime.

The models are SYNTHETIC graphs built with onnx.helper whose outputs are constant tensors in
exactly the layout of the official exports (YOLOX: [1, N, 85] raw grid outputs for strides
8/16/32; RTMPose: simcc_x [1, 17, 384] and simcc_y [1, 17, 512]), plus a zero-weighted term from
the input so the graph consumes it. We know the decoded result, so the tests assert grid decode,
score = obj * cls, NMS, the top-left letterbox / crop mapping back to frame pixels, and that the
joint angles from edge_ai/pose/angles.py come out right. No network access, no real weights.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
cv2 = pytest.importorskip("cv2")

from onnx import TensorProto, helper, numpy_helper  # noqa: E402


def _constant_model(
    path: Path, input_shape: list[int], outputs: dict[str, np.ndarray], in_name: str = "images"
) -> None:
    """Each output = const + 0 * reduce_mean(x): independent of x, but x is consumed."""
    x = helper.make_tensor_value_info(in_name, TensorProto.FLOAT, input_shape)
    nodes = [
        helper.make_node("ReduceMean", [in_name], ["m"], keepdims=0),
        helper.make_node("Mul", ["m", "zero"], ["z"]),
    ]
    inits = [numpy_helper.from_array(np.array(0.0, dtype=np.float32), "zero")]
    outs = []
    for name, value in outputs.items():
        inits.append(numpy_helper.from_array(value.astype(np.float32), f"{name}_const"))
        nodes.append(helper.make_node("Add", [f"{name}_const", "z"], [name]))
        outs.append(helper.make_tensor_value_info(name, TensorProto.FLOAT, list(value.shape)))
    graph = helper.make_graph(nodes, "synthetic", [x], outs, initializer=inits)
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    onnx.save(model, str(path))


def _frame(w: int = 1280, h: int = 720, bgr: tuple[int, int, int] = (90, 90, 90)) -> np.ndarray:
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = bgr
    return img


# ----------------------------------------------------------------------------------------------
# YOLOX
# ----------------------------------------------------------------------------------------------

YOLOX_IN = 416  # yolox_nano / yolox_tiny input; N = 52² + 26² + 13² = 3549


def _yolox_index(stride: int, gx: int, gy: int, size: int = YOLOX_IN) -> int:
    """Row index of grid cell (gx, gy) at `stride` in the concatenated [N, 85] output."""
    offset = 0
    for s in (8, 16, 32):
        n = size // s
        if s == stride:
            return offset + gy * n + gx
        offset += n * n
    raise AssertionError(stride)


def _encode_yolox(
    pred: np.ndarray, box_in: tuple[float, float, float, float], stride: int, obj: float, cls: dict[int, float]
) -> None:
    """Write the raw YOLOX head values whose decode is `box_in` (xyxy, input pixels) into `pred`."""
    cx, cy = (box_in[0] + box_in[2]) / 2, (box_in[1] + box_in[3]) / 2
    w, h = box_in[2] - box_in[0], box_in[3] - box_in[1]
    gx, gy = int(cx // stride), int(cy // stride)
    row = _yolox_index(stride, gx, gy)
    pred[0, row, 0] = cx / stride - gx
    pred[0, row, 1] = cy / stride - gy
    pred[0, row, 2] = math.log(w / stride)
    pred[0, row, 3] = math.log(h / stride)
    pred[0, row, 4] = obj
    for c, v in cls.items():
        pred[0, row, 5 + c] = v


def _yolox_scene(scale: float) -> tuple[np.ndarray, list[tuple[float, float, float, float]]]:
    """Raw output with two people (one with a near-duplicate), a bicycle, and a low-score person."""
    n = sum((YOLOX_IN // s) ** 2 for s in (8, 16, 32))
    assert n == 3549
    pred = np.zeros((1, n, 85), dtype=np.float32)
    person_a = (300.0, 100.0, 420.0, 420.0)  # image pixels
    person_b = (800.0, 200.0, 1000.0, 700.0)

    def to_in(b: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        return (b[0] * scale, b[1] * scale, b[2] * scale, b[3] * scale)  # top-left padding: no offset

    _encode_yolox(pred, to_in(person_a), 8, obj=0.9, cls={0: 0.95})  # score 0.855
    dup = tuple(v + 1.5 for v in to_in(person_a))
    _encode_yolox(pred, dup, 16, obj=0.8, cls={0: 0.9})  # 0.72, overlaps A → suppressed by NMS
    _encode_yolox(pred, to_in(person_b), 32, obj=0.85, cls={0: 0.8})  # 0.68, separate → kept
    _encode_yolox(pred, (20.0, 20.0, 60.0, 60.0), 8, obj=0.99, cls={1: 0.99, 0: 0.01})  # bicycle
    _encode_yolox(pred, (200.0, 150.0, 260.0, 220.0), 16, obj=0.5, cls={0: 0.5})  # 0.25 < 0.35
    return pred, [person_a, person_b]


def _yolox_model(tmp_path: Path) -> tuple[Path, list[tuple[float, float, float, float]]]:
    scale = min(YOLOX_IN / 1280, YOLOX_IN / 720)
    pred, gts = _yolox_scene(scale)
    path = tmp_path / "yolox_nano.onnx"
    _constant_model(path, [1, 3, YOLOX_IN, YOLOX_IN], {"output": pred})
    return path, gts


def test_yolox_grids_follow_head_order():
    from edge_ai.detection.onnx_detector import yolox_grids

    grids, strides = yolox_grids((416, 416))
    assert grids.shape == (3549, 2) and strides.shape == (3549, 1)
    assert grids[1].tolist() == [1, 0] and grids[52].tolist() == [0, 1]  # x fastest, then y
    assert strides[2703, 0] == 8 and strides[2704, 0] == 16 and strides[-1, 0] == 32
    assert grids[-1].tolist() == [12, 12]
    # non-square input: 640×384 → (80·48) + (40·24) + (20·12)
    g2, _ = yolox_grids((640, 384))
    assert g2.shape[0] == 80 * 48 + 40 * 24 + 20 * 12


def test_yolox_decode_nms_and_top_left_letterbox(tmp_path: Path):
    from edge_ai.detection.onnx_detector import OnnxPersonDetector

    path, (person_a, person_b) = _yolox_model(tmp_path)
    det = OnnxPersonDetector(str(path), layout="yolox", providers=["CPUExecutionProvider"])
    assert det.input_size == (416, 416)  # read from the model's static input shape
    out = det.detect(_frame())
    assert len(out) == 2, out  # duplicate suppressed, bicycle and low score dropped
    a, b = out  # sorted by score
    assert a.score == pytest.approx(0.9 * 0.95, abs=1e-5)
    assert b.score == pytest.approx(0.85 * 0.8, abs=1e-5)
    assert a.class_id == 0
    for got, want in zip(a.box, person_a, strict=True):
        assert abs(got - want) < 0.05
    for got, want in zip(b.box, person_b, strict=True):
        assert abs(got - want) < 0.05


def test_yolox_preprocess_is_bgr_0_255_top_left_padded(tmp_path: Path):
    from edge_ai.detection.onnx_detector import OnnxPersonDetector

    path, _ = _yolox_model(tmp_path)
    det = OnnxPersonDetector(str(path), layout="yolox", providers=["CPUExecutionProvider"])
    real = det.session
    seen: dict[str, np.ndarray] = {}

    class Spy:
        def run(self, names, feed):  # noqa: ANN001, ANN202
            seen.update(feed)
            return real.run(names, feed)

        def get_providers(self):  # noqa: ANN202
            return real.get_providers()

    det.session = Spy()  # type: ignore[assignment]
    det.detect(_frame(bgr=(10, 20, 30)))
    blob = seen[det.input_name]
    assert blob.shape == (1, 3, 416, 416) and blob.dtype == np.float32
    # 1280×720 → 416×234 at the top-left; rows 234.. are padding (114). BGR order, no normalisation.
    assert blob[0, :, 0, 0].tolist() == [10.0, 20.0, 30.0]
    assert blob[0, :, 233, 415].tolist() == [10.0, 20.0, 30.0]
    assert blob[0, :, 234, 0].tolist() == [114.0, 114.0, 114.0]
    assert blob[0, :, 415, 415].tolist() == [114.0, 114.0, 114.0]


def test_yolox_rejects_mismatched_anchor_count(tmp_path: Path):
    from edge_ai.detection.onnx_detector import OnnxPersonDetector

    path, _ = _yolox_model(tmp_path)
    det = OnnxPersonDetector(str(path), layout="yolox", input_size=(640, 640), providers=["CPUExecutionProvider"])
    out = np.zeros((1, 3549, 85), dtype=np.float32)
    with pytest.raises(ValueError, match="anchor points"):
        det.postprocess_yolox(out, 0.5, 1280, 720)


def test_auto_layout_unchanged_and_layout_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from edge_ai.detection.onnx_detector import OnnxPersonDetector, create_detector

    path, _ = _yolox_model(tmp_path)
    assert OnnxPersonDetector(str(path), providers=["CPUExecutionProvider"]).layout == "auto"
    with pytest.raises(ValueError):
        OnnxPersonDetector(str(path), layout="yolov99", providers=["CPUExecutionProvider"])  # type: ignore[arg-type]
    monkeypatch.setenv("EDGE_DETECTOR_LAYOUT", "yolox")
    det = create_detector(str(path), providers=["CPUExecutionProvider"])
    assert det.layout == "yolox" and len(det.detect(_frame())) == 2
    assert create_detector(str(path), layout="auto", providers=["CPUExecutionProvider"]).layout == "auto"
    monkeypatch.setenv("EDGE_DETECTOR_LAYOUT", "nope")
    with pytest.raises(ValueError):
        create_detector(str(path), providers=["CPUExecutionProvider"])


# ----------------------------------------------------------------------------------------------
# RTMPose
# ----------------------------------------------------------------------------------------------

RTM_W, RTM_H = 192, 256


def _rtm_center_scale(box: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Independent re-implementation of the mmpose bbox → (centre, scale) with padding 1.25 and aspect fix."""
    x1, y1, x2, y2 = box
    c = np.array([(x1 + x2) / 2, (y1 + y2) / 2])
    w, h = (x2 - x1) * 1.25, (y2 - y1) * 1.25
    if w > h * RTM_W / RTM_H:
        h = w * RTM_H / RTM_W
    else:
        w = h * RTM_W / RTM_H
    return c, np.array([w, h])


def _simcc(
    targets: dict[int, tuple[float, float]],
    box: tuple[float, float, float, float],
    scores: dict[int, tuple[float, float]] | None = None,
) -> tuple[np.ndarray, np.ndarray, dict[int, tuple[float, float]]]:
    """SimCC heads whose argmax lands nearest to the frame-pixel targets; returns the exact decoded points."""
    c, s = _rtm_center_scale(box)
    sx = np.zeros((1, 17, RTM_W * 2), dtype=np.float32)
    sy = np.zeros((1, 17, RTM_H * 2), dtype=np.float32)
    expected: dict[int, tuple[float, float]] = {}
    for k, (fx, fy) in targets.items():
        ix = (fx - c[0] + s[0] / 2) / s[0] * RTM_W  # frame → model-input pixels
        iy = (fy - c[1] + s[1] / 2) / s[1] * RTM_H
        bx, by = int(round(ix * 2)), int(round(iy * 2))
        vx, vy = (scores or {}).get(k, (0.95, 0.95))
        sx[0, k, bx] = vx
        sy[0, k, by] = vy
        sx[0, k, bx + 3] = vx * 0.5  # a weaker secondary peak must not win the argmax
        expected[k] = (bx / 2 / RTM_W * s[0] + c[0] - s[0] / 2, by / 2 / RTM_H * s[1] + c[1] - s[1] / 2)
    return sx, sy, expected


LEG = {11: (560.0, 300.0), 13: (560.0, 420.0), 15: (560.0, 540.0)}  # left hip, knee, ankle: straight leg
BOX = (500.0, 150.0, 640.0, 560.0)


def _rtm_model(tmp_path: Path, scores: dict[int, tuple[float, float]] | None = None):
    sx, sy, expected = _simcc(LEG, BOX, scores)
    path = tmp_path / "rtmpose-m.onnx"
    _constant_model(path, [1, 3, RTM_H, RTM_W], {"simcc_x": sx, "simcc_y": sy}, in_name="input")
    return path, expected


def test_simcc_decode_argmax_split_ratio_and_min_score():
    from edge_ai.pose.rtmpose import decode_simcc

    sx = np.zeros((2, 384), dtype=np.float32)
    sy = np.zeros((2, 512), dtype=np.float32)
    sx[0, 101], sy[0, 300] = 0.9, 0.6
    sx[1, 7], sy[1, 8] = 0.2, 0.8
    locs, scores = decode_simcc(sx, sy, 2.0)
    assert locs.tolist() == [[50.5, 150.0], [3.5, 4.0]]
    assert scores.tolist() == pytest.approx([0.6, 0.2])


def test_crop_warp_matrix_inverts_the_keypoint_mapping():
    from edge_ai.pose.rtmpose import box_to_center_scale, crop_warp_matrix

    c, s = box_to_center_scale(BOX, (RTM_W, RTM_H))
    ec, es = _rtm_center_scale(BOX)
    assert np.allclose(c, ec) and np.allclose(s, es)
    assert s[0] / s[1] == pytest.approx(RTM_W / RTM_H)
    m = crop_warp_matrix(c, s, (RTM_W, RTM_H))
    for fx, fy in [(560.0, 420.0), (BOX[0], BOX[1]), (BOX[2], BOX[3])]:
        ix, iy = m @ np.array([fx, fy, 1.0])
        back = np.array([ix, iy]) / np.array([RTM_W, RTM_H]) * s + c - s / 2
        assert np.allclose(back, [fx, fy], atol=1e-3)
    # the box centre lands on the crop centre; the padded box fits inside the crop
    assert np.allclose(m @ np.array([c[0], c[1], 1.0]), [RTM_W / 2, RTM_H / 2])


def test_rtmpose_maps_keypoints_back_to_frame_and_angles(tmp_path: Path):
    from edge_ai.pose.angles import elbow_angle_mean, knee_angle_mean
    from edge_ai.pose.rtmpose import RtmPoseOnnx

    path, expected = _rtm_model(tmp_path, scores={13: (0.9, 0.7)})
    est = RtmPoseOnnx(str(path), providers=["CPUExecutionProvider"])
    assert est.input_size == (RTM_W, RTM_H)
    poses = est.estimate(_frame(), [BOX, (-500.0, -500.0, -100.0, -100.0)])
    assert poses[1] is None  # box outside the frame
    pose = poses[0]
    assert pose is not None and len(pose.keypoints) == 17
    for k, (ex, ey) in expected.items():
        kp = pose.keypoints[k]
        assert kp.x == pytest.approx(ex, abs=1e-3) and kp.y == pytest.approx(ey, abs=1e-3)
        tx, ty = LEG[k]
        assert abs(kp.x - tx) <= 1.1 and abs(kp.y - ty) <= 1.1  # SimCC quantisation: 0.5 input px
    knee = pose.kp("left_knee")
    assert knee.score == pytest.approx(0.7, abs=1e-6)  # min of the two axis maxima
    assert pose.kp("right_knee").score == 0.0  # empty heads → not visible
    angle, conf = knee_angle_mean(pose)
    assert angle == pytest.approx(180.0, abs=0.5) and conf == pytest.approx(0.7, abs=1e-6)
    elbow, _ = elbow_angle_mean(pose)
    assert math.isnan(elbow)  # arms not visible → NaN, never a guess


def test_rtmpose_same_heads_follow_each_box(tmp_path: Path):
    """The same model output maps relative to each crop: a shifted box shifts the keypoints."""
    from edge_ai.pose.rtmpose import RtmPoseOnnx

    path, expected = _rtm_model(tmp_path)
    est = RtmPoseOnnx(str(path), providers=["CPUExecutionProvider"])
    shifted = (BOX[0] + 100, BOX[1] + 50, BOX[2] + 100, BOX[3] + 50)
    p0, p1 = est.estimate(_frame(), [BOX, shifted])
    assert p0 is not None and p1 is not None
    k0, k1 = p0.kp("left_knee"), p1.kp("left_knee")
    assert k1.x - k0.x == pytest.approx(100.0, abs=1e-3) and k1.y - k0.y == pytest.approx(50.0, abs=1e-3)


def test_rtmpose_preprocess_rgb_normalised_affine_crop(tmp_path: Path):
    from edge_ai.pose.rtmpose import RTMPOSE_MEAN, RTMPOSE_STD, RtmPoseOnnx

    path, _ = _rtm_model(tmp_path)
    est = RtmPoseOnnx(str(path), providers=["CPUExecutionProvider"])
    frame = _frame(bgr=(10, 20, 30))
    prep = est.preprocess(frame, (0.0, 0.0, 100.0, 300.0))  # padded crop sticks out of the top-left corner
    assert prep is not None
    blob, center, scale = prep
    assert blob.shape == (1, 3, RTM_H, RTM_W) and blob.dtype == np.float32
    rgb = np.array([30.0, 20.0, 10.0])
    inside = (rgb - RTMPOSE_MEAN) / RTMPOSE_STD
    outside = (0.0 - RTMPOSE_MEAN) / RTMPOSE_STD  # warpAffine border value 0
    assert np.allclose(blob[0, :, RTM_H // 2, RTM_W // 2], inside, atol=1e-4)
    assert np.allclose(blob[0, :, 0, 0], outside, atol=1e-4)
    assert center.tolist() == [50.0, 150.0]


def test_pose_factory_selects_adapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from edge_ai.pose.factory import create_pose_estimator
    from edge_ai.pose.onnx_pose import YoloPoseOnnx
    from edge_ai.pose.rtmpose import RtmPoseOnnx

    rtm, _ = _rtm_model(tmp_path)
    yolo = tmp_path / "yolo-pose.onnx"
    _constant_model(yolo, [1, 3, 640, 640], {"output0": np.zeros((1, 56, 20), dtype=np.float32)})
    monkeypatch.delenv("EDGE_POSE_KIND", raising=False)
    assert isinstance(create_pose_estimator(str(rtm), providers=["CPUExecutionProvider"]), RtmPoseOnnx)
    assert isinstance(create_pose_estimator(str(yolo), providers=["CPUExecutionProvider"]), YoloPoseOnnx)
    assert isinstance(create_pose_estimator(str(rtm), kind="rtmpose", providers=["CPUExecutionProvider"]), RtmPoseOnnx)
    monkeypatch.setenv("EDGE_POSE_KIND", "yolo-pose")
    assert isinstance(create_pose_estimator(str(yolo), providers=["CPUExecutionProvider"]), YoloPoseOnnx)
    with pytest.raises(ValueError):
        create_pose_estimator(str(rtm), kind="openpose", providers=["CPUExecutionProvider"])


def _run_settings(tmp_path: Path, **over: Any):  # noqa: ANN202
    from uuid import UUID

    from edge_ai.config import Settings

    kwargs: dict[str, Any] = {"_env_file": None, **over}
    return Settings(  # type: ignore[call-arg]
        device_id=UUID("d0000000-0000-4000-8000-000000000001"),
        branch_id=UUID("b0000000-0000-4000-8000-000000000001"),
        device_secret="dev-only-device-secret-change-me",
        ingest_url="https://ingest.test/functions/v1/ingest-events",
        data_dir=tmp_path,
        **kwargs,
    )


def test_run_wiring_uses_the_model_factories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`edge-ai run` picks RTMPose for a SimCC model and the configured YOLOX layout."""
    from edge_ai.__main__ import _build_models
    from edge_ai.pose.onnx_pose import YoloPoseOnnx
    from edge_ai.pose.rtmpose import RtmPoseOnnx

    monkeypatch.delenv("EDGE_DETECTOR_LAYOUT", raising=False)
    monkeypatch.delenv("EDGE_POSE_KIND", raising=False)
    det_path, _ = _yolox_model(tmp_path)
    rtm, _ = _rtm_model(tmp_path)
    yolo = tmp_path / "yolo-pose.onnx"
    _constant_model(yolo, [1, 3, 640, 640], {"output0": np.zeros((1, 56, 20), dtype=np.float32)})

    s = _run_settings(tmp_path, detector_model_path=det_path, pose_model_path=rtm, detector_layout="YOLOX")
    detector, pose, reid = _build_models(s)
    assert detector is not None and detector.layout == "yolox" and len(detector.detect(_frame())) == 2
    assert isinstance(pose, RtmPoseOnnx) and reid is None  # auto → SimCC heads → RTMPose

    detector, pose, _ = _build_models(_run_settings(tmp_path, pose_model_path=yolo), str(det_path))
    assert detector is not None and detector.layout == "auto"  # --detector override, default layout
    assert isinstance(pose, YoloPoseOnnx)
    _, pose, _ = _build_models(_run_settings(tmp_path, pose_model_path=rtm, pose_kind="rtmpose"))
    assert isinstance(pose, RtmPoseOnnx)
    assert _build_models(_run_settings(tmp_path)) == (None, None, None)


def test_missing_detector_file_falls_back_to_camera_health_only(tmp_path: Path):
    """A configured but absent model (fresh host, failed copy) must not crash-loop `edge-ai run`."""
    from edge_ai.__main__ import _build_models

    missing = tmp_path / "models" / "detector.onnx"
    assert _build_models(_run_settings(tmp_path, detector_model_path=missing)) == (None, None, None)
    assert _build_models(_run_settings(tmp_path), str(missing)) == (None, None, None)


def test_unreadable_camera_credentials_name_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A credentials file the service user cannot read fails with an actionable message, not a bare errno."""
    from edge_ai.config import load_camera_credentials

    creds = tmp_path / "camera-credentials.yaml"
    creds.write_text("credentials: {}\n", encoding="utf-8")

    def _denied(*_a: Any, **_k: Any):  # noqa: ANN202
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "open", _denied)
    with pytest.raises(PermissionError, match="chown it to the service user"):
        load_camera_credentials(creds)


def test_model_choices_come_from_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """EDGE_DETECTOR_LAYOUT / EDGE_POSE_KIND in .env apply although they are not in os.environ."""
    monkeypatch.delenv("EDGE_DETECTOR_LAYOUT", raising=False)
    monkeypatch.delenv("EDGE_POSE_KIND", raising=False)
    env = tmp_path / ".env"
    env.write_text("EDGE_DETECTOR_LAYOUT=yolox\nEDGE_POSE_KIND=yolo-pose\n", encoding="utf-8")
    s = _run_settings(tmp_path, _env_file=str(env))
    assert s.detector_layout == "yolox" and s.pose_kind == "yolo_pose"
    with pytest.raises(ValueError):
        _run_settings(tmp_path, pose_kind="openpose")


# ----------------------------------------------------------------------------------------------
# detect_image tool
# ----------------------------------------------------------------------------------------------


def test_detect_image_tool_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    from edge_ai.tools.detect_image import main

    det_path, (person_a, _) = _yolox_model(tmp_path)
    # Pose heads built for person A's detected box so the leg lands on the expected pixels.
    sx, sy, _ = _simcc({11: (360.0, 200.0), 13: (360.0, 300.0), 15: (360.0, 400.0)}, person_a)
    pose_path = tmp_path / "rtmpose-m.onnx"
    _constant_model(pose_path, [1, 3, RTM_H, RTM_W], {"simcc_x": sx, "simcc_y": sy}, in_name="input")
    image = tmp_path / "frame.png"
    cv2.imwrite(str(image), _frame())
    out_png = tmp_path / "annotated.png"
    rc = main([
        "--detector", str(det_path), "--layout", "yolox",
        "--pose", str(pose_path), "--pose-kind", "rtmpose",
        "--image", str(image), "--out", str(out_png), "--repeat", "3",
        "--providers", "CPUExecutionProvider",
    ])  # fmt: skip
    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["image"]["width"] == 1280 and result["image"]["height"] == 720
    assert result["providers"]["detector"] == ["CPUExecutionProvider"]
    assert result["providers"]["pose"] == ["CPUExecutionProvider"]
    for stage in ("detect", "pose", "total"):
        lat = result["latency_ms"][stage]
        assert lat["runs"] == 3 and lat["p50"] <= lat["p95"] and lat["mean"] >= 0
    assert len(result["detections"]) == 2
    assert result["detections"][0]["box"] == pytest.approx(list(person_a), abs=0.1)
    persons = result["persons"]
    assert len(persons) == 2 and persons[0]["detection"] == 0
    assert persons[0]["knee_angle_deg"] == pytest.approx(180.0, abs=0.5)
    assert persons[0]["elbow_angle_deg"] is None  # NaN → null, the output stays valid JSON
    assert out_png.exists() and cv2.imread(str(out_png)).shape == (720, 1280, 3)
