"""ENR-1 — tests for local body re-extraction (option d).

Hermetic: DATA is redirected to tmp_path, extract_article + corpus_writer
are stubbed, so no network / no real corpus writes. No lexicon content.
"""
import json

import pipeline.research.backfill_bodies as bb


def _write_articles(root, day, articles):
    d = root / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "articles.json").write_text(json.dumps(articles), encoding="utf-8")


def _write_bodies(root, day, rows):
    d = root / "corpus" / day
    d.mkdir(parents=True, exist_ok=True)
    (d / "bodies.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )


def test_backfill_day_fetches_missing_skips_present(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "DATA", tmp_path)
    day = "2026-06-01"
    _write_articles(tmp_path, day, [
        {"id": "aaa", "url": "http://x/a", "title_original": "A",
         "source_id": "s1", "source_name": "S1", "published": None},
        {"id": "bbb", "url": "http://x/b", "title_original": "B",
         "source_id": "s1", "source_name": "S1", "published": None},
    ])
    _write_bodies(tmp_path, day, [{"url_hash": "aaa", "body_text": "already here"}])

    monkeypatch.setattr(bb, "extract_article",
                        lambda url: {"body": "fresh body text", "image_url": ""})
    calls = []
    monkeypatch.setattr(bb.corpus_writer, "append_body",
                        lambda day, **kw: calls.append({"day": day, **kw}))
    monkeypatch.setattr(bb.corpus_writer, "update_manifest", lambda day: None)

    stats = bb.backfill_day(day)
    assert stats["already"] == 1     # aaa already has a body
    assert stats["fetched"] == 1     # bbb fetched
    assert stats["failed"] == 0
    assert len(calls) == 1
    # article id must be used as url_hash so en_corpus keeps the row
    assert calls[0]["url_hash"] == "bbb"
    assert calls[0]["body_text"] == "fresh body text"


def test_backfill_day_counts_extraction_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "DATA", tmp_path)
    day = "2026-06-02"
    _write_articles(tmp_path, day, [
        {"id": "raise", "url": "http://x/r", "title_original": "R", "source_id": "s"},
        {"id": "empty", "url": "http://x/e", "title_original": "E", "source_id": "s"},
    ])

    def _extract(url):
        if url.endswith("/r"):
            raise RuntimeError("boom")
        return {"body": "   ", "image_url": ""}  # empty after strip

    monkeypatch.setattr(bb, "extract_article", _extract)
    monkeypatch.setattr(bb.corpus_writer, "append_body", lambda *a, **kw: None)
    monkeypatch.setattr(bb.corpus_writer, "update_manifest", lambda *a, **kw: None)

    stats = bb.backfill_day(day)
    assert stats["fetched"] == 0
    assert stats["failed"] == 2


def test_existing_body_ids_ignores_empty_bodies(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "DATA", tmp_path)
    day = "2026-06-03"
    _write_bodies(tmp_path, day, [
        {"url_hash": "has", "body_text": "content"},
        {"url_hash": "blank", "body_text": "  "},
        {"url_hash": "missing"},
    ])
    assert bb.existing_body_ids(day) == {"has"}


def test_limit_caps_fetches(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "DATA", tmp_path)
    day = "2026-06-04"
    _write_articles(tmp_path, day, [
        {"id": f"id{i}", "url": f"http://x/{i}", "title_original": "T", "source_id": "s"}
        for i in range(5)
    ])
    monkeypatch.setattr(bb, "extract_article", lambda url: {"body": "b" * 50, "image_url": ""})
    monkeypatch.setattr(bb.corpus_writer, "append_body", lambda *a, **kw: None)
    monkeypatch.setattr(bb.corpus_writer, "update_manifest", lambda *a, **kw: None)
    stats = bb.backfill_day(day, limit=2)
    assert stats["fetched"] == 2
