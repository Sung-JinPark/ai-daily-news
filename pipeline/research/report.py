"""Auto-generated Δ report between the current snapshot and the
previous one — highlights new entities, biggest velocity swings, and
network structure changes so the researcher can eyeball what shifted
day-over-day without reading Parquet.

The report is markdown and lives at
``data/research_private/snapshots/<day>/delta_report.md``. It's
regenerated on every snapshot run — reruns for the same day overwrite.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PRIVATE_ROOT = Path("data") / "research_private"
SNAPSHOTS_DIR = PRIVATE_ROOT / "snapshots"


def _load_parquet(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _prior_day_dir(day: str) -> Path | None:
    """Return the newest snapshot directory strictly older than ``day``."""
    if not SNAPSHOTS_DIR.exists():
        return None
    candidates = sorted(
        p for p in SNAPSHOTS_DIR.iterdir()
        if p.is_dir() and p.name < day
    )
    return candidates[-1] if candidates else None


def _fmt_delta(delta: float) -> str:
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:g}"


def build_delta_report(day: str) -> str:
    """Return the markdown body for the Δ report between ``day`` and
    the newest earlier snapshot. Returns a stub note when no prior
    snapshot exists yet.
    """
    today_dir = SNAPSHOTS_DIR / day
    prior_dir = _prior_day_dir(day)

    lines = [f"# Δ report · {day}", ""]
    if prior_dir is None:
        lines.append("_No prior snapshot to diff against — this is the first run._")
        return "\n".join(lines) + "\n"

    lines.append(f"Compared against previous snapshot: **{prior_dir.name}**")
    lines.append("")

    # 1. Mention counts — new entities vs. disappeared entities.
    today_mentions = _load_parquet(today_dir / "entity_mentions.parquet")
    prior_mentions = _load_parquet(prior_dir / "entity_mentions.parquet")
    today_entities = set(today_mentions.get("entity", pd.Series(dtype=str)).unique())
    prior_entities = set(prior_mentions.get("entity", pd.Series(dtype=str)).unique())
    new_entities = sorted(today_entities - prior_entities)
    gone_entities = sorted(prior_entities - today_entities)

    lines.append(f"## Entity roster changes")
    lines.append("")
    lines.append(f"- New since last snapshot: **{len(new_entities)}**")
    if new_entities:
        lines.append(f"  - {', '.join(new_entities[:20])}" + (" …" if len(new_entities) > 20 else ""))
    lines.append(f"- Dropped from corpus: **{len(gone_entities)}** (should be 0 — corpus is append-only)")
    if gone_entities:
        lines.append(f"  - {', '.join(gone_entities[:20])}" + (" …" if len(gone_entities) > 20 else ""))
    lines.append("")

    # 2. Velocity swings today vs. same-entity most recent prior velocity.
    today_vel = _load_parquet(today_dir / "entity_velocity.parquet")
    prior_vel = _load_parquet(prior_dir / "entity_velocity.parquet")
    if not today_vel.empty and not prior_vel.empty:
        latest_today = (
            today_vel.sort_values("day").groupby(["entity", "entity_type"], as_index=False).tail(1)
            [["entity", "entity_type", "day", "velocity"]]
            .rename(columns={"velocity": "velocity_today", "day": "day_today"})
        )
        latest_prior = (
            prior_vel.sort_values("day").groupby(["entity", "entity_type"], as_index=False).tail(1)
            [["entity", "entity_type", "velocity"]]
            .rename(columns={"velocity": "velocity_prior"})
        )
        merged = latest_today.merge(latest_prior, on=["entity", "entity_type"], how="outer").fillna(0.0)
        merged["swing"] = merged["velocity_today"] - merged["velocity_prior"]
        gainers = merged.sort_values("swing", ascending=False).head(5)
        losers = merged.sort_values("swing", ascending=True).head(5)

        lines.append("## Velocity swings vs. previous snapshot")
        lines.append("")
        lines.append("**Top surges**")
        for row in gainers.itertuples(index=False):
            lines.append(
                f"- {row.entity} ({row.entity_type}) · Δv = {_fmt_delta(row.swing)} "
                f"(today {row.velocity_today:g} · prior {row.velocity_prior:g})"
            )
        lines.append("")
        lines.append("**Top decays**")
        for row in losers.itertuples(index=False):
            lines.append(
                f"- {row.entity} ({row.entity_type}) · Δv = {_fmt_delta(row.swing)} "
                f"(today {row.velocity_today:g} · prior {row.velocity_prior:g})"
            )
        lines.append("")

    # 3. Network structure Δ.
    today_net = _load_json(today_dir / "network_metrics.json")
    prior_net = _load_json(prior_dir / "network_metrics.json")
    if today_net and prior_net:
        lines.append("## Network structure Δ")
        lines.append("")
        for key in ("nodes", "edges", "density", "avg_clustering", "connected_components", "largest_component_size"):
            cur = today_net.get(key, 0)
            old = prior_net.get(key, 0)
            delta = cur - old if isinstance(cur, (int, float)) and isinstance(old, (int, float)) else 0
            fmt = ".4f" if isinstance(cur, float) else "g"
            lines.append(f"- {key}: {cur:{fmt}} (Δ {_fmt_delta(delta)})")
        # trust_flag is a string, not a delta — show side by side.
        # .get with a default keeps pre-P2 snapshots (no key) readable.
        lines.append(
            f"- trust: {today_net.get('trust_flag', 'unknown')} "
            f"(prior: {prior_net.get('trust_flag', 'unknown')})"
        )
        lines.append("")

    return "\n".join(lines) + "\n"


def write_delta_report(day: str) -> Path:
    body = build_delta_report(day)
    out = SNAPSHOTS_DIR / day / "delta_report.md"
    out.write_text(body, encoding="utf-8")
    return out


def run(day: str) -> Path:
    path = write_delta_report(day)
    print(f"[report] wrote Δ report → {path}")
    return path


__all__ = [
    "build_delta_report",
    "write_delta_report",
    "run",
]
