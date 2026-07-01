"""Voyage-3 summary embeddings for the archive (ZE1).

Runs after ``summarize`` on every daily CI. For each article that
does not yet appear in ``data/embeddings/manifest.json`` this module
sends its ``summary_ko`` to the Voyage AI embedding API in batches
of 128 and appends the resulting 1024-dim vectors to
``data/embeddings/YYYY-MM-DD.jsonl.gz``. The manifest keeps the
one-shot bookkeeping so re-runs are safe.

Cost profile (Voyage-3, $0.06 / M input tokens):
    * ~150 tokens per Korean summary
    * 60 new articles / day → ~9K tokens / day
    * ~$0.02 / month at current DAILY_CAP=120

The vector file is gzipped JSONL for git-friendly diff size:

    {"article_id": "…", "day": "YYYY-MM-DD",
     "source": "summary_ko", "model": "voyage-3",
     "dim": 1024, "vector": [0.0123, …]}

An environment variable ``VOYAGE_API_KEY`` is required for live
runs. Without it (or with ``--dry-run``) the script still walks the
archive, prints how many articles would be embedded, and exits
without a live API call — safe to invoke in review workflows.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
EMBED_DIR = DATA_DIR / "embeddings"
MANIFEST_FILE = EMBED_DIR / "manifest.json"

MODEL = "voyage-3"
DIM = 1024
BATCH_SIZE = 128
MAX_TEXT_CHARS = 4000  # summary_ko is much shorter, this is a defensive cap

DATE_RE = "^\\d{4}-\\d{2}-\\d{2}$"


# ---------- manifest ----------

def _load_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        return {"schema_version": 1, "model": MODEL, "dim": DIM, "articles": {}}
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 1, "model": MODEL, "dim": DIM, "articles": {}}


def _save_manifest(manifest: dict) -> None:
    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    manifest["schema_version"] = 1
    manifest["model"] = MODEL
    manifest["dim"] = DIM
    manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = MANIFEST_FILE.with_suffix(MANIFEST_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST_FILE)


# ---------- archive walk ----------

def _list_days() -> list[str]:
    import re
    if not DATA_DIR.exists():
        return []
    return sorted(
        p.name for p in DATA_DIR.iterdir()
        if p.is_dir() and re.match(DATE_RE, p.name)
    )


def _iter_pending(manifest: dict) -> list[dict]:
    """Return articles that have a summary and are not yet in the manifest."""
    known = set((manifest.get("articles") or {}).keys())
    pending: list[dict] = []
    for day in _list_days():
        p = DATA_DIR / day / "articles.json"
        if not p.exists():
            continue
        try:
            arts = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for a in arts:
            aid = a.get("id")
            summary = (a.get("summary_ko") or "").strip()
            if not aid or not summary or aid in known:
                continue
            pending.append({
                "id": aid,
                "day": day,
                "text": summary[:MAX_TEXT_CHARS],
                "title_original": a.get("title_original", ""),
                "source_id": a.get("source_id", ""),
            })
    return pending


# ---------- write ----------

def _append_gz(day: str, rows: list[dict]) -> None:
    EMBED_DIR.mkdir(parents=True, exist_ok=True)
    path = EMBED_DIR / f"{day}.jsonl.gz"
    # Read existing rows, filter dedup by article_id, append new ones.
    existing: dict[str, dict] = {}
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    existing[obj.get("article_id", "")] = obj
                except Exception:
                    continue
    for r in rows:
        existing[r["article_id"]] = r
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:
        for aid in sorted(existing):
            f.write(json.dumps(existing[aid], ensure_ascii=False))
            f.write("\n")


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------- API ----------

def _embed_batch(client, texts: list[str]) -> list[list[float]]:
    """Send one batch to Voyage AI and return the vectors in the same
    order. ``client`` is a ``voyageai.Client`` instance."""
    result = client.embed(texts, model=MODEL, input_type="document")
    return list(result.embeddings)


# ---------- main ----------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="walk the archive but skip API calls")
    parser.add_argument("--limit", type=int, default=1500,
                        help="Cap pending articles per invocation so a large backfill can be split across days")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    manifest = _load_manifest()
    pending = _iter_pending(manifest)
    log.info(
        "embed: %d articles pending (already embedded=%d, model=%s, dim=%d)",
        len(pending), len(manifest.get("articles", {})), MODEL, DIM,
    )
    if not pending:
        return 0
    if len(pending) > args.limit:
        log.info("embed: capping this run to %d articles (limit)", args.limit)
        pending = pending[: args.limit]

    if args.dry_run or not os.environ.get("VOYAGE_API_KEY"):
        if args.dry_run:
            log.info("dry-run: skipping API calls")
        else:
            log.warning("VOYAGE_API_KEY not set — treating as dry-run")
        for p in pending[:10]:
            log.info("  would embed: [%s] %s — %s", p["day"], p["id"], p["title_original"][:60])
        return 0

    try:
        import voyageai
    except ImportError:
        log.error("voyageai package not installed. Run: pip install voyageai")
        return 1

    client = voyageai.Client()
    total_written = 0
    per_day: dict[str, list[dict]] = {}
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        texts = [p["text"] for p in batch]
        try:
            vectors = _embed_batch(client, texts)
        except Exception as exc:  # noqa: BLE001
            log.error("embed batch %d failed: %s", i // BATCH_SIZE + 1, exc)
            continue
        now = datetime.now(timezone.utc).isoformat()
        for p, v in zip(batch, vectors):
            row = {
                "article_id": p["id"],
                "day": p["day"],
                "source": "summary_ko",
                "model": MODEL,
                "dim": DIM,
                "embedded_at": now,
                "vector": [float(x) for x in v],
            }
            per_day.setdefault(p["day"], []).append(row)
            manifest.setdefault("articles", {})[p["id"]] = {
                "day": p["day"], "source": "summary_ko", "model": MODEL,
            }
        total_written += len(batch)
        log.info("embed batch %d: %d vectors", i // BATCH_SIZE + 1, len(batch))

    for day, rows in per_day.items():
        _append_gz(day, rows)
    _save_manifest(manifest)
    log.info("embed: wrote %d vectors across %d day file(s)", total_written, len(per_day))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
