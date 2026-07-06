"""PREP-1 — event scaffold for H1/H2/H3 alignment.

The formal hypotheses align concept dynamics to DISCRETE EVENTS: H1 (bursts around
events), H2 (event → news coverage lag), H3 (event → network restructuring). This
builds the private event set the analyses align to:

  * primary  = paper publications (papers.db `published`) — already available.
  * manual   = major model/product releases — a template the researcher fills
               (data/research_private/analysis/events_manual.json).

Private (event set is research material). No LLM. Deterministic.

Usage: python -m pipeline.research.events
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

PAPERS_DB = Path("data") / "papers_private" / "papers.db"
OUT_DIR = Path("data") / "research_private" / "analysis"
EVENTS = OUT_DIR / "events.json"
MANUAL = OUT_DIR / "events_manual.json"

DEFINITION = ("An 'event' is a discrete, dated occurrence that could drive concept "
              "attention: a paper publication (primary, from papers.db published) or "
              "a major model/product release / funding / regulation (manual). "
              "Analyses align bursts/lag/network-shifts to event_day.")

_MANUAL_TEMPLATE = [
    {"kind": "release", "event_day": "YYYY-MM-DD", "label": "<major model/product release>",
     "concepts": ["<optional concept_id hints>"], "note": "researcher fills; delete this template row"}
]


def build_events(papers_db: Path = PAPERS_DB, out: Path = EVENTS) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    paper_events = []
    if papers_db.exists():
        c = sqlite3.connect(papers_db)
        for aid, pub, cat, imp in c.execute(
            "SELECT arxiv_id, published, primary_category, importance_max FROM papers "
            "WHERE published IS NOT NULL"):
            paper_events.append({"kind": "paper", "event_day": (pub or "")[:10],
                                 "id": aid, "category": cat, "importance": imp})
        c.close()
    paper_events.sort(key=lambda e: (e["event_day"], e["id"]))

    # manual slots: keep existing researcher file; else write a template
    if MANUAL.exists():
        try:
            manual = json.loads(MANUAL.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            manual = []
    else:
        MANUAL.write_text(json.dumps(_MANUAL_TEMPLATE, ensure_ascii=False, indent=2), encoding="utf-8")
        manual = []

    payload = {"definition": DEFINITION,
               "n_paper_events": len(paper_events), "n_manual_events": len(manual),
               "paper_events": paper_events, "manual_events": manual}
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"paper_events": len(paper_events), "manual_events": len(manual),
            "manual_template": not MANUAL.exists() or manual == []}


def load_events(path: Path = EVENTS) -> dict:
    if not Path(path).exists():
        return {"paper_events": [], "manual_events": []}
    return json.loads(Path(path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    s = build_events()
    print(f"[events] paper_events={s['paper_events']} manual_events={s['manual_events']} "
          f"(manual template at {MANUAL} for researcher)")
