"""Fetch RSS / arXiv / scrape sources -> raw/YYYY-MM-DD/<source>.json."""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import feedparser
import yaml
from dateutil import parser as date_parser

from pipeline.utils.http import fetch, get_client

log = logging.getLogger(__name__)

SOURCES_FILE = Path("pipeline/sources.yaml")
RAW_DIR = Path("raw")
PER_SOURCE_CAP = 50  # keep raw files small + avoid old backlog dominating


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def from_rss(source: dict, content: bytes) -> list[dict]:
    parsed = feedparser.parse(content)
    items: list[dict] = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        items.append(
            {
                "source_id": source["id"],
                "source_name": source["name"],
                "title": title.strip(),
                "url": link,
                "published": parse_date(entry.get("published") or entry.get("updated")),
                "summary": (entry.get("summary") or "")[:1000],
            }
        )
    return items


def from_arxiv(source: dict) -> list[dict]:
    """arXiv Atom API. Avoids 429 via util throttle (1s/host)."""
    max_results = source.get("max_results", 10)
    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query={source['query']}"
        f"&start=0&max_results={max_results}"
        "&sortBy=submittedDate&sortOrder=descending"
    )
    with get_client() as client:
        resp = fetch(url, client=client)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.content)
    items: list[dict] = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue
        items.append(
            {
                "source_id": source["id"],
                "source_name": source["name"],
                "title": re.sub(r"\s+", " ", title).strip(),
                "url": link,
                "published": parse_date(entry.get("published")),
                "summary": (entry.get("summary") or "")[:1500],
            }
        )
    return items


def from_scrape_news(source: dict) -> list[dict]:
    """Anthropic /news lacks RSS. Light scrape that respects robots and extracts
    `<a href="/news/...">` title links from the listing page only."""
    with get_client() as client:
        resp = fetch(source["url"], client=client)
        resp.raise_for_status()
        html = resp.text
    pattern = re.compile(r'<a[^>]+href="(/news/[^"#?]+)"[^>]*>([^<]{8,200})</a>', re.I)
    items: list[dict] = []
    seen_paths: set[str] = set()
    base = "https://www.anthropic.com"
    for path, raw_title in pattern.findall(html):
        if path in seen_paths or path.rstrip("/") == "/news":
            continue
        seen_paths.add(path)
        title = re.sub(r"\s+", " ", raw_title).strip()
        if len(title) < 8:
            continue
        items.append(
            {
                "source_id": source["id"],
                "source_name": source["name"],
                "title": title,
                "url": base + path,
                "published": None,
                "summary": "",
            }
        )
        if len(items) >= 20:
            break
    return items


def fetch_source(source: dict) -> list[dict]:
    stype = source.get("type", "rss")
    if stype == "rss":
        with get_client() as client:
            resp = fetch(source["url"], client=client)
            resp.raise_for_status()
            return from_rss(source, resp.content)
    if stype == "arxiv":
        return from_arxiv(source)
    if stype == "scrape_news":
        return from_scrape_news(source)
    raise ValueError(f"unknown source type: {stype}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="fetch but don't write")
    parser.add_argument("--only", help="comma-separated source ids to limit run")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    config = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    sources: list[dict[str, Any]] = config["sources"]
    only = set(args.only.split(",")) if args.only else None

    day = today()
    out_dir = RAW_DIR / day
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for source in sources:
        if not source.get("enabled", True):
            continue
        if only and source["id"] not in only:
            continue
        try:
            items = fetch_source(source)
        except Exception as exc:  # noqa: BLE001 - one failing source must not stop pipeline
            log.warning("source %s failed: %s", source["id"], exc)
            continue
        items = items[:PER_SOURCE_CAP]
        log.info("source %s -> %d items", source["id"], len(items))
        total += len(items)
        if args.dry_run:
            continue
        (out_dir / f"{source['id']}.json").write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    log.info("collect done: %d items across active sources", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
