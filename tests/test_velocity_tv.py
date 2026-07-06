"""Time-varying velocity — penalized-spline detector tests (synthetic, no data/lexicon)."""
import numpy as np

from pipeline.research.velocity_tv import (
    diff_penalty,
    fit_penalized,
    instantaneous_velocity,
    rbf_basis,
)


def _fit(t, y, n_centers=6, width=8.0, lam=12.0):
    centers = np.linspace(t[0], t[-1], n_centers)
    B, dB = rbf_basis(t, centers, width)
    D = diff_penalty(len(centers), 2)
    fit = fit_penalized(y, B, D, lam)
    v, se = instantaneous_velocity(dB, fit)
    return v, se, fit


def test_rbf_derivative_matches_finite_difference():
    t = np.linspace(0, 30, 200)
    B, dB = rbf_basis(t, centers=[5.0, 15.0, 25.0], width=6.0)
    fd = np.gradient(B[:, 1], t)                 # numeric d/dt of the middle basis
    assert np.max(np.abs(fd - dB[:, 1])) < 1e-2


def test_diff_penalty_shape():
    D = diff_penalty(6, 2)
    assert D.shape == (4, 6)


def test_recovers_constant_velocity():
    # linear log-rate: slope 0.1 => constant instantaneous velocity ~0.1
    t = np.arange(31, dtype=float)
    y = 0.1 * t
    v, se, fit = _fit(t, y)
    assert abs(np.mean(v) - 0.1) < 0.03
    assert fit["edf"] < 6.0                       # low effective df (anti-overfit)


def test_recovers_sign_change():
    # tent: rises then falls => velocity positive early, negative late
    t = np.arange(31, dtype=float)
    y = np.concatenate([0.1 * np.arange(16), 0.1 * np.arange(15, 0, -1)])
    v, se, fit = _fit(t, y)
    assert v[2] > 0
    assert v[-3] < 0
