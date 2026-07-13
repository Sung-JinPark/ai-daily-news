"""FINALIZE-1 — figure generation (synthetic data, no private JSON or lexicon).

Confirms plot functions emit valid PNGs and that the label-governance check
distinguishes aggregate/kind labels from concept-name-like tokens.
"""
from pipeline.research.make_figures import (
    plot_instrument,
    plot_h2,
    plot_h3,
    plot_summary_table,
    figure_labels_are_aggregate,
    CONCEPT_KINDS,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _is_png(path) -> bool:
    with open(path, "rb") as f:
        return f.read(8) == PNG_MAGIC and path.stat().st_size > 1000


def test_labels_aggregate_true_for_kinds_and_metrics():
    labels = list(CONCEPT_KINDS) + ["full", "originals-only", "paper-first",
                                    "lag (days)", "precision", "recall", "share"]
    assert figure_labels_are_aggregate(labels) is True


def test_labels_aggregate_false_for_concept_name():
    # a hypothetical concept surface form must be rejected by the governance check
    assert figure_labels_are_aggregate(["zzsyntheticconcept"]) is False
    assert figure_labels_are_aggregate(["architecture", "someconceptword"]) is False


def test_plot_instrument_png(tmp_path):
    out = plot_instrument([("Precision", 98.0, 94.2, 99.3), ("Recall", 97.8, 92.2, 99.4)],
                          tmp_path / "f1.png")
    assert _is_png(out)


def test_plot_h2_png(tmp_path):
    out = plot_h2({"paper_first_pct": 91.4, "n": 35, "p": "2.1e-7",
                   "lag_lo": 57, "lag_hi": 125, "lag_note": "range"}, tmp_path / "f2.png")
    assert _is_png(out)


def test_plot_h3_png(tmp_path):
    out = plot_h3({"full": -0.52, "originals_only": -0.25, "reann_only": -0.17},
                  [0.29, 0.30, 0.28, 0.0], -0.59, 0.47, tmp_path / "f3.png")
    assert _is_png(out)


def test_plot_summary_table_png(tmp_path):
    out = plot_summary_table([("v1", 36, "seed"), ("v6", 38, "recall expansion")],
                             [["precision", "98.0%"], ["recall", "97.8%"]],
                             tmp_path / "t1.png")
    assert _is_png(out)
