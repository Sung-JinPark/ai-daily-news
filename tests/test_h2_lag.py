"""PREP-1 — H2 lag detector tests (synthetic series, no data, no lexicon)."""
import numpy as np

from pipeline.research.h2_lag import cross_correlation_lag


def test_detects_known_lag():
    # distinctive spike pattern (non-monotonic, so only the true lag aligns);
    # response follows driver by exactly 3 days
    driver = np.array([0, 0, 1, 0, 0, 0, 2, 0, 0, 0], dtype=float)
    response = np.array([0, 0, 0, 0, 0, 1, 0, 0, 0, 2], dtype=float)  # response[3:] == driver[:7]
    r = cross_correlation_lag(driver, response, max_lag=5)
    assert r["lag"] == 3
    assert r["corr"] > 0.99


def test_zero_lag_when_synchronous():
    x = np.array([1, 3, 2, 5, 4, 6, 3, 7], dtype=float)
    r = cross_correlation_lag(x, x.copy(), max_lag=4)
    assert r["lag"] == 0
    assert r["corr"] > 0.99


def test_constant_series_nan_corr():
    r = cross_correlation_lag(np.ones(8), np.arange(8, dtype=float), max_lag=3)
    assert np.isnan(r["corr"])
