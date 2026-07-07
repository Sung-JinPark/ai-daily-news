"""FIND-1 — paper-side H1 / velocity (re-point of the detectors to the paper panel).

BACK-1 opened a dense paper panel (all-arXiv, ~173 event_days). The H1 (changepoint)
and velocity detectors were wired to the NEWS coverage-robust panel; here we feed
them the PAPER panel: per-concept **coverage-robust rate = paper mentions / papers
that day** (share removes the daily-volume trend). Reuses the already-unit-tested
pure detectors (`changepoint.level_shift/burst_days`, `velocity_tv` P-spline). News
side stays available for the paper-vs-news velocity comparison (H2 link).

Coverage-robust only (never raw counts). Private per-concept output.

Usage: python -m pipeline.research.paper_findings
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np

from pipeline.research.research_db import DB_FILE
from pipeline.research.changepoint import level_shift, burst_days, SSE_REDUCTION_MIN
from pipeline.research.velocity_tv import _smooth_velocity

OUT_DIR = Path("data") / "research_private" / "analysis" / "paper-findings-2026-07-07"
MIN_ACTIVE_DAYS = 8


def load_paper_panel(conn) -> tuple[list[str], dict]:
    """days (sorted) + {concept: rate-series over days} (coverage-robust share)."""
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT event_day FROM concept_mentions WHERE lexicon_version=6 AND source_type='paper' "
        "AND event_day IS NOT NULL ORDER BY event_day")]
    idx = {d: i for i, d in enumerate(days)}
    exposure = np.zeros(len(days))
    for d, n in conn.execute(
        "SELECT event_day, COUNT(DISTINCT source_id) FROM concept_mentions WHERE lexicon_version=6 "
        "AND source_type='paper' AND event_day IS NOT NULL GROUP BY event_day"):
        exposure[idx[d]] = n
    series = {}
    for cid, d, n in conn.execute(
        "SELECT concept_id, event_day, COUNT(DISTINCT source_id) FROM concept_mentions WHERE lexicon_version=6 "
        "AND source_type='paper' AND event_day IS NOT NULL GROUP BY concept_id, event_day"):
        s = series.setdefault(cid, {"count": np.zeros(len(days)), "rate": np.zeros(len(days))})
        s["count"][idx[d]] = n
        s["rate"][idx[d]] = n / exposure[idx[d]] if exposure[idx[d]] > 0 else 0.0
    return days, series


def run(out_dir: Path = OUT_DIR) -> dict:
    conn = sqlite3.connect(DB_FILE)
    days, series = load_paper_panel(conn)
    exposure = None
    # per-day exposure again for velocity weighting
    idx = {d: i for i, d in enumerate(days)}
    exp = np.zeros(len(days))
    for d, n in conn.execute(
        "SELECT event_day, COUNT(DISTINCT source_id) FROM concept_mentions WHERE lexicon_version=6 "
        "AND source_type='paper' AND event_day IS NOT NULL GROUP BY event_day"):
        exp[idx[d]] = n
    conn.close()

    results, takeoffs, bursty = [], 0, 0
    for cid, s in sorted(series.items()):
        active = int((s["count"] > 0).sum())
        if active < MIN_ACTIVE_DAYS:
            continue
        rate = s["rate"]
        ls = level_shift(rate)
        bd = burst_days(rate)
        v, se, edf = _smooth_velocity(rate, exp)
        upshift = bool(ls and ls["shift"] > 0 and ls["sse_reduction"] >= SSE_REDUCTION_MIN)
        rec = {"concept_id": cid, "active_days": active, "level_shift": ls,
               "n_burst_days": len(bd), "mean_velocity": float(np.mean(v)),
               "v_start": float(v[0]), "v_end": float(v[-1]), "edf": round(edf, 2),
               "takeoff": upshift}
        results.append(rec)
        if upshift:
            takeoffs += 1
        if bd:
            bursty += 1
    report = {"as_of": "2026-07-07", "panel": "paper (all-arXiv, coverage-robust share)",
              "n_days": len(days), "n_concepts": len(results),
              "n_takeoffs": takeoffs, "n_with_bursts": bursty,
              "note": "detectors re-pointed from news to paper event_day panel; coverage-robust rate = "
                      "concept mentions / papers that day."}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "paper_findings.json").write_text(
        json.dumps({"report": report, "per_concept": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[paper-H1] {len(results)} concepts over {len(days)} paper-days · take-offs={takeoffs} · "
          f"with-bursts={bursty}")
    return report


if __name__ == "__main__":
    run()
