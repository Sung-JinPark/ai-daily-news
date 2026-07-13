"""R5 alert decision — emit-once + dedup logic (synthetic, no GitHub, no analysis)."""
from pipeline.research.r5_alert import decide


def test_emit_when_green_pending_not_issued():
    d = decide({"r5": {"status": "GREEN", "alert_pending": True, "alert_issued": False,
                       "first_ready_at": "2026-09-01T00:00:00Z"}})
    assert d["emit"] is True
    assert "R5" in d["title"] and "R5_cross_lingual.md" in d["body"]


def test_no_emit_when_already_issued():   # dedup
    d = decide({"r5": {"status": "GREEN", "alert_pending": True, "alert_issued": True}})
    assert d["emit"] is False


def test_no_emit_when_red():
    assert decide({"r5": {"status": "RED", "alert_pending": False}})["emit"] is False


def test_no_emit_when_missing_r5():
    assert decide({})["emit"] is False
    assert decide(None)["emit"] is False


def test_lifecycle_emit_then_dedup():
    # first ready -> emit; after alerter sets alert_issued -> no re-emit
    r5 = {"status": "GREEN", "alert_pending": True, "alert_issued": False,
          "first_ready_at": "2026-09-01T00:00:00Z"}
    assert decide({"r5": r5})["emit"] is True
    r5["alert_issued"] = True            # github-script sets this after creating the issue
    assert decide({"r5": r5})["emit"] is False


def test_alert_body_has_no_concept_vocab():
    # body must reference only the gate + file path — never a concept name
    d = decide({"r5": {"status": "GREEN", "alert_pending": True, "alert_issued": False}})
    assert "R5_cross_lingual" in d["body"] and "human" in d["body"].lower()


def test_r5_alert_imports_no_analysis_module():
    """★guard: the alerter must not import any analysis module."""
    import pipeline.research.r5_alert as m
    banned = ("h3_decide", "h3_formal", "changepoint", "paper_findings",
              "reannounce_preflight", "trend_model", "concept_lifecycle", "news_density_monitor")
    for line in open(m.__file__, encoding="utf-8"):
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            for b in banned:
                assert b not in line, f"r5_alert must not import: {b}"
