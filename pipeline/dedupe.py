"""Cluster near-duplicate articles using SimHash on normalized titles.

Reads raw/<day>/*.json and writes raw/<day>/clusters.json with structure:
  [
    {
      "cluster_id": "<short hash>",
      "representative": <article>,
      "members": [<article>, ...]
    },
    ...
  ]
The representative is the article with the highest (trust, recency) score.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from simhash import Simhash

from pipeline.collect import RAW_DIR, today

log = logging.getLogger(__name__)
HAMMING_THRESHOLD = 12
NGRAM_SIZE = 3
SOURCES_FILE = Path("pipeline/sources.yaml")


def trust_map() -> dict[str, int]:
    config = yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))
    return {s["id"]: s.get("trust", 3) for s in config["sources"]}


def normalize(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^a-z0-9가-힣\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_hash(title: str) -> Simhash:
    """Char n-gram SimHash. More robust for short titles than word tokens."""
    norm = normalize(title).replace(" ", "")
    if len(norm) < NGRAM_SIZE:
        return Simhash([norm or title])
    grams = [norm[i : i + NGRAM_SIZE] for i in range(len(norm) - NGRAM_SIZE + 1)]
    return Simhash(grams)


def parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def load_articles(day_dir: Path) -> list[dict]:
    items: list[dict] = []
    for file in day_dir.glob("*.json"):
        if file.name == "clusters.json":
            continue
        items.extend(json.loads(file.read_text(encoding="utf-8")))
    return items


def cluster(articles: list[dict], trust: dict[str, int]) -> list[dict]:
    hashed = [(a, title_hash(a["title"])) for a in articles]
    clusters: list[list[tuple[dict, Simhash]]] = []
    for item, sh in hashed:
        placed = False
        for group in clusters:
            if any(sh.distance(other_sh) <= HAMMING_THRESHOLD for _, other_sh in group):
                group.append((item, sh))
                placed = True
                break
        if not placed:
            clusters.append([(item, sh)])

    output: list[dict] = []
    for idx, group in enumerate(clusters):
        members = [m for m, _ in group]
        members.sort(
            key=lambda a: (trust.get(a["source_id"], 3), parse_iso(a.get("published"))),
            reverse=True,
        )
        rep = members[0]
        output.append(
            {
                "cluster_id": f"c{idx:04d}-{hex(group[0][1].value)[2:10]}",
                "representative": rep,
                "members": members,
            }
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    day_dir = RAW_DIR / args.day
    if not day_dir.exists():
        log.error("no raw data for %s", args.day)
        return 1

    articles = load_articles(day_dir)
    if not articles:
        log.warning("no articles to cluster")
        return 0
    clusters = cluster(articles, trust_map())
    out = day_dir / "clusters.json"
    out.write_text(json.dumps(clusters, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("dedupe done: %d articles -> %d clusters", len(articles), len(clusters))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
