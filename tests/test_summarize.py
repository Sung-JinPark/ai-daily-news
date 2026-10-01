import json
import types

from pipeline.summarize import (
    MODEL,
    build_request,
    clear_pending,
    defer_today,
    extract_bodies,
    finalize_batch,
    load_pending,
    parse_result,
    save_pending,
    validate,
    wait_for_batch,
)


def test_validate_rejects_unknown_category():
    bad = {
        "summary_ko": "요약",
        "insights_ko": ["i1", "i2"],
        "category": "unknown",
        "importance_score": 3,
    }
    assert validate(bad) is None


def test_validate_rejects_out_of_range_score():
    bad = {
        "summary_ko": "요약",
        "insights_ko": ["i1", "i2"],
        "category": "business",
        "importance_score": 9,
    }
    assert validate(bad) is None


def test_validate_accepts_valid_payload():
    good = {
        "summary_ko": "요약입니다.",
        "insights_ko": ["인사이트1", "인사이트2"],
        "category": "model_research",
        "importance_score": 4,
    }
    assert validate(good) == good


def _fake_batch_result(payload: dict, custom_id: str = "abc123"):
    """Mimic one item from client.messages.batches.results(): a succeeded
    batch entry whose message carries text content + usage."""
    block = types.SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))
    message = types.SimpleNamespace(
        content=[block],
        usage=types.SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=0,
        ),
    )
    inner = types.SimpleNamespace(type="succeeded", message=message)
    return types.SimpleNamespace(custom_id=custom_id, result=inner)


def test_parse_result_extracts_json_and_usage():
    payload = {
        "summary_ko": "요약",
        "insights_ko": ["i1", "i2"],
        "category": "business",
        "importance_score": 3,
    }
    parsed, usage = parse_result(_fake_batch_result(payload))
    assert parsed == payload
    assert usage["input_tokens"] == 100
    assert usage["cache_read_input_tokens"] == 80


def test_parse_result_returns_none_on_non_succeeded():
    failed = types.SimpleNamespace(
        custom_id="x", result=types.SimpleNamespace(type="errored")
    )
    parsed, usage = parse_result(failed)
    assert parsed is None
    assert usage["input_tokens"] == 0


def test_extract_bodies_builds_one_request_per_cluster(monkeypatch):
    # extract_article is the only network touchpoint; stub it. day=None so
    # nothing is persisted to data/corpus/.
    monkeypatch.setattr(
        "pipeline.summarize.extract_article",
        lambda url: {"body": "본문 내용입니다. " * 40, "image_url": "https://img.example/x.png"},
    )
    clusters = [
        {
            "cluster_id": "c0001-abcd",
            "representative": {
                "source_id": "techcrunch_ai",
                "source_name": "TechCrunch AI",
                "title": "Title",
                "url": "https://example.com/x",
                "published": "2026-06-01T00:00:00+00:00",
            },
            "members": [
                {"source_name": "TechCrunch AI", "url": "https://example.com/x"},
                {"source_name": "VentureBeat", "url": "https://example.com/y"},
            ],
        }
    ]
    requests_list, cluster_meta = extract_bodies(clusters)
    assert len(requests_list) == 1
    req = requests_list[0]
    assert req["params"]["model"] == MODEL
    assert req["params"]["system"][0]["cache_control"]["type"] == "ephemeral"
    cid = req["custom_id"]
    assert cid in cluster_meta
    assert cluster_meta[cid]["cluster"]["cluster_id"] == "c0001-abcd"
    assert cluster_meta[cid]["image_url"] == "https://img.example/x.png"


def test_build_request_shape():
    req = build_request("cid1", "A Title", "TechCrunch AI", "some body")
    assert req["custom_id"] == "cid1"
    assert req["params"]["model"] == MODEL
    assert req["params"]["messages"][0]["role"] == "user"


