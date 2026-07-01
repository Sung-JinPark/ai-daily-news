"""Retroactively add ``trust_flag`` to historical network snapshots.

P2 (cycle 1) taught ``network_metrics.graph_metrics`` to emit a
``trust_flag`` so downstream analysis can drop days whose graph was
too small for PageRank / Louvain to mean anything. Snapshots written
before that change lack the key.

This backfill derives the flag **from the values already stored in
each snapshot's ``network_metrics.json``** — the same ``nodes`` /
``edges`` thresholds the live code uses (imported, not copied, so the
two can never drift). It deliberately does NOT re-run the snapshot
pipeline for past days: ``snapshot.py`` always reads the full current
corpus regardless of ``--day``, so a re-run would overwrite historical
metrics with today's data and falsify the archive. Deriving from
stored values keeps every other field byte-identical.

Idempotent — files that already carry ``trust_flag`` are skipped.

Usage:
    python -m pipeline.research.backfill_trust_flag [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.research.network_metrics import MIN_EDGES_FOR_TRUST, MIN_NODES_FOR_TRUST

SNAPSHOTS_DIR = Path("data") / "research_private" / "snapshots"


def derive_trust_flag(metrics: dict) -> str:
    """Same decision rule as ``network_metrics.graph_metrics``."""
    nodes = int(metrics.get("nodes", 0))
    edges = int(metrics.get("edges", 0))
    if nodes == 0:
        return "empty"
    if nodes >= MIN_NODES_FOR_TRUST and edges >= MIN_EDGES_FOR_TRUST:
        return "ok"
    return "small_graph"


def run(dry_run: bool = False) -> dict:
    stats = {"scanned": 0, "backfilled": 0, "already_present": 0, "missing_file": 0}
    if not SNAPSHOTS_DIR.exists():
        print(f"[backfill] {SNAPSHOTS_DIR} does not exist - nothing to do")
        return stats
    for day_dir in sorted(p for p in SNAPSHOTS_DIR.iterdir() if p.is_dir()):
        stats["scanned"] += 1
        metrics_file = day_dir / "network_metrics.json"
        if not metrics_file.exists():
            stats["missing_file"] += 1
            print(f"[backfill] {day_dir.name}: no network_metrics.json - skipped")
            continue
        try:
            metrics = json.loads(metrics_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stats["missing_file"] += 1
            print(f"[backfill] {day_dir.name}: unparseable network_metrics.json - skipped")
            continue
        if "trust_flag" in metrics:
            stats["already_present"] += 1
            continue
        flag = derive_trust_flag(metrics)
        if dry_run:
            print(f"[backfill] {day_dir.name}: would add trust_flag={flag}")
        else:
            metrics["trust_flag"] = flag
            # Match snapshot.py's serialization (indent=2, sort_keys) so
            # a later snapshot re-run produces no spurious diff.
            metrics_file.write_text(
                json.dumps(metrics, indent=2, sort_keys=True), encoding="utf-8",
            )
            print(f"[backfill] {day_dir.name}: trust_flag={flag} added")
        stats["backfilled"] += 1
    print(
        f"[backfill] scanned={stats['scanned']} backfilled={stats['backfilled']} "
        f"already_present={stats['already_present']} missing={stats['missing_file']}"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, no writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
