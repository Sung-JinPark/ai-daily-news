"""Build data/tags_index.json from last N days of articles.

Scans data/<YYYY-MM-DD>/articles.json across the recent window and emits:
{
  "updated_at": ISO timestamp,
  "window_days": N,
  "tags": {
    "<tag>": {
      "count": int,
      "article_ids": ["...", ...]   # newest first, capped
      "categories": ["model_research", "business", ...]  # observed
    }
  }
}
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
# Rolling window used for the tags index. 365d preserves annual trend
# signal (M2 of research-archive plan); older days simply drop out of
# scope but remain reachable via `data/YYYY-MM-DD/articles.json`.
DEFAULT_WINDOW = 365
MAX_IDS_PER_TAG = 500
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def list_days(window: int) -> list[str]:
    if not DATA_DIR.exists():
        return []
    days = sorted(
        (p.name for p in DATA_DIR.iterdir() if p.is_dir() and DATE_RE.match(p.name)),
        reverse=True,
    )
    return days[:window]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    days = list_days(args.window)
    log.info("aggregating tags across %d days", len(days))

    index: dict[str, dict] = {}
    for day in days:
        articles_file = DATA_DIR / day / "articles.json"
        if not articles_file.exists():
            continue
        try:
            articles = json.loads(articles_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.warning("skip %s: %s", day, exc)
            continue
        for a in articles:
            tags = a.get("tags") or []
            if not tags:
                continue
            aid = a.get("id")
            cat = a.get("category", "")
            for tag in tags:
                slot = index.setdefault(tag, {"count": 0, "article_ids": [], "categories": []})
                slot["count"] += 1
                if len(slot["article_ids"]) < MAX_IDS_PER_TAG:
                    slot["article_ids"].append(aid)
                if cat and cat not in slot["categories"]:
                    slot["categories"].append(cat)

    out = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.window,
        "tags": index,
    }
    (DATA_DIR / "tags_index.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log.info("wrote tags_index.json: %d unique tags", len(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
