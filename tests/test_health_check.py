"""AUTO-1 — tests for the local research-run staleness guard.

Pure-unit: detect() takes values + an explicit `now`, so no filesystem or
clock. No lexicon content is involved (health_check reads only day-keys and
timestamps), so tests/ stays lexicon-clean by construction.
"""
from datetime import datetime, timezone

from pipeline.research import health_check as hc

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _detect(**kw):
    base = dict(
        public_day="2026-07-06",
        research_day="2026-07-06",
        last_run_at="2026-07-06T11:00:00+00:00",  # 1h ago
        heartbeat_at=None,
        now=NOW,
    )
    base.update(kw)
    return hc.detect(**base)


def test_healthy_run_is_not_stale():
    s = _detect()
    assert s["stale"] is False
    assert s["reasons"] == []
    assert s["ledger_lag_days"] == 0
    assert s["last_run_age_hours"] == 1.0


def test_stale_when_ledger_lags_beyond_threshold():
    s = _detect(research_day="2026-07-04")  # 2 days behind
    assert s["stale"] is True
    assert s["ledger_lag_days"] == 2
    assert any("behind public frontier" in r for r in s["reasons"])


def test_lag_at_threshold_is_ok():
    # lag exactly max_lag_days (1) is fine; run for today may not have fired
    s = _detect(research_day="2026-07-05")
    assert s["ledger_lag_days"] == 1
    assert s["stale"] is False


def test_stale_when_last_run_too_old():
    s = _detect(last_run_at="2026-07-04T20:00:00+00:00")  # 40h before NOW
    assert s["stale"] is True
    assert s["last_run_age_hours"] == 40.0
    assert any("last research activity" in r for r in s["reasons"])


def test_heartbeat_preferred_over_papers_last_run():
    # papers last_run is old, but a recent full-run heartbeat clears it
    s = _detect(last_run_at="2026-07-01T00:00:00+00:00",
                heartbeat_at="2026-07-06T10:00:00+00:00")
    assert s["last_run_age_hours"] == 2.0
    assert s["stale"] is False


def test_missing_research_day_is_stale():
    s = _detect(research_day=None)
    assert s["stale"] is True
    assert any("no mentions" in r for r in s["reasons"])


def test_missing_all_timestamps_is_stale():
    s = _detect(last_run_at=None, heartbeat_at=None)
    assert s["stale"] is True
    assert any("no heartbeat" in r for r in s["reasons"])


def test_naive_timestamp_treated_as_utc():
    # naive ISO (no tz) should not crash and is read as UTC
    assert hc._age_hours("2026-07-06T11:00:00", NOW) == 1.0


def test_stamp_and_read_heartbeat_roundtrip(tmp_path):
    hb = tmp_path / "health" / "last_success.json"
    status = _detect()
    hc.stamp_success(hb, NOW, status)
    assert hc.read_heartbeat(hb) == NOW.isoformat(timespec="seconds")
