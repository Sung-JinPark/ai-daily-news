"""One-shot: walk existing data/<day>/articles.json and add image_url.

No LLM calls. Re-fetches each article URL to extract og:image. Skips entries
that already have image_url.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline.extract import _extract_og_image
from pipeline.utils.http import fetch, get_client

DATA_DIR = Path("data")
log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    days = sorted([p for p in DATA_DIR.iterdir() if p.is_dir()])
    total_updated = 0
    with get_client() as client:
        for day_dir in days:
            articles_file = day_dir / "articles.json"
            if not articles_file.exists():
                continue
            articles = json.loads(articles_file.read_text(encoding="utf-8"))
            updated = 0
            for art in articles:
                if art.get("image_url"):
                    continue
                try:
                    resp = fetch(art["url"], client=client)
                    if resp.status_code >= 400:
                        art["image_url"] = None
                        continue
                    img = _extract_og_image(resp.text, art["url"])
                    art["image_url"] = img or None
                    if img:
                        updated += 1
                except Exception as exc:  # noqa: BLE001
                    log.warning("backfill failed for %s: %s", art["url"], exc)
                    art["image_url"] = None
            if updated:
                articles_file.write_text(
                    json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                log.info("%s: updated %d / %d articles", day_dir.name, updated, len(articles))
                total_updated += updated
    log.info("backfill done: %d images added across all days", total_updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
