"""KinetiQ Squat Assessment Demo — Defense-ready GUI.

Matches the Squat Assessment System Infographic exactly:
  Input → YOLO11n-pose → Tracker → MotionBERT-Lite → HSMM+Viterbi → Alarm → 5 Judges → Output

Two modes:
  1. Video Analysis: Load a squat video → full pipeline → rendered playback with HUD
  2. Live Webcam: Real-time 2D pose + skeleton overlay (lightweight, no crash)

Launch:  python squat_demo_gui.py
"""
from __future__ import annotations

import gc
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Optional

import cv2
import numpy as np

REPO_ROOT = os.path.abspath(os.path.dirname(__file__))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

MENDELEY_ROOT = os.path.join(REPO_ROOT, "ai", "data", "mendeley", "Dataset Subject-wise")
OUTPUT_DIR = os.path.join(REPO_ROOT, "demo_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

COCO_SKELETON = [
    (5, 6), (5, 7), (7, 9), (6, 8), (8, 10),
    (5, 11), (6, 12), (11, 12),
    (11, 13), (13, 15), (12, 14), (14, 16),
]
COCO_LOWER = {11, 12, 13, 14, 15, 16}


class SquatDemoApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("KinetiQ — Squat Assessment System Demo")
        self.root.geometry("520x720")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1a2e")

        self.detector = None
        self.components = None
        self.analysis_result = None
        self.rendered_video_path: Optional[str] = None
        self.current_video_path: Optional[str] = None
        self.webcam_running = False
        self.playback_running = False
        self._stop_flag = False

        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Title.TLabel", background="#1a1a2e", foreground="#e0e0ff",
                        font=("Segoe UI", 14, "bold"))
        style.configure("Sub.TLabel", background="#1a1a2e", foreground="#b0b0d0",
                        font=("Segoe UI", 9))
        style.configure("Status.TLabel", background="#1a1a2e", foreground="#80ffa0",
                        font=("Consolas", 9))
        style.configure("Result.TLabel", background="#16213e", foreground="#ffffff",
                        font=("Consolas", 9))
        style.configure("Demo.TButton", font=("Segoe UI", 10, "bold"), padding=6)
        style.configure("TFrame", background="#1a1a2e")
        style.configure("Card.TFrame", background="#16213e")

        pad = dict(padx=10, pady=4)

        ttk.Label(self.root, text="Squat Assessment System", style="Title.TLabel").pack(**pad)
        ttk.Label(self.root, text="AI Powered Movement Analysis — Infographic Pipeline Demo",
                  style="Sub.TLabel").pack(padx=10, pady=(0, 8))

        # Buttons
        bf = ttk.Frame(self.root)
        bf.pack(fill="x", **pad)
        self.btn_load = ttk.Button(bf, text="Load Squat Video", style="Demo.TButton",
                                   command=self._on_load_video)
        self.btn_load.pack(side="left", padx=4)
        self.btn_quick = ttk.Button(bf, text="Quick Demo (Good)", style="Demo.TButton",
                                    command=lambda: self._quick_demo("good"))
        self.btn_quick.pack(side="left", padx=4)
        self.btn_quick_bad = ttk.Button(bf, text="Quick Demo (Bad)", style="Demo.TButton",
                                        command=lambda: self._quick_demo("bad"))
        self.btn_quick_bad.pack(side="left", padx=4)

        bf2 = ttk.Frame(self.root)
        bf2.pack(fill="x", **pad)
        self.btn_webcam = ttk.Button(bf2, text="Live Webcam (2D)", style="Demo.TButton",
                                     command=self._on_webcam)
        self.btn_webcam.pack(side="left", padx=4)
        self.btn_play = ttk.Button(bf2, text="Replay Result", style="Demo.TButton",
                                   command=self._on_replay, state="disabled")
        self.btn_play.pack(side="left", padx=4)
        self.btn_stop = ttk.Button(bf2, text="Stop", style="Demo.TButton",
                                   command=self._on_stop)
        self.btn_stop.pack(side="left", padx=4)

        # Progress
        self.progress_var = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(self.root, variable=self.progress_var, maximum=100)
        self.progress.pack(fill="x", padx=14, pady=4)

        # Status
        self.status_var = tk.StringVar(value="Ready — load a squat video or press Quick Demo")
        ttk.Label(self.root, textvariable=self.status_var, style="Status.TLabel",
                  wraplength=480).pack(fill="x", padx=14, pady=2)

        # Pipeline info
        sep = ttk.Separator(self.root, orient="horizontal")
        sep.pack(fill="x", padx=10, pady=6)
        ttk.Label(self.root, text="Pipeline (matching infographic):", style="Sub.TLabel").pack(
            anchor="w", padx=14)
        pipeline_text = (
            "1. YOLO11n-pose  →  2D Pose (17 keypoints)\n"
            "2. Tracker (IoU/ByteTrack)  →  Consistent person ID\n"
            "3. MotionBERT-Lite  →  2D to 3D (243-frame window)\n"
            "4. HSMM + Viterbi  →  Rep & phase segmentation\n"
            "5. Alarm Engine  →  Phase-aware instant feedback (<200ms)\n"
            "6. 5 Judging Models  →  Rules / RF / CNN / GNN / ST-GCN\n"
            "7. Temperature Calibration  →  Honest confidence + abstain"
        )
        self.pipeline_label = ttk.Label(self.root, text=pipeline_text, style="Sub.TLabel",
                                        justify="left")
        self.pipeline_label.pack(anchor="w", padx=20, pady=2)

        # Results card
        sep2 = ttk.Separator(self.root, orient="horizontal")
        sep2.pack(fill="x", padx=10, pady=6)
        ttk.Label(self.root, text="Analysis Results:", style="Sub.TLabel").pack(anchor="w", padx=14)

        card = ttk.Frame(self.root, style="Card.TFrame", padding=10)
        card.pack(fill="both", expand=True, padx=14, pady=(4, 10))

        self.result_var = tk.StringVar(value="No analysis yet — run a demo to see results here.")
        self.result_label = ttk.Label(card, textvariable=self.result_var, style="Result.TLabel",
                                      justify="left", wraplength=460)
        self.result_label.pack(fill="both", expand=True)

    # ------------------------------------------------------------------ actions
    def _set_status(self, text: str):
        self.status_var.set(text)
        self.root.update_idletasks()

    def _set_progress(self, pct: float):
        self.progress_var.set(pct)
        self.root.update_idletasks()

    def _disable_buttons(self):
        for b in (self.btn_load, self.btn_quick, self.btn_quick_bad, self.btn_webcam):
            b.configure(state="disabled")

    def _enable_buttons(self):
        for b in (self.btn_load, self.btn_quick, self.btn_quick_bad, self.btn_webcam):
            b.configure(state="normal")

    def _on_stop(self):
        self._stop_flag = True
        self.webcam_running = False
        self.playback_running = False

    def _on_load_video(self):
        init_dir = MENDELEY_ROOT if os.path.isdir(MENDELEY_ROOT) else REPO_ROOT
        path = filedialog.askopenfilename(
            title="Select a squat video",
            initialdir=init_dir,
            filetypes=[("Video", "*.mp4 *.avi *.mov *.mkv"), ("All", "*.*")],
        )
        if path:
            self._run_analysis(path)

    def _quick_demo(self, quality: str):
        pattern = os.path.join(MENDELEY_ROOT, "subject_002", "squat", quality, "front",
                               f"subject_002_squat_{quality}_front.mp4")
        if not os.path.exists(pattern):
            for subj in ("subject_001", "subject_003"):
                alt = os.path.join(MENDELEY_ROOT, subj, "squat", quality, "front",
                                   f"{subj}_squat_{quality}_front.mp4")
                if os.path.exists(alt):
                    pattern = alt
                    break
        if not os.path.exists(pattern):
            self._set_status(f"ERROR: No Mendeley squat video found at {pattern}")
            return
        self._run_analysis(pattern)

    def _run_analysis(self, video_path: str):
        self.current_video_path = video_path
        self._disable_buttons()
        self._stop_flag = False
        self.btn_play.configure(state="disabled")
        threading.Thread(target=self._analysis_worker, args=(video_path,), daemon=True).start()

    def _analysis_worker(self, video_path: str):
        try:
            self._set_status("Step 1/5: Loading YOLO11n-pose + MotionBERT-Lite...")
            self._set_progress(5)

            from ai.core.squat_pipeline import SquatAssessmentComponents, analyze_squat_video

            if self.components is None:
                self._set_status("Loading YOLO11n-pose detector (first time takes ~10s)...")
                self._set_progress(10)
                self.components = SquatAssessmentComponents(device="cpu", imgsz=640)
                self._set_progress(25)
                self._set_status("Models loaded: YOLO11n-pose + MotionBERT-Lite + 5 Judges")
            else:
                self._set_progress(25)
                self._set_status("Models already cached — skipping reload")

            if self._stop_flag:
                self._finish_analysis(cancelled=True)
                return

            name = os.path.splitext(os.path.basename(video_path))[0]
            render_path = os.path.join(OUTPUT_DIR, f"{name}_demo.mp4")

            self._set_status("Step 2/5: Running YOLO11n-pose + Tracker on every frame...")
            self._set_progress(35)

            self._set_status("Step 2-4: Full pipeline (detect → track → 3D lift → segment → judge)...")
            self._set_progress(40)

            result = analyze_squat_video(
                video_path=video_path,
                comp=self.components,
                render_output_path=render_path,
                model_choice="ensemble",
            )

            self._set_progress(90)
            self._set_status("Step 5/5: Rendering complete — preparing playback...")

            self.analysis_result = result
            self.rendered_video_path = render_path

            # Build results text
            t = result.timings_ms
            errors_str = ", ".join(f"{k}: {v}" for k, v in result.error_counts.items() if v > 0)
            if not errors_str:
                errors_str = "none detected"

            rep_details = ""
            for r in result.reps:
                mv = ""
                if r.model_verdicts:
                    mv = " | " + ", ".join(f"{k.split('_')[1]}={v}" for k, v in r.model_verdicts.items())
                rep_details += (
                    f"  Rep {r.index}: {r.verdict.upper()} (conf={r.confidence:.2f})"
                    f" knee={r.min_knee_angle_deg:.0f}° hip={r.min_hip_angle_deg:.0f}°"
                    f" trunk={r.max_trunk_lean_deg:.0f}°{mv}\n"
                )

            txt = (
                f"Video: {os.path.basename(video_path)}\n"
                f"Duration: {result.duration_s:.1f}s | FPS: {result.fps:.0f} | Frames: {result.n_frames}\n"
                f"Camera View: {result.camera_view}\n"
                f"────────────────────────────────\n"
                f"Total Reps: {result.total_reps}\n"
                f"  Correct: {result.correct_reps}  |  Incorrect: {result.incorrect_reps}"
                f"  |  Uncertain: {result.uncertain_reps}\n"
                f"Errors: {errors_str}\n"
                f"Alarm Events: {result.alarm_events_count}"
                f" (median latency: {result.alarm_latency_median_ms:.1f}ms)\n"
                f"────────────────────────────────\n"
                f"Pipeline Timing:\n"
                f"  YOLO 2D detect: {t.get('detect_2d_ms', 0):.0f}ms\n"
                f"  Tracker: {t.get('track_ms', 0):.0f}ms\n"
                f"  MotionBERT 3D: {t.get('lift_3d_ms', 0):.0f}ms\n"
                f"  Features+Alarm: {t.get('squat_features_ms', 0):.0f}ms + {t.get('instant_alarm_total_ms', 0):.0f}ms\n"
                f"  Rep Judging: {t.get('rep_judging_ms', 0):.0f}ms\n"
                f"────────────────────────────────\n"
                f"Per-Rep Details:\n{rep_details}"
            )
            if result.warnings:
                txt += "Warnings: " + "; ".join(result.warnings) + "\n"

            self.result_var.set(txt)
            self._set_progress(100)
            self._set_status("Analysis complete! Playing rendered video... (press Q to close video window)")
            self.btn_play.configure(state="normal")
            self._enable_buttons()

            self._play_rendered(render_path)

        except Exception as e:
            import traceback
            self._set_status(f"ERROR: {e}")
            self.result_var.set(f"Pipeline error:\n{traceback.format_exc()}")
            self._enable_buttons()
            self._set_progress(0)

    def _finish_analysis(self, cancelled=False):
        self._enable_buttons()
        self._set_progress(0)
        if cancelled:
            self._set_status("Cancelled.")

    def _play_rendered(self, video_path: str):
        if not os.path.exists(video_path):
            self._set_status(f"Rendered video not found: {video_path}")
            return
        self.playback_running = True
        self._stop_flag = False
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self._set_status("Cannot open rendered video for playback")
            return
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        delay = max(1, int(1000.0 / fps))

        cv2.namedWindow("KinetiQ Squat Demo", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("KinetiQ Squat Demo", 960, 720)

        while self.playback_running and not self._stop_flag:
            ok, frame = cap.read()
            if not ok:
                break
            cv2.imshow("KinetiQ Squat Demo", frame)
            key = cv2.waitKey(delay) & 0xFF
            if key == ord("q") or key == 27:
                break

        cap.release()
        cv2.destroyAllWindows()
        self.playback_running = False
        self._set_status("Playback finished. Load another video or Replay.")

    def _on_replay(self):
        if self.rendered_video_path and os.path.exists(self.rendered_video_path):
            threading.Thread(target=self._play_rendered, args=(self.rendered_video_path,),
                             daemon=True).start()

    # ------------------------------------------------------------------ webcam
    def _on_webcam(self):
        if self.webcam_running:
            self.webcam_running = False
            return
        self.webcam_running = True
        self._stop_flag = False
        self._disable_buttons()
        self.btn_webcam.configure(state="normal")
        threading.Thread(target=self._webcam_worker, daemon=True).start()

    def _webcam_worker(self):
        self._set_status("Opening webcam + loading YOLO11n-pose for live 2D...")
        try:
            from ai.core.pose import YoloTopK, YOLO_WEIGHTS

            if self.detector is None:
                self.detector = YoloTopK(weights=YOLO_WEIGHTS, device="cpu", imgsz=640, k=1)
            self._set_status("Webcam live — press Q in the video window to stop")

            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
            if not cap.isOpened():
                self._set_status("ERROR: Cannot open webcam")
                self._enable_buttons()
                return

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            cv2.namedWindow("KinetiQ Live 2D Pose", cv2.WINDOW_NORMAL)
            cv2.resizeWindow("KinetiQ Live 2D Pose", 800, 600)

            frame_count = 0
            fps_timer = time.time()
            measured_fps = 0.0

            while self.webcam_running and not self._stop_flag:
                ok, frame = cap.read()
                if not ok:
                    continue

                # Run YOLO on every other frame for speed
                if frame_count % 2 == 0:
                    kpts, boxes = self.detector(frame)
                    # Draw skeleton for the top detection
                    if np.isfinite(kpts[0, 0, 0]):
                        pts = kpts[0, :, :2].astype(int)
                        confs = kpts[0, :, 2]

                        # Draw bones
                        for i, j in COCO_SKELETON:
                            if confs[i] > 0.3 and confs[j] > 0.3:
                                color = (90, 220, 80) if (i in COCO_LOWER and j in COCO_LOWER) else (255, 180, 50)
                                cv2.line(frame, tuple(pts[i]), tuple(pts[j]), color, 3, cv2.LINE_AA)

                        # Draw joints
                        for j_idx in range(5, 17):
                            if confs[j_idx] > 0.3:
                                color = (90, 220, 80) if j_idx in COCO_LOWER else (255, 180, 50)
                                cv2.circle(frame, tuple(pts[j_idx]), 5, (255, 255, 255), -1, cv2.LINE_AA)
                                cv2.circle(frame, tuple(pts[j_idx]), 7, color, 2, cv2.LINE_AA)

                        # Compute and display 2D knee angle
                        def angle_2d(a, b, c):
                            ba = a - b
                            bc = c - b
                            cos = np.dot(ba, bc) / (np.linalg.norm(ba) * np.linalg.norm(bc) + 1e-9)
                            return float(np.degrees(np.arccos(np.clip(cos, -1, 1))))

                        if all(confs[j] > 0.3 for j in [11, 13, 15]):
                            l_knee_ang = angle_2d(pts[11].astype(float), pts[13].astype(float), pts[15].astype(float))
                        else:
                            l_knee_ang = None
                        if all(confs[j] > 0.3 for j in [12, 14, 16]):
                            r_knee_ang = angle_2d(pts[12].astype(float), pts[14].astype(float), pts[16].astype(float))
                        else:
                            r_knee_ang = None

                        # HUD
                        overlay = frame.copy()
                        cv2.rectangle(overlay, (0, 0), (frame.shape[1], 70), (22, 22, 28), -1)
                        cv2.addWeighted(overlay, 0.8, frame, 0.2, 0, frame)

                        cv2.putText(frame, "KinetiQ Live 2D Pose | YOLO11n-pose",
                                    (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 235), 2, cv2.LINE_AA)

                        angle_txt = "2D Angles -> "
                        if l_knee_ang is not None:
                            angle_txt += f"L.Knee: {l_knee_ang:.0f} deg  "
                        if r_knee_ang is not None:
                            angle_txt += f"R.Knee: {r_knee_ang:.0f} deg  "
                        angle_txt += f"| FPS: {measured_fps:.1f}"
                        cv2.putText(frame, angle_txt,
                                    (12, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 235, 255), 1, cv2.LINE_AA)

                frame_count += 1
                if frame_count % 10 == 0:
                    now = time.time()
                    measured_fps = 10.0 / max(now - fps_timer, 0.001)
                    fps_timer = now

                cv2.imshow("KinetiQ Live 2D Pose", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q") or key == 27:
                    break

            cap.release()
            cv2.destroyAllWindows()
        except Exception as e:
            self._set_status(f"Webcam error: {e}")
        finally:
            self.webcam_running = False
            self._enable_buttons()
            self._set_status("Webcam stopped.")

    # ------------------------------------------------------------------ run
    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = SquatDemoApp()
    app.run()
