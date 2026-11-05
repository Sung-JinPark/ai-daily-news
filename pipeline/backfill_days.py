"""Recover days the daily pipeline never wrote (e.g. the 2026-10-28~11-03 outage).

Two sources of recovery, both keeping real publication dates:

1. ``move`` -- a run after an outage files every article still in the feeds
   under its own day. Articles whose ``published`` falls on a missing day are
   moved to that day (no LLM cost). Their members.jsonl rows move with them.
2. ``collect`` -- arXiv and Hacker News can be queried by date, so the missing
   days get what the normal collectors would have picked: the newest
   ``max_results`` per arXiv category, and HN stories with 50+ points. Each item
   becomes a one-member cluster in raw/<day>/clusters.json for
   ``pipeline.summarize --day``. RSS-only sources expose no history; anything
   they dropped stays missing rather than being approximated.

Usage (order matters):
    python -m pipeline.backfill_days plan --from-day 2026-11-04 --days D1,D2
    python -m pipeline.backfill_days move --from-day 2026-11-04 --days D1,D2
    python -m pipeline.backfill_days collect --days D1,D2
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import feedparser
import yaml

from pipeline import corpus_writer
from pipeline.arxiv_refs import write_refs_file
from pipeline.collect import RAW_DIR, SOURCES_FILE
from pipeline.state import url_hash
from pipeline.summarize import DATA_DIR
from pipeline.utils.atomic import write_text_atomic
from pipeline.utils.http import fetch, get_client

log = logging.getLogger(__name__)

HN_API = "https://hn.algolia.com/api/v1/search_by_date"
HN_QUERIES = ("AI", "LLM", '"machine learning"')
HN_MIN_POINTS = 50
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_SLEEP_SEC = 3.0  # arXiv API terms: at most one request every 3 seconds


def split_by_published_day(articles: list[dict], days: set[str]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Split articles into (kept, {missing_day: articles}) by published date."""
    kept: list[dict] = []
    moved: dict[str, list[dict]] = defaultdict(list)
    for article in articles:
        day = (article.get("published") or "")[:10]
        if day in days:
            moved[day].append(article)
        else:
            kept.append(article)
    return kept, dict(moved)


def one_member_cluster(item: dict) -> dict:
    """Wrap an item in the clusters.json shape, with an id that can't collide with k-ids."""
    day = item["published"][:10].replace("-", "")
    return {
        "cluster_id": f"b{day}-{url_hash(item['url'])[:8]}",
        "representative": item,
        "members": [item],
    }


def new_items_for_day(candidates: list[dict], known_urls: set[str]) -> list[dict]:
    """Drop URLs already archived and repeats within the candidates (cross-listed papers)."""
    out: list[dict] = []
    taken = set(known_urls)
    for item in candidates:
        if item["url"] in taken:
            continue
        taken.add(item["url"])
        out.append(item)
    return out


def _read_articles(day: str) -> list[dict]:
    path = DATA_DIR / day / "articles.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _known_urls() -> set[str]:
    urls: set[str] = set()
    for path in DATA_DIR.glob("20*/articles.json"):
        urls.update(a["url"] for a in json.loads(path.read_text(encoding="utf-8")))
    return urls


def _write_json(path: Path, payload: object) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2))


def move(from_day: str, days: set[str], dry_run: bool) -> dict[str, int]:
    kept, moved = split_by_published_day(_read_articles(from_day), days)
    counts = {d: len(a) for d, a in sorted(moved.items())}
    if dry_run or not moved:
        return counts

    moved_clusters = {d: {a["cluster_id"] for a in arts} for d, arts in moved.items()}
    members_file = DATA_DIR / "corpus" / from_day / "members.jsonl"
    member_rows = [json.loads(line) for line in members_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    kept_rows = list(member_rows)
    for day, arts in moved.items():
        out_dir = DATA_DIR / day
        out_dir.mkdir(parents=True, exist_ok=True)
        _write_json(out_dir / "articles.json", _read_articles(day) + arts)
        rows = [r for r in member_rows if r["cluster_id"] in moved_clusters[day]]
        kept_rows = [r for r in kept_rows if r["cluster_id"] not in moved_clusters[day]]
        target = DATA_DIR / "corpus" / day / "members.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        corpus_writer.update_manifest(day)
    _write_json(DATA_DIR / from_day / "articles.json", kept)
    write_text_atomic(
        members_file, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept_rows)
    )
    corpus_writer.update_manifest(from_day)
    return counts


