"""Persist the raw materials the pipeline used to throw away.

Three per-day JSONL files under ``data/corpus/YYYY-MM-DD/`` capture
what was previously in-memory-only:

* ``bodies.jsonl``  — extracted article text per representative
                       (post-trafilatura, capped at MAX_BODY_CHARS
                       by pipeline/extract.py). Enables re-analysis,
                       embedding, RAG, direct quotes.
                       DBQ-3 (2026-07-03): this file is PRIVATE —
                       gitignored (data/corpus/*/bodies.jsonl) because
                       it holds third-party article full text we must
                       not republish. It is still WRITTEN here every
                       run and consumed locally (research en_corpus,
                       summarize); only git tracking is dropped. The
                       members/skipped/manifest siblings stay public.
* ``members.jsonl`` — every article in every cluster (dedup output),
                       not just the LLM-summarized representative.
                       Enables outlet-level coverage analysis and
                       source diversity metrics.
* ``skipped.jsonl`` — articles the pipeline dropped and the reason
                       (freshness filter, missing body, LLM schema
                       failure, batch error). Enables corpus-
                       completeness audits.

A shared ``data/corpus/manifest.json`` tracks per-day sha256 and
line counts for integrity checks and download indexing.

All writers are idempotent by url_hash / cluster_id so re-running the
pipeline (backfill, retries) is safe.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)

CORPUS_ROOT = Path("data/corpus")
MANIFEST_FILE = CORPUS_ROOT / "manifest.json"


def _day_dir(day: str) -> Path:
    d = CORPUS_ROOT / day
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")
    tmp.replace(path)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def append_body(
    day: str,
    url_hash: str,
    url: str,
    title: str,
    source_id: str,
    source_name: str,
    published: str | None,
    body_text: str,
    body_chars: int,
    extract_status: str = "ok",
) -> None:
    """Append (or update) one row in bodies.jsonl for the day.

    Idempotent by url_hash — a re-run with the same url_hash replaces
    the earlier row so bodies stay in sync with the latest extract.
    """
    path = _day_dir(day) / "bodies.jsonl"
    existing = _load_jsonl(path)
    kept = [r for r in existing if r.get("url_hash") != url_hash]
    kept.append(
        {
            "url_hash": url_hash,
            "url": url,
            "title": title,
            "source_id": source_id,
            "source_name": source_name,
            "published": published,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "body_chars": body_chars,
            "body_text": body_text,
            "extract_status": extract_status,
        }
    )
    _write_jsonl(path, kept)


def write_members(day: str, clusters: list[dict]) -> None:
    """Overwrite members.jsonl with every article in every cluster.

    Called from dedupe once per run; dedupe is deterministic per day so
    a full rewrite matches the run's clustering exactly.
    """
    path = _day_dir(day) / "members.jsonl"
    rows: list[dict] = []
    for cluster in clusters:
        cid = cluster.get("cluster_id", "")
        rep = cluster.get("representative", {})
        rep_url = rep.get("url", "")
        for m in cluster.get("members", []) or []:
            rows.append(
                {
                    "cluster_id": cid,
                    "url_hash": _url_hash(m.get("url", "")),
                    "is_representative": m.get("url", "") == rep_url,
                    "source_id": m.get("source_id", ""),
                    "source_name": m.get("source_name", ""),
                    "title": m.get("title", ""),
                    "url": m.get("url", ""),
                    "published": m.get("published"),
                }
            )
    _write_jsonl(path, rows)


def _load_skipped_keyed(path: Path) -> tuple[list[str], set[tuple[str, str]]]:
    """Return (existing raw lines, set of (url_hash, phase) keys). Used to
    make skipped.jsonl idempotent: re-running the same phase for the same
    URL on the same day updates the timestamp but does not create a new
    row, so a CI retry never inflates the audit count.
    """
    lines: list[str] = []
    keys: set[tuple[str, str]] = set()
    if not path.exists():
        return lines, keys
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        lines.append(line)
        keys.add((str(obj.get("url_hash", "")), str(obj.get("phase", ""))))
    return lines, keys


def _rewrite_skipped(path: Path, kept: list[str], additions: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for line in kept:
            f.write(line)
            f.write("\n")
        for row in additions:
            f.write(json.dumps(row, ensure_ascii=False))
            f.write("\n")


def append_skipped(
    day: str,
    url_hash: str,
    url: str,
    source_id: str,
    title: str,
    phase: str,
    reason: str,
) -> None:
    """Append one row to skipped.jsonl, idempotent by (url_hash, phase).

    A repeated call for the same URL + phase on the same day updates the
    row in place rather than duplicating it. Different phases for the same
    URL do accumulate (they are distinct audit events).
    """
    path = _day_dir(day) / "skipped.jsonl"
    existing_lines, existing_keys = _load_skipped_keyed(path)
    key = (url_hash, phase)
    if key in existing_keys:
        # Rewrite the matching line with the newer timestamp/reason.
        kept: list[str] = []
        for line in existing_lines:
            try:
                obj = json.loads(line)
            except Exception:
                kept.append(line)
                continue
            if (str(obj.get("url_hash", "")), str(obj.get("phase", ""))) == key:
                continue
            kept.append(line)
        row = {
            "logged_at": datetime.now(timezone.utc).isoformat(),
            "url_hash": url_hash, "url": url, "source_id": source_id,
            "title": title, "phase": phase, "reason": reason[:500],
        }
        _rewrite_skipped(path, kept, [row])
        return
    row = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "url_hash": url_hash, "url": url, "source_id": source_id,
        "title": title, "phase": phase, "reason": reason[:500],
    }
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False))
        f.write("\n")


def append_skipped_many(day: str, rows: list[dict]) -> None:
    """Batch variant for freshness filter, idempotent by (url_hash, phase).

    A same-day re-run of dedupe replaces the previous run's freshness-drop
    rows in place. Reason: dedupe is deterministic per day, so re-running
    it should not inflate the audit count.
    """
    if not rows:
        return
    path = _day_dir(day) / "skipped.jsonl"
    existing_lines, _ = _load_skipped_keyed(path)
    new_keys = {(r.get("url_hash", ""), r.get("phase", "")) for r in rows}
    kept: list[str] = []
    for line in existing_lines:
        try:
            obj = json.loads(line)
        except Exception:
            kept.append(line)
            continue
        if (str(obj.get("url_hash", "")), str(obj.get("phase", ""))) in new_keys:
            continue
        kept.append(line)
    now = datetime.now(timezone.utc).isoformat()
    stamped = [{"logged_at": now, **row} for row in rows]
    _rewrite_skipped(path, kept, stamped)


def update_manifest(day: str) -> None:
    """Recompute file sha256 + line counts for the given day.

    Reads existing manifest, updates only this day's entry, atomic write.
    """
    day_dir = CORPUS_ROOT / day
    if not day_dir.exists():
        return
    manifest: dict = {"schema_version": 1, "version": 1, "days": {}}
    if MANIFEST_FILE.exists():
        try:
            manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            manifest = {"version": 1, "days": {}}
    day_entry: dict = {}
    for name in ("bodies.jsonl", "members.jsonl", "skipped.jsonl"):
        path = day_dir / name
        if not path.exists():
            continue
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        day_entry[name] = {
            "sha256": _sha256_of(path),
            "lines": lines,
            "bytes": path.stat().st_size,
        }
    manifest.setdefault("days", {})[day] = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "files": day_entry,
    }
    MANIFEST_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_FILE.with_suffix(MANIFEST_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST_FILE)


def _url_hash(url: str) -> str:
    """Local copy of pipeline.state.url_hash to avoid circular import."""
    if not url:
        return ""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
