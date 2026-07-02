"""Re-canonicalize community labels in pre-P2 snapshots.

Cycle-1 P2 made ``louvain_communities`` assign community ids in a
canonical order (size desc, min-member asc). Snapshots written before
that change carry the same *partition* but with arbitrary label
numbering — so the first cross-snapshot comparison reports dozens of
phantom "community moves" that are pure label permutation, exactly
the artifact P2 exists to eliminate.

This backfill re-derives each old snapshot's communities **from that
snapshot's own stored ``network_edges.parquet``** — never from the
current corpus — so history is preserved; only the labels normalize.

Safety check: before overwriting, the script verifies the new
partition is identical to the stored one up to relabeling (same
node groupings). If the partitions genuinely differ (they shouldn't:
Louvain is seeded), the snapshot is skipped and reported instead of
overwritten.

Idempotent — a snapshot whose labels are already canonical rewrites
to identical bytes-equivalent content (and is reported as such).

Usage:
    python -m pipeline.research.backfill_community_labels [--dry-run]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from pipeline.research.network_metrics import build_graph, louvain_communities

SNAPSHOTS_DIR = Path("data") / "research_private" / "snapshots"


def _partition_signature(df: pd.DataFrame) -> set[frozenset[str]]:
    """Label-independent partition signature: a set of member-sets."""
    return {
        frozenset(sub["entity"])
        for _, sub in df.groupby("community_id")
    }


def run(dry_run: bool = False) -> dict:
    stats = {"scanned": 0, "relabeled": 0, "already_canonical": 0,
             "partition_mismatch": 0, "missing": 0}
    if not SNAPSHOTS_DIR.exists():
        print(f"[communities] {SNAPSHOTS_DIR} does not exist - nothing to do")
        return stats
    for day_dir in sorted(p for p in SNAPSHOTS_DIR.iterdir() if p.is_dir()):
        stats["scanned"] += 1
        comm_file = day_dir / "network_communities.parquet"
        edges_file = day_dir / "network_edges.parquet"
        if not comm_file.exists() or not edges_file.exists():
            stats["missing"] += 1
            continue
        stored = pd.read_parquet(comm_file)
        edges = pd.read_parquet(edges_file)
        canonical = louvain_communities(build_graph(edges))

        if _partition_signature(stored) != _partition_signature(canonical):
            stats["partition_mismatch"] += 1
            print(
                f"[communities] {day_dir.name}: partition differs from stored one - "
                "NOT overwriting (investigate before touching history)"
            )
            continue

        stored_sorted = stored.sort_values(["entity"]).reset_index(drop=True)
        canonical_sorted = canonical.sort_values(["entity"]).reset_index(drop=True)
        if stored_sorted.equals(canonical_sorted):
            stats["already_canonical"] += 1
            continue

        if dry_run:
            n_changed = int((stored_sorted["community_id"] != canonical_sorted["community_id"]).sum())
            print(f"[communities] {day_dir.name}: would relabel ({n_changed} nodes change id)")
        else:
            canonical.to_parquet(comm_file, index=False)
            print(f"[communities] {day_dir.name}: labels canonicalized (partition unchanged)")
        stats["relabeled"] += 1
    print(
        f"[communities] scanned={stats['scanned']} relabeled={stats['relabeled']} "
        f"already_canonical={stats['already_canonical']} "
        f"mismatch={stats['partition_mismatch']} missing={stats['missing']}"
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report only, no writes")
    args = parser.parse_args()
    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
