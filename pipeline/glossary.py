"""Weekly glossary updater.

Runs alongside weekly.py (Sunday UTC 14:00). Loads data/glossary.json
and last 7 days of articles, asks Haiku for 5-10 new technical terms
not yet in the glossary, validates + dedupes, appends to glossary,
caps total at MAX_TERMS (oldest non-seed first).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

from pipeline.summarize import DATA_DIR, MODEL, RETRYABLE
from pipeline.utils.prompts import GLOSSARY_SYSTEM_PROMPT, GLOSSARY_USER_TEMPLATE
from pipeline.weekly import collect_week_articles, iso_week_for

log = logging.getLogger(__name__)

GLOSSARY_FILE = DATA_DIR / "glossary.json"
MAX_TERMS = 50
MAX_OUTPUT_TOKENS = 1800
MAX_DESC_CHARS = 400


def load_existing() -> dict:
    if not GLOSSARY_FILE.exists():
        return {"version": 1, "updated_at": "", "terms": []}
    return json.loads(GLOSSARY_FILE.read_text(encoding="utf-8"))


def format_existing_terms(terms: list[dict]) -> str:
    return ", ".join(t["term"] for t in terms)


def format_stories(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles[:30], 1):  # cap input
        tags = ", ".join(a.get("tags", [])) if a.get("tags") else "-"
        lines.append(
            f"[{i}] {a['title_original']}\n    태그: {tags}\n    요약: {a['summary_ko']}"
        )
    return "\n\n".join(lines)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def call_haiku_glossary(client: anthropic.Anthropic, existing: str, stories: str) -> dict[str, Any]:
    user = GLOSSARY_USER_TEMPLATE.format(existing_terms=existing, stories=stories)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=GLOSSARY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON object in glossary response: {text[:200]!r}")
    parsed = json.loads(match.group(0))
    return {
        "parsed": parsed,
        "usage": {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
    }


def normalize_key(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip()).casefold()


def validate_new_terms(parsed: dict, existing_keys: set[str]) -> list[dict]:
    raw = parsed.get("new_terms")
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    today_str = date.today().isoformat()
    for item in raw:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        full = str(item.get("full", "")).strip()
        desc = str(item.get("desc", "")).strip()
        if not (term and full and desc):
            continue
        if len(desc) > MAX_DESC_CHARS:
            desc = desc[:MAX_DESC_CHARS].rstrip() + "…"
        if normalize_key(term) in existing_keys:
            continue
        out.append({
            "term": term,
            "full": full,
            "desc": desc,
            "added_at": today_str,
        })
        existing_keys.add(normalize_key(term))
    return out


def cap_terms(terms: list[dict], max_n: int) -> list[dict]:
    """Keep all seed terms; drop oldest non-seed if over cap."""
    if len(terms) <= max_n:
        return terms
    seeds = [t for t in terms if t.get("seed")]
    others = [t for t in terms if not t.get("seed")]
    # Sort non-seed by added_at descending (newest first), keep newest up to room.
    others.sort(key=lambda t: t.get("added_at", ""), reverse=True)
    room = max(max_n - len(seeds), 0)
    kept_others = others[:room]
    # Preserve original-ish order: seeds first (in original order), then non-seeds newest first.
    return seeds + kept_others


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", default=iso_week_for(date.today()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    glossary = load_existing()
    existing_terms: list[dict] = glossary.get("terms", [])
    existing_keys = {normalize_key(t["term"]) for t in existing_terms}
    log.info("loaded %d existing terms", len(existing_terms))

    articles = collect_week_articles(args.week)
    log.info("week %s: %d highlight articles", args.week, len(articles))
    if len(articles) < 3:
        log.warning("not enough articles, skipping glossary update")
        return 0

    existing_str = format_existing_terms(existing_terms)
    stories = format_stories(articles)

    if args.dry_run:
        log.info("dry-run: skipping LLM call")
        return 0

    client = anthropic.Anthropic()
    try:
        rsp = call_haiku_glossary(client, existing_str, stories)
    except Exception as exc:  # noqa: BLE001
        log.error("glossary LLM call failed: %s", exc)
        return 1

    new_terms = validate_new_terms(rsp["parsed"], existing_keys)
    if not new_terms:
        log.info("no new terms passed validation")
        return 0
    log.info("adding %d new terms: %s", len(new_terms), [t["term"] for t in new_terms])

    merged = existing_terms + new_terms
    merged = cap_terms(merged, MAX_TERMS)

    out = {
        "version": glossary.get("version", 1),
        "updated_at": datetime.now(timezone.utc).date().isoformat(),
        "terms": merged,
    }
    GLOSSARY_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("glossary written: %d terms total (usage=%s)", len(merged), rsp["usage"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