def fetch_arxiv_day(source: dict, day: str, limit: int, client) -> list[dict]:
    compact = day.replace("-", "")
    query = f"{source['query']} AND submittedDate:[{compact}0000 TO {compact}2359]"
    url = (
        f"{ARXIV_API}?search_query={quote(query)}&start=0&max_results={limit}"
        "&sortBy=submittedDate&sortOrder=descending"
    )
    time.sleep(ARXIV_SLEEP_SEC)
    resp = fetch(url, client=client)
    resp.raise_for_status()
    items = []
    for entry in feedparser.parse(resp.content).entries:
        published = datetime.fromisoformat(entry.published.replace("Z", "+00:00"))
        if published.strftime("%Y-%m-%d") != day:
            continue
        items.append({
            "source_id": source["id"],
            "source_name": source["name"],
            "title": re.sub(r"\s+", " ", entry.title).strip(),
            "url": entry.link,
            "published": published.isoformat(),
            "summary": (entry.get("summary") or "")[:1500],
        })
    return items


def fetch_hn_day(source: dict, day: str, client) -> list[dict]:
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    lo, hi = int(start.timestamp()), int((start + timedelta(days=1)).timestamp())
    items: dict[str, dict] = {}
    for query in HN_QUERIES:
        url = (
            f"{HN_API}?query={quote(query)}&tags=story&hitsPerPage=100"
            f"&numericFilters=points>={HN_MIN_POINTS},created_at_i>={lo},created_at_i<{hi}"
        )
        resp = fetch(url, client=client)
        resp.raise_for_status()
        for hit in resp.json().get("hits", []):
            if hit.get("url"):  # Ask HN etc. have no article to summarize
                items[hit["url"]] = {
                    "source_id": source["id"],
                    "source_name": source["name"],
                    "title": hit.get("title") or "",
                    "url": hit["url"],
                    "published": datetime.fromtimestamp(hit["created_at_i"], timezone.utc).isoformat(),
                    "summary": "",
                }
    return list(items.values())


def collect(days: list[str], dry_run: bool) -> dict[str, Counter]:
    sources = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))["sources"]
    arxiv = [s for s in sources if s.get("type") == "arxiv" and s.get("enabled", True)]
    hn = next(s for s in sources if s["id"] == "hn_ai")
    known = _known_urls()

    with get_client() as client:
        # Stories still in the live feed belong to the next scheduled run: CI's
        # seen cache can't see local summaries, so taking them here would duplicate.
        resp = fetch(hn["url"], client=client)
        resp.raise_for_status()
        known |= {e.link for e in feedparser.parse(resp.content).entries}

        result: dict[str, Counter] = {}
        for day in days:
            existing = Counter(a["source_id"] for a in _read_articles(day))
            candidates: list[dict] = []
            for source in arxiv:
                room = source.get("max_results", 10) - existing[source["id"]]
                if room > 0:
                    candidates += fetch_arxiv_day(source, day, room, client)
            candidates += fetch_hn_day(hn, day, client)
            items = new_items_for_day(candidates, known)
            known.update(i["url"] for i in items)
            result[day] = Counter(i["source_id"] for i in items)
            log.info("%s: %d new items", day, len(items))
            if dry_run:
                continue
            raw_dir = RAW_DIR / day
            raw_dir.mkdir(parents=True, exist_ok=True)
            clusters = [one_member_cluster(i) for i in items]
            _write_json(raw_dir / "clusters.json", clusters)
            rows_file = DATA_DIR / "corpus" / day / "members.jsonl"
            moved_rows = rows_file.read_text(encoding="utf-8") if rows_file.exists() else ""
            corpus_writer.write_members(day, clusters)  # overwrites, so restore moved rows
            with rows_file.open("r+", encoding="utf-8", newline="\n") as f:
                new_rows = f.read()
                f.seek(0)
                f.write(moved_rows + new_rows)
                f.truncate()
            corpus_writer.update_manifest(day)
            write_refs_file(day, items)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("action", choices=["plan", "move", "collect"])
    parser.add_argument("--days", required=True, help="comma-separated missing days")
    parser.add_argument("--from-day", help="day whose articles.json holds the outage backlog")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    days = sorted(args.days.split(","))

    if args.action in ("plan", "move"):
        print("move:", move(args.from_day, set(days), dry_run=args.action == "plan"))
    if args.action in ("plan", "collect"):
        result = collect(days, dry_run=args.action == "plan")
        for day, counts in result.items():
            print(day, sum(counts.values()), dict(counts))
        print("total new:", sum(sum(c.values()) for c in result.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
