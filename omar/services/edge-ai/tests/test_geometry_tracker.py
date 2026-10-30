import math

from edge_ai.detection.base import Detection
from edge_ai.geometry import box_foot_point, iou, joint_angle, point_in_polygon, polygon_area
from edge_ai.tracking.iou_tracker import IouTracker, TrackState

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def test_point_in_polygon_matches_ts_reference():
    assert point_in_polygon((5, 5), SQUARE)
    assert not point_in_polygon((15, 5), SQUARE)
    assert not point_in_polygon((-1, -1), SQUARE)
    assert polygon_area(SQUARE) == 100


def test_iou_and_angles():
    assert iou([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert iou([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    assert abs(iou([0, 0, 10, 10], [5, 0, 15, 10]) - 1 / 3) < 1e-9
    assert abs(joint_angle((0, 0), (1, 0), (2, 0)) - 180) < 1e-9
    assert abs(joint_angle((0, 1), (0, 0), (1, 0)) - 90) < 1e-9
    assert math.isnan(joint_angle((0, 0), (0, 0), (1, 1)))
    assert box_foot_point([0, 0, 10, 20]) == (5, 20)


def _walk(tracker: IouTracker, frames: int, start_x: float, dx: float, score: float = 0.9, t0: float = 0.0):
    tracks = []
    for i in range(frames):
        x = start_x + dx * i
        tracks = tracker.update([Detection((x, 100, x + 60, 260), score)], t0 + i * 0.1)
    return tracks


def test_single_person_keeps_one_id_while_moving():
    tr = IouTracker(min_hits=3)
    tracks = _walk(tr, 30, 100, 4)
    assert len(tracks) == 1
    assert tracks[0].track_id == 1
    assert tracks[0].state == TrackState.CONFIRMED
    assert tracks[0].tracking_confidence > 0.8


def test_two_people_get_two_ids_and_do_not_swap():
    tr = IouTracker()
    for i in range(20):
        dets = [
            Detection((100 + i * 3, 100, 160 + i * 3, 260), 0.9),
            Detection((600 - i * 3, 100, 660 - i * 3, 260), 0.9),
        ]
        tracks = tr.update(dets, i * 0.1)
    ids = sorted(t.track_id for t in tracks)
    assert ids == [1, 2]
    left = next(t for t in tracks if t.track_id == 1)
    assert left.box[0] < 300


def test_short_occlusion_recovers_same_id():
    tr = IouTracker(max_age=15)
    tracks = _walk(tr, 10, 100, 2)
    tid = tracks[0].track_id
    for i in range(5):  # occluded: no detections
        tr.update([], 1.0 + i * 0.1)
    tracks = tr.update([Detection((130, 100, 190, 260), 0.9)], 1.6)
    assert [t.track_id for t in tracks] == [tid]


def test_low_score_noise_never_spawns_tracks_but_sustains_existing():
    tr = IouTracker(high_score=0.5, min_hits=2)
    tr.update([Detection((0, 0, 50, 100), 0.3)], 0.0)
    assert tr.tracks == []
    _walk(tr, 5, 100, 1)
    tracks = tr.update([Detection((104, 100, 164, 260), 0.35)], 0.6)  # low score, still matched
    assert len(tracks) == 1 and tracks[0].time_since_update == 0


def test_lost_track_is_pruned():
    tr = IouTracker(max_age=3)
    _walk(tr, 5, 100, 1)
    for i in range(20):
        tr.update([], 1.0 + i * 0.1)
    assert tr.tracks == []
