"""BACK-1 — arXiv publication-date harvester (all-arXiv-cs backfill).

Harvests arXiv metadata via OAI-PMH (set=cs), keeps records whose `created`
(publication date) falls in the target window AND whose categories intersect the
tracked set. Abstracts/authors/categories come with the OAI record (no separate
enrich). Resumable: the OAI resumptionToken + counters are checkpointed, so a run
capped at ``max_pages`` can be re-invoked to continue.

Writes to a PRIVATE staging JSONL (does NOT touch papers.db). A later loader
promotes staging → papers.db under an explicit population-redefinition step.

Politeness: >=3s between requests, honors 503 retry-after. Local only (never CI).

Usage: python -m pipeline.research.harvest_arxiv --max-pages 40
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
import xml.etree.ElementTree as ET

OAI = "https://export.arxiv.org/oai2"
UA = {"User-Agent": "ai-daily-news-research/1.0 (arXiv backfill; contact 91ssjj@gmail.com)"}
NS = {"oai": "http://www.openarchives.org/OAI/2.0/", "ax": "http://arxiv.org/OAI/arXiv/"}
TRACKED = {"cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "stat.ML"}

STAGE_DIR = Path("data") / "research_private" / "back1_staging"
STAGE_JSONL = STAGE_DIR / "arxiv_harvest.jsonl"
PROGRESS = STAGE_DIR / "harvest_progress.json"

# datestamp window (OAI) slightly wider than the created window to catch
# in-window papers updated shortly after; created filter is the real bound.
OAI_FROM, OAI_UNTIL = "2026-01-01", "2026-06-30"
CREATED_LO, CREATED_HI = "2026-01-01", "2026-06-03"


def _load_progress(progress: Path) -> dict:
    if progress.exists():
        return json.loads(progress.read_text(encoding="utf-8"))
    return {"token": None, "started": False, "pages": 0, "seen": 0, "kept": 0, "done": False}


def _save_progress(p: dict, progress: Path) -> None:
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def _request(url: str, tries: int = 4) -> bytes:
    backoff = 5.0
    for _ in range(tries):
        r = httpx.get(url, headers=UA, timeout=90, follow_redirects=True)
        if r.status_code == 503:  # OAI throttle: honor Retry-After
            wait = float(r.headers.get("retry-after", backoff))
            time.sleep(min(wait, 120)); backoff = min(backoff * 2, 120); continue
        r.raise_for_status()
        return r.content
    raise RuntimeError("arXiv OAI: repeated 503")


def _parse_page(content: bytes):
    root = ET.fromstring(content)
    out = []
    for rec in root.findall(".//oai:record", NS):
        ax = rec.find(".//ax:arXiv", NS)
        if ax is None:
            continue
        def g(tag):
            e = ax.find(f"ax:{tag}", NS)
            return (e.text or "").strip() if e is not None and e.text else ""
        cats = g("categories").split()
        out.append({"arxiv_id": g("id"), "created": g("created"), "updated": g("updated"),
                    "primary_category": cats[0] if cats else None, "categories": cats,
                    "title": " ".join(g("title").split()), "abstract": " ".join(g("abstract").split()),
                    "authors": [a.findtext("ax:keyname", "", NS) for a in ax.findall("ax:authors/ax:author", NS)]})
    tk = root.find(".//oai:resumptionToken", NS)
    token = tk.text.strip() if (tk is not None and tk.text and tk.text.strip()) else None
    return out, token


def run(max_pages: int, sleep: float = 3.0, oai_from: str = OAI_FROM, oai_until: str = OAI_UNTIL,
        created_lo: str = CREATED_LO, created_hi: str = CREATED_HI,
        stage: Path = STAGE_JSONL, progress: Path = PROGRESS) -> dict:
    p = _load_progress(progress)
    if p.get("done"):
        print("[harvest] already complete:", p); return p
    stage.parent.mkdir(parents=True, exist_ok=True)
    fout = stage.open("a", encoding="utf-8")
    try:
        for _ in range(max_pages):
            if p["token"]:
                url = f"{OAI}?verb=ListRecords&resumptionToken={p['token']}"
            else:
                url = (f"{OAI}?verb=ListRecords&metadataPrefix=arXiv&set=cs"
                       f"&from={oai_from}&until={oai_until}")
            recs, token = _parse_page(_request(url))
            for r in recs:
                p["seen"] += 1
                if not r["created"] or not (created_lo <= r["created"] <= created_hi):
                    continue
                if not (set(r["categories"]) & TRACKED):
                    continue
                fout.write(json.dumps(r, ensure_ascii=False) + "\n")
                p["kept"] += 1
            p["pages"] += 1
            p["started"] = True
            p["token"] = token
            _save_progress(p, progress)
            if not token:
                p["done"] = True
                _save_progress(p, progress)
                break
            time.sleep(sleep)
    finally:
        fout.close()
    print(f"[harvest] pages={p['pages']} seen={p['seen']} kept={p['kept']} done={p['done']} "
          f"token={'set' if p['token'] else 'none'}")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-pages", type=int, default=40, help="cap pages this run (resumable)")
    ap.add_argument("--sleep", type=float, default=3.0)
    ap.add_argument("--oai-from", default=OAI_FROM)
    ap.add_argument("--oai-until", default=OAI_UNTIL)
    ap.add_argument("--created-lo", default=CREATED_LO)
    ap.add_argument("--created-hi", default=CREATED_HI)
    ap.add_argument("--stage", default=str(STAGE_JSONL))
    ap.add_argument("--progress", default=str(PROGRESS))
    ap.add_argument("--reset", action="store_true", help="clear progress+staging and restart")
    a = ap.parse_args()
    stage, progress = Path(a.stage), Path(a.progress)
    if a.reset:
        for f in (progress, stage):
            if f.exists(): f.unlink()
        print("[harvest] reset done")
    run(a.max_pages, a.sleep, a.oai_from, a.oai_until, a.created_lo, a.created_hi, stage, progress)


if __name__ == "__main__":
    main()
