"""FIND-1 — formal H3: significance of concept-network evolution.

Promotes the PREP-1 proxy (biweekly snapshots + modularity communities + Jaccard
life-events) to a tested result:

  * per-snapshot **modularity significance** vs a degree-preserving edge-rewire null
    (is there real community structure, not noise?),
  * a **fragmentation trend** test over snapshots (Spearman of modularity /
    community count vs time — is the network restructuring over time?),
  * community **life-events** (birth/death/merge/split) across transitions.

Runs on the coverage-robust co-occurrence ledger (`concept_pairs`). Private output;
only aggregate stats + p-values are shareable. Deterministic (seeded rewiring).

Usage: python -m pipeline.research.h3_formal
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx
import numpy as np
from scipy import stats

from pipeline.research.research_db import DB_FILE
from pipeline.research.h3_network import (
    WINDOW_DAYS, communities, snapshot_graph, track_communities, _windows,
)

OUT_DIR = Path("data") / "research_private" / "analysis" / "H3-formal-2026-07-07"
N_PERM = 200


# ---------- pure stats (unit-tested with synthetic graphs) ----------

def modularity_significance(G: nx.Graph, n_perm: int = N_PERM, seed: int = 0) -> dict | None:
    """Observed modularity vs a degree-preserving rewire null. Returns Q, z, p."""
    if G.number_of_edges() < 3 or G.number_of_nodes() < 3:
        return None
    comms = [set(c) for c in communities(G)]
    Q = nx.community.modularity(G, comms, weight="weight")
    rng = np.random.default_rng(seed)
    nulls = []
    m = G.number_of_edges()
    for _ in range(n_perm):
        H = G.copy()
        if m >= 2:
            try:
                nx.double_edge_swap(H, nswap=m, max_tries=m * 20, seed=int(rng.integers(1_000_000_000)))
            except (nx.NetworkXError, nx.NetworkXAlgorithmError):
                pass
        nulls.append(nx.community.modularity(H, [set(c) for c in communities(H)], weight="weight"))
    nulls = np.asarray(nulls, float)
    sd = nulls.std() or 1e-9
    return {"Q": float(Q), "null_mean": float(nulls.mean()), "z": float((Q - nulls.mean()) / sd),
            "p": float((np.sum(nulls >= Q) + 1) / (n_perm + 1)), "n_comm": len(comms)}


def trend_test(series: list[float]) -> dict:
    """Monotonic trend of a per-snapshot metric over time (Spearman)."""
    n = len(series)
    if n < 3 or len(set(series)) < 2:
        return {"spearman_rho": float("nan"), "p": float("nan"), "n": n}
    rho, p = stats.spearmanr(np.arange(n), np.asarray(series, float))
    return {"spearman_rho": float(rho), "p": float(p), "n": n}


def largest_community_fraction(comms: list, n_nodes: int) -> float:
    if not comms or n_nodes == 0:
        return 0.0
    return max(len(c) for c in comms) / n_nodes


def fixed_density_modularity(g: nx.Graph, k: int) -> float | None:
    """Modularity on the top-k strongest edges — a constant-density control for the
    trend (removes the 'more papers -> denser graph -> lower modularity' confound)."""
    if k < 3 or g.number_of_edges() < 3:
        return None
    top = sorted(g.edges(data="weight"), key=lambda e: -(e[2] or 1))[:k]
    h = nx.Graph()
    h.add_weighted_edges_from((a, b, (w or 1)) for a, b, w in top)
    if h.number_of_edges() < 3:
        return None
    return nx.community.modularity(h, [set(c) for c in communities(h)], weight="weight")


# ---------- run ----------

def _snapshots(conn) -> list[dict]:
    days = [r[0] for r in conn.execute("SELECT DISTINCT day FROM concept_pairs ORDER BY day")]
    snaps = []
    for lo, hi in _windows(days):
        edges = {}
        for a, b in conn.execute("SELECT concept_a, concept_b FROM concept_pairs WHERE day BETWEEN ? AND ?", (lo, hi)):
            k = tuple(sorted((a, b)))
            edges[k] = edges.get(k, 0) + 1
        g = snapshot_graph(edges)
        snaps.append({"window": [lo, hi], "graph": g, "communities": communities(g)})
    return snaps


def run(out_dir: Path = OUT_DIR, n_perm: int = N_PERM) -> dict:
    conn = sqlite3.connect(DB_FILE)
    snaps = _snapshots(conn)
    conn.close()
    per_snap, mod_series, z_series, ncomm_series, graphs, sig_count = [], [], [], [], [], 0
    for i, s in enumerate(snaps):
        sig = modularity_significance(s["graph"], n_perm, seed=i)
        frac = largest_community_fraction(s["communities"], s["graph"].number_of_nodes())
        rec = {"window": s["window"], "nodes": s["graph"].number_of_nodes(),
               "edges": s["graph"].number_of_edges(), "n_comm": len(s["communities"]),
               "largest_frac": round(frac, 3), "modularity": sig}
        per_snap.append(rec)
        if sig:
            mod_series.append(sig["Q"]); z_series.append(sig["z"])
            ncomm_series.append(sig["n_comm"]); graphs.append(s["graph"])
            if sig["p"] < 0.05:
                sig_count += 1
    # density controls for the trend: (a) null-relative excess modularity z (the
    # rewire null matches each snapshot's density); (b) top-K-edge fixed density.
    K = min((g.number_of_edges() for g in graphs), default=0)
    fixed_Q = [q for g in graphs if (q := fixed_density_modularity(g, K)) is not None]
    transitions = []
    for t in range(1, len(snaps)):
        ev = track_communities(snaps[t - 1]["communities"], snaps[t]["communities"])
        transitions.append({k: sum(1 for e in ev if e["type"] == k) for k in ("birth", "death", "merge", "split")})
    life = {k: sum(tr[k] for tr in transitions) for k in ("birth", "death", "merge", "split")}
    report = {"as_of": "2026-07-07", "n_snapshots": len(snaps), "window_days": WINDOW_DAYS,
              "snapshots_with_significant_community_structure": f"{sig_count}/{len(mod_series)}",
              "modularity_trend_raw": trend_test(mod_series),
              "modularity_trend_null_relative": trend_test(z_series),
              "modularity_trend_fixed_density": trend_test(fixed_Q),
              "fixed_density_edges_K": K,
              "community_count_trend": trend_test([float(x) for x in ncomm_series]),
              "life_events_total": life, "n_perm": n_perm,
              "note": "coverage-robust co-occurrence; permutation null = degree-preserving rewire. "
                      "null_relative (z) and fixed_density (top-K edges) trends control for graph density."}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "h3_formal.json").write_text(
        json.dumps({"report": report, "per_snapshot": per_snap, "transitions": transitions},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[h3-formal] {len(snaps)} snapshots · sig-structure "
          f"{report['snapshots_with_significant_community_structure']} · "
          f"raw-trend rho={report['modularity_trend_raw']['spearman_rho']:.2f} p={report['modularity_trend_raw']['p']:.3f} · "
          f"null-rel(z) rho={report['modularity_trend_null_relative']['spearman_rho']:.2f} p={report['modularity_trend_null_relative']['p']:.3f} · "
          f"fixedK rho={report['modularity_trend_fixed_density']['spearman_rho']:.2f} p={report['modularity_trend_fixed_density']['p']:.3f} · "
          f"life {life}")
    return report


if __name__ == "__main__":
    run()
