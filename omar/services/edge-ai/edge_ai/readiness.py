"""
Preflight verification and readiness coordinator for KinetiQ Edge AI.

Manages comprehensive startup preflight validation and truthful readiness states:
1. Singleton lock verification
2. Model loading & ONNX provider check
3. Camera registration & broker startup
4. Physical camera handle verification
5. First frame arrival check
6. Pipeline worker liveness
7. Model telemetry updates
8. Database sync readiness
9. HTTP server response check
"""

from __future__ import annotations

import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)


class ReadinessTracker:
    """Tracks preflight milestones and readiness criteria for the Edge AI service."""

    def __init__(self) -> None:
        self.started_at = time.time()
        self.singleton_verified = False
        self.models_loaded = False
        self.onnx_providers_verified = False
        self.active_provider: str | None = None
        self.cameras_registered = False
        self.camera_broker_started = False
        self.camera_handle_open = False
        self.first_frame_received = False
        self.pipeline_worker_alive = False
        self.model_telemetry_updating = False
        self.database_sync_ready = False
        self.http_server_responding = False
        self.config_valid = True
        self.not_ready_reason: str | None = "Initializing preflight checks"

    def mark_singleton_verified(self) -> None:
        self.singleton_verified = True

    def mark_models_loaded(self, detector: Any, pose: Any, provider: str | None) -> None:
        self.models_loaded = bool(detector and pose)
        self.onnx_providers_verified = bool(provider and provider != "None")
        self.active_provider = provider

    def mark_cameras_registered(self, count: int) -> None:
        self.cameras_registered = count > 0

    def mark_broker_started(self) -> None:
        self.camera_broker_started = True

    def mark_http_ready(self) -> None:
        self.http_server_responding = True

    def mark_db_sync_ready(self, ok: bool) -> None:
        self.database_sync_ready = ok

    def evaluate_live_state(self, pipeline: Any, manager: Any) -> tuple[bool, str | None]:
        """
        Poll live pipeline and camera manager state to update readiness milestones.
        Returns (is_ready, not_ready_reason).
        """
        # 1. Config validity
        if not self.config_valid:
            self.not_ready_reason = "Configuration is invalid"
            return False, self.not_ready_reason

        # 2. Singleton verification
        if not self.singleton_verified:
            self.not_ready_reason = "Singleton lock is not verified"
            return False, self.not_ready_reason

        # 3. Models
        if not self.models_loaded:
            self.not_ready_reason = "AI model loading failed or models missing"
            return False, self.not_ready_reason

        if not self.onnx_providers_verified:
            self.not_ready_reason = "No valid ONNX runtime execution provider active"
            return False, self.not_ready_reason

        # 4. Camera Broker & Registration
        if not self.cameras_registered or not self.camera_broker_started:
            self.not_ready_reason = "Cameras not registered or camera broker not started"
            return False, self.not_ready_reason

        # 5. Check physical camera handles & frames from manager and pipeline
        total_handles = 0
        total_frames_received = 0
        total_frames_processed = 0

        if manager is not None:
            with getattr(manager, "_lock", None) or _DummyLock():
                devices = getattr(manager, "_devices", {}).values()
                unique_devices = list({id(d): d for d in devices}.values())
                total_handles = sum(d.physical_handles for d in unique_devices)
                total_frames_received = sum(d.frames_received for d in unique_devices)
                total_frames_processed = sum(d.frames_processed for d in unique_devices)

        self.camera_handle_open = total_handles > 0
        self.first_frame_received = total_frames_received > 0

        if not self.camera_handle_open:
            self.not_ready_reason = "Physical camera handle is not open or camera unavailable"
            return False, self.not_ready_reason

        if not self.first_frame_received:
            self.not_ready_reason = "No frames arrived from physical camera"
            return False, self.not_ready_reason

        # 6. Pipeline workers alive
        workers_alive = False
        if pipeline is not None:
            workers = getattr(pipeline, "workers", [])
            workers_alive = bool(workers) and all(
                getattr(w, "_thread", None) is not None and w._thread.is_alive()
                for w in workers
            )
            # If metrics processed frames
            metrics = getattr(pipeline, "metrics", None)
            if metrics is not None:
                total_frames_processed = max(total_frames_processed, metrics.frames_processed)

        self.pipeline_worker_alive = workers_alive
        if not self.pipeline_worker_alive:
            self.not_ready_reason = "Pipeline worker thread stopped or dead"
            return False, self.not_ready_reason

        # 7. Model telemetry updating
        self.model_telemetry_updating = total_frames_processed > 0 or total_frames_received > 0

        # 8. Database sync check
        if pipeline is not None:
            sync = getattr(pipeline, "sync", None)
            device_retired = getattr(pipeline, "device_retired", False)
            if device_retired:
                self.database_sync_ready = False
                self.not_ready_reason = "Edge device has been retired from cloud database"
                return False, self.not_ready_reason

            # Offline mode is acceptable, but hard auth failures / invalid credentials are not
            last_err = getattr(sync, "last_error", None)
            if last_err and any(tok in str(last_err).lower() for tok in ("401", "403", "unauthorized", "forbidden")):
                self.database_sync_ready = False
                self.not_ready_reason = f"Database sync authentication rejected: {last_err}"
                return False, self.not_ready_reason
            self.database_sync_ready = True

        # 9. All passed!
        self.not_ready_reason = None
        return True, None

    def get_checks_dict(self) -> dict[str, Any]:
        return {
            "singleton_locked": self.singleton_verified,
            "models_loaded": self.models_loaded,
            "onnx_providers_verified": self.onnx_providers_verified,
            "active_provider": self.active_provider,
            "cameras_registered": self.cameras_registered,
            "camera_broker_started": self.camera_broker_started,
            "camera_handle_open": self.camera_handle_open,
            "first_frame_received": self.first_frame_received,
            "pipeline_worker_alive": self.pipeline_worker_alive,
            "model_telemetry_updating": self.model_telemetry_updating,
            "database_sync_ready": self.database_sync_ready,
            "http_server_responding": self.http_server_responding,
            "config_valid": self.config_valid,
        }

    def ready_payload(self, pipeline: Any, manager: Any) -> tuple[int, dict[str, Any]]:
        is_ready, reason = self.evaluate_live_state(pipeline, manager)
        checks = self.get_checks_dict()
        code = 200 if is_ready else 503
        payload = {
            "status": "ready" if is_ready else "not_ready",
            "ready": is_ready,
            "reason": reason,
            "checks": checks,
            "uptime_seconds": int(time.time() - self.started_at),
        }
        return code, payload


class _DummyLock:
    def __enter__(self) -> None:
        pass

    def __exit__(self, *_: Any) -> None:
        pass
