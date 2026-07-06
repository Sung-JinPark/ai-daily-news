"""ML-A — tests for the coverage-robust count model.

Pure-stats tests on SYNTHETIC counts (fake concept ids, generated series) — no
real lexicon terms (tests/ is public). Fixed numpy seeds for determinism.
Validates: the coverage offset normalises rate (absorbs an exposure trend),
NB detects overdispersion vs Poisson, and velocity sign tracks the trend.
"""
import numpy as np
import pandas as pd

from pipeline.research.trend_model import fit_velocities, overdispersion_test


def test_offset_absorbs_exposure_trend():
    # constant true rate, but exposure rises with time. With the log(exposure)
    # offset the fitted velocity must be ~0 (CI covers 0); without it (flat
    # exposure) the same data shows a spurious upward trend.
    rng = np.random.default_rng(1)
    t = np.arange(30)
    exposure = 10.0 + 3.0 * t
    counts = rng.poisson(0.2 * exposure)  # constant rate 0.2 per covered article
    df = pd.DataFrame({"concept_id": "zzsynthetic_flat", "t": t,
                       "y": counts, "expo": exposure, "flat": 1.0})

    v = fit_velocities(df, "y", "expo", min_nonzero=3)
    assert bool(v.iloc[0]["fit"])
    assert v.iloc[0]["ci_lo"] < 0 < v.iloc[0]["ci_hi"]      # no significant trend
    assert abs(v.iloc[0]["velocity"]) < 0.05

    v_nooff = fit_velocities(df, "y", "flat", min_nonzero=3)  # exposure ignored
    assert v_nooff.iloc[0]["velocity"] > 0.02                 # spurious upward trend


def test_nb_detects_overdispersion():
    # Gamma-Poisson mixture => strong overdispersion.
    rng = np.random.default_rng(2)
    rows = []
    for c in range(4):
        for t in range(25):
            lam = rng.gamma(shape=1.0, scale=5.0)  # mean 5, var 25
            rows.append({"concept_id": f"zzc{c}", "t": t,
                         "enriched": int(rng.poisson(lam)), "n_bodies": 20})
    res = overdispersion_test(pd.DataFrame(rows))
    assert res["poisson_pearson_dispersion"] > 1.5
    assert res["lr_stat"] > 3.84
    assert res["nb_justified"] is True


def test_poisson_data_not_overdispersed():
    rng = np.random.default_rng(3)
    rows = [{"concept_id": f"zzc{c}", "t": t,
             "enriched": int(rng.poisson(5)), "n_bodies": 20}
            for c in range(4) for t in range(25)]
    res = overdispersion_test(pd.DataFrame(rows))
    assert res["poisson_pearson_dispersion"] < 1.4  # well-specified Poisson


def test_velocity_sign_matches_trend():
    rng = np.random.default_rng(4)
    t = np.arange(25)
    up = rng.poisson(np.exp(-1.0 + 0.1 * t))   # rising log-rate
    down = rng.poisson(np.exp(2.0 - 0.1 * t))  # falling log-rate
    vu = fit_velocities(pd.DataFrame({"concept_id": "zzup", "t": t, "y": up, "expo": 20.0}),
                        "y", "expo", min_nonzero=3)
    vd = fit_velocities(pd.DataFrame({"concept_id": "zzdown", "t": t, "y": down, "expo": 20.0}),
                        "y", "expo", min_nonzero=3)
    assert vu.iloc[0]["velocity"] > 0
    assert vd.iloc[0]["velocity"] < 0


def test_sparse_concept_not_forced():
    # a concept with too few non-zero days is reported not-fit, never forced
    df = pd.DataFrame({"concept_id": "zzsparse", "t": np.arange(20),
                       "y": [0] * 18 + [1, 1], "expo": 20.0})
    v = fit_velocities(df, "y", "expo", min_nonzero=5)
    assert bool(v.iloc[0]["fit"]) is False
    assert np.isnan(v.iloc[0]["velocity"])
