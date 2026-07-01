"""Precompute per-article top-K cosine similarity (ZE2).

Runs after ``pipeline.embed`` on every daily CI. Loads every vector
in ``data/embeddings/*.jsonl.gz`` into a normalized numpy matrix,
does one dense (N × 1024) · (1024 × N) matmul to get a full
similarity matrix, and writes the top-K neighbors of every article
to ``data/similarity/similar.json``:

    {
      "schema_version": 1,
      "generated_at": "…",
      "model": "voyage-3",
      "top_k": 8,
      "similar": {
        "<article_id>": [
          {"article_id": "…", "score": 0.876},
          …
        ]
      }
    }

The output stays small enough for the site to load directly at
build time (~1KB per article × 30k articles ≈ 30MB gzipped in git
after a year of accumulation, well within budget).

Idempotent by design: the module always regenerates the file from
the current vector set, so a re-run cannot corrupt state — it just
reflects the latest vectors.
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
EMBED_DIR = DATA_DIR / "embeddings"
SIM_DIR = DATA_DIR / "similarity"
SIM_FILE = SIM_DIR / "similar.json"

DEFAULT_TOP_K = 8


def _load_vectors() -> tuple[list[str], list[list[float]]]:
    """Return (ids, vectors) in the same order. Vectors are the raw
    floats; caller normalizes for cosine similarity."""
    ids: list[str] = []
    vectors: list[list[float]] = []
    if not EMBED_DIR.exists():
        return ids, vectors
    seen: set[str] = set()
    for gz in sorted(EMBED_DIR.glob("*.jsonl.gz")):
        with gzip.open(gz, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                aid = obj.get("article_id")
                vec = obj.get("vector")
                if not aid or not isinstance(vec, list) or aid in seen:
                    continue
                seen.add(aid)
                ids.append(aid)
                vectors.append(vec)
    return ids, vectors


def _top_k_numpy(ids: list[str], vectors: list[list[float]], k: int) -> dict[str, list[dict]]:
    import numpy as np
    arr = np.asarray(vectors, dtype=np.float32)
    # Normalize rows.
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    arr = arr / norms
    # Cosine matrix.
    sims = arr @ arr.T  # (N, N)
    # Zero the self-similarity so it never wins the top-K slot.
    np.fill_diagonal(sims, -1.0)
    out: dict[str, list[dict]] = {}
    for i, aid in enumerate(ids):
        row = sims[i]
        if k >= len(row):
            idx = np.argsort(-row)
        else:
            # Faster partial sort — bring k largest to the front.
            part = np.argpartition(-row, k)[:k]
            idx = part[np.argsort(-row[part])]
        neighbors = []
        for j in idx[:k]:
            score = float(row[j])
            if score < 0:
                continue
            neighbors.append({"article_id": ids[int(j)], "score": round(score, 4)})
        out[aid] = neighbors
    return out


def _top_k_pure(ids: list[str], vectors: list[list[float]], k: int) -> dict[str, list[dict]]:
    """Pure-Python fallback. Only used if numpy is missing."""
    import math
    N = len(vectors)
    # Precompute norms.
    norms = []
    for v in vectors:
        s = 0.0
        for x in v:
            s += x * x
        norms.append(math.sqrt(s) or 1.0)
    out: dict[str, list[dict]] = {}
    for i in range(N):
        vi = vectors[i]
        ni = norms[i]
        scored: list[tuple[float, int]] = []
        for j in range(N):
            if j == i:
                continue
            vj = vectors[j]
            dot = 0.0
            for x, y in zip(vi, vj):
                dot += x * y
            score = dot / (ni * norms[j])
            scored.append((score, j))
        scored.sort(reverse=True)
        neighbors = [
            {"article_id": ids[j], "score": round(float(s), 4)}
            for s, j in scored[:k]
        ]
        out[ids[i]] = neighbors
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    ids, vectors = _load_vectors()
    log.info("similarity: %d vectors loaded", len(ids))
    if not ids:
        log.info("no vectors — writing empty index so /research and site loaders can degrade cleanly")
        SIM_DIR.mkdir(parents=True, exist_ok=True)
        SIM_FILE.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "model": "voyage-3",
                    "top_k": args.top_k,
                    "similar": {},
                },
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        return 0

    try:
        import numpy  # noqa: F401
        top_k = _top_k_numpy(ids, vectors, args.top_k)
    except ImportError:
        log.warning("numpy not installed; using slow pure-python path")
        top_k = _top_k_pure(ids, vectors, args.top_k)

    SIM_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": "voyage-3",
        "top_k": args.top_k,
        "n_articles": len(ids),
        "similar": top_k,
    }
    SIM_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("similarity: wrote top-%d for %d articles -> %s", args.top_k, len(ids), SIM_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
