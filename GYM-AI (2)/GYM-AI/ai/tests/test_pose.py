import numpy as np

from ai.core.pose import coco2h36m


def test_coco2h36m_mapping_and_midpoints():
    # one frame, each COCO joint gets a unique x value = its index, conf = index/100
    x = np.zeros((1, 17, 3))
    x[0, :, 0] = np.arange(17)
    x[0, :, 2] = np.arange(17) / 100.0
    y = coco2h36m(x)

    assert y.shape == (1, 17, 3)
    # direct joints
    assert y[0, 1, 0] == 12   # R hip
    assert y[0, 4, 0] == 11   # L hip
    assert y[0, 9, 0] == 0    # nose
    assert y[0, 11, 0] == 5 and y[0, 14, 0] == 6    # shoulders
    assert y[0, 12, 0] == 7 and y[0, 15, 0] == 8    # elbows
    assert y[0, 13, 0] == 9 and y[0, 16, 0] == 10   # wrists
    # midpoints average coordinates AND confidence
    assert y[0, 0, 0] == (11 + 12) / 2 and np.isclose(y[0, 0, 2], (0.11 + 0.12) / 2)   # pelvis
    assert y[0, 8, 0] == (5 + 6) / 2                                                   # thorax
    assert y[0, 7, 0] == (y[0, 0, 0] + y[0, 8, 0]) / 2                                 # spine
    assert y[0, 10, 0] == (1 + 2) / 2                                                  # head (mid-eye)


def test_coco2h36m_left_right_not_swapped():
    x = np.zeros((1, 17, 3))
    x[0, 5, 0] = -1.0   # COCO L shoulder on the left (negative x)
    x[0, 6, 0] = +1.0   # COCO R shoulder on the right
    y = coco2h36m(x)
    assert y[0, 11, 0] == -1.0   # H36M L_SHO (idx 11)
    assert y[0, 14, 0] == +1.0   # H36M R_SHO (idx 14)


# ----------------------------------------------------------------------------- product detector
import os  # noqa: E402

import pytest  # noqa: E402

from ai.core.pose import YOLO_WEIGHTS, YoloTopK  # noqa: E402
from ai.tests.conftest import REPO_ROOT  # noqa: E402

VIDEO = os.path.join(REPO_ROOT, "ai", "data", "seated_row", "asymmetry", "asymmetry_02.mp4")
CACHE = os.path.join(REPO_ROOT, "ai", "outputs", "phase28", "2d_asymmetry_02.npz")


def test_yolo_missing_weights_raises_explicitly(tmp_path):
    with pytest.raises(FileNotFoundError, match="YOLO weights not found"):
        YoloTopK(weights=str(tmp_path / "nope.pt"))


@pytest.mark.skipif(not os.path.exists(YOLO_WEIGHTS),
                    reason="YOLO weights not downloaded (python -m ai.scripts.download_checkpoints)")
def test_product_detector_turns_off_ultralytics_analytics_for_this_process():
    from ultralytics.utils import events
    events.events.enabled = True          # Ultralytics' default when online with `sync` on
    YoloTopK(device="cpu")
    assert events.events.enabled is False


_NET_PROBE = r'''
import socket
calls = []
def rec(name, orig):
    def f(*a, **k):
        calls.append((name, repr(a[:2])))
        return orig(*a, **k)
    return f
socket.getaddrinfo = rec("getaddrinfo", socket.getaddrinfo)
socket.create_connection = rec("create_connection", socket.create_connection)
socket.socket.connect = rec("connect", socket.socket.connect)
socket.socket.connect_ex = rec("connect_ex", socket.socket.connect_ex)
import numpy as np
from ai.core.pose import YoloTopK
YoloTopK(device="cpu")(np.zeros((480, 640, 3), dtype=np.uint8))
print("CALLS", len(calls), calls)
'''


@pytest.mark.skipif(not os.path.exists(YOLO_WEIGHTS),
                    reason="YOLO weights not downloaded (python -m ai.scripts.download_checkpoints)")
def test_product_detector_makes_no_network_calls():
    """docs/PRIVACY.md: nothing leaves the machine from the product path. Runs in a fresh
    interpreter (Ultralytics' online check runs once, at its first import) with YOLO_OFFLINE unset,
    recording every socket entry point, DNS lookups included."""
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k != "YOLO_OFFLINE"}
    out = subprocess.run([sys.executable, "-c", _NET_PROBE], cwd=REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("CALLS")][-1]
    assert line.startswith("CALLS 0 "), line


@pytest.mark.skipif(not os.path.exists(VIDEO), reason="raw seated-row video not present (git-ignored)")
def test_per_frame_topk_matches_cached_stream_mode_output():
    """Per-frame YoloTopK (live mode) vs the cached video-stream-mode yolo_top8 output every
    product number was computed from — same frames, same weights, same imgsz, CPU."""
    import cv2
    z = np.load(CACHE)
    det = YoloTopK(device="cpu")
    cap = cv2.VideoCapture(VIDEO)
    n = 30
    for t in range(n):
        ok, fr = cap.read()
        assert ok
        k, b = det(fr)
        ck, cb = z["kt"][t], z["bt"][t]
        assert np.array_equal(np.isnan(b), np.isnan(cb)), t
        assert np.allclose(np.nan_to_num(k), np.nan_to_num(ck), atol=1e-3), (t, float(np.nanmax(np.abs(k - ck))))
        assert np.allclose(np.nan_to_num(b), np.nan_to_num(cb), atol=1e-3), t
    cap.release()
