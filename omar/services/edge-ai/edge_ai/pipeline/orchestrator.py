"""
Pipeline orchestrator.

One CameraWorker per camera runs: frame → detector → local tracker → (pose on
tracks inside equipment zones) → fusion. Workers hand their per-frame
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
from edge_ai.geometry import box_foot_point
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
        self.queue = queue or PersistentEventQueue(settings.data_dir / "events.sqlite")
        self.emitter = EventBuilder(self.queue, settings.device_id, settings.branch_id)
        self.sync = sync or SyncClient(
            self.queue, settings.ingest_url, str(settings.device_id), settings.device_secret, settings.sync_batch_size
        )
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
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

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

    def apply_config_sync(self) -> bool:
        if self.config_sync is None:
            return False
        snapshot = self.config_sync.fetch_or_cached()
        if snapshot is None:
            return False
        with self._lock:
            if self.consent_gate is not None:
                self.config_sync.apply_consents(snapshot, self.consent_gate)
            self.exercise_ids.update(snapshot.exercise_ids())
            self.profiles.update(snapshot.profiles())
            if self.template_store is not None and snapshot.revoked_template_refs:
                from edge_ai.identity.enrollment import apply_cloud_revocations

                n = apply_cloud_revocations(revoked_refs=snapshot.revoked_template_refs, store=self.template_store)
                if n:
                    log.info("config_sync.templates_revoked", count=n)
                    if self.entrance is not None:
                        self.entrance.matcher.refresh()
            configured = {str(c.id) for c in self.cameras}
            remote = {str(c.id) for c in snapshot.cameras}
            if configured != remote:
                log.warning(
                    "config_sync.camera_set_differs",
                    local_only=sorted(configured - remote),
                    cloud_only=sorted(remote - configured),
                )
        return True

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
            if signal is not None and st.usage is not None:
                st.on_signal(str(cam.id), signal[0], signal[1], t)

    def on_local_track_lost(self, cam: CameraConfig, local_track_id: int, t: float) -> None:
        with self._lock:
            g = self.registry.end_local_track(str(cam.id), local_track_id)
            if g is None:
                return
            if not g.local_tracks:
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
    ) -> bool:
        """Entrance identification result → bind the global identity and emit check-in."""
        with self._lock:
            g = self.registry.get(global_id)
            if g is None or not g.bind_member(member_id, confidence):
                return False
            st = self.states.get(global_id)
            if st:
                st.member_id = member_id
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
            "sync_last_success_at": self.sync.last_success_at,
            "sync_last_error": self.sync.last_error,
            "frames_processed": self.metrics.frames_processed,
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
            "identification": dict(self.entrance.stats) if self.entrance is not None else None,
            "config_sync_last_success_at": getattr(self.config_sync, "last_success_at", None),
            "config_sync_last_error": getattr(self.config_sync, "last_error", None),
            "consent_cache_stale": self.consent_gate.stale if self.consent_gate is not None else None,
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
            self._stop.wait(max(0.0, period - elapsed))

    def process_frame(self, image: Any, t: float) -> None:
        t0 = time.monotonic()
        dets: list[Detection] = self.pipeline.detector.detect(image) if self.pipeline.detector else []
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
            self.pipeline.on_observation(self.cam, tr.track_id, tr.box, tr.tracking_confidence, t, signal, embedding)
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

    def _try_identify(self, image: Any, tr: Any, t: float) -> None:
        """Entrance face identification for tracks inside an entrance zone (or anywhere if none is defined)."""
        from edge_ai.geometry import point_in_polygon

        g = self.pipeline.registry.global_for_local(str(self.cam.id), tr.track_id)
        if g is None or g.member_id is not None:
            return
        zones = self.pipeline.entrance_zones.get(str(self.cam.id), [])
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

        foot = box_foot_point(box)
        for z in self.pipeline.zones.zones_for_camera(str(self.cam.id)):
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
