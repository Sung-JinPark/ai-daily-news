"""WRITE-1 P1 — re-announcement robustness preflight (LOCAL-ONLY, read-only).

The frozen all-arXiv panel dates papers by arXiv `published`, but ~29% of in-window
papers are **re-announcements** — genuinely older papers (arXiv-id era well before
`published`) that arXiv re-listed inside the window. SUB-1 flagged this as an
event_day-axis property. This preflight asks: **do the H1/H2/H3 findings survive
excluding re-announced papers?**

For each of H1, H3, H2 we recompute the key statistic on (a) the full panel and
(b) the re-announcement-excluded panel, reusing the exact deterministic detectors
(`concept_lifecycle.concept_pairs`, `changepoint.level_shift/burst_days`,
`h3_formal.modularity_significance/trend_test/_snapshots`), and report the delta.

re-announced := arXiv-id era month (from the id's YYMM prefix) precedes `published`
by more than ``--min-lead`` days (default 60 — beyond normal revision latency).

Governance: read-only on the frozen ledgers; concept-level output is private
(gitignored `audits/`); stdout prints kinds + aggregates only (no concept names).

Usage:
    python -m pipeline.research.reannounce_preflight            # full run
    python -m pipeline.research.reannounce_preflight --h3-perm 50   # fast H3 smoke
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from pipeline.research.concept_lifecycle import concept_pairs
from pipeline.research.changepoint import level_shift, burst_days, SSE_REDUCTION_MIN
from pipeline.research.h3_formal import modularity_significance, trend_test, _snapshots

log = logging.getLogger("reannounce_preflight")

REPO = Path(__file__).resolve().parents[2]
RESEARCH_DB = REPO / "data" / "research_private" / "research.db"
PAPERS_DB = REPO / "data" / "papers_private" / "papers.db"
OUT_DIR = REPO / "data" / "research_private" / "audits" / "SUB-1-2026-07-13"

MIN_ACTIVE_DAYS = 8   # H1: concept needs this many non-zero paper-days (paper_findings parity)
WINDOW_LO, WINDOW_HI = "2025-07-01", "2026-07-31"


# ---------- re-announcement identification ----------

def _id_era_month(aid: str) -> date | None:
    m = re.match(r"^(\d{2})(\d{2})\.", aid or "")
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if not (1 <= mm <= 12):
        return None
    return date(2000 + yy, mm, 1)


def identify_reannounced(papers_db: Path, min_lead_days: int) -> tuple[set, dict]:
    """Return (reannounced_arxiv_ids, stats). A paper is re-announced if its
    id-era month precedes `published` by > min_lead_days."""
    conn = sqlite3.connect(papers_db)
    rows = conn.execute(
        "SELECT arxiv_id, published FROM papers WHERE published>=? AND published<=?",
        (WINDOW_LO, WINDOW_HI)).fetchall()
    conn.close()
    reann, leads, jan = set(), [], 0
    for aid, pub in rows:
        era = _id_era_month(aid)
        if era is None or not pub:
            continue
        try:
            pubd = date.fromisoformat(pub[:10])
        except ValueError:
            continue
        lead = (pubd - era).days
        if lead > min_lead_days:
            reann.add(aid)
            leads.append(lead)
            if pub[:10] in ("2026-01-01", "2026-01-02"):
                jan += 1
    leads.sort()
    stats_ = {
        "window_papers": len(rows),
        "reannounced": len(reann),
        "reannounced_share": round(len(reann) / max(1, len(rows)), 4),
        "min_lead_days": min_lead_days,
        "lead_median": leads[len(leads) // 2] if leads else None,
        "lead_p90": leads[int(len(leads) * 0.9)] if leads else None,
        "lead_max": leads[-1] if leads else None,
        "jan_0102_count": jan,
        "jan_0102_share_of_reannounced": round(jan / max(1, len(reann)), 4),
    }
    return reann, stats_


# ---------- shared: mentions frame ----------

def load_mentions(conn, version: int) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT concept_id, source_type, source_id, day, event_day FROM concept_mentions "
        "WHERE lexicon_version=? ORDER BY concept_id, day, source_type, source_id",
        conn, params=(version,))


# ---------- H1: paper-panel take-off / burst (paper_findings parity) ----------

def h1_paper(m: pd.DataFrame, exclude: set | None = None) -> dict:
    p = m[m["source_type"] == "paper"].copy()
    p["basis"] = p["event_day"].where(p["event_day"].notna(), p["day"])
    if exclude:
        p = p[~p["source_id"].isin(exclude)]
    p = p[p["basis"].notna()]
    days = sorted(p["basis"].unique())
    idx = {d: i for i, d in enumerate(days)}
    exposure = np.zeros(len(days))
    for d, g in p.groupby("basis"):
        exposure[idx[d]] = g["source_id"].nunique()
    takeoffs = bursty = analysed = 0
    for cid, g in p.groupby("concept_id"):
        cnt = np.zeros(len(days))
        for d, gg in g.groupby("basis"):
            cnt[idx[d]] = gg["source_id"].nunique()
        active = int((cnt > 0).sum())
        if active < MIN_ACTIVE_DAYS:
            continue
        analysed += 1
        rate = np.divide(cnt, exposure, out=np.zeros_like(cnt), where=exposure > 0)
        ls = level_shift(rate)
        if ls and ls["shift"] > 0 and ls["sse_reduction"] >= SSE_REDUCTION_MIN:
            takeoffs += 1
        if burst_days(rate):
            bursty += 1
    return {"n_days": len(days), "n_concepts": analysed,
            "n_takeoffs": takeoffs, "n_with_bursts": bursty}


# ---------- H2: paper-first direction + sign test (media_lag parity) ----------

def h2_paper_first(m: pd.DataFrame, exclude: set | None = None) -> dict:
    mm = m.copy()
    mm["basis"] = mm.apply(
        lambda r: (r["event_day"] or r["day"]) if r["source_type"] == "paper" else r["day"], axis=1)
    if exclude:
        drop = (mm["source_type"] == "paper") & (mm["source_id"].isin(exclude))
        mm = mm[~drop]
    first = mm.groupby(["concept_id", "source_type"])["basis"].min().unstack()
    for col in ("news", "paper"):
        if col not in first.columns:
            first[col] = None
    both = first.dropna(subset=["news", "paper"])
    paper_first = int((pd.to_datetime(both["paper"]) < pd.to_datetime(both["news"])).sum())
    n = int(len(both))
    lags = (pd.to_datetime(both["news"]) - pd.to_datetime(both["paper"])).dt.days
    # directional (pre-specified paper-first) hypothesis -> one-sided sign test
    bt = stats.binomtest(paper_first, n, 0.5, alternative="greater") if n else None
    return {"n_concepts_both": n, "paper_first": paper_first,
            "paper_first_share": round(paper_first / n, 4) if n else None,
            "sign_test_p_onesided": (bt.pvalue if bt else None),
            "median_news_minus_paper_days": float(lags.median()) if n else None}


# ---------- H3: co-occurrence modularity trend (h3_formal parity) ----------

def h3_network(m: pd.DataFrame, n_perm: int, exclude: set | None = None) -> dict:
    mm = m
    if exclude:
        drop = (mm["source_type"] == "paper") & (mm["source_id"].isin(exclude))
        mm = mm[~drop]
    pairs = concept_pairs(mm)
    tmp = sqlite3.connect(":memory:")
    tmp.execute("CREATE TABLE concept_pairs (concept_a TEXT, concept_b TEXT, "
                "source_type TEXT, source_id TEXT, day TEXT)")
    tmp.executemany("INSERT INTO concept_pairs VALUES (?,?,?,?,?)",
                    list(pairs[["concept_a", "concept_b", "source_type", "source_id", "day"]]
                         .itertuples(index=False, name=None)))
    tmp.commit()
    snaps = _snapshots(tmp)
    tmp.close()
    z_series, mod_series, sig = [], [], 0
    for i, s in enumerate(snaps):
        r = modularity_significance(s["graph"], n_perm, seed=i)
        if r:
            z_series.append(r["z"]); mod_series.append(r["Q"])
            if r["p"] < 0.05:
                sig += 1
    tr_z = trend_test(z_series)
    tr_raw = trend_test(mod_series)
    return {"n_snapshots": len(snaps), "significant_structure": f"{sig}/{len(z_series)}",
            "null_relative_z_trend": {"rho": round(tr_z["spearman_rho"], 3), "p": round(tr_z["p"], 4)},
            "raw_modularity_trend": {"rho": round(tr_raw["spearman_rho"], 3), "p": round(tr_raw["p"], 4)}}


# ---------- orchestrator ----------

def run(min_lead: int, h3_perm: int, research_db: Path, papers_db: Path) -> dict:
    reann, rstats = identify_reannounced(papers_db, min_lead)
    log.info("[reann] %d/%d (%.1f%%) re-announced (>%dd lead); 2026-01-01/02 share=%.1f%%; lead median=%s",
             rstats["reannounced"], rstats["window_papers"], 100 * rstats["reannounced_share"],
             min_lead, 100 * rstats["jan_0102_share_of_reannounced"], rstats["lead_median"])

    conn = sqlite3.connect(research_db)
    version = conn.execute("SELECT MAX(lexicon_version) FROM concept_mentions").fetchone()[0]
    m = load_mentions(conn, version)
    conn.close()

    log.info("[H1] full vs excluded ...")
    h1 = {"full": h1_paper(m), "excluded": h1_paper(m, reann)}
    log.info("     full: takeoff=%d/%d bursty=%d | excluded: takeoff=%d/%d bursty=%d",
             h1["full"]["n_takeoffs"], h1["full"]["n_concepts"], h1["full"]["n_with_bursts"],
             h1["excluded"]["n_takeoffs"], h1["excluded"]["n_concepts"], h1["excluded"]["n_with_bursts"])

    log.info("[H2] full vs excluded ...")
    h2 = {"full": h2_paper_first(m), "excluded": h2_paper_first(m, reann)}
    log.info("     full: paper-first %d/%d (%.1f%%) p1=%.2e | excluded: %d/%d (%.1f%%) p1=%.2e",
             h2["full"]["paper_first"], h2["full"]["n_concepts_both"], 100 * h2["full"]["paper_first_share"],
             h2["full"]["sign_test_p_onesided"], h2["excluded"]["paper_first"], h2["excluded"]["n_concepts_both"],
             100 * h2["excluded"]["paper_first_share"], h2["excluded"]["sign_test_p_onesided"])

    log.info("[H3] full vs excluded (n_perm=%d) ...", h3_perm)
    h3 = {"full": h3_network(m, h3_perm), "excluded": h3_network(m, h3_perm, reann)}
    log.info("     full: null-rel z-trend rho=%.2f p=%.3f sig=%s | excluded: rho=%.2f p=%.3f sig=%s",
             h3["full"]["null_relative_z_trend"]["rho"], h3["full"]["null_relative_z_trend"]["p"],
             h3["full"]["significant_structure"], h3["excluded"]["null_relative_z_trend"]["rho"],
             h3["excluded"]["null_relative_z_trend"]["p"], h3["excluded"]["significant_structure"])

    result = {"study": "WRITE-1 P1 re-announcement preflight",
              "lexicon_version": int(version), "reannouncement": rstats,
              "H1": h1, "H2": h2, "H3": h3, "h3_n_perm": h3_perm}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "reannounce_preflight.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("[done] wrote %s", OUT_DIR / "reannounce_preflight.json")
    return result


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--min-lead", type=int, default=60)
    ap.add_argument("--h3-perm", type=int, default=200)
    ap.add_argument("--research-db", default=str(RESEARCH_DB))
    ap.add_argument("--papers-db", default=str(PAPERS_DB))
    args = ap.parse_args()
    run(args.min_lead, args.h3_perm, Path(args.research_db), Path(args.papers_db))


if __name__ == "__main__":
    main()
