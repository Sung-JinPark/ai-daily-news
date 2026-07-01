"""Extract measurable predictions from high-importance articles.

Runs after `summarize` on each day. Two passes:

  Pass A (extract): For today's articles with importance >= 3,
    ask Haiku to pull out measurable predictions. Append new ones
    to data/predictions/registry.json.

  Pass B (resolve): For pending predictions whose horizon is in the
    past OR that have been pending >60 days, batch-check against
    recent article summaries and mark confirmed/contradicted/
    still_pending. Contradicted predictions are retained (memory).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

from pipeline.collect import today as today_str
from pipeline.state import url_hash
from pipeline.summarize import (
    BATCH_POLL_SEC,
    BATCH_TIMEOUT_MIN,
    DATA_DIR,
    MODEL,
    RETRYABLE,
    parse_result,
    submit_batch,
    wait_for_batch,
)
from pipeline.utils.prompts import (
    PREDICTION_EXTRACT_SYSTEM_PROMPT,
    PREDICTION_EXTRACT_USER_TEMPLATE,
    PREDICTION_RESOLVE_SYSTEM_PROMPT,
    PREDICTION_RESOLVE_USER_TEMPLATE,
)

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 800
IMPORTANCE_MIN = 3
RESOLVE_STALE_DAYS = 60
RESOLVE_LOOKBACK_DAYS = 30
REGISTRY_FILE = DATA_DIR / "predictions" / "registry.json"

VALID_CONFIDENCE = {"low", "medium", "high"}
VALID_VERDICT = {"confirmed", "contradicted", "still_pending"}


def _load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        return {"version": 1, "predictions": []}
    try:
        return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "predictions": []}


def _save_registry(reg: dict) -> None:
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_FILE.write_text(
        json.dumps(reg, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _pred_id(article_id: str, claim: str) -> str:
    h = hashlib.sha256(f"{article_id}\u0001{claim}".encode("utf-8")).hexdigest()
    return h[:12]


def _valid_horizon(h: str) -> bool:
    if h == "unspecified":
        return True
    return bool(re.match(r"^\d{4}-\d{2}$", h))


def _validate_prediction(raw: dict) -> dict | None:
    try:
        claim = str(raw["claim_ko"]).strip()
        who = str(raw["who"]).strip()
        horizon = str(raw["horizon"]).strip()
        confidence = str(raw["confidence"]).strip().lower()
        measurable = bool(raw["measurable"])
    except (KeyError, TypeError, ValueError):
        return None
    if not claim or not who:
        return None
    if not _valid_horizon(horizon):
        return None
    if confidence not in VALID_CONFIDENCE:
        return None
    if not measurable:
        return None
    return {
        "claim_ko": claim,
        "who": who,
        "horizon": horizon,
        "confidence": confidence,
        "measurable": True,
    }


def _build_extract_request(custom_id: str, article: dict) -> dict:
    user = PREDICTION_EXTRACT_USER_TEMPLATE.format(
        title=article.get("title_original", ""),
        source_name=article.get("source_name", ""),
        summary=article.get("summary_ko", ""),
        insights="\n".join(f"- {i}" for i in article.get("insights_ko", []) or []),
    )
    return {
        "custom_id": custom_id,
        "params": {
            "model": MODEL,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": [
                {
                    "type": "text",
                    "text": PREDICTION_EXTRACT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user}],
        },
    }


def _load_articles_for_day(day: str) -> list[dict]:
    p = DATA_DIR / day / "articles.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _load_recent_articles(days: int) -> list[dict]:
    today = date.today()
    out: list[dict] = []
    seen: set[str] = set()
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        for a in _load_articles_for_day(d):
            if a["id"] in seen:
                continue
            seen.add(a["id"])
            out.append({**a, "day": d})
    return out


def extract_predictions(client: anthropic.Anthropic, day: str, dry_run: bool = False) -> int:
    articles = _load_articles_for_day(day)
    candidates = [a for a in articles if a.get("importance_score", 0) >= IMPORTANCE_MIN]
    log.info("extract: %d/%d articles at importance>=%d", len(candidates), len(articles), IMPORTANCE_MIN)

    if not candidates:
        return 0

    registry = _load_registry()
    known_ids = {p["id"] for p in registry["predictions"]}

    if dry_run:
        for a in candidates[:10]:
            log.info("  candidate: [%d] %s", a.get("importance_score", 0), a.get("title_original", "")[:70])
        return 0

    requests_list = []
    meta: dict[str, dict] = {}
    for a in candidates:
        cid = url_hash(a["url"])[:16]
        requests_list.append(_build_extract_request(cid, a))
        meta[cid] = a
    log.info("submitting extract batch: %d requests", len(requests_list))
    batch = submit_batch(client, requests_list)
    log.info("extract batch %s submitted", batch.id)
    batch = wait_for_batch(client, batch.id)

    added = 0
    for result in client.messages.batches.results(batch.id):
        parsed, _usage = parse_result(result)
        art = meta.get(result.custom_id)
        if not parsed or not art:
            continue
        raw_preds = parsed.get("predictions", [])
        if not isinstance(raw_preds, list):
            continue
        for i, raw in enumerate(raw_preds[:3]):
            valid = _validate_prediction(raw) if isinstance(raw, dict) else None
            if valid is None:
                continue
            pid = _pred_id(art["id"], valid["claim_ko"])
            if pid in known_ids:
                continue
            known_ids.add(pid)
            registry["predictions"].append(
                {
                    "id": pid,
                    "article_id": art["id"],
                    "article_url": art["url"],
                    "article_title": art.get("title_original", ""),
                    "source_name": art.get("source_name", ""),
                    "day_made": day,
                    "claim_ko": valid["claim_ko"],
                    "who": valid["who"],
                    "horizon": valid["horizon"],
                    "confidence": valid["confidence"],
                    "status": "pending",
                    "resolution_article_id": None,
                    "resolution_day": None,
                    "resolution_note_ko": None,
                }
            )
            added += 1
    _save_registry(registry)
    log.info("extract: added %d new predictions (registry size=%d)", added, len(registry["predictions"]))
    return added


def _pending_needs_resolution(pred: dict, today: date) -> bool:
    if pred["status"] != "pending":
        return False
    day_made = pred.get("day_made", "")
    if day_made:
        try:
            made = date.fromisoformat(day_made)
            if (today - made).days >= RESOLVE_STALE_DAYS:
                return True
        except ValueError:
            pass
    horizon = pred.get("horizon", "unspecified")
    if horizon != "unspecified":
        try:
            h_year, h_month = int(horizon[:4]), int(horizon[5:7])
            if (h_year, h_month) < (today.year, today.month):
                return True
        except (ValueError, IndexError):
            pass
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def _call_resolve(client: anthropic.Anthropic, predictions_block: str, articles_block: str, n: int) -> dict:
    user = PREDICTION_RESOLVE_USER_TEMPLATE.format(
        n_predictions=n,
        predictions_block=predictions_block,
        articles_block=articles_block,
    )
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=PREDICTION_RESOLVE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in resolve response: {text[:200]!r}")
    return json.loads(m.group(0))


def resolve_predictions(client: anthropic.Anthropic, dry_run: bool = False) -> int:
    registry = _load_registry()
    today = date.today()
    stale = [p for p in registry["predictions"] if _pending_needs_resolution(p, today)]
    log.info("resolve: %d predictions need review", len(stale))
    if not stale:
        return 0

    articles = _load_recent_articles(RESOLVE_LOOKBACK_DAYS)
    if not articles:
        log.info("resolve: no recent articles available for evidence")
        return 0

    if dry_run:
        for p in stale[:10]:
            log.info("  needs review: %s (%s) horizon=%s", p["id"], p["claim_ko"][:60], p["horizon"])
        return 0

    updated = 0
    # Batch predictions in groups of 15 with all recent articles.
    articles_block_full = "\n".join(
        f"[{a['id']}] ({a['day']}) {a.get('title_original','')} — {a.get('summary_ko','')[:200]}"
        for a in articles[:120]  # cap tokens
    )
    for i in range(0, len(stale), 15):
        chunk = stale[i : i + 15]
        preds_block = "\n".join(
            f"[{p['id']}] ({p['day_made']} · {p['who']} · horizon={p['horizon']}) {p['claim_ko']}"
            for p in chunk
        )
        try:
            parsed = _call_resolve(client, preds_block, articles_block_full, len(chunk))
        except Exception as exc:  # noqa: BLE001
            log.warning("resolve chunk failed: %s", exc)
            continue
        results = parsed.get("results", [])
        if not isinstance(results, list):
            continue
        by_id = {p["id"]: p for p in chunk}
        for r in results:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id", ""))
            verdict = str(r.get("verdict", "")).strip()
            if rid not in by_id or verdict not in VALID_VERDICT:
                continue
            pred = by_id[rid]
            if verdict == "still_pending":
                # Explicitly keep as pending but bump a review marker.
                pred["last_reviewed"] = today.isoformat()
                updated += 1
                continue
            pred["status"] = verdict
            pred["resolution_article_id"] = str(r.get("evidence_article_id", "") or "") or None
            pred["resolution_day"] = today.isoformat()
            pred["resolution_note_ko"] = str(r.get("note_ko", "") or "") or None
            updated += 1
    _save_registry(registry)
    log.info("resolve: %d predictions updated", updated)
    return updated


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today_str())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-extract", action="store_true")
    parser.add_argument("--skip-resolve", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    client = anthropic.Anthropic() if not args.dry_run else None

    if not args.skip_extract:
        try:
            extract_predictions(client, args.day, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001
            log.error("extract pass failed: %s", exc)

    if not args.skip_resolve:
        try:
            resolve_predictions(client, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001
            log.error("resolve pass failed: %s", exc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
