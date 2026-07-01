"""Write data/latest.json pointing to the newest day with articles."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pipeline.summarize import DATA_DIR

log = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    days = sorted(
        [p.name for p in DATA_DIR.iterdir() if p.is_dir() and (p / "articles.json").exists()],
        reverse=True,
    )
    if not days:
        log.warning("no day directories found")
        return 0
    latest = days[0]
    # Volume floor — flag days that came in below a research-usable
    # threshold so the site can render a small notice.
    LOW_VOLUME_FLOOR = 25
    latest_count = 0
    try:
        latest_articles = json.loads((DATA_DIR / latest / "articles.json").read_text(encoding="utf-8"))
        latest_count = len(latest_articles)
    except Exception:  # noqa: BLE001
        latest_count = 0
    payload = {
        "latest_day": latest,
        "latest_count": latest_count,
        "low_volume": latest_count < LOW_VOLUME_FLOOR,
        "low_volume_floor": LOW_VOLUME_FLOOR,
        "all_days": days,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA_DIR / "latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("latest.json -> %s (%d days, latest_count=%d, low_volume=%s)",
             latest, len(days), latest_count, payload["low_volume"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
