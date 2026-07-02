"""Shared arXiv reference extraction + per-day persistence.

Two consumers share the exact same conservative patterns so CI and the
local paper pipeline can never drift:

  * ``pipeline.collect`` (public, CI) calls :func:`write_refs_file`
    right after fetching sources, persisting the (article -> arXiv id)
    candidates it saw in RSS titles/summaries to
    ``data/<day>/arxiv_refs.json``. That file is committed by the
    daily workflow's ``git add data/`` step, so reference candidates
    survive even though the raw/ tree itself is transient.
  * ``pipeline.collect_papers`` (local, private DB) consumes the refs
    file to create ``mention_kind='reference'`` rows, falling back to
    a live raw/ scan only for pre-persistence days.

Boundary note (research/README.md contract): the refs file carries
only ids and positions — (article_id, arxiv_id, where, source_id) —
never article text, so nothing beyond the already-public mapping is
republished.

Coverage: full-source coverage begins **2026-07-02** (the first CI run
after this module landed). Earlier days are covered only where a local
raw/ tree existed (2026-06-04, 2026-06-17 — backfilled to refs files
in the same commit); other historical days are honestly unrecoverable
because their raw/ trees were CI-transient.

Extraction rules (deliberately conservative — false positives poison
the paper corpus):
  * URL form: ``arxiv.org/{abs,pdf}/<id>`` (legacy ``cs/0501001`` ids
    included, ``vN`` suffixes stripped)
  * Text form: literal ``arXiv:<id>`` prefix only
  * Bare numeric ids (e.g. ``2606.23662`` alone) are NEVER matched —
    they collide with dates and amounts.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pipeline.state import url_hash

DATA_DIR = Path("data")
SCHEMA_VERSION = 1
# First day every source is covered by CI-persisted refs files.
REFS_COVERAGE_START = "2026-07-02"

# Modern arXiv ids: YYMM.NNNNN(vN). Old ids: archive/YYMMNNN
ARXIV_URL_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(?P<id>[a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)
ARXIV_TEXT_RE = re.compile(
    r"arXiv:\s?(?P<id>[a-z\-]+(?:\.[A-Z]{2})?/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
    re.IGNORECASE,
)
ARXIV_VERSION_RE = re.compile(r"v\d+$", re.IGNORECASE)


def parse_arxiv_id(url: str) -> str | None:
    """Return the version-stripped base arxiv id from a URL, or None.

    Handles both modern ('2606.30626v1' -> '2606.30626') and legacy
    ('cs/0501001v2' -> 'cs/0501001') ids.
    """
    if not url:
        return None
    m = ARXIV_URL_RE.search(url)
    if not m:
        return None
    return ARXIV_VERSION_RE.sub("", m.group("id"))


def extract_arxiv_refs(text: str) -> set[str]:
    """All base arxiv ids found in ``text`` via the two conservative
    patterns. Kept for callers that don't care where a ref was found."""
    ids: set[str] = set()
    for m in ARXIV_URL_RE.finditer(text):
        ids.add(ARXIV_VERSION_RE.sub("", m.group("id")))
    for m in ARXIV_TEXT_RE.finditer(text):
        ids.add(ARXIV_VERSION_RE.sub("", m.group("id")))
    return ids


def _extract_with_where(text: str) -> dict[str, str]:
    """arxiv_id -> 'url' | 'text'. URL hits win when both match."""
    found: dict[str, str] = {}
    for m in ARXIV_TEXT_RE.finditer(text):
        found[ARXIV_VERSION_RE.sub("", m.group("id"))] = "text"
    for m in ARXIV_URL_RE.finditer(text):
        found[ARXIV_VERSION_RE.sub("", m.group("id"))] = "url"
    return found


def extract_refs_from_items(items: list[dict]) -> list[dict]:
    """Reference candidates from raw feed items.

    Skips items whose own url is arXiv (those are primary mentions,
    handled by the url-based path in collect_papers). Scans title +
    summary. Returns rows ``{article_id, arxiv_id, where, source_id}``
    sorted (article_id, arxiv_id) so output is deterministic.
    """
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        item_url = item.get("url") or ""
        if not item_url or "arxiv.org" in item_url:
            continue
        blob = f"{item.get('title') or ''} {item.get('summary') or ''}"
        found = _extract_with_where(blob)
        if not found:
            continue
        article_id = url_hash(item_url)
        for aid, where in found.items():
            key = (article_id, aid)
            if key in seen:
                continue
            seen.add(key)
            rows.append({
                "article_id": article_id,
                "arxiv_id": aid,
                "where": where,
                "source_id": item.get("source_id") or "",
            })
    rows.sort(key=lambda r: (r["article_id"], r["arxiv_id"]))
    return rows


def refs_file_path(day: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / day / "arxiv_refs.json"


def write_refs_file(day: str, items: list[dict], data_dir: Path = DATA_DIR) -> Path:
    """Persist the day's reference candidates.

    Emits the file even when zero refs were found — an empty ``refs``
    list means "extraction ran, nothing referenced", which downstream
    must distinguish from "extraction never ran" (file absent).
    """
    payload = {
        "schema_version": SCHEMA_VERSION,
        "day": day,
        "refs": extract_refs_from_items(items),
    }
    out = refs_file_path(day, data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return out


def load_refs_file(day: str, data_dir: Path = DATA_DIR) -> dict | None:
    """Return the parsed refs payload for ``day``, or None when the
    file is absent (pre-coverage day) or unparseable."""
    path = refs_file_path(day, data_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(payload.get("refs"), list):
        return None
    return payload
