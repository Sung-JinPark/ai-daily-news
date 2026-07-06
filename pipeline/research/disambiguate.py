"""ML-B — embedding sense-disambiguation to augment precision (2nd instrument).

Dual-instrument rule: the deterministic regex lexicon is PRIMARY and stays the
audited backbone. This is a SECONDARY, *validated* filter — the regex proposes a
mention, an embedding only *confirms* it. Each mention's span is embedded and
compared (cosine) to a per-concept prototype (the mean embedding of the concept's
high-confidence spans); a mention whose span is semantically far from its concept
(`sim < tau`) is flagged a false-positive candidate. This targets exactly the
polysemy FPs PV-1 surfaced (e.g. a generic-sense or homonym match).

Validated against PV-1 labels: tau is calibrated on the TRAIN split and precision/
recall are reported on the held-out TEST split (never tune + report on the same
items). Prototypes / tau / labels are PRIVATE (concept-level → data/research_private/
models/). Only aggregate precision/recall may be published. The sentence-transformer
MODEL is public and pinned (name + versions); the prototypes it produces are private.

Deps: sentence-transformers + torch, LOCAL ONLY (never CI). NO change to the
deterministic instrument — the regex mention set is reported as primary alongside.

Usage: python -m pipeline.research.disambiguate
"""
from __future__ import annotations

import json
import sqlite3
import glob
import os
from pathlib import Path

import numpy as np

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
SEED = 71
DATA = Path("data")
RESEARCH_DB = DATA / "research_private" / "research.db"
PAPERS_DB = DATA / "papers_private" / "papers.db"
LABELSET = DATA / "research_private" / "audits" / "PV-1-2026-07-06" / "labelset_extension_labeled.json"
OUT_DIR = DATA / "research_private" / "models" / "ML-B-2026-07-06"
POOL_PER_CONCEPT = 25  # deterministic exemplar spans per concept for the prototype
RECALL_FLOOR = 0.90    # allow <=10% TP loss when maximizing precision


# ---------- pure functions (unit-tested with synthetic embeddings) ----------

def l2norm(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=float)
    n = np.linalg.norm(X, axis=-1, keepdims=True)
    return X / np.maximum(n, 1e-12)


def build_prototype(embs: np.ndarray) -> np.ndarray:
    """Mean of L2-normalised exemplar embeddings, renormalised."""
    return l2norm(l2norm(embs).mean(axis=0))


def cosine_to(mention_embs: np.ndarray, prototype: np.ndarray) -> np.ndarray:
    return l2norm(mention_embs) @ l2norm(prototype)


def calibrate_tau(scores: np.ndarray, is_tp: np.ndarray, recall_floor: float = RECALL_FLOOR) -> dict:
    """Pick tau (cosine cutoff) maximising kept-set precision subject to keeping
    >= recall_floor of the TPs. Baseline (keep all) is the fallback."""
    scores = np.asarray(scores, dtype=float)
    is_tp = np.asarray(is_tp, dtype=bool)
    n_tp = int(is_tp.sum())
    best = {"tau": float(scores.min() - 1.0), "precision": float(is_tp.mean()),
            "recall": 1.0}  # keep-all
    for tau in np.unique(scores):
        keep = scores >= tau
        if keep.sum() == 0:
            continue
        prec = float(is_tp[keep].mean())
        rec = float(is_tp[keep].sum() / max(1, n_tp))
        if rec >= recall_floor and (prec > best["precision"] or
                                    (prec == best["precision"] and rec > best["recall"])):
            best = {"tau": float(tau), "precision": prec, "recall": rec}
    return best


def evaluate(scores: np.ndarray, is_tp: np.ndarray, tau: float) -> dict:
    """Baseline (deterministic, keep-all) vs +embedding filter on a held-out set."""
    scores = np.asarray(scores, dtype=float)
    is_tp = np.asarray(is_tp, dtype=bool)
    n_tp = int(is_tp.sum())
    n_fp = int((~is_tp).sum())
    keep = scores >= tau
    return {
        "n": int(len(scores)), "n_tp": n_tp, "n_fp": n_fp,
        "baseline_precision": float(is_tp.mean()),
        "filtered_precision": float(is_tp[keep].mean()) if keep.sum() else float("nan"),
        "tp_recall": float(is_tp[keep].sum() / max(1, n_tp)),
        "fp_rejected": int((~is_tp & ~keep).sum()), "fp_total": n_fp,
        "kept": int(keep.sum()),
    }


# ---------- span resolution (private text) ----------

def _load_resolvers() -> dict:
    encorpus = {}
    for f in glob.glob(str(DATA / "research_private" / "en_corpus" / "*.jsonl")):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line:
                r = json.loads(line)
                encorpus[r["article_id"]] = {"title": r.get("title", ""), "text": r.get("text_en", "")}
    arttitle = {}
    for d in sorted(glob.glob(str(DATA / "2???-??-??"))):
        p = os.path.join(d, "articles.json")
        if os.path.exists(p):
            try:
                for a in json.load(open(p, encoding="utf-8")):
                    arttitle[a["id"]] = a.get("title_original", "")
            except Exception:  # noqa: BLE001
                pass
    papers = {}
    if PAPERS_DB.exists():
        pc = sqlite3.connect(PAPERS_DB)
        for aid, title, ab in pc.execute("SELECT arxiv_id, title, abstract FROM papers"):
            papers[aid] = {"title": title or "", "abstract": ab or ""}
        pc.close()
    return {"en": encorpus, "title": arttitle, "papers": papers}


