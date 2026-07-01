"""Run Claude Haiku 4.5 (Batch API) on each new cluster representative.

Reads raw/<day>/clusters.json, skips URLs already in .cache/seen.json,
extracts the article body in-memory, sends all new clusters as a single
Batch API job (50% list-price discount, processed asynchronously),
polls until completion, validates each result, and writes
data/<day>/articles.json.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

RETRYABLE = (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.InternalServerError)

from pipeline.collect import RAW_DIR, today
from pipeline.extract import extract_article
from pipeline.state import load_seen, save_seen, url_hash
from pipeline.utils.prompts import SYSTEM_PROMPT, TAG_VOCAB, USER_TEMPLATE
from pipeline import corpus_writer

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
MODEL = "claude-haiku-4-5-20251001"
ALLOWED_CATEGORIES = {"model_research", "business", "policy", "product", "hardware", "community"}
MAX_OUTPUT_TOKENS = 1200
MIN_BODY_CHARS = 300
BATCH_POLL_SEC = 30
BATCH_TIMEOUT_MIN = 50


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
    result: dict = {
        "summary_ko": summary,
        "insights_ko": insights,
        "category": category,
        "importance_score": score,
    }
    subtitle_en = parsed.get("subtitle_en")
    if subtitle_en and isinstance(subtitle_en, str):
        sub = subtitle_en.strip().rstrip(".").rstrip("。")
        if sub and len(sub) <= 200:
            result["subtitle_en"] = sub
    raw_tags = parsed.get("tags")
    if isinstance(raw_tags, list):
        filtered = []
        seen_tags: set[str] = set()
        for t in raw_tags:
            tag = str(t).strip()
            if tag in TAG_VOCAB and tag not in seen_tags:
                filtered.append(tag)
                seen_tags.add(tag)
            if len(filtered) >= 5:
                break
        if filtered:
            result["tags"] = filtered
    if category == "model_research":
        institution = parsed.get("institution")
        authors = parsed.get("authors")
        if institution and str(institution).strip() not in ("null", ""):
            result["institution"] = str(institution).strip()
        if authors and str(authors).strip() not in ("null", ""):
            result["authors"] = str(authors).strip()
    return result


def build_request(custom_id: str, title: str, source_name: str, body: str) -> dict:
    user = USER_TEMPLATE.format(title=title, source_name=source_name, body=body or "(본문 추출 실패)")
    return {
        "custom_id": custom_id,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": [
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user}],
        },
    }


def extract_bodies(new_clusters: list[dict], day: str | None = None) -> tuple[list[dict], dict[str, dict]]:
    """Returns (batch_requests, cluster_meta). cluster_meta maps custom_id to
    {cluster, image_url}.

    If ``day`` is set, every extracted body is persisted to
    ``data/corpus/<day>/bodies.jsonl`` and every failure/skip is logged
    to ``data/corpus/<day>/skipped.jsonl``.
    """
    requests_list: list[dict] = []
    cluster_meta: dict[str, dict] = {}
    for cluster in new_clusters:
        rep = cluster["representative"]
        try:
            fetched = extract_article(rep["url"])
        except Exception as exc:  # noqa: BLE001
            log.warning("extract failed for %s: %s", rep["url"], exc)
            if day:
                corpus_writer.append_skipped(
                    day,
                    url_hash=url_hash(rep["url"]),
                    url=rep["url"],
                    source_id=rep.get("source_id", ""),
                    title=rep.get("title", ""),
                    phase="extract",
                    reason=str(exc),
                )
            continue
        body = fetched["body"]
        image_url = fetched["image_url"]
        extract_status = "ok"
        if len(body) < MIN_BODY_CHARS:
            rss_summary = (rep.get("summary") or "").strip()
            if rss_summary:
                body = f"(본문 추출 실패. RSS 요약만 사용)\n\n{rss_summary}"
                extract_status = "rss_fallback"
            elif not body:
                log.info("skip cluster %s: no body and no RSS summary", cluster["cluster_id"])
                if day:
                    corpus_writer.append_skipped(
                        day,
                        url_hash=url_hash(rep["url"]),
                        url=rep["url"],
                        source_id=rep.get("source_id", ""),
                        title=rep.get("title", ""),
                        phase="body_too_short",
                        reason=f"body<{MIN_BODY_CHARS} and no RSS summary",
                    )
                continue
        custom_id = url_hash(rep["url"])
        if custom_id in cluster_meta:
            continue  # same URL twice within batch — guard
        if day:
            corpus_writer.append_body(
                day,
                url_hash=custom_id,
                url=rep["url"],
                title=rep.get("title", ""),
                source_id=rep.get("source_id", ""),
                source_name=rep.get("source_name", ""),
                published=rep.get("published"),
                body_text=body,
                body_chars=len(body),
                extract_status=extract_status,
            )
        requests_list.append(build_request(custom_id, rep["title"], rep["source_name"], body))
        cluster_meta[custom_id] = {"cluster": cluster, "image_url": image_url}
    return requests_list, cluster_meta


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def submit_batch(client: anthropic.Anthropic, requests_list: list[dict]):
    return client.messages.batches.create(requests=requests_list)


def wait_for_batch(client: anthropic.Anthropic, batch_id: str) -> Any:
    deadline = time.time() + BATCH_TIMEOUT_MIN * 60
    last_status = ""
    while time.time() < deadline:
        batch = client.messages.batches.retrieve(batch_id)
        status = batch.processing_status
        if status != last_status:
            log.info("batch %s status=%s", batch_id, status)
            last_status = status
        if status == "ended":
            return batch
        time.sleep(BATCH_POLL_SEC)
    raise TimeoutError(f"batch {batch_id} did not end within {BATCH_TIMEOUT_MIN} min")


def parse_result(result: Any) -> tuple[dict | None, dict]:
    """Returns (parsed_json, usage). parsed_json is None on failure."""
    empty_usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    rtype = getattr(result.result, "type", "")
    if rtype != "succeeded":
        return None, empty_usage
    message = result.result.message
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text").strip()
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None, empty_usage
    try:
        parsed = json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return None, empty_usage
    usage = {
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
        "cache_read_input_tokens": getattr(message.usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(message.usage, "cache_creation_input_tokens", 0),
    }
    return parsed, usage


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today())
    parser.add_argument("--limit", type=int, default=int(os.environ.get("DAILY_CAP", "120")))
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

    out_dir = DATA_DIR / args.day
    out_dir.mkdir(parents=True, exist_ok=True)
    existing_file = out_dir / "articles.json"
    articles: list[dict] = (
        json.loads(existing_file.read_text(encoding="utf-8")) if existing_file.exists() else []
    )

    stats = {
        "calls": 0,
        "succeeded": 0,
        "schema_failed": 0,
        "errors": 0,
        "skipped_no_body": 0,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    }

    # Phase 1: extract bodies for all new clusters.
    log.info("extracting bodies for %d clusters", len(new_clusters))
    requests_list, cluster_meta = extract_bodies(new_clusters, day=args.day)
    stats["skipped_no_body"] = len(new_clusters) - len(requests_list)
    if not requests_list:
        log.info("no clusters to summarize")
        existing_file.write_text(
            json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (out_dir / "_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        save_seen(seen)
        return 0

    # Phase 2: submit batch.
    client = anthropic.Anthropic()
    stats["calls"] = len(requests_list)
    log.info("submitting batch with %d requests", len(requests_list))
    try:
        batch = submit_batch(client, requests_list)
    except Exception as exc:  # noqa: BLE001
        log.error("batch submission failed: %s", exc)
        return 1
    log.info("batch %s submitted", batch.id)

    # Phase 3: wait.
    try:
        batch = wait_for_batch(client, batch.id)
    except Exception as exc:  # noqa: BLE001
        log.error("batch wait failed: %s", exc)
        return 1

    # Phase 4: collect results.
    log.info("retrieving batch results...")
    for result in client.messages.batches.results(batch.id):
        custom_id = result.custom_id
        meta = cluster_meta.get(custom_id)
        parsed_json, usage = parse_result(result)
        for k, v in usage.items():
            stats["usage"][k] += v
        if parsed_json is None:
            log.warning("batch result %s did not succeed or yielded no JSON", custom_id)
            stats["errors"] += 1
            if meta:
                rep = meta["cluster"]["representative"]
                corpus_writer.append_skipped(
                    args.day,
                    url_hash=custom_id,
                    url=rep.get("url", ""),
                    source_id=rep.get("source_id", ""),
                    title=rep.get("title", ""),
                    phase="llm_batch",
                    reason="batch result did not succeed or yielded no JSON",
                )
            continue
        validated = validate(parsed_json)
        if validated is None:
            log.warning("schema validation failed for %s", custom_id)
            stats["schema_failed"] += 1
            if meta:
                rep = meta["cluster"]["representative"]
                corpus_writer.append_skipped(
                    args.day,
                    url_hash=custom_id,
                    url=rep.get("url", ""),
                    source_id=rep.get("source_id", ""),
                    title=rep.get("title", ""),
                    phase="llm_schema",
                    reason=f"validation failed: {json.dumps(parsed_json, ensure_ascii=False)[:300]}",
                )
            continue
        if meta is None:
            log.warning("no cluster meta for custom_id %s", custom_id)
            continue
        cluster = meta["cluster"]
        image_url = meta["image_url"]
        rep = cluster["representative"]
        article = {
            "id": url_hash(rep["url"]),
            "cluster_id": cluster["cluster_id"],
            "title_original": rep["title"],
            "url": rep["url"],
            "image_url": image_url or None,
            "source_id": rep["source_id"],
            "source_name": rep["source_name"],
            "published": rep.get("published"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "cluster_size": len(cluster["members"]),
            "also_covered_by": [m["source_name"] for m in cluster["members"] if m["url"] != rep["url"]],
            **validated,
        }
        articles.append(article)
        seen.add(custom_id)
        stats["succeeded"] += 1

    existing_file.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "_stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    save_seen(seen)
    corpus_writer.update_manifest(args.day)
    log.info("summarize done: %d articles total (stats=%s)", len(articles), stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
