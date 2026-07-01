"""Sidecar meta for the aggregate JSONL streams (Y2).

The JSONL files under ``data/aggregates/`` are the primary shape the
external research SQLite export (M5) and the M4 ``build_db.py`` step
both consume. Each row is JSON, but the file as a whole has no natural
place to stamp a schema_version — line-level version fields would be
noise. Y2 solves it with a single ``data/aggregates/manifest.json``
sidecar that records per-file metadata:

    {
      "schema_version": 1,
      "generated_at": "…",
      "files": {
        "entity_mentions.jsonl": {
          "schema_version": 1,
          "sha256": "…",
          "lines": 2131,
          "bytes": 411203
        },
        …
      }
    }

The producer for each stream calls :func:`update_files` after
writing; :func:`load_manifest` and :func:`file_schema_version` are the
consumer helpers used by ``build_db`` and by any external downstream
that wants to check version compatibility.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

AGG_DIR = Path("data/aggregates")
MANIFEST_FILE = AGG_DIR / "manifest.json"

# Current schema_version for every aggregate stream. Bump the value
# below when the row-level fields change; consumers that watch the
# manifest will pick up the drift the next time they read it.
STREAM_SCHEMA_VERSIONS: dict[str, int] = {
    "entity_mentions.jsonl": 1,
    "tag_cooccurrence.jsonl": 1,
    "entity_cooccurrence.jsonl": 1,
    "source_health.jsonl": 1,
    "merge_events.jsonl": 1,
}


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load() -> dict:
    if not MANIFEST_FILE.exists():
        return {"schema_version": 1, "generated_at": "", "files": {}}
    try:
        return json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"schema_version": 1, "generated_at": "", "files": {}}


def _save(manifest: dict) -> None:
    AGG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_FILE.with_suffix(MANIFEST_FILE.suffix + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST_FILE)


def update_files(names: list[str]) -> dict:
    """Recompute the manifest entries for ``names`` (relative filenames
    under ``data/aggregates/``) and rewrite ``manifest.json`` atomically.
    Missing files are removed from the manifest so it never advertises
    a stream that no longer exists. Returns the updated manifest.
    """
    manifest = _load()
    manifest["schema_version"] = 1
    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    files = manifest.setdefault("files", {})
    for name in names:
        path = AGG_DIR / name
        if not path.exists():
            files.pop(name, None)
            continue
        lines = sum(1 for _ in path.open("r", encoding="utf-8"))
        files[name] = {
            "schema_version": STREAM_SCHEMA_VERSIONS.get(name, 1),
            "sha256": _sha256_of(path),
            "lines": lines,
            "bytes": path.stat().st_size,
        }
    _save(manifest)
    return manifest


def load_manifest() -> dict:
    """Read-only helper used by consumers."""
    return _load()


def file_schema_version(name: str) -> int | None:
    """Return the recorded schema_version for a JSONL file, or None
    when the file is not tracked. Used by build_db to compare against
    the loader's expected version."""
    files = _load().get("files", {}) or {}
    entry = files.get(name)
    if not entry:
        return None
    return int(entry.get("schema_version", 1))