def _span(res: dict, st: str, field: str, sid: str, match: str, pad: int = 90) -> str:
    if st == "news" and field == "title":
        text = res["title"].get(sid, res["en"].get(sid, {}).get("title", ""))
    elif st == "news" and field == "body_en":
        text = res["en"].get(sid, {}).get("text", "")
    elif st == "paper" and field == "title":
        text = res["papers"].get(sid, {}).get("title", "")
    elif st == "paper" and field == "abstract":
        text = res["papers"].get(sid, {}).get("abstract", "")
    else:
        text = ""
    if not text:
        return match
    i = text.lower().find((match or "").lower())
    if i < 0:
        return text[:200]
    a, b = max(0, i - pad), min(len(text), i + len(match) + pad)
    return text[a:b].replace("\n", " ").strip()


# ---------- model ----------

def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(MODEL_NAME)


def _pin() -> dict:
    import sentence_transformers, torch
    return {"model": MODEL_NAME, "sentence_transformers": sentence_transformers.__version__,
            "torch": torch.__version__}


def run(out_dir: Path = OUT_DIR) -> dict:
    labels = json.loads(LABELSET.read_text(encoding="utf-8"))["items"]
    labels = [x for x in labels if x["verdict"] in ("TP", "FP")]  # drop ambiguous
    res = _load_resolvers()
    labelset_sids = {(x["source_type"], x["source_id"], x["field"].split("_", 1)[1]) for x in labels}

    # prototype pool: deterministic EN mentions per concept NOT in the labelset
    con = sqlite3.connect(RESEARCH_DB)
    mv = con.execute("SELECT MAX(lexicon_version) FROM concept_mentions").fetchone()[0]
    concepts = sorted({x["concept_id"] for x in labels})
    cname = dict(con.execute("SELECT concept_id, canonical_name FROM concepts"))
    pool = {}
    for cid in concepts:
        rows = con.execute(
            "SELECT source_type, field, source_id, match_text FROM concept_mentions "
            "WHERE lexicon_version=? AND concept_id=? AND "
            "((source_type='news' AND field IN ('title','body_en')) OR "
            " (source_type='paper' AND field IN ('title','abstract')))", (mv, cid)).fetchall()
        spans = []
        for st, field, sid, match in rows:
            if (st, sid, field) in labelset_sids:
                continue
            spans.append(_span(res, st, field, sid, match))
            if len(spans) >= POOL_PER_CONCEPT:
                break
        # prototype seed = canonical name + pooled exemplar spans
        pool[cid] = [cname.get(cid, cid)] + spans
    con.close()

    model = _model()
    # embed prototype pools
    prototypes = {}
    for cid, texts in pool.items():
        embs = model.encode(texts, normalize_embeddings=False, show_progress_bar=False)
        prototypes[cid] = build_prototype(np.asarray(embs))

    # score labelset mentions
    span_texts = [_span(res, x["source_type"], x["field"].split("_", 1)[1], x["source_id"], x["match"]) for x in labels]
    mention_embs = np.asarray(model.encode(span_texts, show_progress_bar=False))
    scores = np.array([float(cosine_to(mention_embs[i:i + 1], prototypes[labels[i]["concept_id"]])[0])
                       for i in range(len(labels))])
    is_tp = np.array([x["verdict"] == "TP" for x in labels])
    split = np.array([x.get("split", "train") for x in labels])

    tr = split == "train"
    te = split == "test"
    cal = calibrate_tau(scores[tr], is_tp[tr])
    tau = cal["tau"]
    ev_test = evaluate(scores[te], is_tp[te], tau)
    ev_train = evaluate(scores[tr], is_tp[tr], tau)

    # cosine separation TP vs FP (whole labelset — signal check)
    sep = {"tp_mean_cosine": float(scores[is_tp].mean()),
           "fp_mean_cosine": float(scores[~is_tp].mean()),
           "tp_min": float(scores[is_tp].min()), "fp_max": float(scores[~is_tp].max())}

    report = {"pin": _pin(), "seed": SEED, "tau": tau, "recall_floor": RECALL_FLOOR,
              "calibration_train": cal, "held_out_test": ev_test, "train_fit": ev_train,
              "cosine_separation": sep,
              "n_concepts_with_prototype": len(prototypes),
              "note": "deterministic regex remains PRIMARY; embedding is a validated secondary filter."}

    out_dir.mkdir(parents=True, exist_ok=True)
    # private artifacts (prototypes are concept encodings → private)
    np.savez(out_dir / "prototypes.npz", **{c: prototypes[c] for c in prototypes})
    (out_dir / "scores.json").write_text(json.dumps(
        [{"concept_id": labels[i]["concept_id"], "verdict": labels[i]["verdict"],
          "split": labels[i].get("split", "train"), "score": float(scores[i])}
         for i in range(len(labels))], ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[ml-b] tau=%.3f · test baseline=%.3f filtered=%.3f tp_recall=%.3f fp_rejected=%d/%d · TP~FP cos=%.3f/%.3f" % (
        tau, ev_test["baseline_precision"], ev_test["filtered_precision"], ev_test["tp_recall"],
        ev_test["fp_rejected"], ev_test["fp_total"], sep["tp_mean_cosine"], sep["fp_mean_cosine"]))
    print("[ml-b] wrote", out_dir)
    return report


if __name__ == "__main__":
    run()
