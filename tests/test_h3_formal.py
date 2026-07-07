"""FIND-1 — formal H3 significance tests (synthetic graphs, no data/lexicon)."""
import networkx as nx

from pipeline.research.h3_formal import (
    largest_community_fraction,
    modularity_significance,
    trend_test,
)


def _two_clusters():
    g = nx.Graph()
    for clique in (["a", "b", "c", "d"], ["w", "x", "y", "z"]):
        for i in range(len(clique)):
            for j in range(i + 1, len(clique)):
                g.add_edge(clique[i], clique[j], weight=3)
    g.add_edge("d", "w", weight=1)  # single weak bridge
    return g


def test_modularity_significant_for_clustered_graph():
    res = modularity_significance(_two_clusters(), n_perm=60, seed=1)
    assert res is not None
    assert res["Q"] > 0.2
    assert res["z"] > 0           # observed above the rewire null
    assert res["p"] < 0.2         # community structure not from noise


def test_modularity_none_for_tiny_graph():
    g = nx.Graph(); g.add_edge("a", "b", weight=1)
    assert modularity_significance(g, n_perm=10) is None


def test_trend_increasing():
    r = trend_test([0.1, 0.2, 0.3, 0.45, 0.5, 0.62])
    assert r["spearman_rho"] > 0.9
    assert r["p"] < 0.05


def test_trend_flat_is_nan():
    r = trend_test([0.3, 0.3, 0.3, 0.3])
    assert r["n"] == 4
    assert r["spearman_rho"] != r["spearman_rho"]  # NaN


def test_largest_community_fraction():
    assert largest_community_fraction([{1, 2, 3}, {4}], 4) == 0.75
    assert largest_community_fraction([], 5) == 0.0


def test_merge_and_split_are_detectable():
    # NEWS-2 robustness: real-data merge/split=0 must be data, not a blind detector.
    from pipeline.research.h3_network import track_communities
    merge = track_communities([{"a", "b"}, {"c", "d"}], [{"a", "b", "c", "d"}])
    split = track_communities([{"a", "b", "c", "d"}], [{"a", "b"}, {"c", "d"}])
    assert any(e["type"] == "merge" for e in merge)
    assert any(e["type"] == "split" for e in split)


def test_overlap_coef_and_unequal_merge_split():
    # H3-ROBUST: overlap-coefficient matching catches UNEQUAL merges/splits that
    # Jaccard-0.5 misses (a small community absorbed into a much larger one).
    from pipeline.research.h3_network import overlap_coef, track_communities, jaccard
    assert overlap_coef({"a", "b"}, {"a", "b", "c", "d", "e", "f"}) == 1.0   # fully contained
    assert jaccard({"a", "b"}, {"a", "b", "c", "d", "e", "f"}) < 0.5          # Jaccard would miss it
    big = {"a", "b", "c", "d", "e", "f"}
    merge = track_communities([{"a", "b"}, {"c", "d", "e", "f"}], [big])       # unequal merge
    split = track_communities([big], [{"a", "b"}, {"c", "d", "e", "f"}])       # unequal split
    assert any(e["type"] == "merge" for e in merge)
    assert any(e["type"] == "split" for e in split)
    # a clean continuation must NOT be flagged as merge/split
    cont = track_communities([{"a", "b", "c"}], [{"a", "b", "c"}])
    assert not any(e["type"] in ("merge", "split") for e in cont)
