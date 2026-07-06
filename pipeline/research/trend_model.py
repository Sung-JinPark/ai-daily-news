"""ML-A — coverage-robust concept-count model (NB-GLM + coverage offset).

The paper's dual-instrument rule: the deterministic regex lexicon is the PRIMARY
instrument; this is a SECONDARY, *validated* count model layered on top of it — it
does not replace the backbone, it corrects the naive daily counts for coverage.

Per-day concept mention counts are contaminated by *real volume x coverage*: a
day with many articles and high body_en coverage produces more mentions than a
sparse, link-rotted day regardless of concept dynamics (COV-1). A naive velocity
therefore measures coverage, not dynamics. This fits

    y_{c,t} ~ NegativeBinomial(mu_{c,t}, alpha)          # alpha = overdispersion
    log mu_{c,t} = concept_FE + beta * t + log(exposure_{c,t})

with the covered-article count as an **offset** (coefficient fixed at 1), so mu is
a coverage-corrected rate and beta is the coverage-corrected log-rate velocity.
The coverage-uniform **title-only** series (every article has a title) is fit the
same way as a robustness control; agreement between the two is the dual-instrument
validation.

Private (G2): concept-level output stays in data/research_private/ — never public
(only sanitized aggregates go out). Deterministic: fixed snapshot day + seed;
statsmodels version recorded. NO LLM.

Usage: python -m pipeline.research.trend_model
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels
import statsmodels.api as sm
from patsy import dmatrices

SEED = 20260706
RESEARCH_DB = Path("data") / "research_private" / "research.db"
COVERAGE_JSON = Path("data") / "research_private" / "audits" / "COV-1-2026-07-06" / "coverage_diagnostic.json"
OUT_DIR = Path("data") / "research_private" / "analysis" / "ML-A-2026-07-06"
MIN_NONZERO_DAYS = 5  # a concept needs this many non-zero days to fit a slope


# ---------- data ----------

def load_panel(db: Path = RESEARCH_DB, coverage_json: Path = COVERAGE_JSON) -> pd.DataFrame:
    """concept x day panel of news mention counts + per-day exposure.

    Columns: concept_id, day, t (day index), title, body_en, enriched
    (title+body_en), n_articles, n_bodies (covered = exposure).
    Latest lexicon version only. Full grid (zeros are data for a count model).
    """
    con = sqlite3.connect(db)
    mv = con.execute("SELECT MAX(lexicon_version) FROM concept_mentions").fetchone()[0]
    rows = con.execute(
        "SELECT concept_id, day, field, COUNT(1) FROM concept_mentions "
        "WHERE lexicon_version=? AND source_type='news' AND field IN ('title','body_en') "
        "GROUP BY concept_id, day, field", (mv,)).fetchall()
    con.close()
    long = pd.DataFrame(rows, columns=["concept_id", "day", "field", "n"])
    piv = (long.pivot_table(index=["concept_id", "day"], columns="field",
                            values="n", fill_value=0).reset_index())
    for col in ("title", "body_en"):
        if col not in piv.columns:
            piv[col] = 0

    cov = {r["day"]: r for r in json.loads(Path(coverage_json).read_text(encoding="utf-8"))["per_day"]}
    days = sorted(d for d, r in cov.items() if r["articles"] > 0)
    concepts = sorted(piv["concept_id"].unique())
    grid = pd.MultiIndex.from_product([concepts, days], names=["concept_id", "day"]).to_frame(index=False)
    m = grid.merge(piv, on=["concept_id", "day"], how="left").fillna({"title": 0, "body_en": 0})
    m["title"] = m["title"].astype(int)
    m["body_en"] = m["body_en"].astype(int)
    m["enriched"] = m["title"] + m["body_en"]
    m["n_articles"] = m["day"].map(lambda d: int(cov[d]["articles"]))
    m["n_bodies"] = m["day"].map(lambda d: max(1, int(cov[d]["bodies"])))
    day_idx = {d: i for i, d in enumerate(days)}
    m["t"] = m["day"].map(day_idx).astype(int)
    return m.sort_values(["concept_id", "t"]).reset_index(drop=True)


# ---------- models (pure; unit-testable with synthetic frames) ----------

def overdispersion_test(df: pd.DataFrame, count_col: str = "enriched",
                        exposure_col: str = "n_bodies") -> dict:
    """Pooled Poisson vs NB with concept fixed effects + linear trend + offset.
    Returns dispersion diagnostics and the LR test for overdispersion (alpha>0)."""
    y, X = dmatrices(f"{count_col} ~ C(concept_id) + t", df, return_type="dataframe")
    off = np.log(df[exposure_col].to_numpy(dtype=float))
    pois = sm.Poisson(y, X, offset=off).fit(disp=0, maxiter=200)
    mu = pois.predict()
    yv = df[count_col].to_numpy(dtype=float)
    dof = len(yv) - X.shape[1]
    pearson_disp = float(((yv - mu) ** 2 / np.maximum(mu, 1e-9)).sum() / dof)
    result = {"n_obs": int(len(yv)), "poisson_pearson_dispersion": pearson_disp,
              "poisson_llf": float(pois.llf)}
    try:
        nb = sm.NegativeBinomial(y, X, offset=off).fit(disp=0, maxiter=200)
        alpha = float(nb.params.iloc[-1]) if hasattr(nb.params, "iloc") else float(nb.params[-1])
        lr = float(2 * (nb.llf - pois.llf))
        # boundary test alpha=0: p = 0.5 * P(chi2_1 > LR)
        from scipy import stats as _st
        pval = float(0.5 * _st.chi2.sf(max(lr, 0.0), 1))
        # NB nests Poisson at alpha=0; the LR (boundary) test is the proper
        # decision. Report magnitude separately (dispersion can be modest even
        # when the test is highly significant).
        result.update({"nb_alpha": alpha, "nb_llf": float(nb.llf),
                       "lr_stat": lr, "lr_pvalue": pval,
                       "nb_justified": bool(lr > 3.84),
                       "overdispersion_modest": bool(pearson_disp < 1.5)})
    except Exception as exc:  # noqa: BLE001
        result.update({"nb_alpha": None, "nb_error": str(exc)[:200], "nb_justified": None})
    return result


def fit_velocities(df: pd.DataFrame, count_col: str, exposure_col: str,
                   min_nonzero: int = MIN_NONZERO_DAYS) -> pd.DataFrame:
    """Per-concept NB-GLM: count ~ t with log(exposure) offset. beta = coverage-
    corrected log-rate velocity (per day) + 95% CI. Concepts with too few non-zero
    days are reported as not fit (NaN) rather than forced."""
    out = []
    for cid, g in df.groupby("concept_id"):
        nz = int((g[count_col] > 0).sum())
        rec = {"concept_id": cid, "n_nonzero": nz, "velocity": np.nan,
               "ci_lo": np.nan, "ci_hi": np.nan, "fit": False}
        if nz >= min_nonzero:
            try:
                yv = g[count_col].to_numpy(dtype=float)
                X = sm.add_constant(g["t"].to_numpy(dtype=float))
                off = np.log(g[exposure_col].to_numpy(dtype=float))
                r = sm.NegativeBinomial(yv, X, offset=off).fit(disp=0, maxiter=200)
                ci = r.conf_int()
                rec.update({"velocity": float(r.params[1]),
                            "ci_lo": float(ci[1][0]), "ci_hi": float(ci[1][1]),
                            "fit": True})
            except Exception:  # noqa: BLE001
                pass
        out.append(rec)
    return pd.DataFrame(out)


def corrected_rate_series(df: pd.DataFrame, count_col: str = "enriched",
                          exposure_col: str = "n_bodies") -> pd.DataFrame:
    """Empirical coverage-corrected daily rate per concept = count / exposure."""
    s = df.copy()
    s["rate"] = s[count_col] / s[exposure_col]
    return s[["concept_id", "day", "t", count_col, exposure_col, "rate"]]


# ---------- validation ----------

def _range_ratio(a: np.ndarray) -> float:
    a = a[a > 0]
    return float(a.max() / a.min()) if len(a) else float("nan")


def validate(df: pd.DataFrame, vel_corr: pd.DataFrame, vel_title: pd.DataFrame,
             overdisp: dict) -> dict:
    # raw vs corrected: daily corpus totals vs daily corrected rate
    daily = df.groupby("day").agg(raw=("enriched", "sum"),
                                  bodies=("n_bodies", "first")).reset_index()
    daily["corrected"] = daily["raw"] / daily["bodies"]
    raw_cv = float(daily["raw"].std() / daily["raw"].mean())
    corr_cv = float(daily["corrected"].std() / daily["corrected"].mean())

    # title vs corrected velocity agreement (dual instrument)
    j = vel_title.merge(vel_corr, on="concept_id", suffixes=("_title", "_corr"))
    j = j[j["fit_title"] & j["fit_corr"]]
    from scipy import stats as _st
    if len(j) >= 3:
        pear = float(_st.pearsonr(j["velocity_title"], j["velocity_corr"])[0])
        spear = float(_st.spearmanr(j["velocity_title"], j["velocity_corr"])[0])
        sign_agree = float((np.sign(j["velocity_title"]) == np.sign(j["velocity_corr"])).mean())
    else:
        pear = spear = sign_agree = float("nan")
    return {
        "overdispersion": overdisp,
        "raw_vs_corrected": {
            "raw_daily_range_ratio": _range_ratio(daily["raw"].to_numpy()),
            "corrected_daily_range_ratio": _range_ratio(daily["corrected"].to_numpy()),
            "raw_cv": raw_cv, "corrected_cv": corr_cv,
            "artifact_reduced": bool(corr_cv < raw_cv),
        },
        "dual_instrument_title_vs_corrected": {
            "n_concepts_compared": int(len(j)),
            "pearson_r": pear, "spearman_r": spear, "sign_agreement": sign_agree,
        },
        "n_concepts_fit_corrected": int(vel_corr["fit"].sum()),
        "n_concepts_fit_title": int(vel_title["fit"].sum()),
    }


# ---------- orchestration ----------

def run(out_dir: Path = OUT_DIR) -> dict:
    np.random.seed(SEED)
    df = load_panel()
    overdisp = overdispersion_test(df)
    vel_corr = fit_velocities(df, "enriched", "n_bodies")
    vel_title = fit_velocities(df, "title", "n_articles")
    rates = corrected_rate_series(df)
    report = validate(df, vel_corr, vel_title, overdisp)
    report["snapshot"] = {"as_of": "2026-07-06", "seed": SEED,
                          "statsmodels": statsmodels.__version__,
                          "n_concepts": int(df["concept_id"].nunique()),
                          "n_days": int(df["t"].nunique())}

    out_dir.mkdir(parents=True, exist_ok=True)
    vel = vel_title.merge(vel_corr, on="concept_id", suffixes=("_title", "_corr"))
    vel.to_parquet(out_dir / "concept_velocities.parquet", index=False)
    rates.to_parquet(out_dir / "corrected_rate_series.parquet", index=False)
    (out_dir / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[ml-a] overdispersion:", report["overdispersion"].get("nb_justified"),
          "· raw_cv=%.2f corr_cv=%.2f" % (report["raw_vs_corrected"]["raw_cv"],
                                          report["raw_vs_corrected"]["corrected_cv"]),
          "· title~corr r=%.3f" % report["dual_instrument_title_vs_corrected"]["pearson_r"])
    print("[ml-a] wrote", out_dir)
    return report


if __name__ == "__main__":
    run()
