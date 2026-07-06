"""PREP-1 — H3 network tracking tests (synthetic community sets, no data, no lexicon)."""
from pipeline.research.h3_network import (
    communities,
    jaccard,
    snapshot_graph,
    track_communities,
)


def test_jaccard():
    assert jaccard({1, 2, 3}, {1, 2, 3}) == 1.0
    assert jaccard({1, 2}, {3, 4}) == 0.0
    assert jaccard({1, 2, 3, 4}, {1, 2}) == 0.5


def test_birth_and_death():
    ev = track_communities([{"a", "b"}], [{"a", "b"}, {"x", "y"}])
    assert any(e["type"] == "birth" for e in ev)
    ev2 = track_communities([{"a", "b"}, {"p", "q"}], [{"a", "b"}])
    assert any(e["type"] == "death" for e in ev2)


def test_merge_and_split():
    # two prev communities merge into one
    merge = track_communities([{"a", "b"}, {"c", "d"}], [{"a", "b", "c", "d"}])
    assert any(e["type"] == "merge" for e in merge)
    # one prev community splits into two
    split = track_communities([{"a", "b", "c", "d"}], [{"a", "b"}, {"c", "d"}])
    assert any(e["type"] == "split" for e in split)


def test_snapshot_graph_and_communities():
    edges = {("a", "b"): 3, ("b", "c"): 3, ("a", "c"): 3,   # triangle 1
             ("x", "y"): 3, ("y", "z"): 3, ("x", "z"): 3,   # triangle 2
             ("c", "x"): 1}                                  # weak bridge (< MIN_EDGE_WEIGHT)
    g = snapshot_graph(edges)
    assert g.number_of_edges() == 6           # bridge dropped
    comms = communities(g)
    assert len(comms) == 2                     # two separate triangles
