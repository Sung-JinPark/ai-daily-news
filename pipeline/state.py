"""URL hash cache to avoid re-summarizing previously processed articles."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(".cache")
SEEN_FILE = CACHE_DIR / "seen.json"


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(seen: set[str]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen)), encoding="utf-8")
