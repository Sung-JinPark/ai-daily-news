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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml
from simhash import Simhash

from pipeline.collect import RAW_DIR, today

log = logging.getLogger(__name__)
HAMMING_THRESHOLD = 12
CROSS_DAY_THRESHOLD = 8           # stricter for cross-day to avoid false merges
CONTINUITY_DAYS = 14              # prune index entries older than this
NGRAM_SIZE = 3
MAX_AGE_DAYS = 7                  # drop articles with a published date older than this
SOURCES_FILE = Path("pipeline/sources.yaml")
CONTINUITY_FILE = Path("data/cluster_continuity.json")


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


def filter_fresh(items: list[dict], day_str: str, max_age_days: int = MAX_AGE_DAYS) -> list[dict]:
    """Drop articles whose published date is older than max_age_days.

    Articles with no published date are kept (e.g. scraped listing pages
    typically surface the most recent items first).
    """
    cutoff = datetime.fromisoformat(day_str).replace(tzinfo=timezone.utc) - timedelta(days=max_age_days)
    kept: list[dict] = []
    dropped = 0
    for a in items:
        pub = a.get("published")
        if not pub:
            kept.append(a)
            continue
        try:
            dt = datetime.fromisoformat(pub)
        except ValueError:
            kept.append(a)
            continue
        if dt < cutoff:
            dropped += 1
            continue
        kept.append(a)
    log.info("freshness filter: kept %d, dropped %d older than %dd", len(kept), dropped, max_age_days)
    return kept


def load_continuity() -> dict:
    if not CONTINUITY_FILE.exists():
        return {"version": 1, "next_id": 0, "entries": []}
    try:
        return json.loads(CONTINUITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "next_id": 0, "entries": []}


def save_continuity(data: dict) -> None:
    CONTINUITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTINUITY_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def prune_continuity(data: dict, days: int, today_str: str) -> dict:
    cutoff = (date.fromisoformat(today_str) - timedelta(days=days)).isoformat()
    data["entries"] = [e for e in data["entries"] if e.get("last_seen", "") >= cutoff]
    return data


def find_stable_id(rep_sh: Simhash, continuity: dict, assigned_today: set[str], today_str: str) -> tuple[str, bool]:
    """Return (stable_cluster_id, reused). Updates `continuity` in place."""
    for e in continuity["entries"]:
        if e["cluster_id"] in assigned_today:
            continue
        try:
            existing_sh = Simhash(int(e["simhash"]))
        except Exception:
            continue
        if rep_sh.distance(existing_sh) <= CROSS_DAY_THRESHOLD:
            e["last_seen"] = today_str
            return e["cluster_id"], True
    continuity["next_id"] = int(continuity.get("next_id", 0)) + 1
    new_id = f"k{continuity['next_id']:06d}"
    continuity["entries"].append({
        "cluster_id": new_id,
        "simhash": str(rep_sh.value),
        "last_seen": today_str,
    })
    return new_id, False


def cluster(articles: list[dict], trust: dict[str, int], day_str: str) -> list[dict]:
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

    continuity = load_continuity()
    prune_continuity(continuity, CONTINUITY_DAYS, day_str)
    assigned_today: set[str] = set()

    output: list[dict] = []
    reused = 0
    for group in clusters:
        members = [m for m, _ in group]
        members.sort(
            key=lambda a: (trust.get(a["source_id"], 3), parse_iso(a.get("published"))),
            reverse=True,
        )
        rep = members[0]
        rep_sh = group[0][1]
        stable_id, was_reused = find_stable_id(rep_sh, continuity, assigned_today, day_str)
        if was_reused:
            reused += 1
        assigned_today.add(stable_id)
        output.append(
            {
                "cluster_id": stable_id,
                "representative": rep,
                "members": members,
            }
        )

    save_continuity(continuity)
    log.info("continuity: %d reused, %d new (index size=%d)",
             reused, len(output) - reused, len(continuity["entries"]))
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
    articles = filter_fresh(articles, args.day)
    if not articles:
        log.warning("no fresh articles after age filter")
        return 0
    clusters = cluster(articles, trust_map(), args.day)
    out = day_dir / "clusters.json"
    out.write_text(json.dumps(clusters, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("dedupe done: %d articles -> %d clusters", len(articles), len(clusters))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
