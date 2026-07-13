"""FINALIZE-1 — paper figures/tables from ALREADY-CONFIRMED aggregates (LOCAL-ONLY).

No new analysis or data collection: this reads the existing sanitized aggregate
outputs (audit JSONs) plus the frozen findings constants, and renders publication
figures. **Every label is a concept *kind* or an aggregate/significance value — never
a concept name or a per-concept number.** Output is private (gitignored
`notes/figures/`); the paper embeds these.

Figures:
  F1  instrument validation — precision / recall / frozen-30 with Wilson 95% CIs
  F2  H2 — paper-first share + the coverage-bounded lag range [57,125] d
  F3  H3 mechanism — 3-panel null-relative rho bars + re-announcement share trajectory
       (annotated with rho_share and the z-share coupling; the composition verdict)
  T1  lexicon versions + validation summary table

Plot functions are pure (data in, PNG out) so they unit-test without the private JSONs.

Usage: python -m pipeline.research.make_figures
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402

log = logging.getLogger("make_figures")

REPO = Path(__file__).resolve().parents[2]
AUDIT = REPO / "data" / "research_private" / "audits" / "SUB-1-2026-07-13"
OUT_DIR = REPO / "data" / "research_private" / "notes" / "figures"

# Allowed text labels — the ONLY concept-side vocabulary a figure may show (kinds), plus
# fixed metric/axis words. A leak check (and the unit test) asserts labels stay in here.
CONCEPT_KINDS = ("architecture", "method", "paradigm", "task")

# Confirmed findings not stored in a single JSON (sourced from METHODS_DRAFT/decisions).
FINDINGS = {
    "instrument": [  # (label, value%, ci_lo%, ci_hi%)
        ("Precision\n(n=148)", 98.0, 94.2, 99.3),
        ("Recall\n(n=105)", 97.8, 92.2, 99.4),
        ("Frozen-30\nprecision", 100.0, 88.6, 100.0),
    ],
    "h2": {"paper_first_pct": 91.4, "n": 35, "p": "2.1e-7",
           "lag_lo": 57, "lag_hi": 125, "lag_note": "5-mo dense .. 12-mo"},
    "lexicon_versions": [  # (version, n_concepts, kind-level note — NO concept names)
        ("v1", 36, "evidence-based seed"),
        ("v2", 37, "+1 candidate"),
        ("v3", 37, "EN precision pass; +paradigm"),
        ("v4", 38, "precision tighten (guards, de-dup)"),
        ("v5", 38, "recall alias expansion"),
        ("v6", 38, "recall expansion (context-qualified)"),
    ],
}


# ---------- pure plot functions (data in, PNG out) ----------

def plot_instrument(metrics: list, out: Path) -> Path:
    """F1: point estimate + Wilson CI whiskers for each validation metric."""
    fig, ax = plt.subplots(figsize=(5.2, 3.4))
    xs = range(len(metrics))
    vals = [m[1] for m in metrics]
    lo = [m[1] - m[2] for m in metrics]
    hi = [m[3] - m[1] for m in metrics]
    ax.errorbar(list(xs), vals, yerr=[lo, hi], fmt="o", capsize=6, color="#2b6cb0",
                markersize=8, linewidth=1.6)
    ax.set_xticks(list(xs))
    ax.set_xticklabels([m[0] for m in metrics])
    ax.set_ylim(85, 101)
    ax.set_ylabel("percent (95% Wilson CI)")
    ax.set_title("Instrument validation")
    ax.grid(axis="y", alpha=0.3)
    for x, v in zip(xs, vals):
        ax.annotate(f"{v:.1f}", (x, v), textcoords="offset points", xytext=(10, 0),
                    va="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_h2(h2: dict, out: Path) -> Path:
    """F2: paper-first share bar + coverage-bounded lag range."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(7.4, 3.4))
    a1.bar([0], [h2["paper_first_pct"]], width=0.5, color="#2f855a")
    a1.axhline(50, ls="--", color="gray", lw=1)
    a1.set_ylim(0, 100)
    a1.set_xticks([0]); a1.set_xticklabels([f"paper-first\n(n={h2['n']})"])
    a1.set_ylabel("% of concepts paper-first")
    a1.set_title(f"H2 direction (sign-test p={h2['p']})")
    a1.annotate(f"{h2['paper_first_pct']:.1f}%", (0, h2["paper_first_pct"]),
                textcoords="offset points", xytext=(0, 4), ha="center", fontsize=10)

    a2.hlines(0, h2["lag_lo"], h2["lag_hi"], color="#c05621", lw=6, alpha=0.5)
    a2.plot([h2["lag_lo"], h2["lag_hi"]], [0, 0], "o", color="#c05621", markersize=9)
    a2.set_xlim(0, max(150, h2["lag_hi"] + 20)); a2.set_ylim(-1, 1)
    a2.set_yticks([])
    a2.set_xlabel("paper→news lag (days)")
    a2.set_title("H2 magnitude (coverage-bounded)")
    for x in (h2["lag_lo"], h2["lag_hi"]):
        a2.annotate(f"{x} d", (x, 0), textcoords="offset points", xytext=(0, 10),
                    ha="center", fontsize=9)
    a2.annotate(h2["lag_note"], (0.5, 0.82), xycoords="axes fraction", ha="center",
                fontsize=8, color="gray")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_h3(attribution: dict, s_t: list, rho_share: float, z_share: float, out: Path) -> Path:
    """F3: 3-panel null-relative rho bars + re-announcement share trajectory."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(8.2, 3.6))
    labels = ["full", "originals-\nonly", "re-announced-\nonly"]
    vals = [attribution["full"], attribution["originals_only"], attribution["reann_only"]]
    colors = ["#2b6cb0", "#a0aec0", "#a0aec0"]
    a1.bar(range(3), vals, color=colors, width=0.6)
    a1.axhline(0, color="black", lw=0.8)
    a1.set_xticks(range(3)); a1.set_xticklabels(labels, fontsize=9)
    a1.set_ylabel("null-relative modularity trend  ρ")
    a1.set_title("H3 attribution: only pooled is strong")
    for i, v in enumerate(vals):
        a1.annotate(f"{v:.2f}", (i, v), textcoords="offset points",
                    xytext=(0, -12 if v < 0 else 4), ha="center", fontsize=9)

    a2.plot(range(len(s_t)), [x * 100 for x in s_t], "-o", color="#c05621", markersize=3)
    a2.set_xlabel("biweekly snapshot (time →)")
    a2.set_ylabel("re-announced share (%)")
    a2.set_title("Mechanism: share declines, structure follows")
    a2.grid(alpha=0.3)
    a2.annotate(f"ρ(share,time) = {rho_share:+.2f}\nρ(structure,share) = {z_share:+.2f}",
                (0.04, 0.06), xycoords="axes fraction", fontsize=8.5,
                bbox=dict(boxstyle="round", fc="#fffaf0", ec="#c05621", alpha=0.9))
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def plot_summary_table(lex_rows: list, val_rows: list, out: Path) -> Path:
    """T1: lexicon versions + validation summary as a rendered table image."""
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(7.6, 4.6),
                                 gridspec_kw={"height_ratios": [3, 3]})
    for ax in (a1, a2):
        ax.axis("off")
    t1 = a1.table(cellText=[[v, str(n), note] for v, n, note in lex_rows],
                  colLabels=["lexicon", "concepts", "change (kind-level)"],
                  loc="center", cellLoc="left")
    t1.auto_set_font_size(False); t1.set_fontsize(8.5); t1.scale(1, 1.3)
    a1.set_title("Lexicon versioning (v1–v6)", fontsize=10, loc="left")
    t2 = a2.table(cellText=val_rows, colLabels=["metric", "value (95% CI / detail)"],
                  loc="center", cellLoc="left")
    t2.auto_set_font_size(False); t2.set_fontsize(8.5); t2.scale(1, 1.3)
    a2.set_title("Validation & robustness summary", fontsize=10, loc="left")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------- label governance ----------

def figure_labels_are_aggregate(labels: list) -> bool:
    """True iff every free-text label is a kind, a fixed metric word, or numeric —
    i.e. no concept names. Used by the unit test and pre-commit scan."""
    allowed_words = set(CONCEPT_KINDS) | {
        "full", "originals", "only", "re", "announced", "paper", "first", "recall",
        "precision", "frozen", "lag", "days", "share", "structure", "time", "snapshot",
        "concepts", "metric", "value", "detail", "lexicon", "change", "level", "kind",
    }
    import re as _re
    for lab in labels:
        for tok in _re.findall(r"[A-Za-z]{3,}", str(lab).lower()):
            if tok not in allowed_words:
                return False
    return True


# ---------- orchestrator ----------

def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def build() -> list:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hd = _load(AUDIT / "h3_decide.json", {})
    uc = _load(AUDIT / "undercount_result.json", {})
    boot = _load(AUDIT / "undercount_bootstrap.json", {})

    out = []
    out.append(plot_instrument(FINDINGS["instrument"], OUT_DIR / "F1_instrument.png"))
    out.append(plot_h2(FINDINGS["h2"], OUT_DIR / "F2_h2.png"))

    attr = hd.get("diag2_attribution", {})
    attribution = {
        "full": attr.get("full", {}).get("null_relative", {}).get("rho", -0.52),
        "originals_only": attr.get("originals_only", {}).get("null_relative", {}).get("rho", -0.25),
        "reann_only": attr.get("reann_only", {}).get("null_relative", {}).get("rho", -0.17),
    }
    share = hd.get("diag1_share", {})
    out.append(plot_h3(attribution, share.get("s_t", []),
                       share.get("rho_share_vs_time", {}).get("rho", -0.59),
                       share.get("rho_z_vs_share", {}).get("rho", 0.47),
                       OUT_DIR / "F3_h3_mechanism.png"))

    uc_over = uc.get("overall", {})
    ucr = uc_over.get("undercount_rate", 0.765) * 100
    ucci = uc_over.get("undercount_ci95", [0.733, 0.795])
    val_rows = [
        ["precision", "98.0% (94.2–99.3, n=148)"],
        ["recall", "97.8% (92.2–99.4, n=105)"],
        ["coverage-robust count model", "raw daily range 96.5× → 5.3×"],
        ["dual-instrument agreement", "Pearson 0.69"],
        ["abstract salience under-count", f"{ucr:.1f}% ({ucci[0]*100:.1f}–{ucci[1]*100:.1f})"],
        ["H3 verdict", "composition artifact (power rejected)"],
    ]
    out.append(plot_summary_table(FINDINGS["lexicon_versions"], val_rows,
                                  OUT_DIR / "T1_summary.png"))
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    outs = build()
    for p in outs:
        log.info("[figure] %s", p)
    log.info("[done] %d figures -> %s", len(outs), OUT_DIR)


if __name__ == "__main__":
    main()
