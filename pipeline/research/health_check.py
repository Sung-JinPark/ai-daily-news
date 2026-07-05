"""AUTO-1 (2026-07-06): local research-run staleness guard.

The private research ledger (research.db / papers.db) grows ONLY from the
local ``run-research.bat`` (Windows Task Scheduler, KST 20:00) — never from
CI. If that job stops firing, the public site keeps updating while the
concept / paper ledgers silently freeze (this happened on 2026-07-04 and
was found by accident). This module detects that drift early.

It reads, all locally and offline:
  * public frontier day — newest ``data/<YYYY-MM-DD>/articles.json``
  * research.db latest ``concept_mentions.day``
  * papers.db ``meta.last_run`` timestamp
  * a heartbeat file stamped at the tail of every successful full run

``detect()`` is pure (takes values, returns a status dict) so it unit-tests
without a filesystem. ``main()`` wires the real values and exits non-zero
when stale, so a lightweight standalone check (or the scheduled wrapper)
can alert. ``--stamp-success`` writes the heartbeat and exits 0.

Design mirrors ``pipeline.audit_sources`` (detector + main + flags).

Usage:
  python -m pipeline.research.health_check           # report + exit code
  python -m pipeline.research.health_check --json
  python -m pipeline.research.health_check --stamp-success
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from pipeline.research.research_db import DB_FILE as RESEARCH_DB

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
PAPERS_DB = DATA_DIR / "papers_private" / "papers.db"
HEARTBEAT_FILE = DATA_DIR / "research_private" / "health" / "last_success.json"

# Nightly cadence is 24h; a ledger should sit at most 1 day behind the
# public frontier (the day's run may not have fired yet), and the last
# successful activity should be under ~30h old (>30h = a run was missed).
MAX_LAG_DAYS = 1
MAX_AGE_HOURS = 30.0

_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------- readers (real filesystem/DB) ----------

def public_frontier_day(data_root: Path = DATA_DIR) -> str | None:
    """Newest YYYY-MM-DD dir that actually holds an articles.json."""
    if not data_root.exists():
        return None
    days = [
        p.name for p in data_root.iterdir()
        if p.is_dir() and _DAY_RE.match(p.name) and (p / "articles.json").exists()
    ]
    return max(days) if days else None


def research_latest_day(db: Path = RESEARCH_DB) -> str | None:
    if not Path(db).exists():
        return None
    c = sqlite3.connect(db)
    try:
        row = c.execute("SELECT MAX(day) FROM concept_mentions").fetchone()
    except sqlite3.Error:
        return None
    finally:
        c.close()
    return row[0] if row and row[0] else None


def papers_last_run_at(db: Path = PAPERS_DB) -> str | None:
    if not Path(db).exists():
        return None
    c = sqlite3.connect(db)
    try:
        row = c.execute("SELECT value FROM meta WHERE key='last_run'").fetchone()
    except sqlite3.Error:
        return None
    finally:
        c.close()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0]).get("at")
    except Exception:  # noqa: BLE001
        return None


def read_heartbeat(path: Path = HEARTBEAT_FILE) -> str | None:
    if not Path(path).exists():
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("at")
    except Exception:  # noqa: BLE001
        return None


# ---------- pure detector ----------

def _age_hours(iso_at: str | None, now: datetime) -> float | None:
    if not iso_at:
        return None
    try:
        dt = datetime.fromisoformat(iso_at)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 3600.0


def detect(
    public_day: str | None,
    research_day: str | None,
    last_run_at: str | None,
    heartbeat_at: str | None,
    now: datetime,
    max_lag_days: int = MAX_LAG_DAYS,
    max_age_hours: float = MAX_AGE_HOURS,
) -> dict:
    """Pure staleness judgement. Returns a status dict; ``stale`` is True
    when the local research run has plausibly stopped keeping up."""
    reasons: list[str] = []

    ledger_lag_days: int | None = None
    if public_day and research_day:
        try:
            ledger_lag_days = (
                date.fromisoformat(public_day) - date.fromisoformat(research_day)
            ).days
        except ValueError:
            ledger_lag_days = None

    # Prefer the full-run heartbeat; fall back to papers.db last_run.
    ref_at = heartbeat_at or last_run_at
    age_hours = _age_hours(ref_at, now)

    if ledger_lag_days is not None and ledger_lag_days > max_lag_days:
        reasons.append(
            f"ledger {ledger_lag_days}d behind public frontier "
            f"({research_day} vs {public_day}, > {max_lag_days}d)"
        )
    if age_hours is not None and age_hours > max_age_hours:
        reasons.append(
            f"last research activity {age_hours:.1f}h ago (> {max_age_hours:.0f}h)"
        )
    if research_day is None:
        reasons.append("research.db has no mentions yet")
    if ref_at is None:
        reasons.append("no heartbeat / last_run timestamp")

    return {
        "stale": bool(reasons),
        "reasons": reasons,
        "public_day": public_day,
        "research_day": research_day,
        "ledger_lag_days": ledger_lag_days,
        "last_run_at": ref_at,
        "last_run_age_hours": round(age_hours, 1) if age_hours is not None else None,
        "checked_at": now.isoformat(timespec="seconds"),
        "thresholds": {"max_lag_days": max_lag_days, "max_age_hours": max_age_hours},
    }


def stamp_success(path: Path, now: datetime, status: dict | None = None) -> None:
    """Write the full-run heartbeat (private, gitignored)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "at": now.isoformat(timespec="seconds"),
        "research_day": (status or {}).get("research_day"),
        "public_day": (status or {}).get("public_day"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def gather() -> dict:
    """Read the real local values into a detect() status dict (now=UTC)."""
    return detect(
        public_day=public_frontier_day(),
        research_day=research_latest_day(),
        last_run_at=papers_last_run_at(),
        heartbeat_at=read_heartbeat(),
        now=datetime.now(timezone.utc),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="local research-run staleness guard")
    parser.add_argument("--json", action="store_true", help="emit the status dict as JSON")
    parser.add_argument("--stamp-success", action="store_true",
                        help="write the heartbeat and exit 0 (call at end of a successful run)")
    parser.add_argument("--max-lag-days", type=int, default=MAX_LAG_DAYS)
    parser.add_argument("--max-age-hours", type=float, default=MAX_AGE_HOURS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    now = datetime.now(timezone.utc)
    status = detect(
        public_day=public_frontier_day(),
        research_day=research_latest_day(),
        last_run_at=papers_last_run_at(),
        heartbeat_at=read_heartbeat(),
        now=now,
        max_lag_days=args.max_lag_days,
        max_age_hours=args.max_age_hours,
    )

    if args.stamp_success:
        stamp_success(HEARTBEAT_FILE, now, status)
        log.info("[health] heartbeat stamped: %s (research_day=%s)",
                 HEARTBEAT_FILE, status["research_day"])
        return 0

    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        state = "STALE" if status["stale"] else "OK"
        log.info("[health] %s — research_day=%s public_day=%s lag=%s last_run_age=%sh",
                 state, status["research_day"], status["public_day"],
                 status["ledger_lag_days"], status["last_run_age_hours"])
        for r in status["reasons"]:
            log.warning("[health]   - %s", r)

    return 1 if status["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
