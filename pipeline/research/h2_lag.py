"""PREP-1 — H2 pipeline (paper → news media lag), turnkey for MAT-1.

H2: a concept surfaces in papers before it surfaces in news; measure the lag.
Runs on the **coverage-robust per-source SHARE** series (concept mentions / total
mentions in that source that day) — NEVER raw counts, which track collection
volume, not diffusion. Cross-correlates the paper-share and news-share series per
concept and reports the lag (in days) that maximises correlation.

Simple-start estimator (lag cross-correlation); a Hawkes cross-excitation (tick)
is a v2 drop-in with the same interface. PRELIMINARY on the current panel: only
~30 concepts appear in both corpora and each series is short/sparse — report as
low-power. Formal H2 needs a mature panel (MAT-1).

Private output. Deterministic. Usage: python -m pipeline.research.h2_lag
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from pipeline.research.research_db import DB_FILE

OUT_DIR = Path("data") / "research_private" / "analysis" / "H2-prelim-2026-07-06"
MIN_ACTIVE_DAYS = 5   # each side needs this many non-zero share days
MAX_LAG = 7


# ---------- pure detector (unit-tested with synthetic series) ----------

def cross_correlation_lag(driver: np.ndarray, response: np.ndarray, max_lag: int = MAX_LAG) -> dict:
    """Lag k (>=0) at which `response` best follows `driver` (response[t+k] ~ driver[t]).

    Returns {"lag", "corr"}. lag>0 => response trails driver (news trails paper)."""
    driver = np.asarray(driver, dtype=float)
    response = np.asarray(response, dtype=float)
    n = len(driver)
    best_lag, best_c = 0, float("nan")
    for k in range(0, max_lag + 1):
        if n - k < 3:
            break
        d, r = driver[:n - k], response[k:]
        if d.std() == 0 or r.std() == 0:
            continue
        c = float(np.corrcoef(d, r)[0, 1])
        if np.isnan(best_c) or c > best_c:
            best_lag, best_c = k, c
    return {"lag": best_lag, "corr": best_c}


# ---------- run ----------

def _share_series(conn, version, days):
    """concept -> {'paper': [share by day], 'news': [share by day]} (coverage-robust share)."""
    idx = {d: i for i, d in enumerate(days)}
    totals = {"news": np.zeros(len(days)), "paper": np.zeros(len(days))}
    for st, day, n in conn.execute(
        "SELECT source_type, day, COUNT(*) FROM concept_mentions WHERE lexicon_version=? "
        "GROUP BY source_type, day", (version,)):
        if st in totals and day in idx:
            totals[st][idx[day]] = n
    series = {}
    for cid, st, day, n in conn.execute(
        "SELECT concept_id, source_type, day, COUNT(*) FROM concept_mentions WHERE lexicon_version=? "
        "GROUP BY concept_id, source_type, day", (version,)):
        if st not in totals or day not in idx:
            continue
        s = series.setdefault(cid, {"paper": np.zeros(len(days)), "news": np.zeros(len(days))})
        tot = totals[st][idx[day]]
        s[st][idx[day]] = n / tot if tot > 0 else 0.0
    return series


def run(out_dir: Path = OUT_DIR) -> dict:
    conn = sqlite3.connect(DB_FILE)
    version = conn.execute("SELECT MAX(lexicon_version) FROM concept_mentions").fetchone()[0]
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT day FROM concept_mentions WHERE lexicon_version=? ORDER BY day", (version,))]
    series = _share_series(conn, version, days)
    conn.close()

    per_concept, lags = [], []
    for cid, s in sorted(series.items()):
        if (s["paper"] > 0).sum() < MIN_ACTIVE_DAYS or (s["news"] > 0).sum() < MIN_ACTIVE_DAYS:
            continue
        res = cross_correlation_lag(s["paper"], s["news"])
        per_concept.append({"concept_id": cid, "lag_days": res["lag"], "corr": res["corr"],
                            "paper_active": int((s["paper"] > 0).sum()),
                            "news_active": int((s["news"] > 0).sum())})
        if not np.isnan(res["corr"]):
            lags.append(res["lag"])
    report = {"as_of": "2026-07-06", "n_days": len(days), "n_concepts_measured": len(per_concept),
              "median_lag_days": float(np.median(lags)) if lags else None,
              "mean_lag_days": float(np.mean(lags)) if lags else None,
              "POWER": "LOW / PRELIMINARY — short sparse panel; formal H2 needs MAT-1 (D+90).",
              "estimator": "lag cross-correlation on coverage-robust per-source share (raw counts NOT used)"}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "media_lag_prelim.json").write_text(
        json.dumps({"report": report, "per_concept": per_concept}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[h2] measured {len(per_concept)} concepts · median lag={report['median_lag_days']}d "
          f"(PRELIMINARY/low-power) · wrote {out_dir}")
    return report


if __name__ == "__main__":
    run()
