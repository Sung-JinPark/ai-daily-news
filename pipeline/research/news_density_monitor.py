"""News-density monitor — the trigger for the EXPANSION_ROADMAP (tracking, NOT analysis).

The corpus is paper-dense but **news-thin over the analysis span** (the 12-month news
panel is a Wayback backfill), while current daily collection is dense. News-side
expansion (roadmap R1/R2/R4) needs an **accumulated dense news span**, which grows
forward as the pipeline runs. This module tracks that accumulation and reports a
READINESS gate (RED/GREEN) per expansion path — it runs **no** analysis.

Weekly aggregates from ``research.db`` news mentions (concept-name-free):
  * mentions/week            — news-side concept mentions
  * co-occurrence articles/week — news articles with >=2 distinct concepts (graph density proxy)
  * active concepts/week     — distinct concepts seen
  * coverage span            — first..last news day, week count

Weeks are ordinal//7 buckets so gaps are detectable (a missing collection week = 0 =
breaks a "dense streak"). Output accumulates run snapshots in the private
``notes/news_density.json`` (gitignored). No public exposure; aggregates only.

★ READINESS gate rules are **pre-defined here** (not tuned to a desired answer). A week
is "dense" for a gate if it clears that gate's per-week floors; a gate is GREEN once
there are ``weeks_required`` **consecutive** dense weeks. Thresholds are placeholders
calibrated against the first run's recent-window density (stamped in ``baseline``); a
researcher may recalibrate, but the rule shape is fixed.

Usage: python -m pipeline.research.news_density_monitor
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger("news_density_monitor")

REPO = Path(__file__).resolve().parents[2]
RESEARCH_DB = REPO / "data" / "research_private" / "research.db"
OUT_FILE = REPO / "data" / "research_private" / "notes" / "news_density.json"

# Pre-defined READINESS gates (rule shape fixed; thresholds are calibratable placeholders,
# set ~25% of the first-run recent-window median so a normal collection week clears them
# while thin-backfill/gap weeks do not). weeks_required = 26 ≈ a 6-month dense span.
GATES = {
    "R4_news_keyword": {
        "desc": "R4 news keyword / vocabulary expansion",
        "dense_mentions_min": 150, "dense_cooc_min": 0, "weeks_required": 26},
    "R1R2_news_network": {
        "desc": "R1 news co-occurrence network + R2 paper-vs-news contrast",
        "dense_mentions_min": 0, "dense_cooc_min": 25, "weeks_required": 26},
}


# ---------- pure aggregation (unit-tested with synthetic mentions) ----------

def _week_key(day: str) -> int:
    return date.fromisoformat(day[:10]).toordinal() // 7


def _week_label(key: int) -> str:
    return date.fromordinal(key * 7).isoformat()


def compute_weekly(mentions) -> list:
    """mentions = iterable of (day, source_id, concept_id) news rows. Returns a
    gap-filled weekly series (every week bucket between the first and last), each:
    {week, label, mentions, cooc_articles, active_concepts}."""
    buckets: dict[int, dict] = {}
    for day, source_id, concept_id in mentions:
        if not day:
            continue
        wk = _week_key(day)
        b = buckets.setdefault(wk, {"mentions": 0, "articles": {}, "concepts": set()})
        b["mentions"] += 1
        b["articles"].setdefault(source_id, set()).add(concept_id)
        b["concepts"].add(concept_id)
    if not buckets:
        return []
    out = []
    for wk in range(min(buckets), max(buckets) + 1):
        b = buckets.get(wk)
        if b:
            cooc = sum(1 for cs in b["articles"].values() if len(cs) >= 2)
            out.append({"week": wk, "label": _week_label(wk), "mentions": b["mentions"],
                        "cooc_articles": cooc, "active_concepts": len(b["concepts"])})
        else:
            out.append({"week": wk, "label": _week_label(wk), "mentions": 0,
                        "cooc_articles": 0, "active_concepts": 0})
    return out


def _is_dense(week: dict, gate: dict) -> bool:
    return (week["mentions"] >= gate["dense_mentions_min"]
            and week["cooc_articles"] >= gate["dense_cooc_min"])


def trailing_dense_streak(weeks: list, gate: dict) -> int:
    """Consecutive dense weeks counting back from the most recent."""
    streak = 0
    for w in reversed(weeks):
        if _is_dense(w, gate):
            streak += 1
        else:
            break
    return streak


def evaluate_readiness(weeks: list, gates: dict = GATES) -> dict:
    out = {}
    for name, g in gates.items():
        streak = trailing_dense_streak(weeks, g)
        req = g["weeks_required"]
        out[name] = {"status": "GREEN" if streak >= req else "RED",
                     "streak_weeks": streak, "weeks_required": req,
                     "weeks_remaining": max(0, req - streak), "desc": g["desc"]}
    return out


def _recent_median(weeks: list, key: str, n: int = 8) -> int:
    present = [w[key] for w in weeks if w["mentions"] > 0][-n:]
    return int(statistics.median(present)) if present else 0


# ---------- DB layer ----------

def load_news_mentions(research_db: Path):
    import sqlite3
    conn = sqlite3.connect(research_db)
    try:
        v = conn.execute("SELECT MAX(lexicon_version) FROM concept_mentions").fetchone()[0]
        rows = conn.execute(
            "SELECT day, source_id, concept_id FROM concept_mentions "
            "WHERE source_type='news' AND lexicon_version=?", (v,)).fetchall()
    finally:
        conn.close()
    return rows, v


# ---------- orchestrator ----------

def run(research_db: Path, out_file: Path, now_iso: str | None = None) -> dict:
    rows, version = load_news_mentions(research_db)
    weeks = compute_weekly(rows)
    readiness = evaluate_readiness(weeks)
    span = {"first": weeks[0]["label"] if weeks else None,
            "last": weeks[-1]["label"] if weeks else None, "n_weeks": len(weeks),
            "present_weeks": sum(1 for w in weeks if w["mentions"] > 0)}
    recent = {"mentions": _recent_median(weeks, "mentions"),
              "cooc_articles": _recent_median(weeks, "cooc_articles"),
              "active_concepts": _recent_median(weeks, "active_concepts")}
    stamp = now_iso or datetime.now(timezone.utc).isoformat(timespec="seconds")

    prev = {}
    if out_file.exists():
        try:
            prev = json.loads(out_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prev = {}
    baseline = prev.get("baseline") or {  # set once, on the first run
        "stamped_at": stamp, "recent_window_median": recent,
        "note": "first-run baseline; gate thresholds are calibratable placeholders "
                "(~25% of recent median). Rule shape fixed."}
    run_summary = {"at": stamp, "lexicon_version": version, "span": span,
                   "recent_window_median": recent,
                   "readiness": {k: v["status"] for k, v in readiness.items()},
                   "streaks": {k: v["streak_weeks"] for k, v in readiness.items()}}
    runs = (prev.get("runs") or [])[-49:] + [run_summary]

    result = {"study": "news_density_monitor (tracking, not analysis)",
              "gates": GATES, "baseline": baseline, "span": span,
              "recent_window_median": recent, "readiness": readiness,
              "runs": runs, "weekly_series": weeks}
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _print(r: dict) -> None:
    s = r["span"]; rc = r["recent_window_median"]
    log.info("=" * 60)
    log.info("news density — span %s .. %s (%d weeks, %d present)",
             s["first"], s["last"], s["n_weeks"], s["present_weeks"])
    log.info("recent-window median: mentions=%d cooc-articles=%d active-concepts=%d",
             rc["mentions"], rc["cooc_articles"], rc["active_concepts"])
    for name, g in r["readiness"].items():
        log.info("  %-18s %-5s  streak=%d/%d  (%d weeks remaining)  — %s",
                 name, g["status"], g["streak_weeks"], g["weeks_required"],
                 g["weeks_remaining"], g["desc"])
    log.info("=" * 60)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--research-db", default=str(RESEARCH_DB))
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args()
    r = run(Path(args.research_db), Path(args.out))
    _print(r)
    log.info("[done] wrote %s (private)", args.out)


if __name__ == "__main__":
    main()
