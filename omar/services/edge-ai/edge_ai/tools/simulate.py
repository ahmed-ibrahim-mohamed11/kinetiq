"""
End-to-end SIMULATION (clearly synthetic — no cameras, no real people).

Drives the real Pipeline with a scripted scenario:
  1. a person appears on the entrance camera and is "identified" as the demo member (QR method);
  2. walks into the leg-press zone seen by TWO cameras (A and B);
  3. performs 2 sets of synthetic knee-angle motion; both cameras feed signals →
     dedup must count each rep once;
  4. rests, leaves, and checks out.

The scenario exercises contracts, queue, fusion, rep/set logic and the sync
client against a local Supabase. It proves plumbing, not vision accuracy.

Overrides (Settings, all optional; defaults reproduce the seed-based run):
  EDGE_SIM_MEMBER_ID    member to simulate (default: seed demo member)
  EDGE_SIM_CONSENT_ID   that member's cctv_workout_tracking consent record (sent with identity.checkin)
  EDGE_SIM_CHECKIN      qr (emit identity.checkin) | skip (already checked in at the kiosk: bind only)
  EDGE_SIM_CHECKOUT     edge (emit identity.checkout) | skip (the member checks out in the app)
Camera, zone and equipment ids come from the cameras YAML (`simulate --cameras FILE`).
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from uuid import UUID

import structlog

from edge_ai.config import CamerasFile, Settings
from edge_ai.contracts.events_v1 import IdentityCheckOutPayload
from edge_ai.pipeline.orchestrator import Pipeline
from edge_ai.reps.profiles import RepProfile

log = structlog.get_logger("simulate")

DEMO_MEMBER_ID = "33333333-3333-4333-8333-333333333333"
DEMO_CONSENT_ID = "55555555-5555-4555-8555-555555555556"


def _knee_angle(t_in_set: float, period: float = 2.5) -> float:
    # 165° (extended) ↔ 85° (flexed), one rep per `period` seconds
    return 125 + 40 * math.cos(2 * math.pi * t_in_set / period)


def run_simulation(
    settings: Settings, cams: CamerasFile, profiles: dict[str, RepProfile], seconds: float = 90.0, sync: bool = True
) -> int:
    member_id = str(settings.sim_member_id)
    consent_id = str(settings.sim_consent_id)
    pipeline = Pipeline(settings, cams.cameras, cams.exercise_ids, profiles, detector=None, pose=None)
    entrance = next((c for c in cams.cameras if c.purpose == "entrance"), None) or next(
        (c for c in cams.cameras if c.purpose == "mixed"), None
    )
    if entrance is None:
        raise SystemExit("simulation needs an entrance (or mixed) camera in the cameras YAML")
    floor = [c for c in cams.cameras if c.purpose == "floor"]
    if len(floor) < 2:
        raise SystemExit("simulation needs two floor cameras sharing an equipment zone (see cameras.example.yaml)")
    cam_a, cam_b = floor[0], floor[1]
    zone_a = next((z for z in cam_a.zones if z.exercise_slug == "leg-press"), None)
    zone_b = next((z for z in cam_b.zones if z.exercise_slug == "leg-press"), None)
    if zone_a is None or zone_b is None:
        raise SystemExit("simulation needs a leg-press equipment zone on each of the first two floor cameras")

    def centre(poly: list[tuple[float, float]]) -> tuple[float, float, float, float]:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        return (cx - 40, cy - 150, cx + 40, cy)  # box whose foot point is the polygon centre

    box_a, box_b = centre(zone_a.polygon), centre(zone_b.polygon)
    t0 = time.time()
    dt = 0.1  # 10 Hz observations

    # 1. entrance
    pipeline.on_observation(entrance, 1, (900.0, 300.0, 1000.0, 900.0), 0.95, t0, None, None)
    gid = pipeline.registry.global_for_local(str(entrance.id), 1).global_id  # type: ignore[union-attr]
    # checkin=skip: the member is already checked in at the kiosk; the edge only binds the identity,
    # so the workout events carry member_id and the cloud attaches them to the existing session.
    bound = pipeline.bind_member(
        gid, member_id, 1.0, "qr", entrance.id, consent_id, None, emit_checkin=settings.sim_checkin == "qr"
    )
    assert bound
    pipeline.on_local_track_lost(entrance, 1, t0 + 2.0)

    # 2. appears on both floor cameras; no embeddings, so association must be explicit here:
    # in production a ReID embedding links them. For the simulation we attach both local tracks
    # to the known global identity directly (the registry API used by the ReID stage).
    reg = pipeline.registry
    g = reg.get(gid)
    assert g is not None
    for cam, lid in ((cam_a, 7), (cam_b, 3)):
        reg._local_to_global[(str(cam.id), lid)] = gid  # noqa: SLF001 — simulation shortcut
        g.local_tracks.add((str(cam.id), lid))

    # 3. two sets × ~8 reps, 12 s rest
    t = t0 + 3.0
    plan = [(0.0, 20.0), (32.0, 52.0)]  # (start, end) of movement relative to t
    end_t = t + 60.0
    while t < end_t:
        rel = t - (t0 + 3.0)
        moving = any(s <= rel < e for s, e in plan)
        set_start = next((s for s, e in plan if s <= rel < e), None)
        angle = _knee_angle(rel - set_start) if moving and set_start is not None else 165.0
        pipeline.on_observation(cam_a, 7, box_a, 0.95, t, (angle, 0.9), None)
        # camera B sees the same rep ~120 ms later with slightly lower confidence
        pipeline.on_observation(cam_b, 3, box_b, 0.9, t + 0.12, (angle, 0.8), None)
        for st in pipeline.states.values():
            st.tick(t)
        t += dt

    # 4. leave + checkout
    pipeline.on_local_track_lost(cam_a, 7, t)
    pipeline.on_local_track_lost(cam_b, 3, t)
    if settings.sim_checkout == "edge":
        pipeline.emitter.emit(
            "identity.checkout",
            IdentityCheckOutPayload(member_id=UUID(member_id), method="qr", confidence=1.0, camera_id=None),
            datetime.fromtimestamp(t + 5, tz=timezone.utc),
        )
    pipeline.emit_heartbeat()

    h = pipeline.health()
    log.info(
        "simulation.done",
        queue_depth=h["queue_depth"],
        reps_admitted=h["reps_admitted"],
        duplicates_suppressed=h["reps_suppressed_duplicates"],
    )
    print(
        f"SIMULATION (synthetic data) → events queued={h['queue_depth']} reps_admitted={h['reps_admitted']} "
        f"duplicates_suppressed={h['reps_suppressed_duplicates']}"
    )
    if sync:
        results = pipeline.sync.drain(max_batches=100)
        ok = all(r.ok for r in results) and pipeline.queue.depth() == 0
        print(
            f"SYNC → batches={len(results)} ok={ok} remaining={pipeline.queue.depth()} "
            f"last_error={pipeline.sync.last_error}"
        )
        return 0 if ok else 1
    return 0
