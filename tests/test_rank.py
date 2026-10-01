import json
from datetime import datetime, timedelta, timezone

from pipeline.rank import freshness_hours, main, score


def test_freshness_recent_article_has_low_hours():
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    assert 1.5 <= freshness_hours(recent) <= 2.5


def test_score_prioritizes_high_importance():
    high = {"importance_score": 5, "published": datetime.now(timezone.utc).isoformat(), "cluster_size": 1}
    low = {"importance_score": 1, "published": datetime.now(timezone.utc).isoformat(), "cluster_size": 1}
    assert score(high) > score(low)


def test_score_prioritizes_larger_clusters_when_importance_equal():
    a = {"importance_score": 3, "published": datetime.now(timezone.utc).isoformat(), "cluster_size": 4}
    b = {"importance_score": 3, "published": datetime.now(timezone.utc).isoformat(), "cluster_size": 1}
    assert score(a) > score(b)


def test_main_writes_empty_highlights_when_articles_list_is_empty(monkeypatch, tmp_path):
    # AUD-030: summarize.py can legitimately write an empty articles.json
    # for "today" while its batch is still pending. digest.py requires
    # highlights.json to exist, so rank.py must still write one (empty)
    # instead of returning early without writing anything.
    monkeypatch.setattr("pipeline.rank.DATA_DIR", tmp_path)
    day_dir = tmp_path / "2026-09-30"
    day_dir.mkdir()
    (day_dir / "articles.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["rank.py", "--day", "2026-09-30"])
    assert main() == 0
    highlights = json.loads((day_dir / "highlights.json").read_text(encoding="utf-8"))
    assert highlights == []
