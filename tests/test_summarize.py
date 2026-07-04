import json
import types

from pipeline.summarize import (
    MODEL,
    build_request,
    extract_bodies,
    parse_result,
    validate,
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
