"""SUB-1 — abstract↔full-text concept-undercount sub-study (LOCAL-ONLY).

Quantifies the construct-validity limitation of the abstract-only paper
instrument: how many concept×paper *memberships* that are present when the
full body is read are *missed* when only title+abstract are read. This turns
"abstract-only" from an unspoken assumption into a measured limitation with a
Wilson CI for the paper's Limitations section.

Design (deterministic, reproducible):
  * FRAME  = the frozen all-arXiv analysis panel (papers.db master, the exact
    corpus the H1/H2/H3 findings were computed on: published in
    [2025-07, 2026-07]). Sampling from this frame — NOT the 587 news-linked
    papers — is what makes the findings-defense valid.
  * SAMPLE = N papers, stratified by ``published`` month (Hamilton
    largest-remainder allocation), seed-fixed selection within each month.
  * INSTRUMENT = the v6 lexicon, replicated exactly: aliases compiled with
    ``re.IGNORECASE`` (parity with research_db.compile_alias), a concept
    matches a text if ANY of its aliases matches (rx.search).
  * DUAL APPLICATION per sampled paper p and concept c:
        abstract_match(p,c) = alias hits (title + abstract)          [clean DB text]
        fulltext_match(p,c) = abstract_match OR alias hits (PDF body)
    Containment abstract ⊆ fulltext holds by construction.
  * METRIC  = undercount = |fulltext-only memberships| / |fulltext memberships|
    = 1 − abstract-only recall. Reported overall (Wilson 95% CI), by concept
    kind (§01 predicts method/technique concepts undercount most), and by time
    stratum (2025-H2 vs 2026-H1 — convention stability). event_day (=published)
    is never recomputed, so it is invariant to adding the body (asserted).

GOVERNANCE (concept-research-methodology skill):
  * PDFs + extracted text are LOCAL-ONLY (DBQ-3 spirit): cached under
    ``data/papers_private/`` (gitignored). Never committed / republished.
  * Per-concept results (concept_ids) → gitignored
    ``data/research_private/audits/SUB-1-2026-07-13/`` only.
  * stdout and the paper docs get KINDS + AGGREGATES only — never concept names.
  * Nothing here touches the public corpus manifest or public data schema.

Usage:
    python -m pipeline.research.abstract_undercount            # N=100, seed 20260713
    python -m pipeline.research.abstract_undercount --n 100 --seed 20260713
    python -m pipeline.research.abstract_undercount --dry-run  # sample+match only, no download
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("abstract_undercount")

REPO = Path(__file__).resolve().parents[2]
FROZEN_RESEARCH_DB = REPO / "data" / "research_private" / "research.db.h2-analysis-frozen-2026-07-08"
MASTER_PAPERS_DB = REPO / "data" / "papers_private" / "papers.db"
PDF_CACHE = REPO / "data" / "papers_private" / "undercount_pdfcache"          # gitignored
OUT_DIR = REPO / "data" / "research_private" / "audits" / "SUB-1-2026-07-13"  # gitignored

WINDOW_LO, WINDOW_HI = "2025-07-01", "2026-07-31"
LEXICON_VERSION = 6
ARXIV_UA = ("ai-daily-news-research/1.0 "
            "(abstract-undercount substudy; contact: 91ssjj@gmail.com)")


# ---------- instrument (v6 lexicon replica) ----------

def load_lexicon(research_db: Path, version: int):
    """Return (patterns_by_concept, kind_by_concept) for the given version.

    patterns_by_concept: {concept_id: [compiled regex, ...]}
    kind_by_concept:     {concept_id: kind}
    Compilation parity with research_db.compile_alias (re.IGNORECASE).
    """
    conn = sqlite3.connect(research_db)
    try:
        acols = {r[1] for r in conn.execute("PRAGMA table_info(aliases)")}
        if "added_version" in acols:
            rows = conn.execute(
                "SELECT concept_id, pattern FROM aliases WHERE added_version <= ?",
                (version,),
            ).fetchall()
        else:  # defensive: no versioning column -> use all aliases
            rows = conn.execute("SELECT concept_id, pattern FROM aliases").fetchall()
        kinds = dict(conn.execute("SELECT concept_id, kind FROM concepts").fetchall())
    finally:
        conn.close()
    patterns: dict[str, list] = {}
    for cid, pattern in sorted(rows):
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:  # a broken alias must not silently drop
            raise SystemExit(f"alias compile failed for {cid}: {exc}")
        patterns.setdefault(cid, []).append(rx)
    return patterns, kinds


def concepts_matching(text: str, patterns_by_concept: dict) -> set:
    """Set of concept_ids with >=1 alias matching text (rx.search)."""
    if not text:
        return set()
    hit = set()
    for cid, pats in patterns_by_concept.items():
        for rx in pats:
            if rx.search(text):
                hit.add(cid)
                break
    return hit


# ---------- sampling (stratified by published month, seed-fixed) ----------

def hamilton_allocate(counts: dict, n: int) -> dict:
    """Largest-remainder apportionment of n across strata proportional to
    counts. Deterministic. Returns {stratum: k} summing to exactly n."""
    total = sum(counts.values())
    raw = {k: (n * v / total) for k, v in counts.items()}
    floor = {k: int(math.floor(x)) for k, x in raw.items()}
    remainder = n - sum(floor.values())
    # distribute leftover to largest fractional parts (ties broken by key)
    order = sorted(counts, key=lambda k: (-(raw[k] - floor[k]), k))
    for k in order[:remainder]:
        floor[k] += 1
    return floor


def stratified_sample(papers_db: Path, n: int, seed: int):
    """Return (sample_rows, allocation, month_counts).

    sample_rows: list of dicts {arxiv_id, published, pdf_url, title, abstract}
    stratified by published month over the frozen window, seed-fixed.
    """
    conn = sqlite3.connect(papers_db)
    try:
        month_counts = dict(conn.execute(
            "SELECT substr(published,1,7) ym, COUNT(*) FROM papers "
            "WHERE published >= ? AND published <= ? GROUP BY 1 ORDER BY 1",
            (WINDOW_LO, WINDOW_HI),
        ).fetchall())
        # drop negligible tail months from allocation but keep in the frame report
        alloc_counts = {k: v for k, v in month_counts.items() if v >= 50}
        allocation = hamilton_allocate(alloc_counts, n)
        sample_rows = []
        for ym, k in sorted(allocation.items()):
            if k <= 0:
                continue
            ids = [r[0] for r in conn.execute(
                "SELECT arxiv_id FROM papers WHERE substr(published,1,7)=? ORDER BY arxiv_id",
                (ym,),
            ).fetchall()]
            rng = random.Random(f"{seed}:{ym}")
            picks = rng.sample(ids, min(k, len(ids)))
            for aid in sorted(picks):
                row = conn.execute(
                    "SELECT arxiv_id, published, pdf_url, title, abstract "
                    "FROM papers WHERE arxiv_id=?", (aid,)).fetchone()
                sample_rows.append({
                    "arxiv_id": row[0], "published": row[1],
                    "pdf_url": row[2], "title": row[3] or "",
                    "abstract": row[4] or "",
                })
    finally:
        conn.close()
    return sample_rows, allocation, month_counts


# ---------- PDF fetch + extract (LOCAL-ONLY cache) ----------

def fetch_and_extract(arxiv_id: str, pdf_url: str | None, client, sleep_sec: float) -> tuple[str, str]:
    """Return (status, body_text). status in {ok, cached, http_error, extract_error}.
    Caches extracted text under PDF_CACHE (gitignored). Politeness sleep applies
    only on an actual network fetch."""
    import fitz  # PyMuPDF

    safe = arxiv_id.replace("/", "_")
    txt_path = PDF_CACHE / f"{safe}.txt"
    if txt_path.exists():
        return "cached", txt_path.read_text(encoding="utf-8", errors="replace")

    from pipeline.utils.http import fetch
    url = pdf_url or f"https://arxiv.org/pdf/{arxiv_id}"
    try:
        resp = fetch(url, client=client)
        resp.raise_for_status()
        data = resp.content
    except Exception as exc:
        log.warning("[pdf] %s http failed: %s", arxiv_id, exc)
        time.sleep(sleep_sec)
        return "http_error", ""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
        body = "\n".join(page.get_text() for page in doc)
        doc.close()
    except Exception as exc:
        log.warning("[pdf] %s extract failed: %s", arxiv_id, exc)
        time.sleep(sleep_sec)
        return "extract_error", ""
    txt_path.write_text(body, encoding="utf-8")
    time.sleep(sleep_sec)  # arXiv politeness between network fetches
    return "ok", body


# ---------- stats ----------

def wilson_ci(k: int, n: int, z: float = 1.959963984540054):
    """Wilson score 95% CI for a binomial proportion. Returns (p, lo, hi)."""
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, center - half), min(1.0, center + half))


def _rate_block(fulltext_pairs: list, abstract_pairs: set) -> dict:
    """fulltext_pairs = list of (paper, concept) memberships found with body;
    abstract_pairs = set of those also found abstract-only. Returns undercount
    stats over this group."""
    n_full = len(fulltext_pairs)
    n_missed = sum(1 for pr in fulltext_pairs if pr not in abstract_pairs)
    p, lo, hi = wilson_ci(n_missed, n_full)
    return {
        "fulltext_memberships": n_full,
        "abstract_missed": n_missed,
        "undercount_rate": round(p, 4),
        "undercount_ci95": [round(lo, 4), round(hi, 4)],
        "abstract_recall": round(1 - p, 4),
    }


# ---------- orchestrator ----------

def run(n: int, seed: int, sleep_sec: float, dry_run: bool,
        research_db: Path, papers_db: Path) -> dict:
    PDF_CACHE.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    patterns, kinds = load_lexicon(research_db, LEXICON_VERSION)
    log.info("[lexicon] v%d: %d concepts, %d aliases",
             LEXICON_VERSION, len(patterns), sum(len(v) for v in patterns.values()))

    sample, allocation, month_counts = stratified_sample(papers_db, n, seed)
    log.info("[sample] N=%d over %d months (seed=%s)", len(sample), len(allocation), seed)

    import httpx
    from pipeline.utils.http import DEFAULT_TIMEOUT
    client = None if dry_run else httpx.Client(
        headers={"User-Agent": ARXIV_UA, "Accept": "*/*"},
        timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    fulltext_pairs: list = []          # (arxiv_id, concept_id) present with body
    abstract_pairs: set = set()        # subset present in title+abstract
    per_paper = []
    fetch_status = {"ok": 0, "cached": 0, "http_error": 0, "extract_error": 0, "skipped": 0}
    event_day_invariant = True

    try:
        for i, pap in enumerate(sample, 1):
            aid = pap["arxiv_id"]
            abstract_text = f"{pap['title']}\n{pap['abstract']}"
            abs_hits = concepts_matching(abstract_text, patterns)

            body = ""
            if dry_run:
                fetch_status["skipped"] += 1
                status = "skipped"
            else:
                status, body = fetch_and_extract(aid, pap["pdf_url"], client, sleep_sec)
                fetch_status[status] = fetch_status.get(status, 0) + 1

            body_hits = concepts_matching(body, patterns) if body else set()
            full_hits = abs_hits | body_hits  # containment by construction

            # event_day (=published) is read-only here; assert it is untouched.
            if pap["published"] is None:
                event_day_invariant = False

            processed = bool(body) or dry_run
            if status in ("ok", "cached"):
                for cid in full_hits:
                    fulltext_pairs.append((aid, cid))
                    if cid in abs_hits:
                        abstract_pairs.add((aid, cid))
            per_paper.append({
                "arxiv_id": aid, "published": pap["published"], "status": status,
                "body_chars": len(body),
                "abstract_concepts": sorted(abs_hits),
                "body_only_concepts": sorted(body_hits - abs_hits),
            })
            if i % 10 == 0:
                log.info("[progress] %d/%d (ok=%d cached=%d err=%d)",
                         i, len(sample), fetch_status["ok"], fetch_status["cached"],
                         fetch_status["http_error"] + fetch_status["extract_error"])
                _checkpoint(per_paper, fetch_status)
    finally:
        if client is not None:
            client.close()

    # ---- aggregate metrics (kinds + aggregates only in the public-facing part) ----
    overall = _rate_block(fulltext_pairs, abstract_pairs)

    by_kind = {}
    for kind in sorted(set(kinds.get(c, "unknown") for _, c in fulltext_pairs)):
        grp = [pr for pr in fulltext_pairs if kinds.get(pr[1], "unknown") == kind]
        by_kind[kind] = _rate_block(grp, abstract_pairs)

    def half(pub):
        return "2025-H2" if pub and pub < "2026-01-01" else "2026-H1"
    pub_by_id = {p["arxiv_id"]: p["published"] for p in sample}
    by_time = {}
    for stratum in ("2025-H2", "2026-H1"):
        grp = [pr for pr in fulltext_pairs if half(pub_by_id.get(pr[0])) == stratum]
        by_time[stratum] = _rate_block(grp, abstract_pairs)

    n_processed = fetch_status["ok"] + fetch_status["cached"] + (fetch_status["skipped"] if dry_run else 0)
    result = {
        "study": "SUB-1 abstract-undercount",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frame": {"panel": "frozen all-arXiv (papers.db master)",
                  "window": [WINDOW_LO, WINDOW_HI],
                  "frame_size": sum(month_counts.values())},
        "sample": {"n_requested": n, "n_sampled": len(sample),
                   "n_processed": n_processed, "seed": seed,
                   "allocation": allocation, "fetch_status": fetch_status},
        "lexicon_version": LEXICON_VERSION,
        "event_day_invariant": event_day_invariant,
        "overall": overall,
        "by_concept_kind": by_kind,
        "by_time_stratum": by_time,
        "dry_run": dry_run,
    }
    # public-safe result (aggregates + kinds only)
    (OUT_DIR / "undercount_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    # private detail (concept_ids) — stays in gitignored audits
    (OUT_DIR / "undercount_per_paper.private.json").write_text(
        json.dumps({"per_paper": per_paper,
                    "sample_manifest": [{"arxiv_id": p["arxiv_id"],
                                         "published": p["published"]} for p in sample]},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _checkpoint(per_paper, fetch_status):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "_progress.json").write_text(
        json.dumps({"done": len(per_paper), "fetch_status": fetch_status},
                   ensure_ascii=False), encoding="utf-8")


def _print_report(r: dict) -> None:
    log.info("=" * 64)
    log.info("SUB-1 undercount — frame=%s window=%s frame_size=%d",
             r["frame"]["panel"], r["frame"]["window"], r["frame"]["frame_size"])
    s = r["sample"]
    log.info("sample: requested=%d sampled=%d processed=%d seed=%s",
             s["n_requested"], s["n_sampled"], s["n_processed"], s["seed"])
    log.info("fetch: %s", s["fetch_status"])
    o = r["overall"]
    log.info("OVERALL undercount=%.1f%% CI95=[%.1f,%.1f]  (abstract recall=%.1f%%; "
             "%d/%d memberships missed abstract-only)",
             100 * o["undercount_rate"], 100 * o["undercount_ci95"][0],
             100 * o["undercount_ci95"][1], 100 * o["abstract_recall"],
             o["abstract_missed"], o["fulltext_memberships"])
    log.info("by concept kind:")
    for kind, b in r["by_concept_kind"].items():
        log.info("  %-13s undercount=%.1f%% CI95=[%.1f,%.1f]  (n_full=%d)",
                 kind, 100 * b["undercount_rate"], 100 * b["undercount_ci95"][0],
                 100 * b["undercount_ci95"][1], b["fulltext_memberships"])
    log.info("by time stratum:")
    for st, b in r["by_time_stratum"].items():
        log.info("  %-8s undercount=%.1f%% CI95=[%.1f,%.1f]  (n_full=%d)",
                 st, 100 * b["undercount_rate"], 100 * b["undercount_ci95"][0],
                 100 * b["undercount_ci95"][1], b["fulltext_memberships"])
    log.info("event_day (=published) invariant to adding body: %s", r["event_day_invariant"])
    log.info("=" * 64)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260713)
    ap.add_argument("--sleep", type=float, default=3.0, help="arXiv politeness (sec/req)")
    ap.add_argument("--dry-run", action="store_true", help="sample+abstract match only, no download")
    ap.add_argument("--research-db", default=str(FROZEN_RESEARCH_DB))
    ap.add_argument("--papers-db", default=str(MASTER_PAPERS_DB))
    args = ap.parse_args()
    r = run(args.n, args.seed, args.sleep, args.dry_run,
            Path(args.research_db), Path(args.papers_db))
    _print_report(r)


if __name__ == "__main__":
    main()