def test_pending_batch_roundtrip(monkeypatch, tmp_path):
    # AUD-030 resume mechanism: a timed-out run persists batch_id +
    # cluster_meta so the next run can recover it without resubmitting.
    monkeypatch.setattr("pipeline.summarize.PENDING_FILE", tmp_path / "pending_batch.json")
    assert load_pending() is None
    cluster_meta = {"cid1": {"cluster": {"cluster_id": "c1"}, "image_url": None}}
    stats = {"calls": 1, "succeeded": 0, "schema_failed": 0, "errors": 0,
              "skipped_no_body": 0,
              "usage": {"input_tokens": 0, "output_tokens": 0,
                        "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}
    save_pending("2026-09-30", "msgbatch_abc", cluster_meta, stats)
    pending = load_pending()
    assert pending["day"] == "2026-09-30"
    assert pending["batch_id"] == "msgbatch_abc"
    assert pending["cluster_meta"] == cluster_meta
    clear_pending()
    assert load_pending() is None


def test_wait_for_batch_raises_timeout_instead_of_hanging(monkeypatch):
    # A zero-length budget means the deadline is already past on first
    # check, so this returns fast instead of actually polling.
    monkeypatch.setattr("pipeline.summarize.BATCH_TIMEOUT_MIN", 0)
    fake_client = types.SimpleNamespace(
        messages=types.SimpleNamespace(
            batches=types.SimpleNamespace(
                retrieve=lambda batch_id: types.SimpleNamespace(processing_status="in_progress")
            )
        )
    )
    try:
        wait_for_batch(fake_client, "msgbatch_abc")
        assert False, "expected TimeoutError"
    except TimeoutError as exc:
        assert "msgbatch_abc" in str(exc)


def test_finalize_batch_merges_results_into_articles_json(monkeypatch, tmp_path):
    monkeypatch.setattr("pipeline.summarize.DATA_DIR", tmp_path)
    monkeypatch.setattr("pipeline.summarize.corpus_writer.update_manifest", lambda day: None)
    cluster = {
        "cluster_id": "c0001-abcd",
        "representative": {
            "source_id": "techcrunch_ai",
            "source_name": "TechCrunch AI",
            "title": "Title",
            "url": "https://example.com/x",
            "published": "2026-06-01T00:00:00+00:00",
        },
        "members": [{"source_name": "TechCrunch AI", "url": "https://example.com/x"}],
    }
    cluster_meta = {"cid1": {"cluster": cluster, "image_url": "https://img.example/x.png"}}
    payload = {
        "summary_ko": "요약",
        "insights_ko": ["i1", "i2"],
        "category": "business",
        "importance_score": 3,
    }
    fake_result = _fake_batch_result(payload, custom_id="cid1")
    fake_client = types.SimpleNamespace(
        messages=types.SimpleNamespace(
            batches=types.SimpleNamespace(results=lambda batch_id: [fake_result])
        )
    )
    seen: set[str] = set()
    stats = finalize_batch(fake_client, "2026-09-30", "msgbatch_abc", cluster_meta, seen)
    assert stats["succeeded"] == 1
    assert "cid1" in seen
    articles = json.loads((tmp_path / "2026-09-30" / "articles.json").read_text(encoding="utf-8"))
    assert len(articles) == 1
    assert articles[0]["url"] == "https://example.com/x"
    assert articles[0]["summary_ko"] == "요약"


def test_defer_today_writes_valid_empty_articles_file(monkeypatch, tmp_path):
    # AUD-030: when a pending batch hard-gates a run (or today's own batch
    # times out), rank.py/digest.py still require articles.json to *exist*
    # for the day, just with no new content claimed.
    monkeypatch.setattr("pipeline.summarize.DATA_DIR", tmp_path)
    monkeypatch.setattr("pipeline.state.CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr("pipeline.state.SEEN_FILE", tmp_path / ".cache" / "seen.json")
    seen: set[str] = set()
    result = defer_today("2026-10-01", seen)
    assert result == 0
    articles = json.loads((tmp_path / "2026-10-01" / "articles.json").read_text(encoding="utf-8"))
    assert articles == []
    stats = json.loads((tmp_path / "2026-10-01" / "_stats.json").read_text(encoding="utf-8"))
    assert stats["calls"] == 0
