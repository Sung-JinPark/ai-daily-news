"""Build trending keyword list from today's summaries."""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from pathlib import Path

from pipeline.collect import today
from pipeline.summarize import DATA_DIR

log = logging.getLogger(__name__)
TOP_N = 15

STOPWORDS = {
    # EN stopwords (extended for English-only mode)
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "had",
    "having", "are", "was", "were", "been", "being", "you", "your", "yours",
    "they", "them", "their", "there", "these", "those", "will", "would",
    "could", "should", "what", "when", "where", "which", "while", "who",
    "whom", "whose", "how", "why", "but", "not", "all", "any", "can",
    "more", "most", "such", "into", "than", "then", "out", "its", "it's",
    "also", "only", "just", "new", "old", "use", "used", "using", "make",
    "made", "get", "got", "say", "said", "see", "saw", "one", "two", "three",
    "first", "second", "last", "many", "some", "other", "another", "much",
    "very", "still", "even", "yet", "ever", "never", "always", "often",
    "now", "today", "tomorrow", "yesterday", "year", "month", "week", "day",
    "via", "vs", "per", "each", "both", "either", "neither", "between",
    "during", "without", "within", "through", "across", "about", "above",
    "after", "before", "around", "over", "under", "again", "back", "down",
    "off", "up", "well", "way", "ways", "thing", "things",
    # generic news/AI fillers
    "ai", "company", "companies", "model", "models", "user", "users",
    "tool", "tools", "feature", "features", "team", "teams",
    "research", "report", "reports", "study", "studies", "data",
    "based", "shows", "show", "showed", "says", "according",
    "compared", "include", "includes", "including", "across",
}
# English-only tokens: must start with an ASCII letter, length >= 3
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-+.]{2,}")


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in TOKEN_RE.findall(text):
        t = raw.lower().rstrip(".")
        if len(t) >= 3 and t not in STOPWORDS:
            out.append(t)
    return out


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

    counter: Counter[str] = Counter()
    for a in articles:
        counter.update(tokenize(a.get("summary_ko", "")))
        counter.update(tokenize(a.get("title_original", "")))
        for insight in a.get("insights_ko", []):
            counter.update(tokenize(insight))

    top = [{"keyword": k, "count": c} for k, c in counter.most_common(TOP_N)]
    out = DATA_DIR / args.day / "trending.json"
    out.write_text(json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("trending done: %d keywords", len(top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
