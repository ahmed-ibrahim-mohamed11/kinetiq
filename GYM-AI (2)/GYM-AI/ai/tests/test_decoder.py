import numpy as np
import pytest

from ai.core.config import Cfg, PULL, REST, RET
from ai.core.decoder import (Decoder, build_tables, estimate_tempo,
                             hsmm_decode, peak_loglik)


def test_estimate_tempo_recovers_period():
    fps, period_s = 30.0, 1.5
    t = np.arange(0, 20, 1 / fps)
    fused = 0.5 + 0.5 * np.sin(2 * np.pi * t / period_s)
    est = estimate_tempo(fused, fps)
    assert est == pytest.approx(period_s, abs=2 / fps)


def test_estimate_tempo_none_on_flat_signal():
    assert estimate_tempo(np.zeros(300), 30.0) is None


def _toy_decoder():
    return Decoder(
        mu=np.array([[0.1, 0.0], [0.5, 1.2], [0.5, -1.2]]),
        var=np.full((3, 2), 0.05),
        dur_mu=np.log([0.35, 0.45]), dur_sigma=np.array([0.3, 0.3]),
        dur_min=np.array([0.15, 0.2]), rest_mean=0.2, p_direct=0.2,
        pk_mu=0.9, pk_sd=0.15, tempo_norm=True, ref_tempo_s=1.5, stats={})


def test_build_tables_pmf_normalised_and_min_enforced():
    dec = _toy_decoder()
    tabs = build_tables(dec, fps=30.0, T=300, scale=1.5)
    for k in (PULL, RET):
        lpmf = tabs[k]["lpmf"]
        total = np.logaddexp.reduce(lpmf[np.isfinite(lpmf)])
        assert total == pytest.approx(0.0, abs=1e-6)          # pmf sums to 1
        assert tabs[k]["dmin"] >= 2
        assert not np.isfinite(lpmf[: tabs[k]["dmin"]]).any() # below hard minimum impossible
    assert tabs[REST]["dmin"] == 1


def test_hsmm_decode_grammar_and_coverage():
    """Synthetic 2-rep emission pattern must decode into alternating REST/PULL/RET covering [0, T]."""
    dec = _toy_decoder()
    fps, scale = 30.0, 1.5
    T = 240
    fused = np.zeros(T)
    states = np.zeros(T, int)
    for s, p, e in [(60, 75, 95), (140, 155, 175)]:
        states[s:p] = PULL
        states[p:e] = RET
        fused[s:p] = np.linspace(0, 1, p - s, endpoint=False)
        fused[p:e] = np.linspace(1, 0, e - p, endpoint=False)
    E = np.full((T, 3), -5.0)
    E[np.arange(T), states] = 0.0                              # true state strongly favoured
    tabs = build_tables(dec, fps, T, scale)
    segs = hsmm_decode(E, tabs, dec.p_direct, 1.0, peak_loglik(dec, fused))

    assert segs[0][1] == 0 and segs[-1][2] == T                # full coverage
    for (k1, _, b1, _, _), (k2, a2, _, _, _) in zip(segs[:-1], segs[1:]):
        assert b1 == a2                                        # contiguous
        assert (k1, k2) in {(REST, PULL), (PULL, RET), (RET, REST), (RET, PULL)}  # grammar
    pulls = [s for s in segs if s[0] == PULL]
    assert len(pulls) == 2
    assert abs(pulls[0][1] - 60) <= 3 and abs(pulls[1][1] - 140) <= 3


def test_decoder_json_roundtrip():
    dec = _toy_decoder()
    d2 = Decoder.from_json(dec.to_json())
    np.testing.assert_allclose(d2.mu, dec.mu)
    np.testing.assert_allclose(d2.dur_mu, dec.dur_mu)
    assert d2.p_direct == dec.p_direct and d2.tempo_norm == dec.tempo_norm
