"""Retention policy for the papers.db cold exports.

Guards the 2026-09-28 fix: Mondays used to be kept indefinitely, so the
export directory grew by ~283MB/week without bound.
"""
from pathlib import Path

import pytest

from pipeline.research import export_papers_db as ex


def _seed(tmp_path: Path, days: list[str]) -> None:
    for d in days:
        (tmp_path / f"papers-{d}.db").write_bytes(b"x")


def _names(tmp_path: Path) -> set[str]:
    return {p.name for p in tmp_path.glob("papers-*.db")}


# 2026: every date below is a Monday unless noted.
MONDAYS = [
    "2026-07-06", "2026-07-13", "2026-07-20", "2026-07-27",
    "2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31",
    "2026-09-07", "2026-09-14", "2026-09-21",
]
RECENT = ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25",
          "2026-09-26", "2026-09-27"]  # Tue-Sun, inside keep_days


def test_monday_retention_is_capped(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "EXPORT_DIR", tmp_path)
    _seed(tmp_path, MONDAYS + RECENT)

    deleted = ex.prune(keep_days=7, keep_mondays=4, keep_monthly=3,
                       today="2026-09-28")

    kept = _names(tmp_path)
    # last 7 days
    assert set(RECENT) <= {n[7:17] for n in kept}
    # 4 most recent Mondays
    for d in ("2026-09-21", "2026-09-14", "2026-09-07", "2026-08-31"):
        assert f"papers-{d}.db" in kept
    # month anchors: first Monday of Sep / Aug / Jul
    for d in ("2026-09-07", "2026-08-03", "2026-07-06"):
        assert f"papers-{d}.db" in kept
    # the unbounded middle is gone
    assert {p.name for p in deleted} == {
        f"papers-{d}.db" for d in
        ("2026-07-13", "2026-07-20", "2026-07-27",
         "2026-08-10", "2026-08-17", "2026-08-24")
    }
    assert len(kept) == 12


def test_prune_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "EXPORT_DIR", tmp_path)
    _seed(tmp_path, MONDAYS + RECENT)
    ex.prune(keep_days=7, keep_mondays=4, keep_monthly=3, today="2026-09-28")
    before = _names(tmp_path)
    again = ex.prune(keep_days=7, keep_mondays=4, keep_monthly=3,
                     today="2026-09-28")
    assert again == []
    assert _names(tmp_path) == before


def test_steady_state_does_not_grow(tmp_path, monkeypatch):
    """A year of weekly exports still lands on a bounded file count."""
    monkeypatch.setattr(ex, "EXPORT_DIR", tmp_path)
    import datetime as dt
    d = dt.date(2026, 1, 5)  # a Monday
    weeks = []
    while d <= dt.date(2026, 12, 28):
        weeks.append(d.isoformat())
        d += dt.timedelta(days=7)
    _seed(tmp_path, weeks)
    ex.prune(keep_days=7, keep_mondays=4, keep_monthly=3,
             today="2026-12-31")
    # 4 weekly + 3 monthly anchors, minus any overlap, plus in-window files
    assert len(_names(tmp_path)) <= 8


def test_zero_caps_keep_only_the_window(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "EXPORT_DIR", tmp_path)
    _seed(tmp_path, MONDAYS + RECENT)
    ex.prune(keep_days=7, keep_mondays=0, keep_monthly=0,
             today="2026-09-28")
    assert _names(tmp_path) == {f"papers-{d}.db" for d in RECENT} | {
        "papers-2026-09-21.db"}  # 09-21 is age 7, inside keep_days


def test_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "EXPORT_DIR", tmp_path / "nope")
    assert ex.prune(today="2026-09-28") == []
