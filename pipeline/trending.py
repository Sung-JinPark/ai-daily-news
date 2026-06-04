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
    "그리고", "그러나", "하지만", "그래서", "또한", "이번", "지난", "오늘", "내일",
    "기업", "기술", "회사", "발표", "출시", "공개", "통해", "위해", "있는", "있다",
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "are",
    "is", "to", "of", "in", "on", "by", "an", "a",
}
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-+.]{2,}|[가-힣]{2,}")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(text) if t.lower() not in STOPWORDS]


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
