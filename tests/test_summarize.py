import json
import types
from unittest.mock import MagicMock

from pipeline.summarize import call_haiku, process_cluster, validate


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


def _fake_response(payload: dict):
    block = types.SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))
    return types.SimpleNamespace(
        content=[block],
        usage=types.SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=80,
            cache_creation_input_tokens=0,
        ),
    )


def test_call_haiku_parses_text_and_usage():
    payload = {
        "summary_ko": "요약",
        "insights_ko": ["i1", "i2"],
        "category": "business",
        "importance_score": 3,
    }
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload)
    result = call_haiku(client, "T", "S", "body")
    assert result["parsed"] == payload
    assert result["usage"]["input_tokens"] == 100
    assert result["usage"]["cache_read_input_tokens"] == 80


def test_process_cluster_yields_article(monkeypatch):
    payload = {
        "summary_ko": "요약",
        "insights_ko": ["i1", "i2"],
        "category": "product",
        "importance_score": 4,
    }
    monkeypatch.setattr("pipeline.summarize.extract_body", lambda url: "본문")
    client = MagicMock()
    client.messages.create.return_value = _fake_response(payload)
    cluster = {
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
    article, usage = process_cluster(client, cluster)
    assert article is not None
    assert article["category"] == "product"
    assert article["cluster_size"] == 2
    assert article["also_covered_by"] == ["VentureBeat"]
    assert usage["input_tokens"] == 100
