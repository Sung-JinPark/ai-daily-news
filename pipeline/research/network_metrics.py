"""Network snapshot construction and metrics for the paper.

Reads ``data/aggregates/entity_cooccurrence.jsonl`` and builds a
weighted undirected graph where nodes are entities and edge weights
are the number of distinct clusters two entities co-appear in.
Returns pandas frames + a metrics dict that the snapshot orchestrator
persists as Parquet/JSON.

All formulas match ``research/methodology.md``:
- Node-level: degree, weighted degree (strength), betweenness, PageRank
- Graph-level: density, avg clustering coefficient, connected components
- Community: Louvain partition (weight-aware modularity maximization)
"""
from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pandas as pd

DATA_DIR = Path("data")
COOCCURRENCE_FILE = DATA_DIR / "aggregates" / "entity_cooccurrence.jsonl"

# Minimum edge weight (i.e. distinct-cluster co-mentions) for a pair
# to enter the analytic graph — filters one-off spurious co-occurrences.
MIN_EDGE_WEIGHT = 1

# Below these thresholds PageRank / Louvain lose statistical meaning:
# tiny graphs converge to trivial partitions and centrality collapses
# onto whichever node the algorithm hit first. Metrics still emit so
# the time series is unbroken, but the ``trust_flag`` marks them so
# downstream analysis / the paper can drop untrusted early days.
MIN_NODES_FOR_TRUST = 5
MIN_EDGES_FOR_TRUST = 10

# Louvain seed — pinned so repeated snapshots of the same graph yield
# identical community assignments. Do NOT change between snapshots
# without documenting a methodology bump; downstream evolution diffs
# rely on stable community_ids.
LOUVAIN_SEED = 42


