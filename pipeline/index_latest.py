"""Write data/latest.json pointing to the newest day with articles."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

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

    def article_count(day: str) -> int:
        try:
            return len(json.loads((DATA_DIR / day / "articles.json").read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return 0

    # AUD-030: summarize.py can write an empty articles.json for "today"
    # while its batch is still pending on Anthropic's side. Skip empty days
    # when picking latest_day so the homepage keeps showing yesterday's real
    # content instead of "today, 0 articles". Fall back to days[0] only if
    # every day on record is empty.
    latest = next((d for d in days if article_count(d) > 0), days[0])
    # Volume floor — flag days that came in below a research-usable
    # threshold so the site can render a small notice. Weekends have
    # a lower floor: many labs and outlets don't publish Sat/Sun, so
    # the same "quiet day" trigger would fire every week without a
    # seasonal adjustment. Weekday floor is unchanged from the
    # original tuning (25 articles).
    WEEKDAY_LOW_VOLUME_FLOOR = 25
    WEEKEND_LOW_VOLUME_FLOOR = 15
    latest_count = article_count(latest)
    # AUDIT-1 AUD-011: the banner's audience is KST readers, but data
    # day keys are UTC — deriving the weekday from the day string made
    # KST Saturday mornings (data = Friday UTC) use the weekday floor.
    # Decide by the KST calendar at generation time instead: the index
    # runs right after collect, so "now KST" is the day readers see.
    from datetime import timedelta
    KST = timezone(timedelta(hours=9))
    weekday = datetime.now(KST).weekday()  # 0=Mon, 6=Sun (KST)
    is_weekend = weekday >= 5
    low_volume_floor = WEEKEND_LOW_VOLUME_FLOOR if is_weekend else WEEKDAY_LOW_VOLUME_FLOOR
    payload = {
        "latest_day": latest,
        "latest_count": latest_count,
        "low_volume": latest_count < low_volume_floor,
        "low_volume_floor": low_volume_floor,
        "low_volume_floor_weekday": WEEKDAY_LOW_VOLUME_FLOOR,
        "low_volume_floor_weekend": WEEKEND_LOW_VOLUME_FLOOR,
        "is_weekend_day": is_weekend,
        "all_days": days,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Atomic (AUDIT-1 AUD-006): latest.json is read by nearly every page
    # of the site build — never leave a torn file.
    from pipeline.utils.atomic import write_text_atomic
    write_text_atomic(DATA_DIR / "latest.json", json.dumps(payload, indent=2))
    log.info("latest.json -> %s (%d days, latest_count=%d, low_volume=%s)",
             latest, len(days), latest_count, payload["low_volume"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
