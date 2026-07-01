"""Velocity and acceleration computations for the paper.

Reads ``data/aggregates/entity_mentions.jsonl`` (produced by
``pipeline.entity_index``) and returns pandas frames the snapshot
module can persist as Parquet. Every formula matches the definitions
in ``research/methodology.md`` — velocity is a signed day-over-day
delta, acceleration is a signed delta of velocity, both zero-filled
at the start of an entity's series so every row has a value.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import pandas as pd

DATA_DIR = Path("data")
MENTIONS_FILE = DATA_DIR / "aggregates" / "entity_mentions.jsonl"

# Smoothing windows referenced by the methodology doc.
VELOCITY_EMA_SPAN = 7
ACCELERATION_EMA_SPAN = 28
BURST_MIN_MEAN_STD = 1.0   # avoid divide-by-tiny sigma for rarely-observed entities


def load_mentions_df(path: Path = MENTIONS_FILE) -> pd.DataFrame:
    """Return the raw mention log as a DataFrame with columns
    ``day, entity_type, entity, article_id, cluster_id, source_id,
    importance_score, category``. Empty when the JSONL is absent."""
    if not path.exists():
        return pd.DataFrame(columns=[
            "day", "entity_type", "entity", "article_id", "cluster_id",
            "source_id", "importance_score", "category",
        ])
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    df = pd.DataFrame.from_records(records)
    if "day" not in df.columns and not df.empty:
        raise ValueError("mentions JSONL is missing the 'day' column")
    return df


def daily_counts(df: pd.DataFrame) -> pd.DataFrame:
    """Return a long-format frame ``(day, entity, entity_type, count)``
    where every entity has one row per calendar day between its first
    and last observation (zero-fill inclusive).

    The calendar range is filled with ``pd.date_range`` — not the set
    of days that happened to have any mention — so a missing snapshot
    day (pipeline outage, weekend gap) does not silently compress a
    multi-day change into a single ``diff`` step. Velocity computed
    from this grid is one calendar day per step by construction.
    """
    if df.empty:
        return pd.DataFrame(columns=["day", "entity", "entity_type", "count"])
    grouped = (
        df.groupby(["day", "entity", "entity_type"]).size().rename("count").reset_index()
    )
    # Full calendar-day range between first and last observed day so
    # subsequent diffs are one-calendar-day steps, not step-count steps.
    observed = pd.to_datetime(sorted(grouped["day"].unique()))
    all_days_idx = pd.date_range(observed.min(), observed.max(), freq="D")
    filled: list[pd.DataFrame] = []
    for (entity, entity_type), sub in grouped.groupby(["entity", "entity_type"]):
        sub_indexed = sub.set_index(pd.to_datetime(sub["day"]))["count"]
        # Reindex to the union of all archive days seen in the file, so
        # entities that never appeared on a day get an honest zero.
        reindexed = sub_indexed.reindex(all_days_idx, fill_value=0)
        filled.append(pd.DataFrame({
            "day": reindexed.index.strftime("%Y-%m-%d"),
            "entity": entity,
            "entity_type": entity_type,
            "count": reindexed.values,
        }))
    if not filled:
        return pd.DataFrame(columns=["day", "entity", "entity_type", "count"])
    return pd.concat(filled, ignore_index=True)


def _pivot_counts(long_counts: pd.DataFrame) -> pd.DataFrame:
    """Pivot to a wide (entity × day) count matrix keyed on
    ``(entity, entity_type)``."""
    return long_counts.pivot_table(
        index=["entity", "entity_type"],
        columns="day",
        values="count",
        fill_value=0,
    ).sort_index(axis=1)


def compute_velocity(long_counts: pd.DataFrame) -> pd.DataFrame:
    """Signed day-over-day delta per entity. Returns a long-format
    frame ``(day, entity, entity_type, velocity, velocity_ema7)``.
    """
    if long_counts.empty:
        return pd.DataFrame(columns=["day", "entity", "entity_type", "velocity", "velocity_ema7"])
    wide = _pivot_counts(long_counts)
    velocity_wide = wide.diff(axis=1).fillna(wide.iloc[:, 0].to_frame().reindex(columns=wide.columns).fillna(0))
    # First column: velocity is baseline minus zero, i.e. equal to count.
    velocity_wide.iloc[:, 0] = wide.iloc[:, 0]
    velocity_wide = velocity_wide.astype(float)
    velocity_ema = velocity_wide.T.ewm(span=VELOCITY_EMA_SPAN, adjust=False).mean().T
    return _wide_to_long(velocity_wide, velocity_ema, value_name="velocity", ema_name="velocity_ema7")


def compute_acceleration(velocity_long: pd.DataFrame) -> pd.DataFrame:
    """Signed change of velocity per entity. Returns
    ``(day, entity, entity_type, acceleration, acceleration_ema28)``.
    Expects the frame produced by ``compute_velocity``.
    """
    if velocity_long.empty:
        return pd.DataFrame(columns=[
            "day", "entity", "entity_type", "acceleration", "acceleration_ema28",
        ])
    wide = velocity_long.pivot_table(
        index=["entity", "entity_type"],
        columns="day",
        values="velocity",
        fill_value=0,
    ).sort_index(axis=1)
    accel_wide = wide.diff(axis=1).fillna(0.0).astype(float)
    accel_ema = accel_wide.T.ewm(span=ACCELERATION_EMA_SPAN, adjust=False).mean().T
    return _wide_to_long(accel_wide, accel_ema, value_name="acceleration", ema_name="acceleration_ema28")


def _wide_to_long(wide: pd.DataFrame, ema_wide: pd.DataFrame,
                  value_name: str, ema_name: str) -> pd.DataFrame:
    long = wide.stack().rename(value_name).reset_index()
    long[ema_name] = ema_wide.stack().values
    return long[["day", "entity", "entity_type", value_name, ema_name]]


def burst_scores(velocity_long: pd.DataFrame) -> pd.DataFrame:
    """Return ``(day, entity, entity_type, burst_z)`` where ``burst_z``
    is the standardized velocity per entity — |z| >= 2 flags a burst
    according to the paper's simplified Kleinberg formulation.
    """
    if velocity_long.empty:
        return pd.DataFrame(columns=["day", "entity", "entity_type", "burst_z"])
    stats = velocity_long.groupby(["entity", "entity_type"])["velocity"].agg(["mean", "std"]).reset_index()
    stats["std"] = stats["std"].fillna(0).clip(lower=BURST_MIN_MEAN_STD)
    merged = velocity_long.merge(stats, on=["entity", "entity_type"], how="left")
    merged["burst_z"] = (merged["velocity"] - merged["mean"]) / merged["std"]
    return merged[["day", "entity", "entity_type", "burst_z"]]


def top_movers(velocity_long: pd.DataFrame, day: str, k: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (top gainers, top losers) for a specific day."""
    sub = velocity_long[velocity_long["day"] == day].copy()
    gainers = sub.sort_values("velocity", ascending=False).head(k)
    losers = sub.sort_values("velocity", ascending=True).head(k)
    return gainers, losers


__all__ = [
    "load_mentions_df",
    "daily_counts",
    "compute_velocity",
    "compute_acceleration",
    "burst_scores",
    "top_movers",
    "MENTIONS_FILE",
]
