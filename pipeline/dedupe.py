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
from pipeline.state import url_hash

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
    """AUDIT-1 AUD-001: a MISSING file is a legitimate first run and
    returns a fresh structure; a CORRUPT/unreadable file ABORTS the
    run instead of silently returning empty — the old behavior let the
    same run's save_continuity() overwrite the file, destroying every
    frozen first_url_hash (all /story/s-* canonical URLs) with zero
    signal. Recovery from a genuinely corrupt file is a human
    decision: restore from git, then re-run.
    """
    if not CONTINUITY_FILE.exists():
        return {"schema_version": 1, "version": 1, "next_id": 0, "entries": []}
    try:
        data = json.loads(CONTINUITY_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error(
            "cluster_continuity.json exists but is unreadable (%s) — aborting "
            "instead of resetting %s. Restore it from git history, then re-run.",
            exc, CONTINUITY_FILE,
        )
        raise SystemExit(2)
    if not isinstance(data.get("entries"), list):
        log.error(
            "cluster_continuity.json parsed but has no 'entries' list — aborting "
            "instead of resetting. Restore from git history, then re-run.",
        )
        raise SystemExit(2)
    data.setdefault("schema_version", 1)
    global _LOADED_ENTRY_COUNT, _PRUNED_ENTRY_COUNT
    _LOADED_ENTRY_COUNT = len(data["entries"])
    _PRUNED_ENTRY_COUNT = 0
    return data


# E5 shrink guard state: entry count observed at load time and how many
# the 90-day prune legitimately removed this run. AUD-001's abort only
# covers *reading* a corrupt file; this guard covers a logic bug that
# silently drops a large share of entries between load and save.
_LOADED_ENTRY_COUNT: int | None = None
_PRUNED_ENTRY_COUNT = 0
SHRINK_GUARD_RATIO = 0.30  # refuse to save if >30% vanish unexplained
ALLOW_SHRINK = False       # set by --allow-shrink (deliberate override)


def save_continuity(data: dict) -> None:
    # E5: threshold shrink guard. Legitimate shrink = the prune's own
    # removals (window expiry); anything beyond that at 30%+ of the
    # loaded count means entries were lost by a bug, so refuse to
    # persist. Corrupt-read is a different failure class and already
    # aborted with exit 2 in load_continuity (that path never gets here).
    if _LOADED_ENTRY_COUNT and not ALLOW_SHRINK:
        expected_min = _LOADED_ENTRY_COUNT - _PRUNED_ENTRY_COUNT
        lost = expected_min - len(data.get("entries", []))
        if lost > 0 and lost >= expected_min * SHRINK_GUARD_RATIO:
            log.error(
                "continuity shrink guard: loaded %d entries, prune removed %d, "
                "but only %d remain (%d lost beyond prune, >=%d%% threshold). "
                "NOT saving. Check git history for the cause; if the shrink is "
                "intended, re-run with --allow-shrink.",
                _LOADED_ENTRY_COUNT, _PRUNED_ENTRY_COUNT,
                len(data.get("entries", [])), lost, int(SHRINK_GUARD_RATIO * 100),
            )
            raise SystemExit(3)
    # Atomic (AUD-006): a torn continuity write is the corruption that
    # AUD-001's abort path exists to catch — never create one ourselves.
    from pipeline.utils.atomic import write_text_atomic
    write_text_atomic(CONTINUITY_FILE, json.dumps(data, ensure_ascii=False, indent=2))


def prune_continuity(data: dict, days: int, today_str: str) -> dict:
    global _PRUNED_ENTRY_COUNT
    cutoff = (date.fromisoformat(today_str) - timedelta(days=days)).isoformat()
    before = len(data["entries"])
    data["entries"] = [e for e in data["entries"] if e.get("last_seen", "") >= cutoff]
    _PRUNED_ENTRY_COUNT += before - len(data["entries"])
    return data


def _day_gap(a: str, b: str) -> int:
    """Absolute day difference between two ISO dates. Returns a large sentinel
    when either side fails to parse so the strict path is taken."""
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (ValueError, TypeError):
        return 10_000


def deterministic_first_key(members: list[dict]) -> tuple[str, str]:
    """Return the (published, url_hash) minimum tuple across cluster members.

    The pair is the deterministic identity of a cluster: independent of
    processing order, incremental vs. full recompute, and immune to which
    trust-scored article happens to be "the representative" on any given
    day. It is the invariant that makes the stable slug survive a
    ``data/cluster_continuity.json`` deletion or rebuild (X1 / N2).
    """
    keys: list[tuple[str, str]] = []
    for m in members:
        pub = m.get("published") or ""
        u = m.get("url", "")
        if not u:
            continue
        keys.append((pub, url_hash(u)))
    keys.sort()
    return keys[0] if keys else ("", "")


def _maybe_update_first_key(entry: dict, today_key: tuple[str, str]) -> None:
    """FREEZE semantics (Y1 F-2 fix): once first_url_hash is set, it is
    never changed by a later run.

    The prior strict-lower-bound rule kept re-clustering deterministic
    but let a late-arriving earlier article silently move a cluster's
    stable slug — which is exactly the SEO-URL failure mode the whole
    migration was supposed to prevent (an already-indexed s-<hash> URL
    would 404 after the move). Because ``cluster_continuity.json`` is
    git-tracked and authoritative, freezing on first assignment is
    both safe and sufficient. If the file is ever deleted, callers
    can rebuild by re-running ``pipeline.backfill_first_url_hash`` and
    every entry gets re-frozen at the deterministic minimum computed
    across the archive.
    """
    tp, th = today_key
    if not th:
        return
    if entry.get("first_url_hash"):
        return  # frozen — never overwrite
    entry["first_published"] = tp
    entry["first_url_hash"] = th


def _ensure_unique_hash(th: str, continuity: dict) -> str:
    """AUDIT-1 AUD-003: two clusters must never freeze the same
    first_url_hash — the /story/s-<hash> slug would collide and
    getStaticPaths would emit duplicate routes. When the same article
    seeds two clusters (observed twice in the archive), the later
    freeze gets a deterministic ``-2``/``-3`` suffix.
    """
    taken = {
        e.get("first_url_hash")
        for e in continuity.get("entries", [])
        if e.get("first_url_hash")
    }
    if th not in taken:
        return th
    n = 2
    while f"{th}-{n}" in taken:
        n += 1
    log.warning("first_url_hash collision on %s — disambiguated to %s-%d", th, th, n)
    return f"{th}-{n}"


def _title_tokens(title: str) -> set[str]:
    return {t for t in normalize(title).split() if len(t) >= 2}


def find_stable_id(
    rep_sh: Simhash,
    rep_title: str,
    continuity: dict,
    assigned_today: set[str],
    today_str: str,
    merge_events: list[dict] | None = None,
    today_first_key: tuple[str, str] = ("", ""),
) -> tuple[str, bool]:
    """Return (stable_cluster_id, reused). Updates `continuity` in place.

    Tiered matching:
      * gap ≤ 30d: SimHash distance ≤ 8 (near threshold)
      * gap > 30d: SimHash distance ≤ 6 AND title-token Jaccard ≥ 0.4
    Also records ``last_titles`` (up to 3) per entry so the far-gap Jaccard
    check has a real reference to compare against.

    If ``merge_events`` is provided, appends one event per successful
    cross-day match so audit tooling can plot the Hamming / Jaccard
    distribution over time (N3).
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
        matched_kind: str
        matched_jaccard: float | None = None
        if gap <= FAR_GAP_DAYS:
            if distance > CROSS_DAY_THRESHOLD_NEAR:
                continue
            matched_kind = "cross_near"
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
            matched_kind = "cross_far"
            matched_jaccard = round(best_j, 3)
        # Match — refresh metadata for future comparisons.
        e["last_seen"] = today_str
        titles = e.get("last_titles") or ([e["last_title"]] if e.get("last_title") else [])
        if rep_title and rep_title not in titles:
            titles.append(rep_title)
        e["last_titles"] = titles[-3:]
        e.pop("last_title", None)
        if merge_events is not None:
            event = {
                "day": today_str,
                "cluster_id": e["cluster_id"],
                "kind": matched_kind,
                "hamming": int(distance),
                "gap_days": int(gap),
            }
            if matched_jaccard is not None:
                event["title_jaccard"] = matched_jaccard
            merge_events.append(event)
        # X1: keep the deterministic first key (published, url_hash) in
        # sync so the stable slug reflects the true earliest article
        # ever observed for this cluster.
        tp0, th0 = today_first_key
        if th0 and not e.get("first_url_hash"):
            th0 = _ensure_unique_hash(th0, continuity)
        _maybe_update_first_key(e, (tp0, th0))
        return e["cluster_id"], True
    continuity["next_id"] = int(continuity.get("next_id", 0)) + 1
    new_id = f"k{continuity['next_id']:06d}"
    tp, th = today_first_key
    entry = {
        "cluster_id": new_id,
        "simhash": str(rep_sh.value),
        "last_seen": today_str,
        "last_titles": [rep_title] if rep_title else [],
    }
    if th:
        entry["first_published"] = tp
        entry["first_url_hash"] = _ensure_unique_hash(th, continuity)
    continuity["entries"].append(entry)
    return new_id, False


def cluster(articles: list[dict], trust: dict[str, int], day_str: str) -> list[dict]:
    merge_events: list[dict] = []
    hashed = [(a, title_hash(a["title"])) for a in articles]
    clusters: list[list[tuple[dict, Simhash]]] = []
    for item, sh in hashed:
        placed = False
        for group in clusters:
            best_distance: int | None = None
            for _, other_sh in group:
                d = sh.distance(other_sh)
                if d <= HAMMING_THRESHOLD:
                    if best_distance is None or d < best_distance:
                        best_distance = d
            if best_distance is not None:
                group.append((item, sh))
                merge_events.append({
                    "day": day_str,
                    "cluster_id": "",  # filled later once stable_id assigned
                    "kind": "same_day",
                    "hamming": int(best_distance),
                    "gap_days": 0,
                })
                placed = True
                break
        if not placed:
            clusters.append([(item, sh)])

    continuity = load_continuity()
    prune_continuity(continuity, CONTINUITY_DAYS, day_str)
    assigned_today: set[str] = set()

    output: list[dict] = []
    reused = 0
    # Same-day merge events are collected before stable_id is known, so we
    # replay them per-cluster to attach the id once available.
    same_day_backlog = [e for e in merge_events if e["kind"] == "same_day"]
    same_day_index = 0
    for group in clusters:
        members = [m for m, _ in group]
        members.sort(
            key=lambda a: (trust.get(a["source_id"], 3), parse_iso(a.get("published"))),
            reverse=True,
        )
        rep = members[0]
        rep_sh = group[0][1]
        today_first_key = deterministic_first_key(members)
        stable_id, was_reused = find_stable_id(
            rep_sh, rep.get("title", ""), continuity, assigned_today, day_str,
            merge_events=merge_events,
            today_first_key=today_first_key,
        )
        # Same-day rows that belong to this cluster (one per extra member).
        n_extra = max(0, len(members) - 1)
        for j in range(n_extra):
            if same_day_index < len(same_day_backlog):
                same_day_backlog[same_day_index]["cluster_id"] = stable_id
                same_day_index += 1
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
    _write_merge_events(day_str, merge_events)
    log.info("continuity: %d reused, %d new (index size=%d)",
             reused, len(output) - reused, len(continuity["entries"]))
    return output


def _write_merge_events(day_str: str, events: list[dict]) -> None:
    """Idempotent per-day append to data/aggregates/merge_events.jsonl.
    Any existing rows whose ``day`` matches are dropped before appending
    this run's rows, so re-running dedupe never inflates the count.
    """
    path = Path("data/aggregates/merge_events.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    kept: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if obj.get("day") == day_str:
                continue
            kept.append(line)
    # Atomic (AUDIT-1 AUD-006): read-modify-rewrite stream.
    from pipeline.utils.atomic import write_text_atomic
    body = "\n".join(kept + [json.dumps(e, ensure_ascii=False) for e in events])
    write_text_atomic(path, body + "\n" if body else "")
    # Y2: refresh sidecar meta after the rewrite. Import lazily to
    # avoid a hot dependency at module load.
    from pipeline.aggregates_manifest import update_files as _update_manifest
    _update_manifest(["merge_events.jsonl"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today())
    parser.add_argument("--allow-shrink", action="store_true",
                        help="bypass the E5 continuity shrink guard (deliberate large shrink)")
    args = parser.parse_args()
    global ALLOW_SHRINK
    ALLOW_SHRINK = args.allow_shrink
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
