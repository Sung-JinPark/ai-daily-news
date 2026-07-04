import pipeline.dedupe as dd
from pipeline.dedupe import cluster, normalize, title_hash


def test_normalize_strips_symbols_and_lowercases():
    assert normalize("OpenAI's GPT-5: A New Era!") == "openai s gpt 5 a new era"


def test_simhash_close_titles_below_threshold():
    from pipeline.dedupe import HAMMING_THRESHOLD
    a = title_hash("OpenAI launches GPT-5 with new reasoning")
    b = title_hash("OpenAI Launches GPT 5, New Reasoning Mode")
    assert a.distance(b) <= HAMMING_THRESHOLD


def test_simhash_unrelated_titles_above_threshold():
    from pipeline.dedupe import HAMMING_THRESHOLD
    a = title_hash("OpenAI launches GPT-5 with new reasoning")
    b = title_hash("EU passes new AI act amendment")
    assert a.distance(b) > HAMMING_THRESHOLD


def test_cluster_groups_near_duplicates_and_picks_high_trust_rep(monkeypatch):
    # cluster() now takes a day_str and persists continuity + merge events.
    # Stub the persistence so the unit test stays hermetic (no data/ writes).
    # fresh (first-run) continuity shape, matching load_continuity's default
    monkeypatch.setattr(
        dd, "load_continuity",
        lambda: {"schema_version": 1, "version": 1, "next_id": 0, "entries": []},
    )
    monkeypatch.setattr(dd, "save_continuity", lambda c: None)
    monkeypatch.setattr(dd, "_write_merge_events", lambda day, events: None)

    articles = [
        {
            "source_id": "techcrunch_ai",
            "source_name": "TechCrunch AI",
            "title": "OpenAI launches GPT-5 with new reasoning",
            "url": "https://tc.example/a",
            "published": "2026-06-01T10:00:00+00:00",
        },
        {
            "source_id": "openai_news",
            "source_name": "OpenAI",
            "title": "OpenAI Launches GPT 5, New Reasoning Mode",
            "url": "https://openai.example/b",
            "published": "2026-06-01T09:30:00+00:00",
        },
        {
            "source_id": "venturebeat",
            "source_name": "VentureBeat",
            "title": "EU passes new AI act amendment",
            "url": "https://vb.example/c",
            "published": "2026-06-01T08:00:00+00:00",
        },
    ]
    trust = {"techcrunch_ai": 4, "openai_news": 5, "venturebeat": 3}
    clusters = cluster(articles, trust, "2026-06-01")
    assert len(clusters) == 2
    gpt_cluster = next(c for c in clusters if len(c["members"]) == 2)
    # openai_news has the highest trust (5) -> representative
    assert gpt_cluster["representative"]["source_id"] == "openai_news"
