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
