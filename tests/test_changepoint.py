"""H1-preliminary — tests for the coverage-robust take-off / burst detectors.
Pure functions on synthetic series (no data, no lexicon)."""
import numpy as np

from pipeline.research.changepoint import burst_days, level_shift


def test_level_shift_detects_upward_step():
    y = np.array([0.1] * 10 + [0.9] * 10)  # clear upward level shift at index 10
    ls = level_shift(y)
    assert ls is not None
    assert 8 <= ls["index"] <= 12
    assert ls["shift"] > 0.5
    assert ls["sse_reduction"] > 0.8


def test_level_shift_flat_series_low_reduction():
    rng = np.random.default_rng(0)
    y = 0.5 + 0.01 * rng.normal(size=20)  # essentially flat
    ls = level_shift(y)
    assert ls is not None
    assert ls["sse_reduction"] < 0.3   # no meaningful shift


def test_level_shift_too_short_returns_none():
    assert level_shift(np.array([1.0, 2.0, 3.0])) is None


def test_burst_days_flags_spikes():
    y = np.array([0.1] * 10 + [1.0] + [0.1] * 9)  # one spike at index 10
    b = burst_days(y, z=2.0)
    assert 10 in b


def test_burst_days_constant_series_empty():
    assert burst_days(np.array([0.3] * 15)) == []
