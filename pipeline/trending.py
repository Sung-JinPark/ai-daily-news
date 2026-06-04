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
    # KR connectives & generic verbs
    "그리고", "그러나", "하지만", "그래서", "또한", "이번", "지난", "오늘", "내일",
    "그동안", "현재", "이전", "이후", "위해", "통해", "대한", "관련", "다른", "같은",
    "이러한", "이는", "이를", "이와", "이로써", "또는", "그리고", "이미", "아직",
    # KR verb/copula endings that survive char-class tokenization
    "있는", "있다", "있습니다", "있으며", "있도록", "있게", "있어", "있어서",
    "있을", "없는", "없다", "없습니다", "없으며", "않는", "않은", "않다", "않으며",
    "됩니다", "되는", "되어", "되며", "됐다", "한다", "했다", "합니다", "하는",
    "하며", "하여", "하지만", "위한", "위해서", "보인다", "보입니다", "본다",
    # KR generic nouns / fillers
    "기업", "기술", "회사", "발표", "출시", "공개", "사용", "사용자", "사용한",
    "방법", "방식", "구조", "기능", "기반", "내용", "결과", "수준", "정도",
    "경우", "상황", "환경", "분야", "영역", "측면", "관점", "부분", "전체",
    "새로운", "기존", "실제", "것으로", "것은", "것이", "수도", "가능", "가능성",
    "이번에", "여러", "다양한", "주요", "주로", "특히",
    "보여줍니다", "있음을", "향후", "이러한", "그러한", "보인다", "보여",
    "필요", "필요한", "필요하다", "예상", "예상되는",
    # EN stopwords
    "the", "and", "for", "with", "that", "this", "from", "have", "has", "are",
    "was", "were", "been", "being", "you", "your", "they", "them", "their",
    "will", "would", "could", "should", "what", "when", "where", "which", "who",
    "how", "why", "but", "not", "all", "any", "can", "more", "most", "such",
    "into", "than", "then", "out", "its", "it's", "also", "only", "just", "new",
    "use", "used", "using", "make", "made", "get", "got", "say", "said", "see",
    "one", "two", "three", "first", "last", "many", "some", "other",
}
# Tokens: English >=3 chars / Korean >=2 chars
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-+.]{2,}|[가-힣]{2,}")
# Trailing Korean particles/endings to strip so "성능을" -> "성능"
PARTICLE_RE = re.compile(
    r"(을|를|이|가|은|는|의|에서|에게|에|로서|로써|로|으로|와|과|도|만|뿐|"
    r"까지|부터|마다|보다|처럼|같이|하여|한다|했다|하며|하는|하고|하지만|"
    r"입니다|이며|이고|이라|라고|라는|이라는|이라고|에는|에도|에서는|에서도)$"
)


def strip_particles(tok: str) -> str:
    # Only strip from Korean tokens; leave EN intact.
    if not re.match(r"[가-힣]", tok):
        return tok
    stripped = PARTICLE_RE.sub("", tok)
    return stripped if len(stripped) >= 2 else tok


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in TOKEN_RE.findall(text):
        t = strip_particles(raw).lower()
        if len(t) >= 2 and t not in STOPWORDS:
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
