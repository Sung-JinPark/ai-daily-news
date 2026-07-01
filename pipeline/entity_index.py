"""Extract entity mentions and pair co-occurrences from articles.

Runs after `rank` daily. No LLM cost — entities are matched by
rule against three lists:

  * MODELS   — from pipeline/utils/prompts.TAG_VOCAB (models subset)
  * LABS     — labs subset of the same vocab
  * TAGS     — every article's persisted tags field

Emits three append-only JSONL files under data/aggregates/:

  * entity_mentions.jsonl   — {day, entity_type, entity, article_id,
                                cluster_id, source_id, importance_score,
                                category}
  * tag_cooccurrence.jsonl  — {day, tag_a, tag_b, cluster_id,
                                article_id, category}  (a < b lexicographically)
  * entity_cooccurrence.jsonl — same shape but with entity_a/entity_b
                                (any type, e.g. lab x model, model x tag)

Idempotency: existing rows for the same (day, article_id) are removed
before appending, so re-runs are safe. Supports --backfill to walk all
historical days.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
AGG_DIR = DATA_DIR / "aggregates"
MENTIONS_FILE = AGG_DIR / "entity_mentions.jsonl"
TAG_COOC_FILE = AGG_DIR / "tag_cooccurrence.jsonl"
ENTITY_COOC_FILE = AGG_DIR / "entity_cooccurrence.jsonl"

# Split TAG_VOCAB into typed lists for cross-typed cooccurrence signal.
from pipeline.utils.prompts import TAG_VOCAB

MODELS = {
    "GPT-5", "GPT-4", "Claude", "Gemini", "Llama", "Mistral", "Sora",
    "DALL-E", "Whisper", "Stable Diffusion", "Grok", "DeepSeek", "Qwen", "Phi",
}
LABS = {
    "OpenAI", "Anthropic", "DeepMind", "Meta AI", "xAI", "Mistral AI",
    "Hugging Face", "Microsoft", "Apple", "Amazon", "NVIDIA", "Cohere",
    "Perplexity", "Stability AI",
}


def _list_days(window: int | None) -> list[str]:
    if not DATA_DIR.exists():
        return []
    days = sorted(
        p.name for p in DATA_DIR.iterdir()
        if p.is_dir() and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-"
    )
    if window is not None and window > 0:
        days = days[-window:]
    return days


def _load_articles(day: str) -> list[dict]:
    p = DATA_DIR / day / "articles.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _entity_type(tag: str) -> str:
    if tag in MODELS:
        return "model"
    if tag in LABS:
        return "lab"
    return "tag"


def _rewrite_without_days(path: Path, days: set[str]) -> list[str]:
    """Return existing lines except those whose 'day' is in the given set."""
    if not path.exists():
        return []
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("day") in days:
            continue
        kept.append(line)
    return kept


def process(days: list[str]) -> tuple[int, int, int]:
    if not days:
        return 0, 0, 0
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    day_set = set(days)
    kept_mentions = _rewrite_without_days(MENTIONS_FILE, day_set)
    kept_tag_cooc = _rewrite_without_days(TAG_COOC_FILE, day_set)
    kept_entity_cooc = _rewrite_without_days(ENTITY_COOC_FILE, day_set)

    n_mentions = 0
    n_tag_cooc = 0
    n_entity_cooc = 0

    mentions_out: list[str] = []
    tag_cooc_out: list[str] = []
    entity_cooc_out: list[str] = []

    for day in days:
        for a in _load_articles(day):
            aid = a.get("id", "")
            cid = a.get("cluster_id", "")
            src = a.get("source_id", "")
            imp = int(a.get("importance_score", 0) or 0)
            cat = a.get("category", "")
            tags = list(a.get("tags") or [])
            if not tags:
                continue
            # Mentions: every distinct tag is an entity of some type.
            for tag in tags:
                if tag not in TAG_VOCAB:
                    continue
                mentions_out.append(
                    json.dumps(
                        {
                            "day": day,
                            "entity_type": _entity_type(tag),
                            "entity": tag,
                            "article_id": aid,
                            "cluster_id": cid,
                            "source_id": src,
                            "importance_score": imp,
                            "category": cat,
                        },
                        ensure_ascii=False,
                    )
                )
                n_mentions += 1
            # Cooccurrence: unordered pair (a, b) with a < b.
            sorted_tags = sorted(t for t in tags if t in TAG_VOCAB)
            for t1, t2 in combinations(sorted_tags, 2):
                # tag_cooccurrence.jsonl treats every pair the same.
                tag_cooc_out.append(
                    json.dumps(
                        {
                            "day": day,
                            "tag_a": t1,
                            "tag_b": t2,
                            "cluster_id": cid,
                            "article_id": aid,
                            "category": cat,
                        },
                        ensure_ascii=False,
                    )
                )
                n_tag_cooc += 1
                # entity_cooccurrence.jsonl tags the pair with types.
                t1_type = _entity_type(t1)
                t2_type = _entity_type(t2)
                entity_cooc_out.append(
                    json.dumps(
                        {
                            "day": day,
                            "entity_a": t1,
                            "entity_a_type": t1_type,
                            "entity_b": t2,
                            "entity_b_type": t2_type,
                            "cluster_id": cid,
                            "article_id": aid,
                            "category": cat,
                        },
                        ensure_ascii=False,
                    )
                )
                n_entity_cooc += 1

    MENTIONS_FILE.write_text("\n".join(kept_mentions + mentions_out) + "\n", encoding="utf-8")
    TAG_COOC_FILE.write_text("\n".join(kept_tag_cooc + tag_cooc_out) + "\n", encoding="utf-8")
    ENTITY_COOC_FILE.write_text("\n".join(kept_entity_cooc + entity_cooc_out) + "\n", encoding="utf-8")
    return n_mentions, n_tag_cooc, n_entity_cooc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", help="single day YYYY-MM-DD; default = today")
    parser.add_argument("--backfill", action="store_true", help="process all historical days")
    parser.add_argument("--window", type=int, help="process the last N days")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.backfill:
        days = _list_days(None)
    elif args.window:
        days = _list_days(args.window)
    elif args.day:
        days = [args.day]
    else:
        today = date.today().isoformat()
        days = [today]

    m, tc, ec = process(days)
    log.info(
        "entity_index: %d days processed — %d mentions, %d tag pairs, %d entity pairs",
        len(days), m, tc, ec,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
