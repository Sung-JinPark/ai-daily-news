"""PREP-1 — MAT-1 turnkey runner.

One command runs the whole findings layer on the CURRENT corpus, and the SAME
command re-run at corpus maturity (MAT-1, D+90 ~2026-09-01) produces the formal
results — no re-wiring:

  events (scaffold) → H1 (take-off/burst) → H2 (paper→news lag) → H3 (network).

All stages read the coverage-robust series / private event set; all outputs are
private. Preliminary today (short panel); turnkey for the mature panel.

Usage: python -m pipeline.research.mat1
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline.research import changepoint, events, h2_lag, h3_network

MANIFEST = Path("data") / "research_private" / "analysis" / "mat1_manifest.json"


def run_all() -> dict:
    out = {}
    out["events"] = events.build_events()
    out["H1_takeoff"] = changepoint.run()
    out["H2_lag"] = h2_lag.run()
    out["H3_network"] = h3_network.run()
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(
        {"note": "MAT-1 turnkey run — preliminary until D+90 corpus maturity", "stages": out},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[mat1] turnkey run complete → {MANIFEST}")
    print("[mat1] events:", out["events"], "| H1 take-offs:", out["H1_takeoff"].get("n_takeoffs"),
          "| H2 median-lag:", out["H2_lag"].get("median_lag_days"),
          "| H3 snapshots:", out["H3_network"].get("n_snapshots"))
    return out


if __name__ == "__main__":
    run_all()
