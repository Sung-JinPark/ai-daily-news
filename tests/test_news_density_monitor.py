"""News-density monitor — pure aggregation logic (synthetic mentions, no DB/lexicon).

Synthetic concept ids only; asserts the weekly aggregation, gap-filling, dense-streak,
and readiness gate, and that outputs are aggregate-only (no concept-name fields).
"""
from pipeline.research.news_density_monitor import (
    compute_weekly,
    trailing_dense_streak,
    evaluate_readiness,
    update_r5_state,
    _week_key,
    GATES,
    R5_GATE,
)


def test_week_key_buckets_and_gaps():
    # 3 weeks apart -> key differs by 3
    assert _week_key("2025-07-22") - _week_key("2025-07-01") == 3


def test_compute_weekly_gapfilled_and_cooc():
    mentions = [
        ("2025-07-01", "a1", "zzA"), ("2025-07-01", "a1", "zzB"),  # a1 has 2 concepts
        ("2025-07-01", "a2", "zzA"),                                # a2 has 1
        ("2025-07-22", "a3", "zzA"), ("2025-07-22", "a3", "zzB"),
        ("2025-07-22", "a3", "zzC"),                                # a3 has 3 concepts
    ]
    s = compute_weekly(mentions)
    assert len(s) == 4                       # week0 + 2 gap weeks + week3
    assert s[0]["mentions"] == 3 and s[0]["cooc_articles"] == 1 and s[0]["active_concepts"] == 2
    assert s[1]["mentions"] == 0 and s[2]["mentions"] == 0          # gaps filled with zero
    assert s[3]["mentions"] == 3 and s[3]["cooc_articles"] == 1 and s[3]["active_concepts"] == 3
    # aggregate-only: no concept-name / id field leaks into the series
    assert set(s[0]) == {"week", "label", "mentions", "cooc_articles", "active_concepts"}


def test_compute_weekly_empty():
    assert compute_weekly([]) == []


def _weeks(mentions_list, cooc_list):
    return [{"week": i, "label": str(i), "mentions": m, "cooc_articles": c,
             "active_concepts": 0} for i, (m, c) in enumerate(zip(mentions_list, cooc_list))]


def test_trailing_dense_streak_breaks_on_gap():
    gate = {"dense_mentions_min": 150, "dense_cooc_min": 0}
    weeks = _weeks([200, 200, 5, 200, 200, 200], [30] * 6)   # a thin week breaks the streak
    assert trailing_dense_streak(weeks, gate) == 3


def test_trailing_dense_streak_cooc_criterion():
    gate = {"dense_mentions_min": 0, "dense_cooc_min": 25}
    weeks = _weeks([0] * 4, [30, 10, 30, 30])                # cooc<25 breaks
    assert trailing_dense_streak(weeks, gate) == 2


def test_evaluate_readiness_red_then_green():
    weeks = _weeks([200] * 3, [30] * 3)
    gates = {"g": {"desc": "test", "dense_mentions_min": 150, "dense_cooc_min": 0,
                   "weeks_required": 3}}
    r = evaluate_readiness(weeks, gates)
    assert r["g"]["status"] == "GREEN" and r["g"]["streak_weeks"] == 3
    gates["g"]["weeks_required"] = 26
    r2 = evaluate_readiness(weeks, gates)
    assert r2["g"]["status"] == "RED" and r2["g"]["weeks_remaining"] == 23


# ---------- R5 arm: gate + transition state + unattended-analysis guard ----------

def test_r5_gate_lower_than_others():
    assert R5_GATE in GATES and GATES[R5_GATE]["weeks_required"] == 12   # < 26
    weeks_ready = _weeks([200] * 12, [30] * 12)
    r = evaluate_readiness(weeks_ready, {R5_GATE: GATES[R5_GATE]})
    assert r[R5_GATE]["status"] == "GREEN"
    weeks_short = _weeks([200] * 11, [30] * 11)
    r2 = evaluate_readiness(weeks_short, {R5_GATE: GATES[R5_GATE]})
    assert r2[R5_GATE]["status"] == "RED" and r2[R5_GATE]["weeks_remaining"] == 1


def test_r5_state_first_green_arms_alert():
    s = update_r5_state(None, "GREEN", "2026-09-01T00:00:00Z")
    assert s["status"] == "GREEN"
    assert s["first_ready_at"] == "2026-09-01T00:00:00Z"
    assert s["alert_pending"] is True
    assert s["transitions"][-1]["to"] == "GREEN"


def test_r5_state_red_no_alert():
    s = update_r5_state(None, "RED", "2026-07-13T00:00:00Z")
    assert s["status"] == "RED" and s["first_ready_at"] is None
    assert s["alert_pending"] is False


def test_r5_state_green_persists_and_dedups():
    s1 = update_r5_state(None, "GREEN", "2026-09-01T00:00:00Z")
    s2 = update_r5_state(s1, "GREEN", "2026-09-08T00:00:00Z")
    assert s2["first_ready_at"] == "2026-09-01T00:00:00Z"          # immutable
    assert len(s2["transitions"]) == len(s1["transitions"])        # no dup transition
    assert s2["alert_pending"] is True                             # still pending
    s2["alert_issued"] = True                                      # alerter dedups
    s3 = update_r5_state(s2, "GREEN", "2026-09-15T00:00:00Z")
    assert s3["alert_pending"] is False


def test_monitor_never_imports_analysis_modules():
    """★Unattended-analysis guard: the monitor must not IMPORT any analysis module —
    it records state and alerts, nothing more. (Comments may name them to document the
    guard; only real import statements are prohibited.)"""
    import pipeline.research.news_density_monitor as m
    banned = ("h3_decide", "h3_formal", "h3_network", "changepoint", "paper_findings",
              "reannounce_preflight", "trend_model", "concept_lifecycle", "velocity_tv")
    for line in open(m.__file__, encoding="utf-8"):
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            for b in banned:
                assert b not in line, f"monitor must not import analysis module: {b}"
