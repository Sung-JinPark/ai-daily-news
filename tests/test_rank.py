from datetime import datetime, timedelta, timezone

from pipeline.rank import freshness_hours, score


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
