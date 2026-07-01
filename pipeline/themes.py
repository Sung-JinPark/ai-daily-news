"""Detect 3-5 cross-day narratives from the last 7 days of articles.

Groups the last-7-day articles by cluster_id, formats a compact
"clusters block" for Haiku, asks for 3-5 themes each spanning ≥2
clusters. Writes data/themes/rolling.json every run. On Sunday it
also archives data/themes/YYYY-Www.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

from pipeline.summarize import DATA_DIR, MODEL, RETRYABLE
from pipeline.utils.prompts import THEMES_SYSTEM_PROMPT, THEMES_USER_TEMPLATE
from pipeline.weekly import iso_week_for

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 1600
WINDOW_DAYS = 7
MAX_CLUSTERS_IN = 40  # keep the prompt small; we truncate to the strongest clusters

CATEGORY_KO = {
    "model_research": "모델/연구",
    "business": "비즈니스/투자",
    "policy": "정책/규제",
    "product": "제품/툴",
    "hardware": "하드웨어/인프라",
    "community": "커뮤니티",
}


def _load_recent_articles(days: int) -> tuple[str, str, list[dict]]:
    today = date.today()
    start = today - timedelta(days=days - 1)
    out: list[dict] = []
    seen_ids: set[str] = set()
    cur = start
    while cur <= today:
        p = DATA_DIR / cur.isoformat() / "articles.json"
        if p.exists():
            arts = json.loads(p.read_text(encoding="utf-8"))
            for a in arts:
                if a["id"] in seen_ids:
                    continue
                seen_ids.add(a["id"])
                out.append(a)
        cur += timedelta(days=1)
    return start.isoformat(), today.isoformat(), out


def _cluster_score(group: list[dict]) -> float:
    max_imp = max((a.get("importance_score", 0) for a in group), default=0)
    outlets: set[str] = set()
    for a in group:
        outlets.add(a.get("source_name", ""))
        outlets.update(a.get("also_covered_by", []) or [])
    days = len({(a.get("published") or a.get("fetched_at") or "")[:10] for a in group})
    return max_imp * 1.0 + (len(outlets) - 1) * 0.6 + (days - 1) * 0.8


def _group_by_cluster(articles: list[dict]) -> list[dict]:
    by_cluster: dict[str, list[dict]] = {}
    for a in articles:
        cid = a.get("cluster_id") or a.get("id")
        by_cluster.setdefault(cid, []).append(a)
    grouped = []
    for cid, arts in by_cluster.items():
        arts.sort(key=lambda a: a.get("published") or a.get("fetched_at") or "")
        rep = sorted(arts, key=lambda a: a.get("importance_score", 0), reverse=True)[0]
        outlets: set[str] = set()
        for a in arts:
            outlets.add(a.get("source_name", ""))
            outlets.update(a.get("also_covered_by", []) or [])
        tags = Counter()
        for a in arts:
            for t in a.get("tags", []) or []:
                tags[t] += 1
        first_day = (arts[0].get("published") or arts[0].get("fetched_at") or "")[:10]
        last_day = (arts[-1].get("published") or arts[-1].get("fetched_at") or "")[:10]
        daily = Counter()
        for a in arts:
            d = (a.get("published") or a.get("fetched_at") or "")[:10]
            if d:
                daily[d] += 1
        grouped.append(
            {
                "cluster_id": cid,
                "title": rep.get("title_original", ""),
                "category": rep.get("category", ""),
                "tags": [t for t, _ in tags.most_common(6)],
                "outlets": sorted(outlets),
                "summary_ko": rep.get("summary_ko", ""),
                "importance": rep.get("importance_score", 0),
                "articles": arts,
                "first_day": first_day,
                "last_day": last_day,
                "daily_counts": [{"day": d, "count": n} for d, n in sorted(daily.items())],
                "score": _cluster_score(arts),
            }
        )
    grouped.sort(key=lambda g: g["score"], reverse=True)
    return grouped


def _format_clusters(clusters: list[dict]) -> str:
    lines: list[str] = []
    for i, c in enumerate(clusters, 1):
        cat_ko = CATEGORY_KO.get(c["category"], c["category"])
        tags = ", ".join(c["tags"]) if c["tags"] else "-"
        outlets = f"{len(c['outlets'])}개 매체"
        span = f"{c['first_day']} → {c['last_day']}" if c["first_day"] != c["last_day"] else c["first_day"]
        lines.append(
            f"[{i}] ({cat_ko}) {c['title']}\n    {outlets} · {span} · 태그: {tags}\n    요약: {c['summary_ko']}"
        )
    return "\n\n".join(lines)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def _call_haiku(client: anthropic.Anthropic, start: str, end: str, n: int, block: str) -> dict[str, Any]:
    user = THEMES_USER_TEMPLATE.format(start=start, end=end, n=n, clusters=block)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=THEMES_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON object in themes response: {text[:200]!r}")
    parsed = json.loads(match.group(0))
    return {
        "parsed": parsed,
        "usage": {
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        },
    }


SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _clean_slug(raw: str, fallback: str) -> str:
    s = raw.lower().strip()
    s = SLUG_RE.sub("-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or fallback


def _validate(parsed: dict, clusters: list[dict]) -> list[dict] | None:
    themes_raw = parsed.get("themes")
    if not isinstance(themes_raw, list) or not themes_raw:
        return None
    out: list[dict] = []
    used_slugs: set[str] = set()
    for i, t in enumerate(themes_raw):
        if not isinstance(t, dict):
            continue
        name = str(t.get("name", "")).strip()
        thesis = str(t.get("thesis_ko", "")).strip()
        slug = _clean_slug(str(t.get("slug", "")), fallback=f"theme-{i+1}")
        idx_list = t.get("cluster_indices", [])
        if not name or not thesis or not isinstance(idx_list, list):
            continue
        cluster_ids: list[str] = []
        for idx in idx_list:
            try:
                n = int(idx)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(clusters):
                cid = clusters[n - 1]["cluster_id"]
                if cid not in cluster_ids:
                    cluster_ids.append(cid)
        if len(cluster_ids) < 2:
            continue
        # ensure unique slug
        base_slug = slug
        j = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{j}"
            j += 1
        used_slugs.add(slug)
        # aggregate daily counts across constituent clusters
        daily = Counter()
        for cid in cluster_ids:
            for cl in clusters:
                if cl["cluster_id"] == cid:
                    for dc in cl["daily_counts"]:
                        daily[dc["day"]] += dc["count"]
                    break
        out.append(
            {
                "slug": slug,
                "name": name,
                "thesis_ko": thesis,
                "cluster_ids": cluster_ids,
                "daily_counts": [{"day": d, "count": n} for d, n in sorted(daily.items())],
            }
        )
    if len(out) < 2:
        return None
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--archive-week", action="store_true", help="also write data/themes/<week>.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    start, end, articles = _load_recent_articles(WINDOW_DAYS)
    log.info("themes: window %s..%s, %d articles", start, end, len(articles))
    if len(articles) < 5:
        log.warning("not enough articles, skipping themes")
        return 0

    clusters = _group_by_cluster(articles)
    top = clusters[:MAX_CLUSTERS_IN]
    log.info("themes: %d clusters, top %d selected", len(clusters), len(top))

    if args.dry_run:
        for c in top[:10]:
            log.info("  [%s] %.1f  %s", c["cluster_id"], c["score"], c["title"][:80])
        return 0

    block = _format_clusters(top)
    client = anthropic.Anthropic()
    try:
        rsp = _call_haiku(client, start, end, len(top), block)
    except Exception as exc:  # noqa: BLE001
        log.error("themes LLM call failed: %s", exc)
        return 1

    themes = _validate(rsp["parsed"], top)
    if themes is None:
        log.error("themes schema validation failed: %s", rsp["parsed"])
        return 1

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start,
        "window_end": end,
        "themes": themes,
    }
    out_dir = DATA_DIR / "themes"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rolling.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("themes: wrote rolling.json with %d themes (usage=%s)", len(themes), rsp["usage"])

    if args.archive_week:
        week = iso_week_for(date.today())
        (out_dir / f"{week}.json").write_text(
            json.dumps({**payload, "week": week}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("themes: also archived %s.json", week)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
