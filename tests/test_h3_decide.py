"""H3-DECIDE — mechanism-discrimination helpers (synthetic, no data/lexicon).

Tests the pure pieces: paper universe/day extraction and the per-window
re-announcement share. Synthetic source ids only.
"""
import pandas as pd

from pipeline.research.h3_decide import (
    paper_universe,
    paper_days,
    _share_per_window,
)


def _mentions():
    return pd.DataFrame([
        {"concept_id": "zzA", "source_type": "paper", "source_id": "pOld",
         "day": "2025-07-05", "event_day": "2025-07-05"},
        {"concept_id": "zzB", "source_type": "paper", "source_id": "pOld",
         "day": "2025-07-05", "event_day": "2025-07-05"},
        {"concept_id": "zzA", "source_type": "paper", "source_id": "pNew",
         "day": "2026-06-05", "event_day": "2026-06-05"},
        {"concept_id": "zzA", "source_type": "news", "source_id": "nX",
         "day": "2025-08-01", "event_day": None},
    ])


def test_paper_universe_excludes_news():
    assert paper_universe(_mentions()) == {"pOld", "pNew"}


def test_paper_days_min_per_paper():
    pd_map = paper_days(_mentions())
    assert pd_map == {"pOld": "2025-07-05", "pNew": "2026-06-05"}


def test_share_per_window():
    windows = [("2025-07-01", "2025-07-14"),   # early: pOld only -> 1.0 re-announced
               ("2026-06-01", "2026-06-14"),   # late: pNew only -> 0.0
               ("2027-01-01", "2027-01-14")]   # empty window -> 0.0 by convention
    items = [("pOld", "2025-07-05"), ("pNew", "2026-06-05")]
    reann = {"pOld"}
    s = _share_per_window(windows, items, reann)
    assert s == [1.0, 0.0, 0.0]


def test_share_per_window_mixed():
    windows = [("2025-07-01", "2025-07-31")]
    items = [("a", "2025-07-02"), ("b", "2025-07-10"),
             ("c", "2025-07-20"), ("d", "2025-07-25")]
    reann = {"a", "b"}                       # 2 of 4 in-window are re-announced
    assert _share_per_window(windows, items, reann) == [0.5]
