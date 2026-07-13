"""WRITE-1 P1 — re-announcement preflight logic (synthetic, no data/lexicon).

Tests the pure pieces: arXiv-id era parsing and the H2 paper-first / re-announcement
exclusion on a synthetic mentions frame. Synthetic concept ids only.
"""
from datetime import date

import pandas as pd

from pipeline.research.reannounce_preflight import _id_era_month, h2_paper_first


def test_id_era_month():
    assert _id_era_month("2506.12345") == date(2025, 6, 1)
    assert _id_era_month("2201.00001") == date(2022, 1, 1)
    assert _id_era_month("2312.99999") == date(2023, 12, 1)
    assert _id_era_month("notanid") is None
    assert _id_era_month("2599.00001") is None   # month 99 invalid


def _mentions():
    # zzA: paper (event 2025-07-01) before news (2025-08-01) -> paper-first
    # zzB: news (2025-07-01) before paper (event 2025-08-01) -> news-first
    return pd.DataFrame([
        {"concept_id": "zzA", "source_type": "paper", "source_id": "pA",
         "day": "2025-07-10", "event_day": "2025-07-01"},
        {"concept_id": "zzA", "source_type": "news", "source_id": "nA",
         "day": "2025-08-01", "event_day": None},
        {"concept_id": "zzB", "source_type": "paper", "source_id": "pB",
         "day": "2025-08-10", "event_day": "2025-08-01"},
        {"concept_id": "zzB", "source_type": "news", "source_id": "nB",
         "day": "2025-07-01", "event_day": None},
    ])


def test_h2_paper_first_direction():
    r = h2_paper_first(_mentions())
    assert r["n_concepts_both"] == 2
    assert r["paper_first"] == 1          # only zzA is paper-first
    assert abs(r["paper_first_share"] - 0.5) < 1e-9


def test_h2_exclude_drops_paper_side():
    # excluding pA removes zzA's only paper mention -> zzA no longer has both sides
    r = h2_paper_first(_mentions(), exclude={"pA"})
    assert r["n_concepts_both"] == 1     # only zzB remains with both
    assert r["paper_first"] == 0         # zzB is news-first
    # excluding a NEWS-side id must NOT change the paper set (exclude is paper-only)
    r2 = h2_paper_first(_mentions(), exclude={"nA"})
    assert r2["n_concepts_both"] == 2
