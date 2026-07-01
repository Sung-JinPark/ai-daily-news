"""Daily → weekly → monthly rollups for the private research corpus.

Reads the same aggregate mention log ``trend_metrics.load_mentions_df``
consumes and writes three append-friendly Parquet files under
``data/research_private/timeseries/``:

- ``daily.parquet``    — one row per (day, entity, entity_type, count)
- ``weekly.parquet``   — ISO-week rollup with velocity computed on the
                          weekly totals so week-over-week deltas are honest
- ``monthly.parquet``  — calendar-month rollup

Rollups overwrite atomically per run — the underlying JSONL is
append-only so the derived Parquet stays deterministic given the same
input hash.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.research import trend_metrics

PRIVATE_ROOT = Path("data") / "research_private"
TIMESERIES_DIR = PRIVATE_ROOT / "timeseries"


def _prepare_days(counts: pd.DataFrame) -> pd.DataFrame:
    if counts.empty:
        return counts
    counts = counts.copy()
    counts["date"] = pd.to_datetime(counts["day"])
    return counts


def build_daily(counts: pd.DataFrame) -> pd.DataFrame:
    if counts.empty:
        return pd.DataFrame(columns=["day", "entity", "entity_type", "count"])
    return counts[["day", "entity", "entity_type", "count"]].copy()


def build_weekly(counts: pd.DataFrame) -> pd.DataFrame:
    if counts.empty:
        return pd.DataFrame(columns=["week", "entity", "entity_type", "count", "velocity"])
    prepped = _prepare_days(counts)
    # ISO week key so calendars line up across years.
    prepped["week"] = prepped["date"].dt.strftime("%G-W%V")
    agg = (
        prepped.groupby(["week", "entity", "entity_type"], as_index=False)["count"].sum()
        .sort_values(["entity", "entity_type", "week"])
    )
    agg["velocity"] = agg.groupby(["entity", "entity_type"])["count"].diff().fillna(agg["count"])
    return agg


def build_monthly(counts: pd.DataFrame) -> pd.DataFrame:
    if counts.empty:
        return pd.DataFrame(columns=["month", "entity", "entity_type", "count", "velocity"])
    prepped = _prepare_days(counts)
    prepped["month"] = prepped["date"].dt.strftime("%Y-%m")
    agg = (
        prepped.groupby(["month", "entity", "entity_type"], as_index=False)["count"].sum()
        .sort_values(["entity", "entity_type", "month"])
    )
    agg["velocity"] = agg.groupby(["entity", "entity_type"])["count"].diff().fillna(agg["count"])
    return agg


def write_rollups(daily: pd.DataFrame, weekly: pd.DataFrame, monthly: pd.DataFrame) -> dict:
    TIMESERIES_DIR.mkdir(parents=True, exist_ok=True)
    daily_path = TIMESERIES_DIR / "daily.parquet"
    weekly_path = TIMESERIES_DIR / "weekly.parquet"
    monthly_path = TIMESERIES_DIR / "monthly.parquet"
    daily.to_parquet(daily_path, index=False)
    weekly.to_parquet(weekly_path, index=False)
    monthly.to_parquet(monthly_path, index=False)
    return {
        "daily_rows": len(daily),
        "weekly_rows": len(weekly),
        "monthly_rows": len(monthly),
        "paths": {
            "daily": str(daily_path),
            "weekly": str(weekly_path),
            "monthly": str(monthly_path),
        },
    }


def run() -> dict:
    df = trend_metrics.load_mentions_df()
    counts = trend_metrics.daily_counts(df)
    daily = build_daily(counts)
    weekly = build_weekly(counts)
    monthly = build_monthly(counts)
    stats = write_rollups(daily, weekly, monthly)
    print(
        f"[rollup] daily={stats['daily_rows']} weekly={stats['weekly_rows']} "
        f"monthly={stats['monthly_rows']} → {TIMESERIES_DIR}"
    )
    return stats


__all__ = [
    "build_daily",
    "build_weekly",
    "build_monthly",
    "write_rollups",
    "run",
    "TIMESERIES_DIR",
]
