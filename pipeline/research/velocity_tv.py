"""Time-varying velocity — a penalized-spline extension of ML-A's constant beta.

ML-A fits a single log-linear slope (constant velocity) per concept. Here we let
velocity vary in time: we smooth the **coverage-robust** log-rate series with a
penalized radial-basis (P-spline-style) smoother and read the **instantaneous
velocity** off the analytic derivative of the smooth.

Runs on the coverage-robust rate (never raw counts); days are weighted by exposure
so low-coverage days contribute less. **Overfitting control (a ~1-month panel is
short for time-varying trends): few basis centers + a strong 2nd-difference penalty
=> small effective df; report wide CIs; results are PRELIMINARY and confirmed at
MAT-1 (D+90).** We also check that the mean instantaneous velocity agrees with
ML-A's constant beta.

Private (per-concept) output. Deterministic. Usage: python -m pipeline.research.velocity_tv
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pipeline.research.trend_model import load_panel, corrected_rate_series, fit_velocities

OUT_DIR = Path("data") / "research_private" / "analysis" / "velocity-tv-2026-07-06"
N_CENTERS = 6      # few centers -> low effective df (anti-overfit on short panel)
WIDTH = 8.0        # RBF width (days)
LAM = 12.0         # strong 2nd-difference penalty
MIN_ACTIVE_DAYS = 8
EPS = 1e-3


# ---------- pure functions (unit-tested with synthetic series) ----------

def rbf_basis(t, centers, width: float):
    """Gaussian RBF design matrix B and its time-derivative dB."""
    t = np.asarray(t, dtype=float)[:, None]
    c = np.asarray(centers, dtype=float)[None, :]
    B = np.exp(-0.5 * ((t - c) / width) ** 2)
    dB = B * (-(t - c) / width ** 2)
    return B, dB


def diff_penalty(m: int, order: int = 2) -> np.ndarray:
    D = np.eye(m)
    for _ in range(order):
        D = np.diff(D, axis=0)
    return D


def fit_penalized(y, B, D, lam: float, w=None) -> dict:
    """Weighted penalized least squares: coef, effective df, coef covariance."""
    y = np.asarray(y, dtype=float)
    n, m = B.shape
    w = np.ones(n) if w is None else np.asarray(w, dtype=float)
    Wv = w[:, None]
    BtWB = B.T @ (Wv * B)
    A = BtWB + lam * (D.T @ D)
    Ainv = np.linalg.inv(A)
    coef = Ainv @ (B.T @ (w * y))
    H = B @ Ainv @ (B.T * w)            # hat matrix
    edf = float(np.trace(H))
    resid = y - B @ coef
    dof = max(n - edf, 1.0)
    sigma2 = float((w * resid) @ resid / dof)
    cov = sigma2 * (Ainv @ BtWB @ Ainv)
    return {"coef": coef, "edf": edf, "sigma2": sigma2, "cov": cov}


def instantaneous_velocity(dB, fit: dict):
    """v(t) = dB·coef and its standard error (from coef covariance)."""
    v = dB @ fit["coef"]
    var = np.einsum("ij,jk,ik->i", dB, fit["cov"], dB)
    return v, np.sqrt(np.maximum(var, 0.0))


# ---------- run ----------

def _smooth_velocity(rate, exposure):
    n = len(rate)
    t = np.arange(n)
    centers = np.linspace(0, n - 1, N_CENTERS)
    B, dB = rbf_basis(t, centers, WIDTH)
    D = diff_penalty(len(centers), 2)
    y = np.log(np.asarray(rate, float) + EPS)
    w = np.asarray(exposure, float) + 1e-6
    fit = fit_penalized(y, B, D, LAM, w)
    v, se = instantaneous_velocity(dB, fit)
    return v, se, fit["edf"]


def run(out_dir: Path = OUT_DIR) -> dict:
    df = load_panel()
    rates = corrected_rate_series(df)
    vel = fit_velocities(df, "enriched", "n_bodies")
    beta_by = {r["concept_id"]: float(r["velocity"]) for _, r in vel.iterrows()}

    results, mean_v, betas = [], [], []
    for cid, g in rates.sort_values("t").groupby("concept_id"):
        g = g.sort_values("t")
        if int((g["enriched"] > 0).sum()) < MIN_ACTIVE_DAYS:
            continue
        v, se, edf = _smooth_velocity(g["rate"].to_numpy(), g["n_bodies"].to_numpy())
        mv = float(np.mean(v))
        rec = {"concept_id": cid, "edf": round(edf, 2), "mean_velocity": mv,
               "v_start": float(v[0]), "v_end": float(v[-1]),
               "mean_se": float(np.mean(se)), "mla_beta": beta_by.get(cid)}
        results.append(rec)
        if rec["mla_beta"] is not None:
            mean_v.append(mv); betas.append(rec["mla_beta"])
    agree = float(np.corrcoef(mean_v, betas)[0, 1]) if len(mean_v) > 2 else None
    report = {"as_of": "2026-07-06", "n_concepts": len(results),
              "mean_edf": round(float(np.mean([r["edf"] for r in results])), 2) if results else None,
              "mean_velocity_vs_MLA_beta_pearson": agree,
              "POWER": "PRELIMINARY — 31-day panel is short for time-varying velocity; strong penalty "
                       "(low edf), wide CIs. Confirm at MAT-1 (D+90).",
              "overfit_control": f"{N_CENTERS} RBF centers, 2nd-diff penalty lam={LAM} -> small edf"}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "velocity_tv_prelim.json").write_text(
        json.dumps({"report": report, "per_concept": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[vtv] {len(results)} concepts · mean edf={report['mean_edf']} · "
          f"mean-v vs ML-A beta Pearson={agree} (PRELIMINARY) · wrote {out_dir}")
    return report


if __name__ == "__main__":
    run()
