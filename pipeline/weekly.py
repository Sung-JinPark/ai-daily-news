"""Generate weekly digest from the last 7 days of highlights.

Runs once per ISO week (typically Sunday cron). Loads each day's
highlights.json + articles.json from the last 7 days, sends to Haiku
with WEEKLY_SYSTEM_PROMPT, writes data/weekly/<YYYY-Www>.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

from pipeline.summarize import DATA_DIR, MODEL, RETRYABLE
from pipeline.utils.prompts import WEEKLY_SYSTEM_PROMPT, WEEKLY_USER_TEMPLATE

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 1600

CATEGORY_KO = {
    "model_research": "모델/연구",
    "business": "비즈니스/투자",
    "policy": "정책/규제",
    "product": "제품/툴",
    "hardware": "하드웨어/인프라",
    "community": "커뮤니티",
}


def iso_week_for(d: date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def week_to_date_range(week_str: str) -> tuple[date, date]:
    """Returns (Monday, Sunday) for given ISO week 'YYYY-Www'."""
    m = re.match(r"^(\d{4})-W(\d{1,2})$", week_str)
    if not m:
        raise ValueError(f"invalid week: {week_str}")
    year, week = int(m.group(1)), int(m.group(2))
    monday = date.fromisocalendar(year, week, 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def collect_week_articles(week_str: str) -> list[dict]:
    """Return de-duped highlight articles from each day in the week."""
    monday, sunday = week_to_date_range(week_str)
    days: list[str] = []
    cur = monday
    while cur <= sunday:
        days.append(cur.isoformat())
        cur += timedelta(days=1)

    seen_ids: set[str] = set()
    out: list[dict] = []
    for day in days:
        day_dir = DATA_DIR / day
        if not day_dir.exists():
            continue
        articles_file = day_dir / "articles.json"
        highlights_file = day_dir / "highlights.json"
        if not (articles_file.exists() and highlights_file.exists()):
            continue
        articles = json.loads(articles_file.read_text(encoding="utf-8"))
        highlights = json.loads(highlights_file.read_text(encoding="utf-8"))
        by_id = {a["id"]: a for a in articles}
        for hid in highlights:
            if hid in seen_ids:
                continue
            if hid in by_id:
                seen_ids.add(hid)
                out.append(by_id[hid])
    return out


def format_stories(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles, 1):
        cat = CATEGORY_KO.get(a.get("category", ""), a.get("category", ""))
        tags = ", ".join(a.get("tags", [])) if a.get("tags") else "-"
        lines.append(
            f"[{i}] ({cat}) {a['title_original']}\n    매체: {a['source_name']} · 태그: {tags}\n    요약: {a['summary_ko']}"
        )
    return "\n\n".join(lines)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def call_haiku_weekly(client: anthropic.Anthropic, week: str, stories: str, n: int) -> dict[str, Any]:
    user = WEEKLY_USER_TEMPLATE.format(week=week, n=n, stories=stories)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=WEEKLY_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON object in weekly response: {text[:200]!r}")
    parsed = json.loads(match.group(0))
    return {
        "parsed": parsed,
        "usage": {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
    }


def map_indices(indices: list, articles: list[dict]) -> list[str]:
    """Convert 1-indexed integers to article IDs, skipping invalid ones."""
    ids: list[str] = []
    for idx in indices:
        try:
            i = int(idx)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(articles):
            aid = articles[i - 1]["id"]
            if aid not in ids:
                ids.append(aid)
    return ids


def validate_and_map(parsed: dict, articles: list[dict]) -> dict | None:
    try:
        top_indices = parsed["top_indices"]
        recap = str(parsed["theme_recap_ko"]).strip()
        themes_raw = parsed.get("themes", [])
    except (KeyError, TypeError):
        return None
    if not recap or not isinstance(top_indices, list) or not isinstance(themes_raw, list):
        return None

    top_ids = map_indices(top_indices, articles)
    if len(top_ids) < 3:
        return None

    themes: list[dict] = []
    for t in themes_raw:
        if not isinstance(t, dict):
            continue
        name = str(t.get("name", "")).strip()
        summary = str(t.get("summary_ko", "")).strip()
        idx_list = t.get("indices", [])
        if not name or not summary or not isinstance(idx_list, list):
            continue
        ids = map_indices(idx_list, articles)
        if len(ids) < 2:
            continue
        themes.append({"name": name, "summary_ko": summary, "article_ids": ids})

    return {
        "top_story_ids": top_ids[:10],
        "theme_recap_ko": recap,
        "themes": themes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--week", default=iso_week_for(date.today()))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    articles = collect_week_articles(args.week)
    log.info("week %s: collected %d unique highlight articles", args.week, len(articles))

    if len(articles) < 3:
        log.warning("not enough articles (%d), skipping weekly digest", len(articles))
        return 0

    stories = format_stories(articles)

    if args.dry_run:
        log.info("dry-run: skipping LLM call")
        return 0

    client = anthropic.Anthropic()
    try:
        rsp = call_haiku_weekly(client, args.week, stories, len(articles))
    except Exception as exc:  # noqa: BLE001
        log.error("weekly LLM call failed: %s", exc)
        return 1

    payload = validate_and_map(rsp["parsed"], articles)
    if payload is None:
        log.error("weekly schema validation failed: %s", rsp["parsed"])
        return 1

    out = {
        "week": args.week,
        "n_input": len(articles),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    weekly_dir = DATA_DIR / "weekly"
    weekly_dir.mkdir(parents=True, exist_ok=True)
    (weekly_dir / f"{args.week}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("weekly digest written: %d top stories, %d themes (usage=%s)",
             len(payload["top_story_ids"]), len(payload["themes"]), rsp["usage"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
