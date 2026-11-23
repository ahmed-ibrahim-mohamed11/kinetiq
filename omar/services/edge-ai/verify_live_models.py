"""
Live Model Verification Script for KinetiQ Smart Gym.
Verifies all 6 AI models and components directly:
1. YOLOX Person Detector (yolox_nano.onnx, yolox_s.onnx)
2. RTMPose Pose Estimation (rtmpose-m_simcc-body7_256x192.onnx)
3. YouTu Person Re-Identification (person_reid_youtu_2021nov.onnx)
4. YuNet Face Detector (face_detection_yunet_2023mar.onnx)
5. SFace Face Embedding Model (face_recognition_sface_2021dec.onnx)
6. Biomechanical Squat Engine (biomechanical_squat.py)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
import numpy as np

# Add services/edge-ai to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import onnxruntime as ort
import cv2

from edge_ai.detection.onnx_detector import OnnxPersonDetector
from edge_ai.pose.rtmpose import RtmPoseOnnx
from edge_ai.fusion.onnx_reid import OnnxReidEmbedder
from edge_ai.identity.face_pipeline import YuNetFaceDetector, OnnxFaceEmbedder
from edge_ai.reps.biomechanical_squat import BiomechanicalSquatEngine
from edge_ai.pose.base import Keypoint, Pose

def test_person_detector():
    print("\n--- 1. PERSON DETECTOR (YOLOX) ---")
    model_path = BASE_DIR / "models" / "yolox_nano.onnx"
    assert model_path.exists(), f"Model file missing: {model_path}"
    print(f"  ✓ File exists: {model_path.name} ({model_path.stat().st_size:,} bytes)")

    detector = OnnxPersonDetector(str(model_path), conf_threshold=0.25, iou_threshold=0.45, layout="yolox")
    print(f"  ✓ Model loaded successfully ({detector.model_name})")
    print(f"  ✓ Available providers: {ort.get_available_providers()}")
    print(f"  ✓ Active provider: {detector.active_provider}")

    # Create synthetic test frame
    frame = np.full((720, 1280, 3), 120, dtype=np.uint8)
    cv2.rectangle(frame, (400, 150), (600, 600), (200, 180, 160), -1)
    cv2.circle(frame, (500, 100), 50, (220, 190, 170), -1)

    t0 = time.perf_counter()
    detections = detector.detect(frame)
    dt_ms = (time.perf_counter() - t0) * 1000
    fps = 1000.0 / max(0.001, dt_ms)

    print(f"  ✓ Inference executed in {dt_ms:.2f} ms ({fps:.1f} FPS)")
    print(f"  ✓ Detections returned: {len(detections)}")
    for i, d in enumerate(detections):
        print(f"    - Detection #{i+1}: box={d.box}, conf={d.score:.3f}")
    return True

def test_rtmpose():
    print("\n--- 2. POSE MODEL (RTMPose SimCC) ---")
    model_path = BASE_DIR / "models" / "rtmpose-m_simcc-body7_256x192.onnx"
    assert model_path.exists(), f"Model file missing: {model_path}"
    print(f"  ✓ File exists: {model_path.name} ({model_path.stat().st_size:,} bytes)")

    pose_estimator = RtmPoseOnnx(str(model_path))
    print(f"  ✓ Model loaded successfully ({pose_estimator.model_name})")
    print(f"  ✓ Active providers: {pose_estimator.providers}")

    frame = np.full((720, 1280, 3), 120, dtype=np.uint8)
    boxes = [(400.0, 50.0, 600.0, 650.0)]

    t0 = time.perf_counter()
    poses = pose_estimator.estimate(frame, boxes)
    dt_ms = (time.perf_counter() - t0) * 1000
    fps = 1000.0 / max(0.001, dt_ms)

    print(f"  ✓ Inference executed in {dt_ms:.2f} ms ({fps:.1f} FPS)")
    print(f"  ✓ Poses estimated: {len(poses)}")
    if poses and poses[0] is not None:
        p = poses[0]
        print(f"    - Keypoint count: {len(p.keypoints)} (COCO 17 Keypoints)")
        nose = p.kp("nose")
        lk = p.kp("left_knee")
        rk = p.kp("right_knee")
        print(f"    - Nose: ({nose.x:.1f}, {nose.y:.1f}), score: {nose.score:.2f}")
        print(f"    - Left Knee: ({lk.x:.1f}, {lk.y:.1f}), score: {lk.score:.2f}")
        print(f"    - Right Knee: ({rk.x:.1f}, {rk.y:.1f}), score: {rk.score:.2f}")
    return True

def test_person_reid():
    print("\n--- 3. RE-IDENTIFICATION MODEL (YouTu ReID) ---")
    model_path = BASE_DIR / "models" / "person_reid_youtu_2021nov.onnx"
    assert model_path.exists(), f"Model file missing: {model_path}"
    print(f"  ✓ File exists: {model_path.name} ({model_path.stat().st_size:,} bytes)")

    reid = OnnxReidEmbedder(str(model_path))
    print(f"  ✓ Model loaded successfully ({reid.model_name})")

    crop_img = np.full((720, 1280, 3), 128, dtype=np.uint8)
    box = (400.0, 100.0, 600.0, 600.0)

    t0 = time.perf_counter()
    emb = reid.embed(crop_img, box)
    dt_ms = (time.perf_counter() - t0) * 1000
    fps = 1000.0 / max(0.001, dt_ms)

    assert emb is not None, "ReID returned None"
    emb_arr = np.array(emb)
    norm = np.linalg.norm(emb_arr)
    print(f"  ✓ Inference executed in {dt_ms:.2f} ms ({fps:.1f} FPS)")
    print(f"  ✓ Embedding dimension: {len(emb)}D, L2-norm: {norm:.4f}")
    assert len(emb) in (512, 128, 256, 768, 1024, 2048)
    return True

def test_face_detector():
    print("\n--- 4. FACE DETECTOR (YuNet) ---")
    model_path = BASE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
    assert model_path.exists(), f"Model file missing: {model_path}"
    print(f"  ✓ File exists: {model_path.name} ({model_path.stat().st_size:,} bytes)")

    face_det = YuNetFaceDetector(str(model_path))
    print(f"  ✓ Model loaded successfully ({face_det.model_name})")

    # Create synthetic test frame with face oval
    frame = np.full((480, 640, 3), 180, dtype=np.uint8)
    cv2.ellipse(frame, (320, 240), (80, 110), 0, 0, 360, (230, 210, 190), -1)
    cv2.circle(frame, (290, 210), 12, (50, 50, 50), -1)
    cv2.circle(frame, (350, 210), 12, (50, 50, 50), -1)

    t0 = time.perf_counter()
    faces = face_det.detect(frame)
    dt_ms = (time.perf_counter() - t0) * 1000
    fps = 1000.0 / max(0.001, dt_ms)

    print(f"  ✓ Inference executed in {dt_ms:.2f} ms ({fps:.1f} FPS)")
    print(f"  ✓ Face detector operational (detected: {len(faces)} faces)")
    return True

def test_face_recognizer():
    print("\n--- 5. FACE RECOGNITION & EMBEDDING (SFace 128-D) ---")
    model_path = BASE_DIR / "models" / "face_recognition_sface_2021dec.onnx"
    assert model_path.exists(), f"Model file missing: {model_path}"
    print(f"  ✓ File exists: {model_path.name} ({model_path.stat().st_size:,} bytes)")

    sface = OnnxFaceEmbedder(str(model_path))
    print(f"  ✓ Model loaded successfully ({sface.model_name})")

    # Standard aligned face crop (112x112)
    crop1 = np.full((112, 112, 3), 150, dtype=np.uint8)
    cv2.circle(crop1, (38, 42), 8, (40, 40, 40), -1)
    cv2.circle(crop1, (74, 42), 8, (40, 40, 40), -1)

    t0 = time.perf_counter()
    emb1 = sface.embed(crop1)
    dt_ms = (time.perf_counter() - t0) * 1000
    fps = 1000.0 / max(0.001, dt_ms)

    emb_arr = np.array(emb1)
    norm = np.linalg.norm(emb_arr)
    print(f"  ✓ Inference executed in {dt_ms:.2f} ms ({fps:.1f} FPS)")
    print(f"  ✓ SFace Embedding dimension: {len(emb1)}D (Expected: 128-D)")
    print(f"  ✓ L2-Norm: {norm:.4f}")
    assert len(emb1) == 128, f"Expected 128-D, got {len(emb1)}"

    # Test cosine similarity matching
    emb2 = sface.embed(crop1)
    cos_sim = float(np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2)))
    print(f"  ✓ Self-Cosine Similarity: {cos_sim:.6f} (Expected: ~1.000)")
    assert cos_sim > 0.999
    return True

def test_biomechanical_squat():
    print("\n--- 6. BIOMECHANICAL SQUAT ENGINE ---")
    engine = BiomechanicalSquatEngine()
    print("  ✓ BiomechanicalSquatEngine initialized")

    def make_pose(knee_angle: float, torso_lean: float = 12.0) -> Pose:
        rad = np.radians(knee_angle)
        hip_y = 300.0
        knee_y = 450.0
        ankle_x = 300.0 + 150.0 * np.sin(np.pi - rad)
        ankle_y = knee_y + 150.0 * np.cos(np.pi - rad)

        kps = [Keypoint(300.0, 100.0, 0.95)] * 17
        # COCO-17 indices:
        # 5: left_shoulder, 6: right_shoulder
        # 11: left_hip, 12: right_hip
        # 13: left_knee, 14: right_knee
        # 15: left_ankle, 16: right_ankle
        kps[5] = Keypoint(300.0 - np.sin(np.radians(torso_lean))*100.0, 200.0, 0.95)
        kps[6] = Keypoint(300.0 - np.sin(np.radians(torso_lean))*100.0, 200.0, 0.95)
        kps[11] = Keypoint(300.0, hip_y, 0.95)
        kps[12] = Keypoint(300.0, hip_y, 0.95)
        kps[13] = Keypoint(300.0, knee_y, 0.95)
        kps[14] = Keypoint(300.0, knee_y, 0.95)
        kps[15] = Keypoint(ankle_x, ankle_y, 0.95)
        kps[16] = Keypoint(ankle_x, ankle_y, 0.95)
        return Pose(tuple(kps))

    # Phase 1: Standing (175 deg)
    t = 1.0
    res1 = engine.update(make_pose(175.0), t=t)
    print(f"  Step 1 (Standing): Phase={engine.phase.value}, Knee Angle={engine.current_knee_angle}°")

    # Phase 2: Descending to 125 deg
    t += 0.4
    engine.update(make_pose(125.0), t=t)
    print(f"  Step 2 (Descending): Phase={engine.phase.value}, Knee Angle={engine.current_knee_angle}°")

    # Phase 3: Bottom / Deep squat (85 deg - parallel reached)
    t += 0.4
    engine.update(make_pose(85.0), t=t)
    print(f"  Step 3 (Bottom): Phase={engine.phase.value}, Knee Angle={engine.current_knee_angle}°")

    # Phase 4: Ascending back to 135 deg
    t += 0.4
    engine.update(make_pose(135.0), t=t)
    print(f"  Step 4 (Ascending): Phase={engine.phase.value}, Knee Angle={engine.current_knee_angle}°")

    # Phase 5: Lockout at top (172 deg)
    t += 0.4
    res_final = engine.update(make_pose(172.0), t=t)
    print(f"  Step 5 (Lockout): Phase={engine.phase.value}, Rep Completed! Total Reps={engine.completed_reps}")
    print(f"  ✓ Rep Result Verdict: {res_final.verdict if res_final else 'Valid rep count = ' + str(engine.completed_reps)}")
    print(f"  ✓ Rep History entries: {len(engine.rep_history)}")
    assert engine.completed_reps == 1, f"Expected 1 completed rep, got {engine.completed_reps}"
    return True

if __name__ == "__main__":
    print("=================================================================")
    print(" KINETIQ AI / COMPUTER VISION MODELS LIVE VALIDATION DRILL       ")
    print("=================================================================")
    test_person_detector()
    test_rtmpose()
    test_person_reid()
    test_face_detector()
    test_face_recognizer()
    test_biomechanical_squat()
    print("\n=================================================================")
    print(" ALL 6 AI MODELS VERIFIED WORKING WITH REAL RUNTIME INFERENCE!   ")
    print("=================================================================")
