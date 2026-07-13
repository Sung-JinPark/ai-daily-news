"""SUB-1 — abstract-undercount sub-study logic (synthetic concepts, no data/lexicon).

Tests the pure, deterministic pieces: instrument replica matching, stratified
allocation, Wilson CI, and the undercount metric. Uses synthetic concept names
only (governance: never real lexicon terms in the public tests/ tree).
"""
import re

from pipeline.research.abstract_undercount import (
    concepts_matching,
    hamilton_allocate,
    wilson_ci,
    _rate_block,
    _percentile,
    paper_level_bootstrap,
)


def _synthetic_patterns():
    # synthetic concept ids + aliases — NOT real lexicon terms
    return {
        "zzsynthetic_alpha": [re.compile(r"sprocket[- ]?based", re.IGNORECASE)],
        "zzsynthetic_beta": [re.compile(r"\bwidget\b", re.IGNORECASE),
                             re.compile(r"gizmo", re.IGNORECASE)],
    }


def test_matching_any_alias_and_ignorecase():
    pats = _synthetic_patterns()
    # first concept via its single alias, case-insensitive
    assert concepts_matching("A Sprocket-Based approach", pats) == {"zzsynthetic_alpha"}
    # second concept matches on EITHER alias (union within concept)
    assert concepts_matching("we use a GIZMO here", pats) == {"zzsynthetic_beta"}
    assert concepts_matching("the widget count", pats) == {"zzsynthetic_beta"}
    # both concepts in one text
    assert concepts_matching("sprocket based widget", pats) == {"zzsynthetic_alpha", "zzsynthetic_beta"}


def test_matching_empty_and_no_hit():
    pats = _synthetic_patterns()
    assert concepts_matching("", pats) == set()
    assert concepts_matching("unrelated prose about turtles", pats) == set()


def test_containment_abstract_subset_of_fulltext():
    # the study defines full = abstract_hits | body_hits, so abstract ⊆ full always
    pats = _synthetic_patterns()
    abs_hits = concepts_matching("sprocket-based intro", pats)       # {alpha}
    body_hits = concepts_matching("later we add a gizmo", pats)      # {beta}
    full = abs_hits | body_hits
    assert abs_hits <= full
    assert full == {"zzsynthetic_alpha", "zzsynthetic_beta"}


def test_hamilton_allocate_sums_to_n_exactly():
    counts = {"2025-07": 100, "2025-08": 100, "2025-09": 100}
    alloc = hamilton_allocate(counts, 9)
    assert sum(alloc.values()) == 9
    assert alloc == {"2025-07": 3, "2025-08": 3, "2025-09": 3}


def test_hamilton_allocate_proportional_and_remainder():
    counts = {"a": 50, "b": 30, "c": 20}
    alloc = hamilton_allocate(counts, 10)
    assert sum(alloc.values()) == 10
    assert alloc == {"a": 5, "b": 3, "c": 2}
    # remainder goes to the largest fractional part (ties broken by key)
    counts2 = {"a": 100, "b": 100, "c": 100}
    alloc2 = hamilton_allocate(counts2, 10)
    assert sum(alloc2.values()) == 10
    assert alloc2["a"] == 4 and alloc2["b"] == 3 and alloc2["c"] == 3


def test_hamilton_allocate_deterministic():
    counts = {"a": 137, "b": 59, "c": 211, "d": 12}
    assert hamilton_allocate(counts, 25) == hamilton_allocate(counts, 25)


def test_wilson_ci_basic():
    p, lo, hi = wilson_ci(5, 10)
    assert p == 0.5
    assert 0.0 < lo < 0.5 < hi < 1.0
    # symmetric around 0.5 for k=n/2
    assert abs((0.5 - lo) - (hi - 0.5)) < 1e-9


def test_wilson_ci_edges():
    assert wilson_ci(0, 0) == (0.0, 0.0, 0.0)     # empty group guarded
    p, lo, hi = wilson_ci(0, 20)
    assert p == 0.0 and lo == 0.0 and hi > 0.0     # one-sided, bounded in [0,1]
    p2, lo2, hi2 = wilson_ci(20, 20)
    assert p2 == 1.0 and hi2 == 1.0 and lo2 < 1.0


def test_rate_block_undercount():
    fulltext = [("p1", "cA"), ("p1", "cB"), ("p2", "cA")]
    abstract = {("p1", "cA")}                        # only 1 of 3 seen abstract-only
    b = _rate_block(fulltext, abstract)
    assert b["fulltext_memberships"] == 3
    assert b["abstract_missed"] == 2
    assert abs(b["undercount_rate"] - 2 / 3) < 1e-3   # result is rounded to 4 dp
    assert abs(b["abstract_recall"] - 1 / 3) < 1e-3


def test_rate_block_no_undercount_when_all_in_abstract():
    fulltext = [("p1", "cA"), ("p2", "cB")]
    abstract = {("p1", "cA"), ("p2", "cB")}
    b = _rate_block(fulltext, abstract)
    assert b["undercount_rate"] == 0.0
    assert b["abstract_recall"] == 1.0


def test_rate_block_empty_group():
    b = _rate_block([], set())
    assert b["fulltext_memberships"] == 0
    assert b["undercount_rate"] == 0.0


def test_percentile_known_values():
    xs = [0.0, 0.25, 0.5, 0.75, 1.0]
    assert _percentile(xs, 0.0) == 0.0
    assert _percentile(xs, 1.0) == 1.0
    assert abs(_percentile(xs, 0.5) - 0.5) < 1e-9
    assert abs(_percentile(xs, 0.25) - 0.25) < 1e-9  # interpolates on the grid


def _synthetic_papers():
    # kinds: zzM/zzM2 = method, zzP = paradigm. abstract concepts seen; body-only missed.
    return [
        {"abstract_concepts": ["zzM"], "body_only_concepts": ["zzM2", "zzP"]},
        {"abstract_concepts": ["zzM"], "body_only_concepts": ["zzP"]},
        {"abstract_concepts": ["zzM", "zzP"], "body_only_concepts": ["zzM2"]},
        {"abstract_concepts": [], "body_only_concepts": ["zzM2"]},
    ]


def test_bootstrap_point_estimate_and_ci_order():
    kinds = {"zzM": "method", "zzM2": "method", "zzP": "paradigm"}
    b = paper_level_bootstrap(_synthetic_papers(), kinds, B=500, seed=1)
    # memberships: seen = zzM,zzM,(zzM,zzP)=4 seen ; missed = zzM2,zzP,zzP,zzM2 = 4? recount:
    # p1: seen{zzM}, missed{zzM2,zzP}; p2: seen{zzM}, missed{zzP}; p3: seen{zzM,zzP}, missed{zzM2};
    # p4: seen{}, missed{zzM2}. total=9, missed=5 -> 0.5556
    assert b["overall"]["undercount"] == round(5 / 9, 4)
    lo, hi = b["overall"]["ci95_bootstrap"]
    assert 0.0 <= lo <= b["overall"]["undercount"] <= hi <= 1.0
    assert set(b["by_concept_kind"]) == {"method", "paradigm"}


def test_bootstrap_deterministic():
    kinds = {"zzM": "method", "zzM2": "method", "zzP": "paradigm"}
    a = paper_level_bootstrap(_synthetic_papers(), kinds, B=300, seed=7)
    c = paper_level_bootstrap(_synthetic_papers(), kinds, B=300, seed=7)
    assert a == c  # same seed -> identical CI
