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
from pipeline import corpus_writer

log = logging.getLogger(__name__)
HAMMING_THRESHOLD = 12
# Cross-day merges use a *tiered* threshold. Same threshold across 90 days
# would increase false merges (recurring press releases with similar titles,
# regulatory hearings, quarterly earnings, etc). We tighten as the gap grows
# and require a secondary title-token Jaccard check for far-apart matches.
CROSS_DAY_THRESHOLD_NEAR = 8      # gap ≤ 30d
CROSS_DAY_THRESHOLD_FAR = 6       # gap > 30d
FAR_JACCARD_MIN = 0.4             # extra guard for gap > 30d
FAR_GAP_DAYS = 30
CONTINUITY_DAYS = 90              # prune index entries older than this (M2: 14→90 for longer story tracking)

# NOTE: data/cluster_continuity.json is the *authoritative* mapping from
# SimHash → cluster_id. Deleting the file forces every existing cluster to
# be re-assigned a new numeric ID on the next run, which breaks the URL of
# every /story/[cluster] page. Treat it as a committed piece of state.
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
    dropped: list[dict] = []
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
            dropped.append(a)
            continue
        kept.append(a)
    log.info("freshness filter: kept %d, dropped %d older than %dd", len(kept), len(dropped), max_age_days)
    if dropped:
        corpus_writer.append_skipped_many(
            day_str,
            [
                {
                    "url_hash": corpus_writer._url_hash(a.get("url", "")),
                    "url": a.get("url", ""),
                    "source_id": a.get("source_id", ""),
                    "title": a.get("title", ""),
                    "phase": "freshness_filter",
                    "reason": f"published={a.get('published','')} older than {max_age_days}d",
                }
                for a in dropped
            ],
        )
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


def _day_gap(a: str, b: str) -> int:
    """Absolute day difference between two ISO dates. Returns a large sentinel
    when either side fails to parse so the strict path is taken."""
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (ValueError, TypeError):
        return 10_000


def _title_tokens(title: str) -> set[str]:
    return {t for t in normalize(title).split() if len(t) >= 2}


def find_stable_id(
    rep_sh: Simhash,
    rep_title: str,
    continuity: dict,
    assigned_today: set[str],
    today_str: str,
) -> tuple[str, bool]:
    """Return (stable_cluster_id, reused). Updates `continuity` in place.

    Tiered matching:
      * gap ≤ 30d: SimHash distance ≤ 8 (near threshold)
      * gap > 30d: SimHash distance ≤ 6 AND title-token Jaccard ≥ 0.4
    Also records ``last_titles`` (up to 3) per entry so the far-gap Jaccard
    check has a real reference to compare against.
    """
    rep_tokens: set[str] | None = None
    for e in continuity["entries"]:
        if e["cluster_id"] in assigned_today:
            continue
        try:
            existing_sh = Simhash(int(e["simhash"]))
        except Exception:
            continue
        distance = rep_sh.distance(existing_sh)
        gap = _day_gap(e.get("last_seen", ""), today_str)
        if gap <= FAR_GAP_DAYS:
            if distance > CROSS_DAY_THRESHOLD_NEAR:
                continue
        else:
            if distance > CROSS_DAY_THRESHOLD_FAR:
                continue
            # Secondary title-Jaccard guard against far-apart false merges.
            if rep_tokens is None:
                rep_tokens = _title_tokens(rep_title)
            past_titles = e.get("last_titles", []) or ([e.get("last_title")] if e.get("last_title") else [])
            best_j = 0.0
            for t in past_titles:
                if not t:
                    continue
                tokens = _title_tokens(t)
                if not tokens or not rep_tokens:
                    continue
                inter = len(rep_tokens & tokens)
                union = len(rep_tokens | tokens)
                if union == 0:
                    continue
                j = inter / union
                if j > best_j:
                    best_j = j
            if best_j < FAR_JACCARD_MIN:
                continue
        # Match — refresh metadata for future comparisons.
        e["last_seen"] = today_str
        titles = e.get("last_titles") or ([e["last_title"]] if e.get("last_title") else [])
        if rep_title and rep_title not in titles:
            titles.append(rep_title)
        e["last_titles"] = titles[-3:]
        e.pop("last_title", None)
        return e["cluster_id"], True
    continuity["next_id"] = int(continuity.get("next_id", 0)) + 1
    new_id = f"k{continuity['next_id']:06d}"
    continuity["entries"].append({
        "cluster_id": new_id,
        "simhash": str(rep_sh.value),
        "last_seen": today_str,
        "last_titles": [rep_title] if rep_title else [],
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
        stable_id, was_reused = find_stable_id(
            rep_sh, rep.get("title", ""), continuity, assigned_today, day_str,
        )
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
    corpus_writer.write_members(args.day, clusters)
    corpus_writer.update_manifest(args.day)
    log.info("dedupe done: %d articles -> %d clusters", len(articles), len(clusters))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
