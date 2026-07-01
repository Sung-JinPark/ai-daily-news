"""Extract per-model facts (version, benchmarks, pricing, strengths,
weaknesses) from articles that mention a known model.

Runs after `summarize` daily. Only touches articles whose tags include
one of the MODEL_ENTITIES. Uses Batch API for cost control. Emits:

  data/models/facts.jsonl    (append-only, one line per article that
                              produced any facts, replaces prior lines
                              from the same article if re-run)
  data/models/index.json     (rebuilt every run from the last 60 days
                              of facts.jsonl, one aggregate row per
                              model with top-3 benchmarks, pricing,
                              strengths, weaknesses).
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

load_dotenv()

from pipeline.collect import today as today_str
from pipeline.state import url_hash
from pipeline.summarize import (
    DATA_DIR,
    MODEL,
    parse_result,
    submit_batch,
    wait_for_batch,
)
from pipeline.utils.prompts import MODEL_FACTS_SYSTEM_PROMPT, MODEL_FACTS_USER_TEMPLATE

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 800
LOOKBACK_DAYS = 60

# Must match the "모델 어휘" section in MODEL_FACTS_SYSTEM_PROMPT.
MODEL_ENTITIES = {
    "GPT-5",
    "GPT-4",
    "Claude",
    "Gemini",
    "Llama",
    "Mistral",
    "Sora",
    "DALL-E",
    "Whisper",
    "Stable Diffusion",
    "Grok",
    "DeepSeek",
    "Qwen",
    "Phi",
}

FACTS_JSONL = DATA_DIR / "models" / "facts.jsonl"
INDEX_FILE = DATA_DIR / "models" / "index.json"


def _load_articles_for_day(day: str) -> list[dict]:
    p = DATA_DIR / day / "articles.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _relevant(article: dict) -> bool:
    tags = set(article.get("tags", []) or [])
    if tags & MODEL_ENTITIES:
        return True
    # Heuristic backup: model name mentioned in title (some articles miss the tag).
    title = article.get("title_original", "")
    return any(m in title for m in MODEL_ENTITIES)


# Simple hand-maintained list of tokens that look like new model names but
# are NOT in MODEL_ENTITIES yet. When one shows up in a title we log the
# article to data/models/candidates.jsonl so the vocabulary can be
# reviewed periodically instead of silently missing coverage.
CANDIDATE_HINTS = {
    "GPT-6", "GPT-7", "Claude 4", "Claude 5", "Claude Opus", "Claude Sonnet",
    "Claude Haiku", "Gemini 3", "Gemini 4", "Llama 4", "Llama 5",
    "o1", "o3", "Reasoner", "Command R",
}
CANDIDATES_FILE = DATA_DIR / "models" / "candidates.jsonl"


def _log_candidates(day: str, articles: list[dict]) -> int:
    """Append rows for articles that mention a plausible new model name
    but no MODEL_ENTITIES tag. This surfaces vocabulary gaps for human
    review without disrupting the current whitelist."""
    if not articles:
        return 0
    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    if CANDIDATES_FILE.exists():
        for line in CANDIDATES_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            seen.add(f"{obj.get('day','')}|{obj.get('article_id','')}|{obj.get('hint','')}")
    n = 0
    with CANDIDATES_FILE.open("a", encoding="utf-8", newline="\n") as f:
        for a in articles:
            title = a.get("title_original", "") or ""
            tags = set(a.get("tags", []) or [])
            if tags & MODEL_ENTITIES:
                continue  # already covered by whitelist
            hint = next((h for h in CANDIDATE_HINTS if h in title), None)
            if not hint:
                continue
            key = f"{day}|{a.get('id','')}|{hint}"
            if key in seen:
                continue
            seen.add(key)
            f.write(json.dumps({
                "logged_at": datetime.now(timezone.utc).isoformat(),
                "day": day,
                "article_id": a.get("id", ""),
                "url": a.get("url", ""),
                "title": title,
                "hint": hint,
                "source_name": a.get("source_name", ""),
            }, ensure_ascii=False))
            f.write("\n")
            n += 1
    return n


def _build_request(custom_id: str, article: dict) -> dict:
    user = MODEL_FACTS_USER_TEMPLATE.format(
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
                    "text": MODEL_FACTS_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user}],
        },
    }


def _validate_fact(raw: dict) -> dict | None:
    try:
        model = str(raw["model"]).strip()
    except (KeyError, TypeError):
        return None
    if model not in MODEL_ENTITIES:
        return None
    version = raw.get("version")
    version = str(version).strip() if version else None
    if version in ("null", ""):
        version = None
    def _list_of_dicts(v: Any, keys: tuple[str, ...]) -> list[dict]:
        if not isinstance(v, list):
            return []
        out = []
        for item in v:
            if not isinstance(item, dict):
                continue
            row = {k: str(item.get(k, "")).strip() for k in keys}
            if any(row.values()):
                out.append(row)
            if len(out) >= 3:
                break
        return out
    benchmarks = _list_of_dicts(raw.get("benchmarks"), ("name", "score"))
    pricing = _list_of_dicts(raw.get("pricing"), ("unit", "value"))
    def _list_of_strs(v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        out: list[str] = []
        for item in v:
            s = str(item).strip()
            if s and s not in out:
                out.append(s)
            if len(out) >= 3:
                break
        return out
    strengths = _list_of_strs(raw.get("strengths_ko"))
    weaknesses = _list_of_strs(raw.get("weaknesses_ko"))
    return {
        "model": model,
        "version": version,
        "benchmarks": benchmarks,
        "pricing": pricing,
        "strengths_ko": strengths,
        "weaknesses_ko": weaknesses,
    }


def _append_facts_line(article: dict, day: str, facts: list[dict]) -> None:
    FACTS_JSONL.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "article_id": article["id"],
            "day": day,
            "url": article.get("url", ""),
            "title": article.get("title_original", ""),
            "source_name": article.get("source_name", ""),
            "facts": facts,
        },
        ensure_ascii=False,
    )
    # Replace any prior line for this article (poor-man's upsert).
    if FACTS_JSONL.exists():
        keep: list[str] = []
        aid = article["id"]
        for existing in FACTS_JSONL.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(existing)
            except Exception:
                continue
            if obj.get("article_id") == aid:
                continue
            keep.append(existing)
        FACTS_JSONL.write_text("\n".join(keep + [line]) + "\n", encoding="utf-8")
    else:
        FACTS_JSONL.write_text(line + "\n", encoding="utf-8")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def rebuild_index() -> None:
    if not FACTS_JSONL.exists():
        log.info("no facts.jsonl, nothing to index")
        return
    cutoff = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    per_model: dict[str, dict] = {}
    for line in FACTS_JSONL.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(line)
        except Exception:
            continue
        day = obj.get("day", "")
        if day < cutoff:
            continue
        aid = obj.get("article_id", "")
        for fact in obj.get("facts", []) or []:
            m = fact.get("model")
            if not m:
                continue
            row = per_model.setdefault(
                m,
                {
                    "model": m,
                    "latest_version": None,
                    "latest_seen": "",
                    "articles": [],
                    "top_benchmarks": [],
                    "pricing": [],
                    "strengths_ko": [],
                    "weaknesses_ko": [],
                    "_bench_counter": Counter(),
                    "_bench_source": {},
                    "_price_counter": Counter(),
                    "_price_source": {},
                    "_strength_counter": Counter(),
                    "_weakness_counter": Counter(),
                    "_seen_articles": set(),
                },
            )
            if aid and aid not in row["_seen_articles"]:
                row["_seen_articles"].add(aid)
                row["articles"].append({"id": aid, "day": day, "title": obj.get("title", ""), "url": obj.get("url", "")})
            if day > row["latest_seen"]:
                row["latest_seen"] = day
                if fact.get("version"):
                    row["latest_version"] = fact["version"]
            for b in fact.get("benchmarks", []) or []:
                key = _norm(f"{b.get('name','')} {b.get('score','')}")
                if not key:
                    continue
                row["_bench_counter"][key] += 1
                if key not in row["_bench_source"]:
                    row["_bench_source"][key] = {"name": b.get("name", ""), "score": b.get("score", ""), "article_id": aid}
            for p in fact.get("pricing", []) or []:
                key = _norm(f"{p.get('unit','')} {p.get('value','')}")
                if not key:
                    continue
                row["_price_counter"][key] += 1
                if key not in row["_price_source"]:
                    row["_price_source"][key] = {"unit": p.get("unit", ""), "value": p.get("value", ""), "article_id": aid}
            for s in fact.get("strengths_ko", []) or []:
                key = _norm(s)
                if key:
                    row["_strength_counter"][key] += 1
            for w in fact.get("weaknesses_ko", []) or []:
                key = _norm(w)
                if key:
                    row["_weakness_counter"][key] += 1

    # Finalize each model row.
    out: list[dict] = []
    for m, row in per_model.items():
        row["top_benchmarks"] = [row["_bench_source"][k] for k, _ in row["_bench_counter"].most_common(3)]
        row["pricing"] = [row["_price_source"][k] for k, _ in row["_price_counter"].most_common(3)]
        # For strengths/weaknesses we lose case with _norm, so use the counter keys as-is (they were user-provided).
        row["strengths_ko"] = [k for k, _ in row["_strength_counter"].most_common(3)]
        row["weaknesses_ko"] = [k for k, _ in row["_weakness_counter"].most_common(3)]
        row["articles"].sort(key=lambda a: a["day"], reverse=True)
        row["article_count"] = len(row["articles"])
        for k in list(row.keys()):
            if k.startswith("_"):
                del row[k]
        out.append(row)
    out.sort(key=lambda r: r["latest_seen"], reverse=True)
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    INDEX_FILE.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "lookback_days": LOOKBACK_DAYS,
                "models": out,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info("index rebuilt: %d models", len(out))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", default=today_str())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--index-only", action="store_true", help="skip LLM, only rebuild the index from existing facts.jsonl")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    if args.index_only:
        rebuild_index()
        return 0

    articles = _load_articles_for_day(args.day)
    candidates = [a for a in articles if _relevant(a)]
    log.info("model_facts: %d/%d articles reference a known model", len(candidates), len(articles))
    n_hints = _log_candidates(args.day, articles)
    if n_hints:
        log.info("model_facts: logged %d vocabulary-gap candidates -> %s", n_hints, CANDIDATES_FILE)
    if not candidates:
        rebuild_index()
        return 0

    if args.dry_run:
        for a in candidates[:10]:
            tags = ", ".join(a.get("tags", []) or [])
            log.info("  candidate: %s [tags=%s]", a.get("title_original", "")[:70], tags)
        return 0

    client = anthropic.Anthropic()
    requests_list = []
    meta: dict[str, dict] = {}
    for a in candidates:
        cid = url_hash(a["url"])[:16]
        requests_list.append(_build_request(cid, a))
        meta[cid] = a
    log.info("submitting model_facts batch: %d requests", len(requests_list))
    try:
        batch = submit_batch(client, requests_list)
    except Exception as exc:  # noqa: BLE001
        log.error("batch submit failed: %s", exc)
        return 1
    log.info("batch %s submitted", batch.id)
    try:
        batch = wait_for_batch(client, batch.id)
    except Exception as exc:  # noqa: BLE001
        log.error("batch wait failed: %s", exc)
        return 1

    n_wrote = 0
    for result in client.messages.batches.results(batch.id):
        parsed, _usage = parse_result(result)
        art = meta.get(result.custom_id)
        if not parsed or not art:
            continue
        raw_facts = parsed.get("facts", [])
        if not isinstance(raw_facts, list):
            continue
        valid = [v for v in (_validate_fact(f) for f in raw_facts if isinstance(f, dict)) if v]
        if not valid:
            continue
        _append_facts_line(art, args.day, valid)
        n_wrote += 1
    log.info("model_facts: wrote %d article rows to facts.jsonl", n_wrote)
    rebuild_index()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
