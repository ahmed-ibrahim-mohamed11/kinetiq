"""ai.core.config.Cfg validation (Phase 3 P3.7: an invalid config must fail at construction,
with a clear message naming the field, not with a confusing downstream error)."""
import pytest

from ai.core.config import Cfg, GRID


def test_default_cfg_is_valid():
    Cfg()  # must not raise


@pytest.mark.parametrize("field,bad", [
    ("smooth_hz", 0.0), ("smooth_hz", -1.0), ("detrend_s", 0.0), ("detrend_s", -5.0),
    ("alpha", -0.1), ("conf_thr", -0.01), ("conf_thr", 1.01), ("dur_weight", -1.0),
    ("dur_sigma_floor", 0.0), ("emis_var_floor", 0.0), ("min_dur_frac", 0.0),
    ("min_dur_frac", 1.5), ("direct_gap_frac", -0.1), ("cens_q", -0.01), ("cens_q", 1.0),
    ("peak_sd_floor", 0.0),
])
def test_cfg_rejects_invalid_field(field, bad):
    with pytest.raises(ValueError, match=field):
        Cfg(**{field: bad})


def test_cfg_every_grid_combination_is_valid():
    """Every value this project's own hyper-parameter GRID has ever proposed must pass."""
    for sm in GRID["smooth_hz"]:
        for dt in GRID["detrend_s"]:
            for dw in GRID["dur_weight"]:
                for sf in GRID["dur_sigma_floor"]:
                    for tn in GRID["tempo_norm"]:
                        Cfg(smooth_hz=sm, detrend_s=dt, dur_weight=dw, dur_sigma_floor=sf,
                           tempo_norm=tn)  # must not raise
