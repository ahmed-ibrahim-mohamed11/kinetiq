"""
Pipeline orchestrator.

One CameraWorker per camera runs: frame → detector → exclusion-zone filter →
local tracker → (pose on tracks inside equipment zones) → fusion. Detections
whose foot point or most of whose box lies in an `exclusion` zone are dropped
before tracking, so they are never tracked, ReID-embedded or face-identified.
Workers hand their per-frame
observations to the shared Pipeline, which owns the global identity registry,
the zone associator, the rep deduplicator and the event emitter. A background
sync loop drains the persistent queue; a heartbeat loop reports health.

Inference frame budget: each worker samples the live stream at
`inference_fps` (default 10), which is enough for rep cadences up to ~2 Hz
(Nyquist with margin) while keeping GPU load predictable. Measured throughput
and latency are exported for the Phase 2 benchmark.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

import structlog

from edge_ai.camera.rtsp_reader import FrameSource, StreamStatus
from edge_ai.config import CameraConfig, Settings
from edge_ai.constants import TRACKING_VERIFIED_THRESHOLD
from edge_ai.contracts.events_v1 import (
    CameraStatusPayload,
    DeviceHeartbeatPayload,
    IdentityCheckInPayload,
    TrackEndedPayload,
    TrackingUncertainPayload,
    TrackStartedPayload,
)
from edge_ai.detection.base import Detection, Detector
from edge_ai.events.builder import EventBuilder
from edge_ai.events.queue import PersistentEventQueue
from edge_ai.events.sync import SyncClient
from edge_ai.fusion.equipment_association import ZoneAssociator, ZoneSpec
from edge_ai.fusion.global_identity import GlobalIdentityRegistry, LocalObservation
from edge_ai.fusion.rep_dedup import RepDeduplicator
from edge_ai.geometry import box_foot_point, box_in_exclusion
from edge_ai.pose.angles import extract_signal
from edge_ai.pose.base import PoseEstimator
from edge_ai.reps.profiles import RepProfile
from edge_ai.tracking.iou_tracker import IouTracker, TrackState

from .session_state import IdentityWorkoutState

log = structlog.get_logger(__name__)


@dataclass
class PipelineMetrics:
    frames_processed: int = 0
    detections: int = 0
    detections_excluded: int = 0
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=500))
    started_at: float = field(default_factory=time.time)

    def record(self, latency_ms: float, n_det: int) -> None:
        self.frames_processed += 1
        self.detections += n_det
        self.latencies_ms.append(latency_ms)

    def avg_latency_ms(self) -> float | None:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else None

    def p95_latency_ms(self) -> float | None:
        if not self.latencies_ms:
            return None
        s = sorted(self.latencies_ms)
        return s[min(len(s) - 1, int(0.95 * len(s)))]


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        cameras: list[CameraConfig],
        exercise_ids: dict[str, UUID],
        profiles: dict[str, RepProfile],
        detector: Detector | None,
        pose: PoseEstimator | None,
        queue: PersistentEventQueue | None = None,
        sync: SyncClient | None = None,
        reid: Any | None = None,
        entrance: Any | None = None,
        config_sync: Any | None = None,
        consent_gate: Any | None = None,
        template_store: Any | None = None,
        reid_every_n_frames: int = 10,
    ) -> None:
        self.settings = settings
        self.cameras = cameras
        self.exercise_ids = exercise_ids
        self.profiles = profiles
        self.detector = detector
        self.pose = pose
        self.reid = reid  # OnnxReidEmbedder | None
        self.entrance = entrance  # EntranceIdentifier | None
        self.config_sync = config_sync  # DeviceSyncClient | None
        self.consent_gate = consent_gate  # ConsentGate | None
        self.template_store = template_store  # EncryptedTemplateStore | None
        self.reid_every_n_frames = max(1, reid_every_n_frames)
        self.entrance_zones: dict[str, list[tuple[tuple[float, float], ...]]] = {
            str(c.id): [tuple(z.polygon) for z in c.zones if z.type == "entrance"] for c in cameras
        }
        # Exclusion zones from cameras.yaml; the cloud's zones (device-sync) are added on each config sync.
        self._local_exclusions: dict[str, list[tuple[tuple[float, float], ...]]] = {
            str(c.id): [tuple(z.polygon) for z in c.zones if z.type == "exclusion"] for c in cameras
        }
        self.exclusion_zones: dict[str, list[tuple[tuple[float, float], ...]]] = {
            k: list(v) for k, v in self._local_exclusions.items()
        }
        self.queue = queue or PersistentEventQueue(settings.data_dir / "events.sqlite")
        self.emitter = EventBuilder(self.queue, settings.device_id, settings.branch_id)
        self.sync = sync or SyncClient(
            self.queue, settings.ingest_url, str(settings.device_id), settings.device_secret, settings.sync_batch_size
        )
        if template_store is not None and hasattr(self.sync, "add_rejection_listener"):
            self.sync.add_rejection_listener(self._on_event_rejected)
        self.templates_reconciled = 0
        self.identities_unbound_no_consent = 0
        self.registry = GlobalIdentityRegistry()
        self.dedup = RepDeduplicator()
        self.zones = ZoneAssociator(
            [
                ZoneSpec(str(z.id), str(c.id), str(z.equipment_id), z.exercise_slug, tuple(z.polygon))
                for c in cameras
                for z in c.zones
                if z.type == "equipment" and z.equipment_id
            ]
        )
        self.states: dict[str, IdentityWorkoutState] = {}
        self.metrics = PipelineMetrics()
        self.workers: list[CameraWorker] = []
        self._last_uncertain_emitted: dict[str, float] = {}
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._latest_jpeg: bytes | None = None
        self._latest_frame: Any | None = None
        self._latest_frame_at: float = 0.0
        self._camera_paused: bool = False
        self._live_people: dict[str, dict[str, Any]] = {}
        self._member_names_path = settings.data_dir / "member_names.json"
        self._member_names: dict[str, str] = self._load_member_names()

    def _load_member_names(self) -> dict[str, str]:
        import json

        try:
            if self._member_names_path.is_file():
                data = json.loads(self._member_names_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
        except Exception:  # noqa: BLE001
            pass
        return {}

    def _save_member_name(self, member_id: str, member_name: str) -> None:
        import json

        if not member_id or not member_name:
            return
        self._member_names[member_id] = member_name
        try:
            from edge_ai.camera.manager import CentralCameraManager
            CentralCameraManager.get_instance()._member_names[member_id] = member_name
        except Exception:
            pass
        try:
            self._member_names_path.parent.mkdir(parents=True, exist_ok=True)
            self._member_names_path.write_text(json.dumps(self._member_names, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ── lifecycle ────────────────────────────────────────────────────────────
    def add_worker(self, cam: CameraConfig, source: FrameSource) -> CameraWorker:
        w = CameraWorker(self, cam, source)
        self.workers.append(w)
        return w

    def start(self) -> None:
        for w in self.workers:
            w.start()
        self._threads = [
            threading.Thread(target=self._sync_loop, name="sync", daemon=True),
            threading.Thread(target=self._heartbeat_loop, name="heartbeat", daemon=True),
            threading.Thread(target=self._tick_loop, name="tick", daemon=True),
        ]
        if self.config_sync is not None:
            self._threads.append(threading.Thread(target=self._config_sync_loop, name="config-sync", daemon=True))
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for w in self.workers:
            w.stop()
        for t in self._threads:
            t.join(timeout=5)
        self.sync.drain(max_batches=5)

    # ── background loops ─────────────────────────────────────────────────────
    def _sync_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.sync.drain()
            except Exception:  # noqa: BLE001
                log.exception("sync.loop_error")
            self._stop.wait(self.settings.sync_interval_seconds)

    def _config_sync_loop(self) -> None:
        """Pull consents / catalogue / revocations from the cloud; keep working from cache when offline."""
        while not self._stop.is_set():
            try:
                self.apply_config_sync()
            except Exception:  # noqa: BLE001
                log.exception("config_sync.error")
            self._stop.wait(self.settings.config_sync_interval_seconds)

    @property
    def device_retired(self) -> bool:
        return bool(getattr(self.sync, "retired", False) or getattr(self.config_sync, "retired", False))

    def _pending_enrollment_refs(self) -> set[str]:
        return {str(p.get("template_ref")) for p in self.queue.pending_payloads("biometric.enrolled")}

    def apply_config_sync(self) -> bool:
        if self.config_sync is None:
            return False
        # Cut-off first, pending refs second, request last: an enrolment whose event already left the
        # queue must be in the snapshot, and one enrolled meanwhile is newer than the cut-off
        # (enroll_member queues its event before it stores the template).
        requested_at = time.time()
        pending_refs = self._pending_enrollment_refs() if self.template_store is not None else set()
        snapshot = self.config_sync.fetch_or_cached()
        if self.device_retired:
            with self._lock:
                if self.consent_gate is not None:
                    self.consent_gate.clear()  # retired: deny all biometric processing and tracking binding
                self._enforce_tracking_consent(time.time())
            return False
        if snapshot is None:
            with self._lock:
                self._enforce_tracking_consent(time.time())  # the cache may have gone stale meanwhile
            return False
        with self._lock:
            if self.consent_gate is not None:
                self.config_sync.apply_consents(snapshot, self.consent_gate)
            self.exercise_ids.update(snapshot.exercise_ids())
            self.profiles.update(snapshot.profiles())
            self._merge_cloud_exclusions(snapshot.cameras)
            if self.template_store is not None:
                self._sync_templates(snapshot, pending_refs, requested_at)
            self._enforce_tracking_consent(time.time())
            configured = {str(c.id) for c in self.cameras}
            remote = {str(c.id) for c in snapshot.cameras}
            if configured != remote:
                log.warning(
                    "config_sync.camera_set_differs",
                    local_only=sorted(configured - remote),
                    cloud_only=sorted(remote - configured),
                )
        return True

    def _sync_templates(self, snapshot: Any, pending_refs: set[str], requested_at: float) -> None:
        from edge_ai.identity.enrollment import apply_cloud_revocations, reconcile_templates

        assert self.template_store is not None
        active = getattr(snapshot, "active_template_refs", None)
        if not getattr(snapshot, "from_cache", False) and active is not None:
            n = reconcile_templates(
                store=self.template_store,
                active_refs=active,
                revoked_refs=snapshot.revoked_template_refs,
                pending_refs=pending_refs,
                enrolled_before=requested_at,
            )
        elif snapshot.revoked_template_refs:
            n = apply_cloud_revocations(revoked_refs=snapshot.revoked_template_refs, store=self.template_store)
        else:
            n = 0
        if n:
            self.templates_reconciled += n
            log.info("config_sync.templates_deleted", count=n)
            if self.entrance is not None:
                self.entrance.matcher.refresh()

    def _merge_cloud_exclusions(self, cloud_cameras: list[CameraConfig]) -> None:
        """Exclusion zones drawn in the admin app apply without editing cameras.yaml (only ever added)."""
        cloud = {str(c.id): [tuple(z.polygon) for z in c.zones if z.type == "exclusion"] for c in cloud_cameras}
        for cam_id, local in self._local_exclusions.items():
            merged = list(local)
            for poly in cloud.get(cam_id, []):
                if poly not in merged:
                    merged.append(poly)
            self.exclusion_zones[cam_id] = merged

    def _enforce_tracking_consent(self, now: float) -> int:
        """Unbind every identity whose member no longer passes the CCTV-tracking consent gate
        (withdrawn, expired, missing from the snapshot, stale cache, retired device). The workout
        in progress is closed, the global track ends, and the person continues as an anonymous track."""
        if self.consent_gate is None:
            return 0
        n = 0
        for g in self.registry.all():
            if g.member_id is None or self.consent_gate.allows_tracking(g.member_id) is not None:
                continue
            st = self.states.get(g.global_id)
            for zev in self.zones.track_lost(g.global_id, now):
                if st is not None:
                    st.on_zone_event(zev)
            self._on_identity_gone(g.global_id, now, "checkout")
            self.registry.drop(g.global_id)
            n += 1
            log.info("consent.tracking_not_allowed_unbound", global_id=g.global_id)
        self.identities_unbound_no_consent += n
        return n

    def _on_event_rejected(self, event: dict[str, Any], code: str | None, reason: str | None) -> None:
        """The cloud refused `biometric.enrolled` for good → the local template would be an orphan the
        registry can never revoke. Delete it now."""
        if event.get("event_type") != "biometric.enrolled" or self.template_store is None:
            return
        ref = str(event.get("payload", {}).get("template_ref", ""))
        if ref and self.template_store.delete_refs([ref]):
            log.warning("biometric.orphan_template_deleted", template_ref=ref, code=code)

    def _tick_loop(self) -> None:
        while not self._stop.is_set():
            now = time.time()
            with self._lock:
                for st in self.states.values():
                    st.tick(now)
                for g in self.registry.expire(now):
                    self._on_identity_gone(g.global_id, now, "timeout")
            self._stop.wait(0.5)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.emit_heartbeat()
            except Exception:  # noqa: BLE001
                log.exception("heartbeat.error")
            self._stop.wait(self.settings.heartbeat_interval_seconds)

    def emit_heartbeat(self) -> None:
        gpu_util, gpu_mem = _gpu_stats()
        online = sum(1 for w in self.workers if w.source.status() == StreamStatus.ONLINE)
        self.emitter.emit(
            "device.heartbeat",
            DeviceHeartbeatPayload(
                software_version=self.settings.software_version,
                uptime_seconds=int(time.time() - self.metrics.started_at),
                gpu_utilization=gpu_util,
                gpu_memory_used_mb=gpu_mem,
                cpu_utilization=None,
                queue_depth=self.queue.depth(),
                cameras_online=online,
                cameras_total=len(self.workers),
                avg_pipeline_latency_ms=self.metrics.avg_latency_ms(),
            ),
        )

    # ── fusion entry points (called by workers) ──────────────────────────────
    def on_camera_status(
        self, cam: CameraConfig, status: StreamStatus, error: str | None, fps: float, res: tuple[int, int] | None
    ) -> None:
        self.emitter.emit(
            "camera.status",
            CameraStatusPayload(
                camera_id=cam.id,
                status=status.value,
                fps_measured=fps or None,
                resolution_w=res[0] if res else None,
                resolution_h=res[1] if res else None,
                error=error,
            ),
        )

    def on_observation(
        self,
        cam: CameraConfig,
        local_track_id: int,
        box: tuple[float, float, float, float],
        tracking_conf: float,
        t: float,
        signal: tuple[float, float] | None,
        embedding: list[float] | None,
        pose: Any | None = None,
    ) -> None:
        with self._lock:
            g, is_new = self.registry.observe(
                LocalObservation(str(cam.id), local_track_id, t, embedding, None, tracking_conf)
            )
            st = self.states.get(g.global_id)
            if st is None:
                st = IdentityWorkoutState(
                    g.global_id, g.member_id, self.emitter, self.dedup, self.exercise_ids, self.profiles
                )
                self.states[g.global_id] = st
            if is_new:
                self.emitter.emit(
                    "track.started",
                    TrackStartedPayload(
                        global_track_id=g.global_id,
                        member_id=UUID(g.member_id) if g.member_id else None,
                        identity_confidence=g.identity_confidence,
                        tracking_confidence=tracking_conf,
                        first_camera_id=cam.id,
                    ),
                    t,
                )
                if g.ambiguous:
                    self.emitter.emit(
                        "tracking.uncertain",
                        TrackingUncertainPayload.model_validate(
                            {
                                "global_track_id": g.global_id,
                                "member_id": None,
                                "from": _dt(t),
                                "to": _dt(t),
                                "reason": "identity_ambiguous",
                            }
                        ),
                        t,
                    )
            st.tracking_confidence = tracking_conf
            if tracking_conf < TRACKING_VERIFIED_THRESHOLD and st.usage is not None:
                last_u = self._last_uncertain_emitted.get(g.global_id, 0.0)
                if t - last_u >= 5.0:
                    self._last_uncertain_emitted[g.global_id] = t
                    self.emitter.emit(
                        "tracking.uncertain",
                        TrackingUncertainPayload.model_validate(
                            {
                                "global_track_id": g.global_id,
                                "member_id": g.member_id,
                                "from": _dt(t),
                                "to": _dt(t),
                                "reason": "low_tracking_confidence",
                            }
                        ),
                        t,
                    )
            for zev in self.zones.observe(g.global_id, str(cam.id), box_foot_point(box), t, tracking_conf):
                st.on_zone_event(zev)
            if pose is not None:
                if st.usage is not None:
                    st.on_pose(str(cam.id), pose, t)
                else:
                    st.update_live_pose(pose, t)
            elif signal is not None and st.usage is not None:
                st.on_signal(str(cam.id), signal[0], signal[1], t)
            self._update_live_person(g, st, str(cam.id), local_track_id, box, tracking_conf, pose, t)

    def _update_live_person(
        self,
        g: Any,
        st: IdentityWorkoutState,
        camera_id: str,
        local_track_id: int,
        box: tuple[float, float, float, float],
        tracking_conf: float,
        pose: Any | None,
        t: float,
    ) -> None:
        eng = st.live_squat_engine
        member_name = st.member_name or (self._member_names.get(g.member_id) if g.member_id else None)
        if g.member_id and not g.ambiguous:
            id_status = "verified"
        elif g.ambiguous:
            id_status = "uncertain"
        else:
            id_status = "unknown"
        seg_idx = getattr(st.usage.segmenter, "set_index", 0) if (st.usage and st.usage.segmenter) else 0
        set_idx = seg_idx if seg_idx > 0 else (1 if eng.completed_reps > 0 else 0)
        kps = (
            [[round(float(kp.x), 1), round(float(kp.y), 1), round(float(kp.score), 2)] for kp in pose.keypoints]
            if (pose is not None and hasattr(pose, "keypoints"))
            else []
        )
        self._live_people[g.global_id] = {
            "global_id": g.global_id,
            "local_track_id": local_track_id,
            "camera_id": camera_id,
            "member_id": g.member_id,
            "member_name": member_name,
            "identity_status": id_status,
            "identity_confidence": round(float(g.identity_confidence), 3),
            "tracking_confidence": round(float(tracking_conf), 3),
            "box": [round(float(v), 1) for v in box],
            "keypoints": kps,
            "exercise": (st.usage.exercise_slug or "squat") if st.usage else "squat",
            "phase": eng.phase.value,
            "completed_reps": eng.completed_reps,
            "valid_reps": eng.valid_reps,
            "imperfect_reps": eng.imperfect_reps,
            "partial_reps": eng.partial_reps,
            "set_index": set_idx,
            "knee_angle_deg": round(float(eng.current_knee_angle), 1) if eng.current_knee_angle is not None else None,
            "trunk_lean_deg": round(float(eng.current_trunk_angle), 1) if eng.current_trunk_angle is not None else None,
            "form_verdict": "RIGHT FORM" if not eng.active_errors else "WRONG FORM",
            "active_errors": [getattr(e, "value", str(e)) for e in eng.active_errors],
            "status_message": eng.status_message,
            "rep_history": [
                {
                    "rep_number": r.rep_number,
                    "started_at": r.started_at,
                    "completed_at": r.completed_at,
                    "duration_s": r.duration_s,
                    "min_knee_angle_deg": r.min_knee_angle_deg,
                    "max_trunk_lean_deg": r.max_trunk_lean_deg,
                    "is_valid": getattr(r, "is_valid", r.verdict == "correct"),
                    "quality": getattr(getattr(r, "quality", r.verdict), "value", str(getattr(r, "quality", r.verdict))),
                    "form_verdict": getattr(r, "form_verdict", "RIGHT FORM" if r.verdict == "correct" else "WRONG FORM"),
                    "feedback": getattr(r, "primary_feedback", r.primary_error or "Good form"),
                    "confidence": r.confidence,
                    "errors": [getattr(e, "value", str(e)) for e in r.detected_errors],
                }
                for r in eng.rep_history[-25:]
            ],
            "updated_at": t,
        }

    def on_local_track_lost(self, cam: CameraConfig, local_track_id: int, t: float) -> None:
        with self._lock:
            g = self.registry.end_local_track(str(cam.id), local_track_id)
            if g is None:
                return
            if not g.local_tracks:
                self._last_uncertain_emitted.pop(g.global_id, None)
                self._live_people.pop(g.global_id, None)
                # No camera sees this person any more. Usage ends; identity stays re-associable for a while.
                st = self.states.get(g.global_id)
                if st:
                    for zev in self.zones.track_lost(g.global_id, t):
                        st.on_zone_event(zev)
                    st.on_track_lost(t)
                self.emitter.emit(
                    "tracking.uncertain",
                    TrackingUncertainPayload.model_validate(
                        {
                            "global_track_id": g.global_id,
                            "member_id": g.member_id,
                            "from": _dt(t),
                            "to": _dt(t),
                            "reason": "no_camera_coverage",
                        }
                    ),
                    t,
                )

    def _on_identity_gone(
        self,
        global_id: str,
        t: float,
        reason: Literal["left_view", "checkout", "merged", "timeout", "ambiguous_identity"],
    ) -> None:
        self._live_people.pop(global_id, None)
        st = self.states.pop(global_id, None)
        if st:
            st.on_track_lost(t)
        self.emitter.emit(
            "track.ended", TrackEndedPayload(global_track_id=global_id, reason=reason, last_camera_id=None), t
        )

    def bind_member(
        self,
        global_id: str,
        member_id: str,
        confidence: float,
        method: str,
        camera_id: UUID | None,
        consent_record_id: str | None,
        pad_score: float | None,
        emit_checkin: bool = True,
        member_name: str | None = None,
    ) -> bool:
        """Entrance identification result → bind the global identity and emit check-in.

        `emit_checkin=False` binds only (the member already has an attendance session, e.g. a kiosk
        check-in); the workout events then carry member_id and the cloud attaches them to it."""
        with self._lock:
            if member_name:
                self._save_member_name(member_id, member_name)
            g = self.registry.get(global_id)
            if g is None or not g.bind_member(member_id, confidence):
                return False
            st = self.states.get(global_id)
            if st:
                st.member_id = member_id
                st.member_name = member_name or self._member_names.get(member_id)
            if global_id in self._live_people:
                self._live_people[global_id]["member_id"] = member_id
                self._live_people[global_id]["member_name"] = self._member_names.get(member_id)
                self._live_people[global_id]["identity_status"] = "verified"
                self._live_people[global_id]["identity_confidence"] = round(float(confidence), 3)
            if not emit_checkin:
                return True
            self.emitter.emit(
                "identity.checkin",
                IdentityCheckInPayload(
                    member_id=UUID(member_id),
                    method=method,  # type: ignore[arg-type]
                    confidence=confidence,
                    camera_id=camera_id,
                    global_track_id=global_id,
                    consent_record_id=UUID(consent_record_id) if consent_record_id else None,
                    presentation_attack_score=pad_score,
                ),
            )
            return True

    def latest_jpeg(self) -> bytes | None:
        with self._lock:
            frame = self._latest_frame
        if frame is None:
            return self._latest_jpeg
        try:
            import cv2
            _, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
            self._latest_jpeg = buf.tobytes()
            return self._latest_jpeg
        except Exception:
            return self._latest_jpeg

    def live_state(self) -> dict[str, Any]:
        with self._lock:
            active_gids = {g.global_id for g in self.registry.active()}
            people = [
                dict(p)
                for gid, p in self._live_people.items()
                if gid in active_gids
            ]
            people.sort(key=lambda x: int(x.get("local_track_id", 0)))
            any_online = any(w.source.status() == StreamStatus.ONLINE for w in self.workers)
            return {
                "status": "paused" if self._camera_paused else ("online" if any_online else "offline"),
                "camera_owner": "browser" if self._camera_paused else "edge-ai",
                "camera_paused": self._camera_paused,
                "frame_at": self._latest_frame_at,
                "active_provider": getattr(self.detector, "active_provider", "CPUExecutionProvider") if self.detector else "None",
                "models": {
                    "detector": getattr(self.detector, "model_name", None),
                    "pose": getattr(self.pose, "model_name", None),
                    "reid": getattr(self.reid, "model_name", None),
                    "face": getattr(getattr(self.entrance, "embedder", None), "model_name", None),
                },
                "biometric_templates": self.template_store.count() if self.template_store is not None else 0,
                "people_count": len(people),
                "people": people,
            }

    def release_camera(self) -> dict[str, Any]:
        with self._lock:
            self._camera_paused = True
            for w in self.workers:
                w.source.stop()
            self._live_people.clear()
        from edge_ai.camera.manager import CentralCameraManager
        CentralCameraManager.get_instance().release_physical_handles()
        return {"ok": True, "camera_owner": "browser", "camera_paused": True}

    def acquire_camera(self) -> dict[str, Any]:
        with self._lock:
            self._camera_paused = False
            for w in self.workers:
                w.source.start()
        return {"ok": True, "camera_owner": "edge-ai", "camera_paused": False}

    def reset_workout(self, global_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            targets = [global_id] if global_id else list(self.states.keys())
            for gid in targets:
                if gid is None:
                    continue
                st = self.states.get(gid)
                if st:
                    st.live_squat_engine.reset()
                    if st.usage:
                        for eng in st.usage.squat_engines.values():
                            eng.reset()
                if gid in self._live_people:
                    self._live_people[gid]["completed_reps"] = 0
                    self._live_people[gid]["valid_reps"] = 0
                    self._live_people[gid]["imperfect_reps"] = 0
                    self._live_people[gid]["partial_reps"] = 0
                    self._live_people[gid]["rep_history"] = []
        return {"ok": True, "reset": targets}

    def enroll_face_base64(
        self,
        member_id: str,
        member_name: str | None,
        images_b64: list[str],
        consent_record_id: str | None = None,
    ) -> dict[str, Any]:
        import base64
        from uuid import uuid4

        import cv2
        import numpy as np

        from edge_ai.identity.consent_gate import BIOMETRIC, TRACKING
        from edge_ai.identity.enrollment import enroll_member

        if self.template_store is None or self.entrance is None or self.consent_gate is None:
            return {"ok": False, "error": "Biometric store or SFace ONNX model is not initialized on Edge AI."}
        decoded_images: list[Any] = []
        if not images_b64 or (len(images_b64) == 1 and str(images_b64[0]).strip() in ("", "current")):
            with self._lock:
                if self._latest_frame is not None:
                    decoded_images.append(self._latest_frame.copy())
        else:
            for raw_b64 in images_b64:
                clean = raw_b64.split(",", 1)[-1] if "," in raw_b64 else raw_b64
                buf = np.frombuffer(base64.b64decode(clean), dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
                if img is not None:
                    decoded_images.append(img)
        if not decoded_images:
            return {"ok": False, "error": "Could not decode any valid image from base64 payload or live camera."}
        cid = consent_record_id or str(uuid4())
        with self._lock:
            granted = dict(self.consent_gate._granted)
            granted[member_id] = {BIOMETRIC: cid, TRACKING: cid}
            self.consent_gate.replace(granted, generated_at=time.time(), expires=self.consent_gate._expires)
            if member_name:
                self._save_member_name(member_id, member_name)
            try:
                res = enroll_member(
                    member_id=member_id,
                    images=decoded_images,
                    detector=self.entrance.detector,
                    embedder=self.entrance.embedder,
                    store=self.template_store,
                    consent=self.consent_gate,
                    emitter=self.emitter,
                    min_quality=0.10,
                )
                self.entrance.matcher.refresh()
                # Clear retry attempts so any active track in view is immediately re-matched on the next frame
                self.entrance._attempts.clear()
                return {
                    "ok": True,
                    "template_ref": res.template_ref,
                    "images_used": res.images_used,
                    "quality": res.quality,
                    "member_id": member_id,
                    "member_name": member_name,
                }
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc)}

    def identify_face_base64(self, image_b64: str | None = None, min_confidence: float | None = None) -> dict[str, Any]:
        """Direct 1:N face identification against local biometric store from base64 image or live frame."""
        import base64
        import cv2
        import numpy as np

        if self.entrance is None:
            return {"ok": False, "error": "Entrance/Face identification is not initialized on Edge AI."}

        img = None
        if not image_b64 or str(image_b64).strip() in ("", "current"):
            with self._lock:
                if self._latest_frame is not None:
                    img = self._latest_frame.copy()
            if img is None:
                return {"ok": False, "error": "No live camera frame currently available on Edge AI."}
        else:
            clean = str(image_b64).split(",", 1)[-1] if "," in str(image_b64) else str(image_b64)
            try:
                buf = np.frombuffer(base64.b64decode(clean), dtype=np.uint8)
                img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": f"Failed to decode base64 image: {exc}"}
            if img is None:
                return {"ok": False, "error": "Could not decode valid image from base64 string."}

        faces = self.entrance.detector.detect(img)
        if not faces:
            return {"ok": True, "matched": False, "reason": "no_face"}

        best_face = faces[0]
        emb = self.entrance.embedder.embed_box(img, best_face)
        if emb is None:
            return {"ok": True, "matched": False, "reason": "no_face"}

        match = self.entrance.matcher.match(emb)
        threshold = min_confidence if min_confidence is not None else getattr(self.entrance.matcher, "match_threshold", 0.60)

        if getattr(match, "ambiguous", False):
            return {"ok": True, "matched": False, "reason": "ambiguous", "confidence": match.confidence}

        if match.member_id and match.confidence >= threshold:
            name = self._member_names.get(match.member_id)
            return {
                "ok": True,
                "matched": True,
                "member_id": match.member_id,
                "member_name": name,
                "confidence": match.confidence,
                "template_ref": getattr(match, "template_ref", None),
            }
        return {
            "ok": True,
            "matched": False,
            "reason": "no_match",
            "confidence": getattr(match, "confidence", 0.0),
        }

    def health(self) -> dict[str, Any]:
        return {
            "status": "ok"
            if any(w.source.status() == StreamStatus.ONLINE for w in self.workers) or not self.workers
            else "degraded",
            "uptime_seconds": int(time.time() - self.metrics.started_at),
            "cameras": {
                str(w.cam.id): {
                    "name": w.cam.name,
                    "status": w.source.status().value,
                    "fps": round(w.source.measured_fps(), 2),
                    "inference_fps": round(w.inference_fps_measured(), 2),
                }
                for w in self.workers
            },
            "queue_depth": self.queue.depth(),
            "queue_oldest_age_s": self.queue.oldest_age_seconds(),
            "backend_connected": self.sync.last_error is None and self.sync.last_success_at is not None,
            "offline_mode": self.sync.last_error is not None or self.sync.last_success_at is None,
            "active_provider": getattr(self.detector, "active_provider", "CPUExecutionProvider") if self.detector else "None",
            "sync_last_success_at": self.sync.last_success_at,
            "sync_last_error": self.sync.last_error,
            "device_retired": self.device_retired,
            "sync": self.sync.health() if hasattr(self.sync, "health") else None,
            "frames_processed": self.metrics.frames_processed,
            "detections": self.metrics.detections,
            "detections_excluded": self.metrics.detections_excluded,
            "last_processed_frame_at": getattr(self, "_latest_frame_at", None),
            "model_status": "loaded" if (self.detector and self.pose) else "degraded",
            "avg_latency_ms": self.metrics.avg_latency_ms(),
            "p95_latency_ms": self.metrics.p95_latency_ms(),
            "identities_active": len(self.registry.active()),
            "reps_admitted": self.dedup.admitted,
            "reps_suppressed_duplicates": self.dedup.suppressed,
            "merges_refused": self.registry.merges_refused,
            "stages": {
                "detector": getattr(self.detector, "model_name", None),
                "pose": getattr(self.pose, "model_name", None),
                "reid": getattr(self.reid, "model_name", None),
                "face": getattr(getattr(self.entrance, "embedder", None), "model_name", None),
            },
            "identification": dict(getattr(self.entrance, "stats", {})) if self.entrance is not None else None,
            "config_sync_last_success_at": getattr(self.config_sync, "last_success_at", None),
            "config_sync_last_error": getattr(self.config_sync, "last_error", None),
            "consent_cache_stale": self.consent_gate.stale if self.consent_gate is not None else None,
            "consent_snapshot_generated_at": self.consent_gate.generated_at if self.consent_gate is not None else None,
            "identities_unbound_no_consent": self.identities_unbound_no_consent,
            "biometric_templates": self.template_store.count() if self.template_store is not None else None,
            "biometric_templates_undecryptable": getattr(self.template_store, "undecryptable", None),
            "biometric_templates_deleted_by_sync": self.templates_reconciled,
        }


class CameraWorker:
    def __init__(self, pipeline: Pipeline, cam: CameraConfig, source: FrameSource) -> None:
        self.pipeline = pipeline
        self.cam = cam
        self.source = source
        self.tracker = IouTracker()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_frame_index = -1
        self._known_tracks: set[int] = set()
        self._inf_times: deque[float] = deque(maxlen=60)
        self._last_status: StreamStatus | None = None
        self._frame_counter = 0
        self.is_entrance = cam.purpose in ("entrance", "mixed")

    def start(self) -> None:
        self.source.start()
        self._thread = threading.Thread(target=self._run, name=f"worker-{self.cam.name}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.source.stop()
        if self._thread:
            self._thread.join(timeout=5)

    def inference_fps_measured(self) -> float:
        if len(self._inf_times) < 2:
            return 0.0
        span = self._inf_times[-1] - self._inf_times[0]
        return (len(self._inf_times) - 1) / span if span > 0 else 0.0

    def _run(self) -> None:
        period = 1.0 / max(0.1, self.cam.inference_fps or self.pipeline.settings.inference_fps)
        while not self._stop.is_set():
            loop_start = time.monotonic()
            status = self.source.status()
            if status != self._last_status:
                self._last_status = status
                res = getattr(self.source, "resolution", None)
                self.pipeline.on_camera_status(
                    self.cam,
                    status,
                    getattr(self.source, "last_error", lambda: None)(),
                    self.source.measured_fps(),
                    res,
                )
            frame = self.source.latest()
            if frame is not None and frame.frame_index != self._last_frame_index:
                self._last_frame_index = frame.frame_index
                try:
                    self.process_frame(frame.image, frame.captured_at)
                except Exception:  # noqa: BLE001
                    log.exception("worker.frame_error", camera=self.cam.name)
            elapsed = time.monotonic() - loop_start
            wait_time = max(0.015, period - elapsed)
            self._stop.wait(wait_time)

    def process_frame(self, image: Any, t: float) -> None:
        t0 = time.monotonic()
        with self.pipeline._lock:
            self.pipeline._latest_frame = image
            self.pipeline._latest_frame_at = t

        dets: list[Detection] = self.pipeline.detector.detect(image) if self.pipeline.detector else []
        # Privacy: people in exclusion zones (changing/prayer-room doorways, ...) are dropped BEFORE
        # tracking, so nothing downstream (tracks, ReID, pose, face identification) ever sees them.
        exclusions = self.pipeline.exclusion_zones.get(str(self.cam.id), [])
        if exclusions:
            kept = [d for d in dets if not box_in_exclusion(d.box, exclusions)]
            self.pipeline.metrics.detections_excluded += len(dets) - len(kept)
            dets = kept
        tracks = self.tracker.update(dets, t)
        confirmed = [tr for tr in tracks if tr.state == TrackState.CONFIRMED]

        # Pose only for people inside an equipment zone → GPU budget scales with active users, not crowd size.
        zone_tracks = [tr for tr in confirmed if any(self._foot_in_any_zone(tr.box))]
        poses = (
            self.pipeline.pose.estimate(image, [tr.box for tr in zone_tracks])
            if (self.pipeline.pose and zone_tracks)
            else [None] * len(zone_tracks)
        )
        pose_by_id = {tr.track_id: p for tr, p in zip(zone_tracks, poses, strict=False)}

        self._frame_counter += 1
        run_reid = self.pipeline.reid is not None and (self._frame_counter % self.pipeline.reid_every_n_frames == 0)

        current_ids = {tr.track_id for tr in confirmed}
        for tr in confirmed:
            signal = None
            p = pose_by_id.get(tr.track_id)
            if p is not None:
                # Which signal? The zone's exercise profile decides. Use the first matching zone.
                for z in self.pipeline.zones.zones_for_camera(str(self.cam.id)):
                    prof = self.pipeline.profiles.get(z.exercise_slug or "")
                    if prof and self.pipeline.zones.active_zone(self._gid(tr.track_id) or "") in (None, z):
                        signal = extract_signal(prof.signal, p)
                        break
            embedding = None
            # Appearance embedding on first sighting (so cross-camera association can happen at once) and periodically.
            if self.pipeline.reid is not None and (run_reid or tr.track_id not in self._known_tracks):
                try:
                    embedding = self.pipeline.reid.embed(image, tr.box)
                except Exception:  # noqa: BLE001
                    log.exception("reid.error", camera=self.cam.name)
            self.pipeline.on_observation(self.cam, tr.track_id, tr.box, tr.tracking_confidence, t, signal, embedding, pose=p)
            if self.is_entrance and self.pipeline.entrance is not None:
                self._try_identify(image, tr, t)
        for lost in self._known_tracks - current_ids:
            self.pipeline.on_local_track_lost(self.cam, lost, t)
            if self.pipeline.entrance is not None:
                self.pipeline.entrance.forget(str(self.cam.id), lost)
        self._known_tracks = current_ids

        latency_ms = (time.monotonic() - t0) * 1000
        self._inf_times.append(time.monotonic())
        self.pipeline.metrics.record(latency_ms, len(dets))
        from edge_ai.camera.manager import CentralCameraManager
        dev = CentralCameraManager.get_instance()._devices.get(str(self.cam.id))
        if dev is not None:
            dev.frames_processed += 1
            dev.detections += len(dets)
            dev.last_processed_frame_at = t
            dev.inference_fps = round(self.inference_fps_measured(), 1)

    def _try_identify(self, image: Any, tr: Any, t: float) -> None:
        """Entrance face identification for tracks inside an entrance zone. A dedicated `entrance`
        camera without a drawn zone identifies anywhere in view; a `mixed` camera also sees the floor,
        so it identifies ONLY inside an entrance zone (floor cameras never identify faces)."""
        from edge_ai.geometry import point_in_polygon

        if self.pipeline.device_retired:
            return
        g = self.pipeline.registry.global_for_local(str(self.cam.id), tr.track_id)
        if g is None or g.member_id is not None:
            return
        zones = self.pipeline.entrance_zones.get(str(self.cam.id), [])
        if not zones and self.cam.purpose != "entrance":
            return
        if zones and not any(point_in_polygon(box_foot_point(tr.box), z) for z in zones):
            return
        ident = self.pipeline.entrance
        assert ident is not None
        if not ident.should_try(str(self.cam.id), tr.track_id, t):
            return
        try:
            outcome = ident.identify(image, tr.box)
        except Exception:  # noqa: BLE001
            log.exception("entrance.identify_error", camera=self.cam.name)
            return
        if outcome.member_id is not None:
            bound = self.pipeline.bind_member(
                g.global_id,
                outcome.member_id,
                outcome.confidence,
                "face",
                self.cam.id,
                outcome.consent_record_id,
                outcome.pad_score,
            )
            log.info(
                "entrance.identified",
                camera=self.cam.name,
                global_id=g.global_id,
                confidence=outcome.confidence,
                bound=bound,
            )

    def _gid(self, local_id: int) -> str | None:
        g = self.pipeline.registry.global_for_local(str(self.cam.id), local_id)
        return g.global_id if g else None

    def _foot_in_any_zone(self, box: tuple[float, float, float, float]):  # type: ignore[no-untyped-def]
        from edge_ai.geometry import point_in_polygon

        cam_zones = self.pipeline.zones.zones_for_camera(str(self.cam.id))
        if not cam_zones:
            yield True
            return
        foot = box_foot_point(box)
        for z in cam_zones:
            yield point_in_polygon(foot, z.polygon)


def _dt(t: float):  # type: ignore[no-untyped-def]
    from datetime import datetime, timezone

    return datetime.fromtimestamp(t, tz=timezone.utc)


def _gpu_stats() -> tuple[float | None, float | None]:
    """Best-effort NVIDIA stats via nvidia-smi; None when unavailable."""
    import shutil
    import subprocess

    smi = shutil.which("nvidia-smi")
    if not smi:
        return None, None
    try:
        out = (
            subprocess.run(  # noqa: S603 — fixed argv, absolute path, no shell
                [smi, "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            .stdout.strip()
            .splitlines()
        )
        if not out:
            return None, None
        util, mem = out[0].split(",")
        return float(util) / 100.0, float(mem)
    except Exception:  # noqa: BLE001
        return None, None
