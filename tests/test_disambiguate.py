"""ML-B — tests for the embedding sense-disambiguation filter.

Pure functions on SYNTHETIC embeddings / scores (no model, no real lexicon,
no real prototypes — tests/ is public). Validates: prototype gives higher
cosine to in-sense spans than out-of-sense ones, tau calibration separates
TP/FP, the filter rejects sub-tau FPs, and scoring is deterministic.
"""
import numpy as np

from pipeline.research.disambiguate import (
    build_prototype, calibrate_tau, cosine_to, evaluate, l2norm,
)


def test_prototype_is_unit_norm():
    embs = np.random.default_rng(3).normal(size=(4, 10))
    p = build_prototype(embs)
    assert abs(float(np.linalg.norm(p)) - 1.0) < 1e-6


def test_in_sense_spans_score_higher_than_out_of_sense():
    rng = np.random.default_rng(1)
    d = 16
    direction = rng.normal(size=d)
    tp = direction + 0.05 * rng.normal(size=(10, d))   # near the concept sense
    fp = rng.normal(size=(6, d))                        # unrelated sense
    proto = build_prototype(tp)
    assert cosine_to(tp, proto).mean() > cosine_to(fp, proto).mean() + 0.2
    assert cosine_to(tp, proto).min() > 0.4


def test_calibrate_tau_separates_tp_fp():
    scores = np.array([0.8, 0.85, 0.9, 0.75, 0.7, 0.2, 0.15, 0.3])
    is_tp = np.array([1, 1, 1, 1, 1, 0, 0, 0], dtype=bool)
    cal = calibrate_tau(scores, is_tp, recall_floor=0.9)
    assert 0.3 < cal["tau"] <= 0.7      # cutoff lands in the gap
    assert cal["precision"] == 1.0
    assert cal["recall"] == 1.0


def test_calibrate_respects_recall_floor():
    # one TP sits low (0.25) among the FPs; a high recall floor forbids dropping it
    scores = np.array([0.9, 0.85, 0.8, 0.25, 0.2, 0.15])
    is_tp = np.array([1, 1, 1, 1, 0, 0], dtype=bool)
    cal = calibrate_tau(scores, is_tp, recall_floor=1.0)
    assert cal["recall"] == 1.0          # cannot drop the low TP
    assert cal["tau"] <= 0.25


def test_evaluate_baseline_vs_filtered():
    scores = np.array([0.8, 0.9, 0.2, 0.7, 0.15])
    is_tp = np.array([1, 1, 0, 1, 0], dtype=bool)
    ev = evaluate(scores, is_tp, tau=0.5)
    assert ev["baseline_precision"] == 3 / 5
    assert ev["filtered_precision"] == 1.0
    assert ev["tp_recall"] == 1.0
    assert ev["fp_rejected"] == 2


def test_scoring_is_deterministic():
    rng = np.random.default_rng(2)
    proto = build_prototype(rng.normal(size=(5, 8)))
    m = rng.normal(size=(3, 8))
    assert np.allclose(cosine_to(m, proto), cosine_to(m, proto))


def test_l2norm_handles_zero_vector():
    z = np.zeros((1, 5))
    out = l2norm(z)
    assert np.isfinite(out).all()
