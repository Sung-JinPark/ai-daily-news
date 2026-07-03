"""EN research corpus ledger — English source text for the instrument.

F0 verdict (decisions.md, 2026-07-03): the summarizer already fetches
full English article bodies (trafilatura, 6000-char cap) and persists
them git-tracked at ``data/corpus/<day>/bodies.jsonl`` — so no CI
artifact pipe is needed. This module converts those public rows (plus,
for the two surviving raw/ days, feed teasers) into the private
research ledger:

    data/research_private/en_corpus/<day>.jsonl
    {article_id, source_id, title, text_en, text_kind: body|teaser}

Tiers (documented in CODEBOOK/INSTRUMENT): body 2026-07-02+ · teaser
2026-06-04/17 (raw backfill) · title-only before that.

Deterministic (sorted by article_id), idempotent (same day re-run =
same bytes), atomic. Rows keep only articles that exist in the day's
articles.json (the ledger's day/join semantics).

Usage: python -m pipeline.research.en_corpus [--day D | --backfill]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from pipeline.research.research_db import _atomic_write

DATA = Path("data")
OUT_DIR = DATA / "research_private" / "en_corpus"
TEXT_CAP = 6000  # matches summarize MAX_BODY_CHARS; body already capped upstream
_TAG = re.compile(r"<[^>]+>")
_FALLBACK_PREFIX = "(본문 추출 실패. RSS 요약만 사용)"


def _clean(text: str) -> str:
    if text.startswith(_FALLBACK_PREFIX):
        text = text[len(_FALLBACK_PREFIX):]
    text = _TAG.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()[:TEXT_CAP]


def _article_ids(day: str) -> set[str]:
    f = DATA / day / "articles.json"
    if not f.exists():
        return set()
    return {a["id"] for a in json.loads(f.read_text(encoding="utf-8"))}


def build_day(day: str) -> int:
    ids = _article_ids(day)
    rows = []
    bodies = DATA / "corpus" / day / "bodies.jsonl"
    if bodies.exists():
        for line in bodies.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("url_hash") not in ids:
                continue
            text = _clean(r.get("body_text") or "")
            if not text:
                continue
            kind = "teaser" if r.get("extract_status") == "rss_fallback" else "body"
            rows.append({"article_id": r["url_hash"], "source_id": r.get("source_id", ""),
                         "title": r.get("title", ""), "text_en": text, "text_kind": kind})
    elif (Path("raw") / day).exists():
        # teaser backfill for the surviving raw days (E11a: 06-04, 06-17)
        url_to_id = {}
        f = DATA / day / "articles.json"
        if f.exists():
            for a in json.loads(f.read_text(encoding="utf-8")):
                url_to_id[a.get("url", "")] = a["id"]
        for src in sorted((Path("raw") / day).glob("*.json")):
            try:
                entries = json.loads(src.read_text(encoding="utf-8"))
            except Exception:
                continue
            for e in entries:
                aid = url_to_id.get(e.get("url", ""))
                text = _clean(e.get("summary") or "")
                if not aid or not text:
                    continue
                rows.append({"article_id": aid, "source_id": e.get("source_id", ""),
                             "title": e.get("title", ""), "text_en": text, "text_kind": "teaser"})
    if not rows:
        return 0
    rows.sort(key=lambda r: r["article_id"])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write(OUT_DIR / f"{day}.jsonl",
                  "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n")
    return len(rows)


def iter_en_texts():
    """Yield (day, article_id, 'body_en', text) — instrument v3 field."""
    if not OUT_DIR.exists():
        return
    for f in sorted(OUT_DIR.glob("2???-??-??.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                yield f.stem, r["article_id"], "body_en", r["text_en"]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--day", default=None)
    p.add_argument("--backfill", action="store_true")
    a = p.parse_args()
    days = (sorted(set(p2.name for p2 in DATA.glob("2???-??-??"))) if a.backfill
            else [a.day or max(p2.name for p2 in DATA.glob("2???-??-??"))])
    total = 0
    for d in days:
        n = build_day(d)
        if n:
            print(f"[en-corpus] {d}: {n} rows")
            total += n
    print(f"[en-corpus] total {total}")


if __name__ == "__main__":
    main()
