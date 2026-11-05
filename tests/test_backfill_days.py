from pipeline.backfill_days import new_items_for_day, one_member_cluster, split_by_published_day


def test_split_moves_only_articles_published_on_missing_days():
    articles = [
        {"id": "a", "published": "2026-11-03T10:00:00+00:00"},
        {"id": "b", "published": "2026-11-04T10:00:00+00:00"},
        {"id": "c", "published": None},
    ]
    kept, moved = split_by_published_day(articles, {"2026-11-03"})
    assert ([a["id"] for a in kept], [a["id"] for a in moved["2026-11-03"]]) == (["b", "c"], ["a"])


def test_new_items_drop_archived_urls_and_cross_listed_repeats():
    items = [{"url": "u1"}, {"url": "u2"}, {"url": "u2"}]
    assert new_items_for_day(items, {"u1"}) == [{"url": "u2"}]


def test_one_member_cluster_uses_id_outside_the_continuity_namespace():
    item = {"url": "https://example.com/x", "published": "2026-11-03T10:00:00+00:00"}
    assert one_member_cluster(item)["cluster_id"].startswith("b20261103-")
