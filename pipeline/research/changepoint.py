"""H1-preliminary — coverage-robust concept take-off / burst detection.

H1 (micro-velocity): entity attention shows sharp asymmetric bursts / take-offs
around discrete events. This is the first *findings* layer, and it runs on the
**coverage-robust** rate series (ML-A / COV-1) — NEVER raw counts, which would
detect coverage swings instead of concept dynamics.

Two simple, auditable detectors (no black box, small-panel-appropriate):
  * `level_shift` — a single best changepoint (binary segmentation minimising
    within-segment SSE) → an upward level shift = a "take-off".
  * `burst_days` — days whose rate exceeds mean + z·std (Kleinberg-lite).

Preliminary: on a ~1-month panel most concepts are flat (ML-A found ~0 significant
trends), so this DEMONSTRATES the pipeline and flags candidates; formal H1 (bursts
aligned to discrete events, asymmetry) needs event annotations + a mature panel
(MAT-1, D+90). Private (concept-level) output; deterministic.

Usage: python -m pipeline.research.changepoint
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pipeline.research.trend_model import load_panel, corrected_rate_series, fit_velocities

OUT_DIR = Path("data") / "research_private" / "analysis" / "H1-prelim-2026-07-06"
MIN_ACTIVE_DAYS = 8       # concept needs this many non-zero days to analyse
SSE_REDUCTION_MIN = 0.30  # a level shift must explain >=30% of the variance


# ---------- pure detectors (unit-tested with synthetic series) ----------

def level_shift(y: np.ndarray, min_seg: int = 4) -> dict | None:
    """Single best changepoint by SSE-minimising binary segmentation."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 2 * min_seg:
        return None
    total_sse = float(((y - y.mean()) ** 2).sum())
    best = None
    for k in range(min_seg, n - min_seg + 1):
        a, b = y[:k], y[k:]
        sse = float(((a - a.mean()) ** 2).sum() + ((b - b.mean()) ** 2).sum())
        if best is None or sse < best[0]:
            best = (sse, k, float(a.mean()), float(b.mean()))
    sse, k, m0, m1 = best
    return {"index": int(k), "before_mean": m0, "after_mean": m1,
            "shift": m1 - m0,
            "sse_reduction": (1 - sse / total_sse) if total_sse > 0 else 0.0}


def burst_days(y: np.ndarray, z: float = 2.0) -> list[int]:
    y = np.asarray(y, dtype=float)
    s = y.std()
    if s == 0:
        return []
    thr = y.mean() + z * s
    return [int(i) for i, v in enumerate(y) if v > thr]


# ---------- run ----------

def run(out_dir: Path = OUT_DIR) -> dict:
    df = load_panel()
    rates = corrected_rate_series(df)
    vel = fit_velocities(df, "enriched", "n_bodies")
    vel_by = {r["concept_id"]: r for _, r in vel.iterrows()}
    days = sorted(df["day"].unique())

    takeoffs, bursty, results = [], [], []
    for cid, g in rates.sort_values("t").groupby("concept_id"):
        y = g["rate"].to_numpy(dtype=float)
        active = int((g["enriched"] > 0).sum())
        if active < MIN_ACTIVE_DAYS:
            continue
        ls = level_shift(y)
        bd = burst_days(y)
        v = vel_by.get(cid, {})
        rec = {"concept_id": cid, "active_days": active,
               "velocity": float(v.get("velocity", float("nan"))),
               "vel_ci_lo": float(v.get("ci_lo", float("nan"))),
               "vel_ci_hi": float(v.get("ci_hi", float("nan"))),
               "level_shift": ls, "n_burst_days": len(bd)}
        results.append(rec)
        # take-off = meaningful upward level shift OR a significantly-positive trend
        upshift = ls and ls["shift"] > 0 and ls["sse_reduction"] >= SSE_REDUCTION_MIN
        uptrend = not np.isnan(rec["vel_ci_lo"]) and rec["vel_ci_lo"] > 0
        if upshift or uptrend:
            takeoffs.append(cid)
        if len(bd) > 0:
            bursty.append(cid)

    report = {"snapshot": {"as_of": "2026-07-06", "n_days": len(days),
                           "n_concepts_analysed": len(results)},
              "n_takeoffs": len(takeoffs), "n_with_bursts": len(bursty),
              "note": "coverage-robust; preliminary (short panel — formal H1 needs event alignment + MAT-1)."}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "concept_dynamics.json").write_text(
        json.dumps({"report": report, "per_concept": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[h1] analysed {len(results)} concepts (>= {MIN_ACTIVE_DAYS} active days) over {len(days)} days · "
          f"take-offs={len(takeoffs)} · with-bursts={len(bursty)}")
    print("[h1] wrote", out_dir)
    return report


if __name__ == "__main__":
    run()
