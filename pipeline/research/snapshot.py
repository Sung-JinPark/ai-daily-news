"""Daily snapshot orchestrator for the private research corpus.

Reads the public aggregate JSONL streams under ``data/aggregates/``
and writes derived Parquet + JSON artifacts to
``data/research_private/snapshots/<day>/``. Everything under the
``research_private`` root is gitignored — the analysis code lives in
the tracked ``pipeline/research/`` package so the paper stays
reproducible while the raw findings stay unpublished.

Usage:
    python -m pipeline.research.snapshot                 # today (KST)
    python -m pipeline.research.snapshot --day 2026-07-01
    python -m pipeline.research.snapshot --dry-run       # log only

The orchestrator is idempotent — rerunning for the same day overwrites
that day's snapshot. Aggregate JSONL inputs are append-only so the
derived Parquet is deterministic given the input hash.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pipeline.research import network_metrics, report, rollup, trend_metrics

PRIVATE_ROOT = Path("data") / "research_private"
SNAPSHOTS_DIR = PRIVATE_ROOT / "snapshots"
MANIFEST_FILE = PRIVATE_ROOT / "manifest.json"
SCHEMA_VERSION = 1

KST = timezone(timedelta(hours=9))


def _today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        return {"schema_version": SCHEMA_VERSION, "snapshots": {}}
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": SCHEMA_VERSION, "snapshots": {}}


def _save_manifest(manifest: dict) -> None:
    MANIFEST_FILE.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _write_report(snapshot_dir: Path, day: str, stats: dict) -> None:
    lines = [
        f"# Research snapshot · {day}",
        "",
        f"- Generated (KST): {datetime.now(KST).isoformat(timespec='seconds')}",
        f"- Mention rows: {stats['mention_rows']}",
        f"- Distinct entities: {stats['distinct_entities']}",
        f"- Velocity rows: {stats['velocity_rows']}",
        f"- Acceleration rows: {stats['acceleration_rows']}",
        f"- Network: {stats['network']['nodes']} nodes · {stats['network']['edges']} edges · density {stats['network']['density']:.3f}",
        "",
        "## Top velocity gainers today",
        "",
    ]
    for row in stats["top_gainers"]:
        lines.append(f"- {row['entity']} ({row['entity_type']}) · v = {row['velocity']:+.1f}")
    lines.append("")
    lines.append("## Top velocity losers today")
    lines.append("")
    for row in stats["top_losers"]:
        lines.append(f"- {row['entity']} ({row['entity_type']}) · v = {row['velocity']:+.1f}")
    lines.append("")
    (snapshot_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run(day: str, dry_run: bool = False) -> dict:
    """Materialize the snapshot for ``day`` and return a stats dict."""
    print(f"[snapshot] day = {day}  dry_run = {dry_run}")

    mentions_df = trend_metrics.load_mentions_df()
    print(f"[snapshot] loaded {len(mentions_df):,} mention rows from {trend_metrics.MENTIONS_FILE}")

    long_counts = trend_metrics.daily_counts(mentions_df)
    print(f"[snapshot] daily counts: {len(long_counts):,} (entity × day) rows")

    velocity = trend_metrics.compute_velocity(long_counts)
    print(f"[snapshot] velocity rows: {len(velocity):,}")

    acceleration = trend_metrics.compute_acceleration(velocity)
    print(f"[snapshot] acceleration rows: {len(acceleration):,}")

    bursts = trend_metrics.burst_scores(velocity)

    # Network layer — full-corpus co-occurrence collapsed to a weighted graph.
    cooc_df = network_metrics.load_cooccurrence_df()
    print(f"[snapshot] loaded {len(cooc_df):,} co-occurrence rows from {network_metrics.COOCCURRENCE_FILE}")
    edges = network_metrics.edge_list(cooc_df)
    graph = network_metrics.build_graph(edges)
    node_df = network_metrics.node_metrics(graph)
    graph_stats = network_metrics.graph_metrics(graph)
    communities = network_metrics.louvain_communities(graph)
    print(
        f"[snapshot] network: {graph_stats['nodes']} nodes / {graph_stats['edges']} edges / "
        f"{len(communities['community_id'].unique()) if not communities.empty else 0} communities"
    )

    gainers, losers = trend_metrics.top_movers(velocity, day, k=5)
    stats = {
        "mention_rows": int(len(mentions_df)),
        "distinct_entities": int(mentions_df["entity"].nunique()) if not mentions_df.empty else 0,
        "velocity_rows": int(len(velocity)),
        "acceleration_rows": int(len(acceleration)),
        "network": graph_stats,
        "top_gainers": gainers.to_dict(orient="records"),
        "top_losers": losers.to_dict(orient="records"),
    }

    if dry_run:
        print("[snapshot] dry-run: skipping file writes")
        return stats

    snapshot_dir = SNAPSHOTS_DIR / day
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    mentions_df.to_parquet(snapshot_dir / "entity_mentions.parquet", index=False)
    velocity.to_parquet(snapshot_dir / "entity_velocity.parquet", index=False)
    acceleration.to_parquet(snapshot_dir / "entity_acceleration.parquet", index=False)
    bursts.to_parquet(snapshot_dir / "entity_bursts.parquet", index=False)
    edges.to_parquet(snapshot_dir / "network_edges.parquet", index=False)
    node_df.to_parquet(snapshot_dir / "network_nodes.parquet", index=False)
    communities.to_parquet(snapshot_dir / "network_communities.parquet", index=False)
    (snapshot_dir / "network_metrics.json").write_text(
        json.dumps(graph_stats, indent=2, sort_keys=True), encoding="utf-8",
    )

    _write_report(snapshot_dir, day, stats)

    # Long-horizon rollups + Δ report against the previous snapshot.
    rollup_stats = rollup.run()
    stats["rollup"] = rollup_stats
    report.run(day)

    # Update manifest with input hash + file inventory for reproducibility.
    manifest = _load_manifest()
    files = {}
    for p in sorted(snapshot_dir.iterdir()):
        if p.is_file():
            files[p.name] = {"bytes": p.stat().st_size, "sha256": _sha256_file(p)}
    input_hash = _sha256_file(trend_metrics.MENTIONS_FILE) if trend_metrics.MENTIONS_FILE.exists() else None
    manifest["snapshots"][day] = {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "input_mentions_sha256": input_hash,
        "counts": {
            "mention_rows": stats["mention_rows"],
            "distinct_entities": stats["distinct_entities"],
            "velocity_rows": stats["velocity_rows"],
            "acceleration_rows": stats["acceleration_rows"],
        },
        "network": stats["network"],
        "files": files,
    }
    manifest["schema_version"] = SCHEMA_VERSION
    _save_manifest(manifest)

    print(f"[snapshot] wrote {len(files)} files to {snapshot_dir}")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", default=None, help="YYYY-MM-DD (default: today KST)")
    parser.add_argument("--dry-run", action="store_true", help="log only, do not write files")
    args = parser.parse_args()

    day = args.day or _today_kst()
    run(day=day, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
