"""Generate daily TL;DR digest from the day's highlights.

Runs once per day after rank.py. Sends the top 5 highlighted articles'
metadata + summary_ko to Claude Haiku 4.5, writes data/<day>/digest.json
with {tldr_ko, bullets_ko, theme_of_day}.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

from pipeline.collect import today
from pipeline.summarize import DATA_DIR, MODEL, RETRYABLE
from pipeline.utils.prompts import DIGEST_SYSTEM_PROMPT, DIGEST_USER_TEMPLATE

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 700

CATEGORY_KO = {
    "model_research": "모델/연구",
    "business": "비즈니스/투자",
    "policy": "정책/규제",
    "product": "제품/툴",
    "hardware": "하드웨어/인프라",
    "community": "커뮤니티",
}


def format_stories(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        cat = CATEGORY_KO.get(a.get("category", ""), a.get("category", ""))
        lines.append(
            f"[{i}] ({cat}) {a['title_original']}\n    매체: {a['source_name']}\n    요약: {a['summary_ko']}"
        )
    return "\n\n".join(lines)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def call_haiku_digest(client: anthropic.Anthropic, stories: str, n: int) -> dict[str, Any]:
    user = DIGEST_USER_TEMPLATE.format(n=n, stories=stories)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=DIGEST_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON object found in digest response: {text[:200]!r}")
    parsed = json.loads(match.group(0))
    return {
        "parsed": parsed,
        "usage": {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
    }


def validate(parsed: dict) -> dict | None:
    try:
        tldr = str(parsed["tldr_ko"]).strip()
        bullets = [str(b).strip() for b in parsed["bullets_ko"] if str(b).strip()]
        theme = str(parsed["theme_of_day"]).strip()
    except (KeyError, TypeError):
        return None
    if not tldr or not theme or not (3 <= len(bullets) <= 5):
        return None
    return {"tldr_ko": tldr, "bullets_ko": bullets, "theme_of_day": theme}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    day_dir = DATA_DIR / args.day
    articles_file = day_dir / "articles.json"
    highlights_file = day_dir / "highlights.json"
    if not articles_file.exists() or not highlights_file.exists():
        log.error("missing articles/highlights for day %s", args.day)
        return 1

    articles = json.loads(articles_file.read_text(encoding="utf-8"))
    highlight_ids = json.loads(highlights_file.read_text(encoding="utf-8"))
    by_id = {a["id"]: a for a in articles}
    picks = [by_id[i] for i in highlight_ids if i in by_id]

    if len(picks) < 2:
        log.warning("not enough highlights (%d), skipping digest", len(picks))
        return 0

    stories = format_stories(picks)
    log.info("digest input: %d stories", len(picks))

    if args.dry_run:
        log.info("dry-run: skipping LLM call")
        return 0

    client = anthropic.Anthropic()
    try:
        rsp = call_haiku_digest(client, stories, len(picks))
    except Exception as exc:  # noqa: BLE001
        log.error("digest LLM call failed: %s", exc)
        return 1

    parsed = validate(rsp["parsed"])
    if parsed is None:
        log.error("digest schema validation failed: %s", rsp["parsed"])
        return 1

    out = {
        "day": args.day,
        "n_input": len(picks),
        **parsed,
    }
    (day_dir / "digest.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("digest written: theme=%r (usage=%s)", parsed["theme_of_day"], rsp["usage"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
