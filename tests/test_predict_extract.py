"""Pass B (resolve) 비용 가드 테스트 — COST-1.

쿨다운·만료·런당 상한은 resolve 루프가 레지스트리 전체를 매 런 재전송하던
비용 사고(2026-10-28, 월 ~$45)의 회귀 방지선이다.
"""
from datetime import date

from pipeline.predict_extract import (
    EXPIRE_DAYS,
    RESOLVE_MAX_PREDS_PER_RUN,
    RESOLVE_REVIEW_COOLDOWN_DAYS,
    _expire_old,
    _pending_needs_resolution,
    _select_for_review,
)

TODAY = date(2026, 10, 28)


def _pred(**overrides) -> dict:
    base = {
        "id": "p-test",
        "status": "pending",
        "day_made": "2026-08-01",  # 60일 초과 → stale
        "horizon": "unspecified",
    }
    base.update(overrides)
    return base


def test_stale_pending_prediction_needs_resolution_after_60_days():
    assert _pending_needs_resolution(_pred(), TODAY)


def test_recently_reviewed_prediction_is_skipped_during_cooldown():
    reviewed = _pred(last_reviewed="2026-10-26")  # 2일 전 검토
    assert not _pending_needs_resolution(reviewed, TODAY)


def test_review_reenables_after_cooldown_elapses():
    old_review = (TODAY.toordinal() - RESOLVE_REVIEW_COOLDOWN_DAYS)
    reviewed = _pred(last_reviewed=date.fromordinal(old_review).isoformat())
    assert _pending_needs_resolution(reviewed, TODAY)


def test_passed_horizon_triggers_review_even_when_recent():
    fresh = _pred(day_made="2026-10-20", horizon="2026-09")
    assert _pending_needs_resolution(fresh, TODAY)


def test_resolved_prediction_is_never_selected():
    assert not _pending_needs_resolution(_pred(status="confirmed"), TODAY)
    assert not _pending_needs_resolution(_pred(status="expired"), TODAY)


def test_expire_old_marks_only_pending_past_expire_days():
    preds = [
        _pred(id="ancient", day_made="2026-03-01"),
        _pred(id="recent", day_made="2026-09-01"),
        _pred(id="resolved", day_made="2026-03-01", status="confirmed"),
    ]
    n = _expire_old(preds, TODAY)
    assert n == 1
    assert preds[0]["status"] == "expired"
    assert preds[1]["status"] == "pending"
    assert preds[2]["status"] == "confirmed"


def test_expire_threshold_matches_constant():
    boundary = date.fromordinal(TODAY.toordinal() - EXPIRE_DAYS).isoformat()
    preds = [_pred(day_made=boundary)]
    assert _expire_old(preds, TODAY) == 1


def test_select_for_review_caps_batch_size_per_run():
    preds = [_pred(id=f"p{i}") for i in range(RESOLVE_MAX_PREDS_PER_RUN + 50)]
    selected = _select_for_review(preds, TODAY)
    assert len(selected) == RESOLVE_MAX_PREDS_PER_RUN


def test_select_for_review_prefers_never_and_oldest_reviewed_first():
    never = _pred(id="never")
    old = _pred(id="old", last_reviewed="2026-09-01")
    newer = _pred(id="newer", last_reviewed="2026-10-01")
    selected = _select_for_review([newer, never, old], TODAY)
    assert [p["id"] for p in selected] == ["never", "old", "newer"]
