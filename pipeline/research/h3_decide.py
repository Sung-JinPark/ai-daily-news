"""H3-DECIDE — mechanism of the H3 composition-conditional trend (LOCAL-ONLY, read-only).

WRITE-1 found the H3 "integration" trend (null-relative modularity decline) is
significant on the full 12-month panel (ρ=−0.52, p=0.005) but loses significance when
arXiv re-announcements are excluded (ρ=−0.24, p=0.22). This module decides *why*, via
FOUR pre-registered diagnostics (rules fixed in decisions.md BEFORE this ran):

  1. Re-announcement share time-structure: per-snapshot re-announced-paper share s_t,
     Spearman(s_t, t) = ρ_share (+ auxiliary Spearman of null-relative z_t vs s_t).
  2. Attribution split: null-relative modularity trend on three panels —
     full / originals-only (drop re-announced) / reann-only (keep only re-announced).
  3. Size-matched power: K random composition-preserving subsamples that drop the same
     *count* of papers as the re-announcement exclusion; distribution of the trend ρ.
  4. Cutoff sensitivity: originals-only trend at re-announcement cutoffs 30/90/180 days.

Reuses the exact deterministic detectors (concept_lifecycle.concept_pairs,
h3_formal.modularity_significance/trend_test/_snapshots, h3_network._windows) and the
re-announcement tagging (reannounce_preflight). Read-only on the frozen ledgers;
concept-level artifacts private; stdout prints aggregates/significance only.

Usage:
    python -m pipeline.research.h3_decide                       # full run
    python -m pipeline.research.h3_decide --k 5 --perm 40 --power-perm 20   # smoke
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from pipeline.research.concept_lifecycle import concept_pairs
from pipeline.research.h3_formal import modularity_significance, trend_test, _snapshots
from pipeline.research.h3_network import _windows
from pipeline.research.reannounce_preflight import (
    identify_reannounced, load_mentions, RESEARCH_DB, PAPERS_DB, OUT_DIR,
)

log = logging.getLogger("h3_decide")


# ---------- shared panel machinery (parity with reannounce_preflight.h3_network) ----------

def _conn_from_pairs(pairs: pd.DataFrame) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE concept_pairs (concept_a TEXT, concept_b TEXT, "
                 "source_type TEXT, source_id TEXT, day TEXT)")
    conn.executemany("INSERT INTO concept_pairs VALUES (?,?,?,?,?)",
                     list(pairs[["concept_a", "concept_b", "source_type", "source_id", "day"]]
                          .itertuples(index=False, name=None)))
    conn.commit()
    return conn


def panel_trend(full_pairs: pd.DataFrame, n_perm: int, exclude: set | None = None) -> dict:
    """Null-relative + raw modularity trend over biweekly snapshots. Takes the
    PRECOMPUTED full co-occurrence pairs and filters by paper source_id (equivalent
    to excluding those papers before pair construction, since each pair row belongs to
    exactly one source_id — but far cheaper than rebuilding concept_pairs). Returns the
    trend rho/p plus per-window z (None where a snapshot has too few edges)."""
    pairs = full_pairs
    if exclude:
        pairs = pairs[~((pairs["source_type"] == "paper") & (pairs["source_id"].isin(exclude)))]
    conn = _conn_from_pairs(pairs)
    snaps = _snapshots(conn)
    conn.close()
    z_by_win, z_series, mod_series, sig = [], [], [], 0
    for i, s in enumerate(snaps):
        r = modularity_significance(s["graph"], n_perm, seed=i)
        if r:
            z_by_win.append(r["z"]); z_series.append(r["z"]); mod_series.append(r["Q"])
            if r["p"] < 0.05:
                sig += 1
        else:
            z_by_win.append(None)
    tz, traw = trend_test(z_series), trend_test(mod_series)
    return {"n_snapshots": len(snaps), "n_valid": len(z_series),
            "significant_structure": f"{sig}/{len(z_series)}",
            "null_relative": {"rho": round(tz["spearman_rho"], 3), "p": round(tz["p"], 4)},
            "raw": {"rho": round(traw["spearman_rho"], 3), "p": round(traw["p"], 4)},
            "z_by_window": z_by_win}


def snapshot_windows(full_pairs: pd.DataFrame) -> list:
    days = sorted(full_pairs["day"].dropna().unique())
    return list(_windows(days))


def paper_universe(m: pd.DataFrame) -> set:
    return set(m.loc[m["source_type"] == "paper", "source_id"].unique())


def paper_days(m: pd.DataFrame) -> dict:
    p = m[m["source_type"] == "paper"]
    return p.groupby("source_id")["day"].min().to_dict()


# ---------- diagnostic 1: re-announcement share time-structure ----------

def _share_per_window(windows: list, pday_items: list, reann: set) -> list:
    """Fraction of papers active in each window that are re-announced. Pure/testable."""
    out = []
    for lo, hi in windows:
        ps = [sid for sid, d in pday_items if d is not None and lo <= d <= hi]
        out.append(sum(1 for sid in ps if sid in reann) / len(ps) if ps else 0.0)
    return out


def diag_share(m: pd.DataFrame, full_pairs: pd.DataFrame, reann: set, full_trend: dict) -> dict:
    windows = snapshot_windows(full_pairs)
    pday = paper_days(m)
    s_t = _share_per_window(windows, list(pday.items()), reann)
    t = np.arange(len(s_t))
    rho_s, p_s = stats.spearmanr(t, s_t)
    # auxiliary: z_t vs s_t on the aligned, valid windows
    z = full_trend["z_by_window"]
    pairs = [(s_t[i], z[i]) for i in range(min(len(s_t), len(z))) if z[i] is not None]
    if len(pairs) >= 3:
        sv, zv = zip(*pairs)
        rho_zs, p_zs = stats.spearmanr(sv, zv)
    else:
        rho_zs, p_zs = float("nan"), float("nan")
    return {"n_windows": len(windows),
            "share_first": round(s_t[0], 3), "share_last": round(s_t[-1], 3),
            "share_min": round(min(s_t), 3), "share_max": round(max(s_t), 3),
            "rho_share_vs_time": {"rho": round(float(rho_s), 3), "p": round(float(p_s), 4)},
            "rho_z_vs_share": {"rho": round(float(rho_zs), 3), "p": round(float(p_zs), 4)},
            "s_t": [round(x, 3) for x in s_t]}


# ---------- diagnostic 3: size-matched power ----------

def diag_power(full_pairs: pd.DataFrame, universe: list, n_remove: int, k: int,
               seed: int, n_perm: int) -> dict:
    rhos = []
    for j in range(k):
        rng = random.Random(f"{seed}:power:{j}")
        drop = set(rng.sample(universe, min(n_remove, len(universe))))
        r = panel_trend(full_pairs, n_perm, exclude=drop)["null_relative"]["rho"]
        if r == r:  # not NaN
            rhos.append(r)
        if (j + 1) % 10 == 0:
            log.info("     power subsample %d/%d ...", j + 1, k)
    rhos.sort()
    def pct(q):
        if not rhos:
            return float("nan")
        return round(float(np.percentile(rhos, q)), 3)
    return {"k": k, "n_removed": n_remove, "removed_frac": round(n_remove / len(universe), 3),
            "n_perm": n_perm, "rho_median": pct(50), "rho_iqr": [pct(25), pct(75)],
            "rho_min": rhos[0] if rhos else None, "rho_max": rhos[-1] if rhos else None,
            "rhos": rhos}


# ---------- orchestrator ----------

def run(perm: int, power_perm: int, k: int, seed: int,
        research_db: Path, papers_db: Path) -> dict:
    reann_sets = {c: identify_reannounced(papers_db, c)[0] for c in (30, 60, 90, 180)}
    log.info("[reann sets] " + " ".join(f"{c}d:{len(s)}" for c, s in reann_sets.items()))

    conn = sqlite3.connect(research_db)
    version = conn.execute("SELECT MAX(lexicon_version) FROM concept_mentions").fetchone()[0]
    m = load_mentions(conn, version)
    conn.close()

    reann60 = reann_sets[60]
    universe = paper_universe(m)
    reann_in_frame = reann60 & universe
    non_reann_in_frame = universe - reann60

    # precompute full co-occurrence pairs ONCE; all panels filter this (cheap) rather
    # than rebuilding concept_pairs (a 300k-row groupby) per panel/subsample.
    log.info("[pairs] building full co-occurrence pairs once ...")
    full_pairs = concept_pairs(m)
    log.info("        %d pair-rows", len(full_pairs))

    # diagnostic 2 (attribution) — full is also reused by diagnostic 1 (z_t)
    log.info("[diag2] full panel trend (perm=%d) ...", perm)
    full = panel_trend(full_pairs, perm)
    log.info("        full null-rel rho=%.3f p=%.4f", full["null_relative"]["rho"], full["null_relative"]["p"])
    log.info("[diag2] originals-only ...")
    originals = panel_trend(full_pairs, perm, exclude=reann60)
    log.info("[diag2] reann-only ...")
    reann_only = panel_trend(full_pairs, perm, exclude=non_reann_in_frame)
    attribution = {
        "full": {k2: full[k2] for k2 in ("null_relative", "raw", "significant_structure")},
        "originals_only": {k2: originals[k2] for k2 in ("null_relative", "raw", "significant_structure")},
        "reann_only": {k2: reann_only[k2] for k2 in ("null_relative", "raw", "significant_structure")},
        "n_reann_in_frame": len(reann_in_frame), "n_paper_frame": len(universe)}
    log.info("        full=%.3f originals=%.3f reann-only=%.3f (null-rel rho)",
             full["null_relative"]["rho"], originals["null_relative"]["rho"],
             reann_only["null_relative"]["rho"])

    # diagnostic 1 (share time-structure) — uses full z_by_window
    log.info("[diag1] re-announcement share vs time ...")
    share = diag_share(m, full_pairs, reann60, full)
    log.info("        rho_share=%.3f p=%.4f (share %0.2f->%0.2f)",
             share["rho_share_vs_time"]["rho"], share["rho_share_vs_time"]["p"],
             share["share_first"], share["share_last"])

    # diagnostic 3 (size-matched power)
    log.info("[diag3] size-matched power: K=%d random subsamples (drop=%d, perm=%d) ...",
             k, len(reann_in_frame), power_perm)
    power = diag_power(full_pairs, sorted(universe), len(reann_in_frame), k, seed, power_perm)
    log.info("        subsample rho median=%.3f IQR=%s", power["rho_median"], power["rho_iqr"])

    # diagnostic 4 (cutoff sensitivity) — originals-only at 30/90/180
    log.info("[diag4] cutoff sensitivity ...")
    cutoffs = {}
    for c in (30, 90, 180):
        r = panel_trend(full_pairs, perm, exclude=reann_sets[c])["null_relative"]
        cutoffs[f"{c}d"] = {"n_reann": len(reann_sets[c]), "rho": r["rho"], "p": r["p"]}
        log.info("        cutoff %dd: excl-reann n=%d -> rho=%.3f p=%.4f",
                 c, len(reann_sets[c]), r["rho"], r["p"])
    cutoffs["60d"] = {"n_reann": len(reann60), "rho": originals["null_relative"]["rho"],
                      "p": originals["null_relative"]["p"]}

    result = {"study": "H3-DECIDE mechanism discrimination", "lexicon_version": int(version),
              "perm": perm, "seed": seed,
              "diag1_share": share, "diag2_attribution": attribution,
              "diag3_power": power, "diag4_cutoff": cutoffs}
    # strip bulky z_by_window before persisting the aggregate
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "h3_decide.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("[done] wrote %s", OUT_DIR / "h3_decide.json")
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--perm", type=int, default=200, help="perms for attribution/cutoff panels")
    ap.add_argument("--power-perm", type=int, default=30, help="perms per power subsample")
    ap.add_argument("--k", type=int, default=50, help="number of power subsamples")
    ap.add_argument("--seed", type=int, default=20260713)
    ap.add_argument("--research-db", default=str(RESEARCH_DB))
    ap.add_argument("--papers-db", default=str(PAPERS_DB))
    args = ap.parse_args()
    run(args.perm, args.power_perm, args.k, args.seed, Path(args.research_db), Path(args.papers_db))


if __name__ == "__main__":
    main()
