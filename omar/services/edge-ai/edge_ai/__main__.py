"""
CLI entry point.

  edge-ai run                      start the full pipeline from .env + cameras.yaml
  edge-ai probe <url> [--seconds]  Phase 0 camera compatibility probe
  edge-ai probe --cameras FILE     probe every camera in a cameras.yaml
  edge-ai simulate [--seconds]     synthetic workout → events → sync (no cameras; clearly test data)
  edge-ai drain                    push any queued events to the cloud and exit
  edge-ai sync                     pull consents / catalogue / revocations from the cloud once
  edge-ai discover [--seconds]     ONVIF WS-Discovery on the camera network
  edge-ai enroll --member ID --image PATH [--image PATH ...]   enrol a consenting member's face
  edge-ai revoke --member ID       delete a member's local templates and notify the cloud
  edge-ai revoke --undecryptable   delete templates the current key cannot decrypt (after a key change)
  edge-ai benchmark ...            see edge_ai.tools.benchmark
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from edge_ai.config import CamerasFile, Settings
    from edge_ai.events.sync import RejectionListener
    from edge_ai.identity.face_store import EncryptedTemplateStore
    from edge_ai.reps.profiles import RepProfile


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s", stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
    )


def cmd_probe(args: argparse.Namespace) -> int:
    from edge_ai.camera.probe import probe_from_config, probe_stream, results_to_json

    if args.cameras:
        results = probe_from_config(Path(args.cameras), Path(args.secrets) if args.secrets else None, args.seconds)
    elif args.url:
        results = [probe_stream(args.url, args.seconds)]
    else:
        print("probe: give a URL or --cameras FILE", file=sys.stderr)
        return 2
    print(results_to_json(results))
    return 0 if all(r.ok for r in results) else 1


def _load_runtime() -> tuple[Settings, CamerasFile, dict[str, RepProfile]]:
    from edge_ai.config import Settings, load_cameras
    from edge_ai.reps.profiles import RepProfile

    settings = Settings()  # type: ignore[call-arg]
    cams = load_cameras(settings.cameras_file)
    # Profiles: in production pulled from exercise_catalog; the bundled defaults mirror seed.sql.
    from edge_ai.tools.default_profiles import DEFAULT_PROFILES

    profiles = {slug: RepProfile.from_catalog(p) for slug, p in DEFAULT_PROFILES.items()}
    return settings, cams, profiles


def _build_identity_stack(settings: Settings):  # type: ignore[no-untyped-def]
    """Consent gate + encrypted store + (optional) face matcher/entrance identifier + config sync client."""
    from edge_ai.cloud.device_sync import DeviceSyncClient
    from edge_ai.identity.consent_gate import ConsentGate

    # Consents live only in memory and in the encrypted snapshot cache; remove the plaintext file
    # older versions wrote.
    (settings.data_dir / "consents.json").unlink(missing_ok=True)
    gate = ConsentGate(None)
    key = settings.biometric_key
    sync = DeviceSyncClient(
        settings.effective_sync_url,
        str(settings.device_id),
        settings.device_secret,
        settings.data_dir / "device-sync.json",
        cache_key=key,
    )
    store = None
    entrance = None
    if key is not None:
        from edge_ai.identity.face_store import EncryptedTemplateStore, FaceMatcher

        store = EncryptedTemplateStore(settings.data_dir / "biometrics.sqlite", key)
        if settings.face_model_path:
            from edge_ai.identity.entrance import EntranceIdentifier
            from edge_ai.identity.face_pipeline import HaarFaceDetector, OnnxFaceEmbedder, YuNetFaceDetector

            yunet_path = Path(settings.face_model_path).parent / "face_detection_yunet_2023mar.onnx"
            face_det = YuNetFaceDetector(str(yunet_path)) if yunet_path.is_file() else HaarFaceDetector()
            entrance = EntranceIdentifier(
                face_det, OnnxFaceEmbedder(str(settings.face_model_path)), FaceMatcher(store), gate
            )
    return gate, store, entrance, sync


def _build_models(settings: Settings, detector_override: str | None = None) -> tuple[Any, Any, Any]:
    """Detector, pose estimator and ReID embedder for `run` (None = stage disabled).

    Built through the model factories so EDGE_DETECTOR_LAYOUT / EDGE_POSE_KIND (from the
    environment, .env or the Docker env_file, via Settings) pick the right decoder.
    """
    log = structlog.get_logger("edge-ai")
    detector = pose = reid = None
    detector_path = detector_override or (str(settings.detector_model_path) if settings.detector_model_path else None)
    if detector_path and not Path(detector_path).is_file():
        # A configured but absent model (failed copy, fresh host) must not crash-loop the service:
        # heartbeats, camera.status and the queued events (biometric.revoked included) keep flowing.
        log.error("detector.not_found", model=detector_path, msg="detector model file missing: camera health only")
        detector_path = None
    if detector_path:
        from edge_ai.detection.onnx_detector import create_detector

        detector = create_detector(detector_path, layout=settings.detector_layout)
        log.info("detector.loaded", model=detector_path, layout=detector.layout, provider=detector.active_provider)
    else:
        log.warning(
            "detector.missing", msg="running without a detector: camera health only. Set EDGE_DETECTOR_MODEL_PATH"
        )
    if settings.pose_model_path:
        from edge_ai.pose.factory import create_pose_estimator

        pose = create_pose_estimator(str(settings.pose_model_path), kind=settings.pose_kind)
        log.info("pose.loaded", model=str(settings.pose_model_path), adapter=type(pose).__name__)
    else:
        log.warning("pose.missing", msg="no pose model: equipment occupancy only, no rep counting")
    if settings.reid_model_path:
        from edge_ai.fusion.onnx_reid import OnnxReidEmbedder

        reid = OnnxReidEmbedder(str(settings.reid_model_path))
        log.info("reid.loaded", model=str(settings.reid_model_path))
    return detector, pose, reid


def cmd_run(args: argparse.Namespace) -> int:
    import signal
    from edge_ai.config import Settings, load_cameras
    from edge_ai.singleton import SingletonLock

    # 1. Load settings to locate data_dir and health_port BEFORE initializing OpenCV, ONNX, or HTTP server
    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        return 1

    primary_camera_id = "default"
    try:
        cams = load_cameras(settings.cameras_file)
        if cams.cameras:
            primary_camera_id = str(cams.cameras[0].id)
    except Exception:
        cams = None

    lock = SingletonLock(
        lock_file=settings.data_dir / "edge_ai.lock",
        metadata_file=settings.data_dir / "edge_ai_singleton.json",
        camera_id=primary_camera_id,
        health_port=settings.health_port,
        version=settings.software_version,
    )

    if not lock.acquire():
        # Another valid instance holds the lock. Output already printed by lock.acquire().
        # Never open camera, never kill other process, exit cleanly with non-zero status.
        return 1

    _configure_logging(settings.log_level)
    log = structlog.get_logger("edge-ai")
    log.info("edge-ai.singleton_lock_acquired", pid=os.getpid(), lock_file=str(lock.lock_file))

    from edge_ai.readiness import ReadinessTracker
    readiness = ReadinessTracker()
    readiness.mark_singleton_verified()

    # Now that singleton lock is verified, initialize OpenCV, ONNX models, and HTTP server
    import cv2
    cv2.setNumThreads(2)

    from edge_ai.config import build_authenticated_url, load_camera_credentials
    from edge_ai.health import serve_health
    from edge_ai.pipeline.orchestrator import Pipeline
    from edge_ai.reps.profiles import RepProfile
    from edge_ai.tools.default_profiles import DEFAULT_PROFILES

    profiles = {slug: RepProfile.from_catalog(p) for slug, p in DEFAULT_PROFILES.items()}
    if cams is None:
        cams = load_cameras(settings.cameras_file)

    detector, pose, reid = _build_models(settings, args.detector)
    gate, store, entrance, config_sync = _build_identity_stack(settings)
    if entrance is None:
        log.warning("face.missing", msg="no face model/biometric key: entrance identification disabled (QR/NFC only)")

    active_provider = getattr(detector, "active_provider", None) if detector else None
    readiness.mark_models_loaded(detector, pose, active_provider)

    pipeline = Pipeline(
        settings,
        cams.cameras,
        cams.exercise_ids,
        profiles,
        detector,
        pose,
        reid=reid,
        entrance=entrance,
        config_sync=config_sync,
        consent_gate=gate,
        template_store=store,
    )
    pipeline.apply_config_sync()

    from edge_ai.camera.broker_source import BrokerFrameSource
    from edge_ai.camera.manager import CentralCameraManager

    manager = CentralCameraManager.get_instance()
    creds = load_camera_credentials(settings.camera_secrets_file)
    for cam in cams.cameras:
        url = build_authenticated_url(cam.url, creds.get(cam.id))
        manager.register_camera(str(cam.id), url=url, name=cam.name)
        manager.register_camera("default", url=url, name=cam.name)
        pipeline.add_worker(cam, BrokerFrameSource(camera_id=str(cam.id), url=url, name=cam.name))

    readiness.mark_cameras_registered(len(cams.cameras))
    readiness.mark_broker_started()

    if entrance is not None:
        manager.set_identity_stack(
            face_detector=getattr(entrance, "detector", None),
            face_embedder=getattr(entrance, "embedder", None),
            template_store=store,
            consent_gate=gate,
            member_names=getattr(pipeline, "_member_names", None),
            emitter=getattr(pipeline, "emitter", None),
        )

    server = serve_health(settings.health_port, pipeline.health, pipeline=pipeline, readiness=readiness)
    readiness.mark_http_ready()

    pipeline.start()
    log.info("edge-ai.started", cameras=len(cams.cameras), health_port=settings.health_port)

    # Preflight verification loop: wait up to 10s for physical camera handle & first frame
    preflight_deadline = time.time() + 10.0
    while time.time() < preflight_deadline:
        is_ready, reason = readiness.evaluate_live_state(pipeline, manager)
        if is_ready:
            log.info("edge-ai.readiness_passed", uptime_s=round(time.time() - readiness.started_at, 2))
            break
        time.sleep(0.2)
    else:
        log.warning("edge-ai.preflight_pending", reason=readiness.not_ready_reason)

    shutdown_called = False

    def do_shutdown() -> None:
        nonlocal shutdown_called
        if shutdown_called:
            return
        shutdown_called = True
        log.info("edge-ai.shutting_down", reason="signal_or_exit")

        # 1. Stop new HTTP camera subscriptions
        try:
            manager.close_subscriptions()
        except Exception:
            pass

        # 2. Stop pipeline workers
        try:
            pipeline.stop()
        except Exception:
            pass

        # 3. Stop camera consumers
        try:
            manager.stop_all_consumers()
        except Exception:
            pass

        # 4. Release physical camera handle
        try:
            manager.release_physical_handles()
        except Exception:
            pass
        if sys.platform == "win32":
            time.sleep(0.15)

        # 5. Close HTTP server
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass

        # 6. Release singleton lock
        try:
            lock.release()
        except Exception:
            pass

        # 7. Flush logs and queued events
        try:
            logging.shutdown()
        except Exception:
            pass

    def _sig_handler(signum: int, frame: Any) -> None:
        do_shutdown()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import ctypes

            def _console_ctrl_handler(dwCtrlType: int) -> int:
                do_shutdown()
                return 1

            HANDLER_ROUTINE = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
            _handler_func = HANDLER_ROUTINE(_console_ctrl_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_handler_func, True)
        except Exception:
            pass

    try:
        while not shutdown_called:
            time.sleep(0.5)
            readiness.evaluate_live_state(pipeline, manager)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        do_shutdown()

    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from edge_ai.config import load_cameras
    from edge_ai.tools.simulate import run_simulation

    settings, cams, profiles = _load_runtime()
    if args.cameras:
        cams = load_cameras(Path(args.cameras))
    _configure_logging(settings.log_level)
    return run_simulation(settings, cams, profiles, seconds=args.seconds, sync=not args.no_sync)


def _orphan_template_listener(store: EncryptedTemplateStore) -> RejectionListener:
    """Delete the local template when the cloud refuses its biometric.enrolled for good."""

    def on_rejected(event: dict[str, Any], code: str | None, reason: str | None) -> None:
        if event.get("event_type") == "biometric.enrolled":
            store.delete_refs([str(event.get("payload", {}).get("template_ref", ""))])

    return on_rejected


def cmd_drain(args: argparse.Namespace) -> int:
    from edge_ai.config import Settings
    from edge_ai.events.queue import PersistentEventQueue
    from edge_ai.events.sync import SyncClient

    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.log_level)
    q = PersistentEventQueue(settings.data_dir / "events.sqlite")
    client = SyncClient(
        q, settings.ingest_url, str(settings.device_id), settings.device_secret, settings.sync_batch_size
    )
    key = settings.biometric_key
    if key is not None:
        from edge_ai.identity.face_store import EncryptedTemplateStore

        client.add_rejection_listener(
            _orphan_template_listener(EncryptedTemplateStore(settings.data_dir / "biometrics.sqlite", key))
        )
    results = client.drain(max_batches=1000)
    print(
        f"depth_before={sum(r.sent for r in results)} ok_batches={sum(1 for r in results if r.ok)} "
        f"depth_after={q.depth()} dead_letter={q.dead_letter_depth()} device_retired={client.retired} "
        f"last_error={client.last_error}"
    )
    return 0 if q.depth() == 0 else 1


def cmd_sync(args: argparse.Namespace) -> int:
    from edge_ai.config import Settings
    from edge_ai.events.queue import PersistentEventQueue
    from edge_ai.identity.enrollment import apply_cloud_revocations, reconcile_templates

    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.log_level)
    gate, store, _entrance, client = _build_identity_stack(settings)
    # Same order as Pipeline.apply_config_sync: cut-off, then pending refs, then the request.
    requested_at = time.time()
    pending: set[str] = set()
    if store is not None:
        q = PersistentEventQueue(settings.data_dir / "events.sqlite")
        pending = {str(p.get("template_ref")) for p in q.pending_payloads("biometric.enrolled")}
    snap = client.fetch()
    if snap is None:
        print(f"sync failed: {client.last_error}")
        return 1
    client.apply_consents(snap, gate)
    deleted = 0
    if store is not None and snap.active_template_refs is not None:
        deleted = reconcile_templates(
            store=store,
            active_refs=snap.active_template_refs,
            revoked_refs=snap.revoked_template_refs,
            pending_refs=pending,
            enrolled_before=requested_at,
        )
    elif store is not None:
        deleted = apply_cloud_revocations(revoked_refs=snap.revoked_template_refs, store=store)
    print(
        f"synced: cameras={len(snap.cameras)} equipment={len(snap.equipment)} exercises={len(snap.exercises)} "
        f"members_with_consent={len(snap.consents)} templates_deleted_locally={deleted}"
    )
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    import json
    from dataclasses import asdict

    from edge_ai.camera.onvif_discovery import discover

    result = discover(timeout_s=args.seconds, interface_ip=args.interface)
    print(
        json.dumps(
            {
                "duration_s": result.duration_s,
                "responses": result.raw_responses,
                "devices": [asdict(d) for d in result.devices],
            },
            indent=2,
        )
    )
    return 0


def cmd_enroll(args: argparse.Namespace) -> int:
    from edge_ai.config import Settings
    from edge_ai.events.builder import EventBuilder
    from edge_ai.events.queue import PersistentEventQueue
    from edge_ai.identity.enrollment import EnrollmentError, enroll_member, load_images
    from edge_ai.identity.face_pipeline import HaarFaceDetector, OnnxFaceEmbedder

    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.log_level)
    gate, store, _entrance, client = _build_identity_stack(settings)
    if store is None or not settings.face_model_path:
        print("enroll: EDGE_BIOMETRIC_KEY_B64 and EDGE_FACE_MODEL_PATH must be configured", file=sys.stderr)
        return 2
    snap = client.fetch_or_cached()
    if client.retired:
        # fetch() saw 403 device_retired; a still-fresh cache must not let a retired device enrol.
        print("enroll: the cloud retired this device; enrolment is refused", file=sys.stderr)
        return 1
    if snap is not None:
        client.apply_consents(snap, gate)
    queue = PersistentEventQueue(settings.data_dir / "events.sqlite")
    emitter = EventBuilder(queue, settings.device_id, settings.branch_id)
    try:
        res = enroll_member(
            member_id=args.member,
            images=load_images([Path(p) for p in args.image]),
            detector=HaarFaceDetector(),
            embedder=OnnxFaceEmbedder(str(settings.face_model_path)),
            store=store,
            consent=gate,
            emitter=emitter,
        )
    except EnrollmentError as exc:
        print(f"enroll failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"enrolled template_ref={res.template_ref} images_used={res.images_used} quality={res.quality}; "
        "event queued (run `edge-ai drain`)"
    )
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    from edge_ai.config import Settings
    from edge_ai.events.builder import EventBuilder
    from edge_ai.events.queue import PersistentEventQueue
    from edge_ai.identity.enrollment import revoke_member, revoke_undecryptable

    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.log_level)
    if bool(args.member) == bool(args.undecryptable):
        print("revoke: give exactly one of --member ID or --undecryptable", file=sys.stderr)
        return 2
    _gate, store, _entrance, _client = _build_identity_stack(settings)
    if store is None:
        print("revoke: EDGE_BIOMETRIC_KEY_B64 (or EDGE_BIOMETRIC_KEY_FILE) must be configured", file=sys.stderr)
        return 2
    queue = PersistentEventQueue(settings.data_dir / "events.sqlite")
    emitter = EventBuilder(queue, settings.device_id, settings.branch_id)
    # A running `edge-ai run` notices the store generation change and reloads its gallery before
    # its next match, so the deletion takes effect without a restart.
    if args.undecryptable:
        n = revoke_undecryptable(store=store, emitter=emitter, reason=args.reason or "key_rotation")
        print(f"revoked {n} undecryptable template(s)")
        return 0
    n = revoke_member(member_id=args.member, store=store, emitter=emitter, reason=args.reason or "staff_request")
    print(f"revoked {n} template(s) for member {args.member}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="edge-ai")
    sub = p.add_subparsers(dest="cmd", required=True)

    sy = sub.add_parser("sync")
    sy.set_defaults(fn=cmd_sync)

    dc = sub.add_parser("discover")
    dc.add_argument("--seconds", type=float, default=3.0)
    dc.add_argument("--interface", help="local IP of the camera-VLAN interface")
    dc.set_defaults(fn=cmd_discover)

    en = sub.add_parser("enroll")
    en.add_argument("--member", required=True)
    en.add_argument("--image", action="append", required=True)
    en.set_defaults(fn=cmd_enroll)

    rv = sub.add_parser("revoke")
    rv.add_argument("--member")
    rv.add_argument("--undecryptable", action="store_true", help="delete templates the current key cannot decrypt")
    rv.add_argument(
        "--reason",
        default=None,
        choices=["consent_withdrawn", "member_request", "staff_request", "key_rotation", "retired_device"],
        help="default: staff_request (--member) / key_rotation (--undecryptable)",
    )
    rv.set_defaults(fn=cmd_revoke)

    pr = sub.add_parser("probe")
    pr.add_argument("url", nargs="?")
    pr.add_argument("--cameras")
    pr.add_argument("--secrets")
    pr.add_argument("--seconds", type=float, default=15.0)
    pr.set_defaults(fn=cmd_probe)

    rn = sub.add_parser("run")
    rn.add_argument("--detector", help="path to ONNX person detector")
    rn.set_defaults(fn=cmd_run)

    sm = sub.add_parser("simulate")
    sm.add_argument("--seconds", type=float, default=90.0)
    sm.add_argument("--no-sync", action="store_true")
    sm.add_argument("--cameras", help="cameras.yaml with real camera/zone/equipment ids (default EDGE_CAMERAS_FILE)")
    sm.set_defaults(fn=cmd_simulate)

    dr = sub.add_parser("drain")
    dr.set_defaults(fn=cmd_drain)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
