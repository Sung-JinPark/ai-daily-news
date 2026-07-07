"""NEWS-2 — news Tier-2 backfill via Wayback archived feeds (full-body 5 sources).

Reconstructs past news from Internet Archive snapshots of each source's RSS feed
(Phase 0: these 5 carry full body in the feed). For each source: CDX → snapshot
timestamps in the window → fetch each archived feed (raw `id_` bytes) → parse items
→ keep those published in-window → dedup by URL. Then match the v6 lexicon on
title + body and insert news-side mentions (fields `title`, `body_en`). Bodies are
stored to a PRIVATE jsonl (DBQ-3, gitignored); public state untouched.

Coverage is snapshot-frequency dependent and per-source uneven — recorded honestly
in the coverage curve; COV-1's coverage-robust measurement absorbs it. No fabrication:
missing source/dates are simply absent. Local only, polite pacing.

Usage: python -m pipeline.research.backfill_news_wayback [--max-snapshots 80]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from urllib.parse import quote_plus

import feedparser
import httpx

from pipeline.research.research_db import compile_alias, open_db

UA = {"User-Agent": "ai-daily-news-research/1.0 (news Tier-2 backfill; 91ssjj@gmail.com)"}
SOURCES = {
    "theverge_ai": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "venturebeat": "https://feeds.feedburner.com/venturebeat/SZYF",
    "arstechnica": "https://feeds.arstechnica.com/arstechnica/index",
    "mit_tr": "https://www.technologyreview.com/feed/",
    "aws_ml_blog": "https://aws.amazon.com/blogs/machine-learning/feed/",
}
WINDOW_LO, WINDOW_HI = "2026-01-01", "2026-06-03"
STAGE_DIR = Path("data") / "research_private" / "back1_staging"
BODIES = STAGE_DIR / "news_wayback_bodies.jsonl"
COVERAGE = STAGE_DIR / "news_wayback_coverage.json"


def _uh(url: str) -> str:
    try:
        from pipeline.state import url_hash
        return url_hash(url)
    except Exception:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _get(url, t=50):
    return httpx.get(url, headers=UA, timeout=t, follow_redirects=True)


def _snapshots(feed: str, cap: int, lo: str, hi: str) -> list[str]:
    cdx = (f"https://web.archive.org/cdx/search/cdx?url={quote_plus(feed)}"
           f"&from={lo.replace('-', '')}&to={hi.replace('-', '')}"
           f"&output=json&fl=timestamp&collapse=timestamp:8&limit=800")
    try:
        rows = json.loads(_get(cdx).text)
    except Exception:  # noqa: BLE001
        return []
    ts = [r[0] for r in rows if r and r[0].isdigit()]
    return ts[:cap]


def _articles(feed: str, snaps: list[str], lo: str, hi: str) -> dict:
    """url -> {day,title,body} for items published in-window, deduped across snapshots."""
    out = {}
    for ts in snaps:
        try:
            content = _get(f"https://web.archive.org/web/{ts}id_/{feed}").content
        except Exception:  # noqa: BLE001
            continue
        for e in feedparser.parse(content).entries:
            pp = e.get("published_parsed") or e.get("updated_parsed")
            if not pp:
                continue
            day = time.strftime("%Y-%m-%d", pp)
            if not (lo <= day <= hi):
                continue
            link = e.get("link") or ""
            if not link or link in out:
                continue
            content_body = (e.get("content") or [{}])[0].get("value", "") if e.get("content") else ""
            body = content_body or e.get("summary", "") or ""
            out[link] = {"day": day, "title": (e.get("title") or "").strip(), "body": body}
        time.sleep(1.2)
    return out


def run(max_snapshots: int = 80, window_lo: str = WINDOW_LO, window_hi: str = WINDOW_HI) -> dict:
    conn = open_db()
    ver = conn.execute("SELECT MAX(version) FROM lexicon_versions").fetchone()[0]
    pats = [(cid, compile_alias(p)) for cid, p in conn.execute(
        "SELECT concept_id, pattern FROM aliases WHERE added_version <= ?", (ver,)).fetchall()]
    STAGE_DIR.mkdir(parents=True, exist_ok=True)
    fbody = BODIES.open("a", encoding="utf-8")   # append: multiple windows accumulate
    cur = conn.cursor()
    cov, tot_art, tot_ment = {}, 0, 0
    for sid, feed in SOURCES.items():
        snaps = _snapshots(feed, max_snapshots, window_lo, window_hi)
        arts = _articles(feed, snaps, window_lo, window_hi) if snaps else {}
        s_ment = 0
        days = set()
        for url, a in arts.items():
            aid = _uh(url)
            days.add(a["day"])
            fbody.write(json.dumps({"source_id": sid, "day": a["day"], "title": a["title"],
                                    "url": url, "article_id": aid, "body_len": len(a["body"])}, ensure_ascii=False) + "\n")
            text_title = a["title"]
            text_body = f"{a['title']}. {a['body']}"
            for cid, rx in pats:
                for field, text in (("title", text_title), ("body_en", text_body)):
                    m = rx.search(text)
                    if not m:
                        continue
                    cur.execute(
                        "INSERT OR IGNORE INTO concept_mentions (concept_id, source_type, source_id, day, field, "
                        "match_text, lexicon_version, event_day) VALUES (?,'news',?,?,?,?,?,?)",
                        (cid, aid, a["day"], field, m.group(0), ver, a["day"]))
                    if cur.rowcount > 0:
                        s_ment += 1
        conn.commit()
        cov[sid] = {"snapshots": len(snaps), "articles": len(arts), "days_covered": len(days),
                    "day_range": [min(days), max(days)] if days else None, "new_mentions": s_ment}
        tot_art += len(arts); tot_ment += s_ment
        print(f"  {sid:<14} snaps={len(snaps)} articles={len(arts)} days={len(days)} mentions={s_ment}")
    fbody.close()
    conn.close()
    COVERAGE.write_text(json.dumps({"lexicon_version": ver, "window": [window_lo, window_hi],
                                    "sources": cov, "total_articles": tot_art, "total_mentions": tot_ment},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[news-wayback] v{ver} · {window_lo}..{window_hi} · articles={tot_art} · new_mentions={tot_ment}")
    return {"total_articles": tot_art, "total_mentions": tot_ment, "sources": cov}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--max-snapshots", type=int, default=80)
    ap.add_argument("--window-lo", default=WINDOW_LO)
    ap.add_argument("--window-hi", default=WINDOW_HI)
    a = ap.parse_args()
    run(a.max_snapshots, a.window_lo, a.window_hi)


if __name__ == "__main__":
    main()
