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
    payload = {
        "latest_day": latest,
        "all_days": days,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (DATA_DIR / "latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("latest.json -> %s (%d days)", latest, len(days))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
