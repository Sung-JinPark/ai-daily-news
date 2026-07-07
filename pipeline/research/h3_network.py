"""PREP-1 — H3 pipeline (concept co-occurrence network evolution), turnkey for MAT-1.

H3: the concept co-occurrence network fragments / restructures over time. Builds
biweekly snapshots of the co-occurrence graph (research.db `concept_pairs`),
detects communities per snapshot (greedy modularity), and tracks them across
snapshots by Jaccard overlap → birth / death / merge / split events.

Simple-start (modularity communities + Jaccard tracking); a dynamic SBM
(graph-tool) is a v2 drop-in. PRELIMINARY: ~1 month = only 2 biweekly snapshots,
so community *events* are not yet meaningful — pipeline is READY and results are
confirmed at MAT-1 (D+90).

Private output. Deterministic (fixed snapshot bins, seedless modularity).
Usage: python -m pipeline.research.h3_network
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx

from pipeline.research.research_db import DB_FILE

OUT_DIR = Path("data") / "research_private" / "analysis" / "H3-prelim-2026-07-06"
WINDOW_DAYS = 14
MIN_EDGE_WEIGHT = 2   # a pair must co-occur >= this many times in a window to be an edge


# ---------- pure functions (unit-tested with synthetic community sets) ----------

def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    u = a | b
    return len(a & b) / len(u) if u else 0.0


def overlap_coef(a, b) -> float:
    """Max-containment: |a∩b| / min(|a|,|b|). Unlike Jaccard, it is NOT diluted
    when one community is much larger — so an unequal merge (a small community
    absorbed into a big one) still scores high. Needed for merge/split detection."""
    a, b = set(a), set(b)
    inter = len(a & b)
    return inter / min(len(a), len(b)) if a and b else 0.0


def track_communities(prev: list, curr: list, thr: float = 0.5) -> list:
    """Match communities across two snapshots → birth/death/merge/split.

    Uses the overlap coefficient (max-containment), so unequal merges/splits are
    detected (Jaccard misses them — H3-ROBUST). prev, curr: lists of iterables."""
    prev = [set(c) for c in prev]
    curr = [set(c) for c in curr]
    events = []
    for pc in prev:  # deaths & splits
        tgt = [j for j, cc in enumerate(curr) if overlap_coef(pc, cc) >= thr]
        if not tgt:
            events.append({"type": "death", "members": sorted(pc)})
        elif len(tgt) > 1:
            events.append({"type": "split", "members": sorted(pc), "into": len(tgt)})
    for cc in curr:   # births & merges
        src = [i for i, pc in enumerate(prev) if overlap_coef(pc, cc) >= thr]
        if not src:
            events.append({"type": "birth", "members": sorted(cc)})
        elif len(src) > 1:
            events.append({"type": "merge", "members": sorted(cc), "from": len(src)})
    return events


def snapshot_graph(edges: dict, min_weight: int = MIN_EDGE_WEIGHT) -> nx.Graph:
    """edges: {(a,b): weight} -> weighted graph keeping edges >= min_weight."""
    g = nx.Graph()
    for (a, b), w in edges.items():
        if w >= min_weight:
            g.add_edge(a, b, weight=w)
    return g


def communities(g: nx.Graph) -> list:
    if g.number_of_edges() == 0:
        return []
    return [sorted(c) for c in nx.community.greedy_modularity_communities(g, weight="weight")]


# ---------- run ----------

def _windows(days: list[str]) -> list[tuple[str, str]]:
    days = sorted(days)
    if not days:
        return []
    out, i = [], 0
    while i < len(days):
        out.append((days[i], days[min(i + WINDOW_DAYS - 1, len(days) - 1)]))
        i += WINDOW_DAYS
    return out


def run(out_dir: Path = OUT_DIR) -> dict:
    conn = sqlite3.connect(DB_FILE)
    version = conn.execute("SELECT MAX(lexicon_version) FROM concept_mentions").fetchone()[0]
    days = [r[0] for r in conn.execute("SELECT DISTINCT day FROM concept_pairs ORDER BY day")]
    wins = _windows(days)
    snaps = []
    for lo, hi in wins:
        edges = {}
        for a, b in conn.execute(
            "SELECT concept_a, concept_b FROM concept_pairs WHERE day BETWEEN ? AND ?", (lo, hi)):
            key = tuple(sorted((a, b)))
            edges[key] = edges.get(key, 0) + 1
        g = snapshot_graph(edges)
        comms = communities(g)
        snaps.append({"window": [lo, hi], "nodes": g.number_of_nodes(),
                      "edges": g.number_of_edges(), "n_communities": len(comms), "communities": comms})
    conn.close()

    transitions = []
    for t in range(1, len(snaps)):
        ev = track_communities(snaps[t - 1]["communities"], snaps[t]["communities"])
        transitions.append({"from_window": snaps[t - 1]["window"], "to_window": snaps[t]["window"],
                            "events": ev,
                            "counts": {k: sum(1 for e in ev if e["type"] == k)
                                       for k in ("birth", "death", "merge", "split")}})
    report = {"as_of": "2026-07-06", "window_days": WINDOW_DAYS, "n_snapshots": len(snaps),
              "n_transitions": len(transitions),
              "POWER": "PRELIMINARY — too few snapshots (~1 month) for meaningful community events; "
                       "pipeline READY, confirm at MAT-1 (D+90).",
              "method": "biweekly co-occurrence snapshots + greedy-modularity communities + Jaccard tracking"}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "network_evolution_prelim.json").write_text(
        json.dumps({"report": report, "snapshots": snaps, "transitions": transitions},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[h3] {len(snaps)} snapshots ({WINDOW_DAYS}d) · {len(transitions)} transitions "
          f"(PRELIMINARY — ready for MAT-1) · wrote {out_dir}")
    return report


if __name__ == "__main__":
    run()
