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
  edge-ai benchmark ...            see edge_ai.tools.benchmark
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from edge_ai.config import CamerasFile, Settings
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

    gate = ConsentGate(settings.data_dir / "consents.json")
    gate.load()
    sync = DeviceSyncClient(
        settings.effective_sync_url,
        str(settings.device_id),
        settings.device_secret,
        settings.data_dir / "device-sync.json",
    )
    store = None
    entrance = None
    key = settings.biometric_key
    if key is not None:
        from edge_ai.identity.face_store import EncryptedTemplateStore, FaceMatcher

        store = EncryptedTemplateStore(settings.data_dir / "biometrics.sqlite", key)
        if settings.face_model_path:
            from edge_ai.identity.entrance import EntranceIdentifier
            from edge_ai.identity.face_pipeline import HaarFaceDetector, OnnxFaceEmbedder

            entrance = EntranceIdentifier(
                HaarFaceDetector(), OnnxFaceEmbedder(str(settings.face_model_path)), FaceMatcher(store), gate
            )
    return gate, store, entrance, sync


def cmd_run(args: argparse.Namespace) -> int:
    from edge_ai.camera.rtsp_reader import RtspReader
    from edge_ai.config import build_authenticated_url, load_camera_credentials
    from edge_ai.health import serve_health
    from edge_ai.pipeline.orchestrator import Pipeline

    settings, cams, profiles = _load_runtime()
    _configure_logging(settings.log_level)
    log = structlog.get_logger("edge-ai")

    detector = pose = reid = None
    detector_path = args.detector or (str(settings.detector_model_path) if settings.detector_model_path else None)
    if detector_path:
        from edge_ai.detection.onnx_detector import OnnxPersonDetector

        detector = OnnxPersonDetector(detector_path)
        log.info("detector.loaded", model=detector_path, provider=detector.active_provider)
    else:
        log.warning(
            "detector.missing", msg="running without a detector: camera health only. Set EDGE_DETECTOR_MODEL_PATH"
        )
    if settings.pose_model_path:
        from edge_ai.pose.onnx_pose import YoloPoseOnnx

        pose = YoloPoseOnnx(str(settings.pose_model_path))
        log.info("pose.loaded", model=str(settings.pose_model_path))
    else:
        log.warning("pose.missing", msg="no pose model: equipment occupancy only, no rep counting")
    if settings.reid_model_path:
        from edge_ai.fusion.onnx_reid import OnnxReidEmbedder

        reid = OnnxReidEmbedder(str(settings.reid_model_path))
        log.info("reid.loaded", model=str(settings.reid_model_path))
    gate, store, entrance, config_sync = _build_identity_stack(settings)
    if entrance is None:
        log.warning("face.missing", msg="no face model/biometric key: entrance identification disabled (QR/NFC only)")

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
    creds = load_camera_credentials(settings.camera_secrets_file)
    for cam in cams.cameras:
        url = build_authenticated_url(cam.url, creds.get(cam.id))
        pipeline.add_worker(cam, RtspReader(url=url, name=cam.name))
    server = serve_health(settings.health_port, pipeline.health)
    pipeline.start()
    log.info("edge-ai.started", cameras=len(cams.cameras), health_port=settings.health_port)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.stop()
        server.shutdown()
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from edge_ai.tools.simulate import run_simulation

    settings, cams, profiles = _load_runtime()
    _configure_logging(settings.log_level)
    return run_simulation(settings, cams, profiles, seconds=args.seconds, sync=not args.no_sync)


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
    results = client.drain(max_batches=1000)
    print(
        f"depth_before={sum(r.sent for r in results)} ok_batches={sum(1 for r in results if r.ok)} "
        f"depth_after={q.depth()} last_error={client.last_error}"
    )
    return 0 if q.depth() == 0 else 1


def cmd_sync(args: argparse.Namespace) -> int:
    from edge_ai.config import Settings
    from edge_ai.identity.enrollment import apply_cloud_revocations

    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.log_level)
    gate, store, _entrance, client = _build_identity_stack(settings)
    snap = client.fetch()
    if snap is None:
        print(f"sync failed: {client.last_error}")
        return 1
    client.apply_consents(snap, gate)
    revoked = apply_cloud_revocations(revoked_refs=snap.revoked_template_refs, store=store) if store else 0
    print(
        f"synced: cameras={len(snap.cameras)} equipment={len(snap.equipment)} exercises={len(snap.exercises)} "
        f"members_with_consent={len(snap.consents)} members={len(snap.members)} templates_revoked_locally={revoked}"
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
    from edge_ai.identity.enrollment import revoke_member

    settings = Settings()  # type: ignore[call-arg]
    _configure_logging(settings.log_level)
    _gate, store, _entrance, _client = _build_identity_stack(settings)
    if store is None:
        print("revoke: EDGE_BIOMETRIC_KEY_B64 must be configured", file=sys.stderr)
        return 2
    queue = PersistentEventQueue(settings.data_dir / "events.sqlite")
    n = revoke_member(
        member_id=args.member,
        store=store,
        emitter=EventBuilder(queue, settings.device_id, settings.branch_id),
        reason=args.reason,
    )
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
    rv.add_argument("--member", required=True)
    rv.add_argument(
        "--reason",
        default="staff_request",
        choices=["consent_withdrawn", "member_request", "staff_request", "key_rotation", "retired_device"],
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
    sm.set_defaults(fn=cmd_simulate)

    dr = sub.add_parser("drain")
    dr.set_defaults(fn=cmd_drain)

    args = p.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
