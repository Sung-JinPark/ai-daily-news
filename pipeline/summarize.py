"""Run Claude Haiku 4.5 on each new cluster representative.

Reads raw/<day>/clusters.json, skips URLs already in .cache/seen.json,
calls extract.extract_body, sends to Haiku with prompt-caching on the system
prompt, validates the JSON output, and writes data/<day>/articles.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

RETRYABLE = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)

from pipeline.collect import RAW_DIR, today
from pipeline.extract import extract_body
from pipeline.state import load_seen, save_seen, url_hash
from pipeline.utils.prompts import SYSTEM_PROMPT, USER_TEMPLATE

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
MODEL = "claude-haiku-4-5-20251001"
ALLOWED_CATEGORIES = {"model_research", "business", "policy", "product", "hardware", "community"}
MAX_OUTPUT_TOKENS = 700


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def call_haiku(client: anthropic.Anthropic, title: str, source_name: str, body: str) -> dict[str, Any]:
    user = USER_TEMPLATE.format(title=title, source_name=source_name, body=body or "(본문 추출 실패)")
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text").strip()
    # Robust JSON extraction: grab outermost {...} block to tolerate fences / preambles.
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON object found in response: {text[:200]!r}")
    parsed = json.loads(match.group(0))
    usage = {
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "cache_read_input_tokens": getattr(msg.usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0),
    }
    return {"parsed": parsed, "usage": usage}


def validate(parsed: dict) -> dict | None:
    try:
        summary = str(parsed["summary_ko"]).strip()
        insights = [str(s).strip() for s in parsed["insights_ko"] if str(s).strip()]
        category = str(parsed["category"]).strip()
        score = int(parsed["importance_score"])
    except (KeyError, TypeError, ValueError):
        return None
    if not summary or not (2 <= len(insights) <= 3):
        return None
    if category not in ALLOWED_CATEGORIES:
        return None
    if not (1 <= score <= 5):
        return None
    return {
        "summary_ko": summary,
        "insights_ko": insights,
        "category": category,
        "importance_score": score,
    }


MIN_BODY_CHARS = 300


def process_cluster(client: anthropic.Anthropic, cluster: dict) -> tuple[dict | None, dict]:
    rep = cluster["representative"]
    body = extract_body(rep["url"])
    if len(body) < MIN_BODY_CHARS:
        # Fall back to RSS summary when paywalled / SPA / extractor blank.
        rss_summary = (rep.get("summary") or "").strip()
        if rss_summary:
            body = f"(본문 추출 실패. RSS 요약만 사용)\n\n{rss_summary}"
        elif not body:
            log.info("skip cluster %s: no body and no RSS summary", cluster["cluster_id"])
            return None, {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    rsp = call_haiku(client, rep["title"], rep["source_name"], body)
    parsed = validate(rsp["parsed"])
    if parsed is None:
        log.warning("schema validation failed for %s", rep["url"])
        return None, rsp["usage"]
    article = {
        "id": url_hash(rep["url"]),
        "cluster_id": cluster["cluster_id"],
        "title_original": rep["title"],
        "url": rep["url"],
        "source_id": rep["source_id"],
        "source_name": rep["source_name"],
        "published": rep.get("published"),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "cluster_size": len(cluster["members"]),
        "also_covered_by": [m["source_name"] for m in cluster["members"] if m["url"] != rep["url"]],
        **parsed,
    }
    return article, rsp["usage"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today())
    parser.add_argument("--limit", type=int, default=int(os.environ.get("DAILY_CAP", "100")))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    clusters_file = RAW_DIR / args.day / "clusters.json"
    if not clusters_file.exists():
        log.error("missing clusters file: %s", clusters_file)
        return 1
    clusters = json.loads(clusters_file.read_text(encoding="utf-8"))

    seen = load_seen()
    new_clusters = [c for c in clusters if url_hash(c["representative"]["url"]) not in seen]
    log.info("clusters: %d total, %d new (cap=%d)", len(clusters), len(new_clusters), args.limit)
    new_clusters = new_clusters[: args.limit]

    if args.dry_run:
        log.info("dry-run: skipping LLM calls")
        return 0

    client = anthropic.Anthropic()
    out_dir = DATA_DIR / args.day
    out_dir.mkdir(parents=True, exist_ok=True)
    existing_file = out_dir / "articles.json"
    articles: list[dict] = (
        json.loads(existing_file.read_text(encoding="utf-8")) if existing_file.exists() else []
    )

    stats = {"calls": 0, "succeeded": 0, "schema_failed": 0, "errors": 0, "usage": {
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
    }}

    for cluster in new_clusters:
        stats["calls"] += 1
        try:
            article, usage = process_cluster(client, cluster)
        except Exception as exc:  # noqa: BLE001
            log.warning("cluster %s failed: %s", cluster["cluster_id"], exc)
            stats["errors"] += 1
            continue
        for k, v in usage.items():
            stats["usage"][k] += v
        if article is None:
            stats["schema_failed"] += 1
            continue
        articles.append(article)
        stats["succeeded"] += 1
        seen.add(url_hash(cluster["representative"]["url"]))

    existing_file.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    save_seen(seen)
    log.info("summarize done: %d articles (stats=%s)", len(articles), stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