def load_cooccurrence_df(path: Path = COOCCURRENCE_FILE) -> pd.DataFrame:
    """Return the raw co-occurrence log as a DataFrame with columns
    ``day, entity_a, entity_a_type, entity_b, entity_b_type,
    cluster_id, article_id, category``. Empty when the JSONL is absent.
    """
    if not path.exists():
        return pd.DataFrame(columns=[
            "day", "entity_a", "entity_a_type", "entity_b", "entity_b_type",
            "cluster_id", "article_id", "category",
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
    return pd.DataFrame.from_records(records)


def edge_list(df: pd.DataFrame, min_weight: int = MIN_EDGE_WEIGHT) -> pd.DataFrame:
    """Collapse the raw log to weighted edges.

    weight(a, b) = number of distinct cluster_ids that mention both
    a and b (matches the methodology doc's definition).
    """
    if df.empty:
        return pd.DataFrame(columns=[
            "source", "target", "source_type", "target_type", "weight",
        ])
    # Canonicalize edge orientation so (a,b) and (b,a) collapse.
    ordered = df[["entity_a", "entity_b", "entity_a_type", "entity_b_type", "cluster_id"]].copy()
    swap = ordered["entity_a"] > ordered["entity_b"]
    ordered.loc[swap, ["entity_a", "entity_b"]] = ordered.loc[swap, ["entity_b", "entity_a"]].values
    ordered.loc[swap, ["entity_a_type", "entity_b_type"]] = (
        ordered.loc[swap, ["entity_b_type", "entity_a_type"]].values
    )
    edges = (
        ordered.groupby(["entity_a", "entity_b", "entity_a_type", "entity_b_type"])
        ["cluster_id"].nunique().rename("weight").reset_index()
    )
    edges = edges[edges["weight"] >= min_weight]
    return edges.rename(columns={
        "entity_a": "source",
        "entity_b": "target",
        "entity_a_type": "source_type",
        "entity_b_type": "target_type",
    })


def build_graph(edges: pd.DataFrame) -> nx.Graph:
    """Weighted undirected graph from the edge list."""
    g = nx.Graph()
    for row in edges.itertuples(index=False):
        g.add_node(row.source, entity_type=row.source_type)
        g.add_node(row.target, entity_type=row.target_type)
        g.add_edge(row.source, row.target, weight=int(row.weight))
    return g


def node_metrics(g: nx.Graph) -> pd.DataFrame:
    """Per-node degree/strength/betweenness/PageRank frame."""
    if g.number_of_nodes() == 0:
        return pd.DataFrame(columns=[
            "entity", "entity_type", "degree", "strength", "betweenness", "pagerank",
        ])
    degree = dict(g.degree())
    strength = dict(g.degree(weight="weight"))
    # Betweenness on weighted graphs uses 1/weight as distance so higher
    # co-mention weight = shorter path. Skip when graph is too small.
    if g.number_of_nodes() >= 3:
        betweenness = nx.betweenness_centrality(g, weight="weight", normalized=True)
    else:
        betweenness = {n: 0.0 for n in g.nodes()}
    pagerank = nx.pagerank(g, weight="weight") if g.number_of_edges() > 0 else {n: 0.0 for n in g.nodes()}
    rows = []
    for n, data in g.nodes(data=True):
        rows.append({
            "entity": n,
            "entity_type": data.get("entity_type", "unknown"),
            "degree": int(degree.get(n, 0)),
            "strength": int(strength.get(n, 0)),
            "betweenness": float(betweenness.get(n, 0.0)),
            "pagerank": float(pagerank.get(n, 0.0)),
        })
    return pd.DataFrame(rows).sort_values("pagerank", ascending=False).reset_index(drop=True)


def graph_metrics(g: nx.Graph) -> dict:
    """Whole-graph summary statistics.

    Adds a ``trust_flag`` field: ``"ok"`` when the graph is large
    enough for PageRank / Louvain to be statistically meaningful,
    ``"small_graph"`` otherwise. Metrics still emit either way — a
    downstream consumer can filter untrusted rows without losing the
    time series.
    """
    n = g.number_of_nodes()
    m = g.number_of_edges()
    if n == 0:
        return {
            "nodes": 0, "edges": 0, "density": 0.0,
            "avg_clustering": 0.0, "connected_components": 0,
            "largest_component_size": 0,
            "trust_flag": "empty",
        }
    components = list(nx.connected_components(g))
    largest = max((len(c) for c in components), default=0)
    trust = "ok" if n >= MIN_NODES_FOR_TRUST and m >= MIN_EDGES_FOR_TRUST else "small_graph"
    return {
        "nodes": int(n),
        "edges": int(m),
        "density": float(nx.density(g)),
        "avg_clustering": float(nx.average_clustering(g, weight="weight")) if m > 0 else 0.0,
        "connected_components": int(len(components)),
        "largest_component_size": int(largest),
        "trust_flag": trust,
    }


def louvain_communities(g: nx.Graph, seed: int = LOUVAIN_SEED) -> pd.DataFrame:
    """Return ``(entity, community_id)`` frame using the Louvain
    weight-aware modularity algorithm.

    Determinism: ``seed`` defaults to ``LOUVAIN_SEED`` (module-level
    constant). Community IDs are assigned by sorting parts by (size
    descending, min-entity-name) so that trivial permutations of the
    algorithm's internal set ordering do not renumber communities
    across runs. This is what makes evolution diffs between snapshots
    honest — a paper "moved from community 2 to community 0" now
    reflects real motion, not label shuffling.
    """
    if g.number_of_edges() == 0:
        return pd.DataFrame(columns=["entity", "community_id"])
    parts = nx.community.louvain_communities(g, weight="weight", seed=seed)
    # Canonicalize part order: bigger first; tiebreak on the
    # lexicographically smallest member so equal-sized parts still
    # get a stable id.
    ordered = sorted(parts, key=lambda s: (-len(s), min(s) if s else ""))
    rows = []
    for cid, members in enumerate(ordered):
        for e in members:
            rows.append({"entity": e, "community_id": cid})
    return pd.DataFrame(rows)


__all__ = [
    "load_cooccurrence_df",
    "edge_list",
    "build_graph",
    "node_metrics",
    "graph_metrics",
    "louvain_communities",
    "COOCCURRENCE_FILE",
    "MIN_EDGE_WEIGHT",
]
