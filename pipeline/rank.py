"""Pick top 5 highlights based on importance, recency, and cluster size."""
from __future__ import annotations

import argparse
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from pipeline.collect import today
from pipeline.summarize import DATA_DIR

log = logging.getLogger(__name__)
TOP_N = 5
_TITLE_NON_ALNUM = re.compile(r"[^\w]+", flags=re.UNICODE)


def title_key(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFKC", t).lower()
    t = re.sub(r"&#?\w+;", " ", t)
    return _TITLE_NON_ALNUM.sub(" ", t).strip()


def freshness_hours(published: str | None) -> float:
    if not published:
        return 48.0
    try:
        dt = datetime.fromisoformat(published)
    except ValueError:
        return 48.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 0.0)


def score(article: dict) -> float:
    importance = article.get("importance_score", 3) / 5.0
    fresh = max(1.0 - freshness_hours(article.get("published")) / 48.0, 0.0)
    cluster = min(article.get("cluster_size", 1) / 5.0, 1.0)
    return importance * 0.6 + fresh * 0.3 + cluster * 0.1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    articles_file = DATA_DIR / args.day / "articles.json"
    if not articles_file.exists():
        log.error("missing articles file: %s", articles_file)
        return 1
    articles = json.loads(articles_file.read_text(encoding="utf-8"))
    if not articles:
        log.warning("no articles to rank")
        return 0

    ranked = sorted(articles, key=score, reverse=True)
    # Dedupe by id, cluster_id, AND normalized title (catches re-published
    # articles where the headline is identical but URL differs).
    seen_ids: set[str] = set()
    seen_clusters: set[str] = set()
    seen_titles: set[str] = set()
    highlights: list[str] = []
    for a in ranked:
        aid = a.get("id")
        cid = a.get("cluster_id")
        tk = title_key(a.get("title_original", ""))
        if not aid or aid in seen_ids:
            continue
        if cid and cid in seen_clusters:
            continue
        if tk and tk in seen_titles:
            continue
        seen_ids.add(aid)
        if cid:
            seen_clusters.add(cid)
        if tk:
            seen_titles.add(tk)
        highlights.append(aid)
        if len(highlights) >= TOP_N:
            break
    out = DATA_DIR / args.day / "highlights.json"
    out.write_text(json.dumps(highlights, indent=2), encoding="utf-8")
    log.info("rank done: top %d highlights", len(highlights))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
