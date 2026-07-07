"""BACK-1 Phase 2 — news backfill from the 3 back-catalog RSS sources.

openai / huggingface / deepmind expose their full post history in their own RSS
feed (Phase 0 finding). Fetch each, keep entries in the target window, match the
v6 lexicon on titles, and insert news-side concept_mentions (field='title',
event_day = day). Title-only (these feeds carry no body); COV-1's coverage-robust
measurement absorbs the resulting non-uniformity. All private.

Honest limitation: the gap news corpus is 3-source title-only — a narrower source
mix than the recent full-pipeline corpus. Recorded in the coverage curve.

Usage: python -m pipeline.research.backfill_news
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import feedparser
import httpx

from pipeline.research.research_db import DB_FILE, compile_alias, open_db

UA = {"User-Agent": "ai-daily-news-research/1.0 (news backfill; 91ssjj@gmail.com)"}
SOURCES = {
    "openai_news": "https://openai.com/news/rss.xml",
    "huggingface_blog": "https://huggingface.co/blog/feed.xml",
    "deepmind_blog": "https://deepmind.google/blog/rss.xml",
}
WINDOW_LO, WINDOW_HI = "2026-01-01", "2026-06-03"
STAGE = Path("data") / "research_private" / "back1_staging" / "news_backfill.jsonl"


def _url_hash(url: str) -> str:
    try:
        from pipeline.state import url_hash
        return url_hash(url)
    except Exception:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _v6_patterns(conn):
    ver = conn.execute("SELECT MAX(version) FROM lexicon_versions").fetchone()[0]
    return ver, [(cid, compile_alias(p)) for cid, p in conn.execute(
        "SELECT concept_id, pattern FROM aliases WHERE added_version <= ?", (ver,)).fetchall()]


def run(window_lo: str = WINDOW_LO, window_hi: str = WINDOW_HI) -> dict:
    conn = open_db()
    ver, pats = _v6_patterns(conn)
    STAGE.parent.mkdir(parents=True, exist_ok=True)
    fout = STAGE.open("a", encoding="utf-8")   # append: multiple windows accumulate
    st = {"sources": {}, "articles": 0, "mentions_new": 0, "lexicon_version": ver}
    cur = conn.cursor()
    for sid, url in SOURCES.items():
        try:
            content = httpx.get(url, headers=UA, timeout=45, follow_redirects=True).content
        except Exception as e:  # noqa: BLE001
            st["sources"][sid] = f"ERR:{type(e).__name__}"
            continue
        fp = feedparser.parse(content)
        n_win = n_ment = 0
        for e in fp.entries:
            pp = e.get("published_parsed") or e.get("updated_parsed")
            if not pp:
                continue
            day = time.strftime("%Y-%m-%d", pp)
            if not (window_lo <= day <= window_hi):
                continue
            title = (e.get("title") or "").strip()
            link = e.get("link") or ""
            aid = _url_hash(link)
            n_win += 1
            fout.write(json.dumps({"source_id": sid, "day": day, "title": title, "url": link, "article_id": aid},
                                  ensure_ascii=False) + "\n")
            for cid, rx in pats:
                m = rx.search(title)
                if not m:
                    continue
                cur.execute(
                    """INSERT OR IGNORE INTO concept_mentions
                       (concept_id, source_type, source_id, day, field, match_text, lexicon_version, event_day)
                       VALUES (?, 'news', ?, ?, 'title', ?, ?, ?)""",
                    (cid, aid, day, m.group(0), ver, day))
                if cur.rowcount > 0:
                    n_ment += 1
        conn.commit()
        st["sources"][sid] = {"in_window_articles": n_win, "new_mentions": n_ment}
        st["articles"] += n_win
        st["mentions_new"] += n_ment
        time.sleep(1.0)
    fout.close()
    conn.close()
    print(f"[news-backfill] v{ver} · articles={st['articles']} new_mentions={st['mentions_new']} · {st['sources']}")
    return st


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--window-lo", default=WINDOW_LO)
    ap.add_argument("--window-hi", default=WINDOW_HI)
    a = ap.parse_args()
    run(a.window_lo, a.window_hi)
