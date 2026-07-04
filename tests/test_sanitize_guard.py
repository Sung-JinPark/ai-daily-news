"""SG-1 (2026-07-04) — regression tests for the public-stats sanitize guard.

The guard (`export_public_stats._sanitize_check`) aborts the export if any
research.db concept name leaks into the serialized public payload. SG-1
promoted its matcher from substring to ASCII word-boundary / non-ASCII
literal, so a short concept token no longer false-positives inside an
unrelated English word.

GOVERNANCE: tests/ is a PUBLIC surface. These tests inject only obviously
SYNTHETIC dummy concept names into a throwaway sqlite db — never a real
lexicon term (that would itself be the leak DBQ-3 guards against).
"""
import json
import sqlite3

import pytest

import pipeline.research.export_public_stats as eps


def _synthetic_db(tmp_path, concepts):
    """concepts: list of (concept_id, canonical_name). Returns the db Path."""
    db = tmp_path / "research.db"
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE concepts (concept_id TEXT, canonical_name TEXT, "
        "status TEXT, kind TEXT)"
    )
    c.executemany(
        "INSERT INTO concepts (concept_id, canonical_name, status, kind) "
        "VALUES (?, ?, 'active', 'method')",
        concepts,
    )
    c.commit()
    c.close()
    return db


def test_positive_ascii_standalone_word_aborts(tmp_path, monkeypatch):
    # A synthetic concept name appearing as a standalone word must abort.
    db = _synthetic_db(tmp_path, [("zzsynthetic_concept", "zzsynthetic concept")])
    monkeypatch.setattr(eps, "RESEARCH_DB", db)
    payload = json.dumps({"note": "the zzsynthetic concept surfaced here"})
    with pytest.raises(SystemExit):
        eps._sanitize_check(payload)


def test_false_positive_substring_no_longer_aborts(tmp_path, monkeypatch):
    # THE CORE SG-1 PROOF: token "cat" must NOT match inside "education" /
    # "vacation" now that matching is word-boundary aware.
    db = _synthetic_db(tmp_path, [("cat", "cat")])
    monkeypatch.setattr(eps, "RESEARCH_DB", db)
    payload = json.dumps({"x": "education and vacation statistics"})
    eps._sanitize_check(payload)  # must not raise


def test_generic_kind_taxonomy_is_safe(tmp_path, monkeypatch):
    # kind taxonomy words are NOT concept names; a payload exposing them
    # (as /stats legitimately does) must not trigger the guard.
    db = _synthetic_db(tmp_path, [("zzconcept", "zzconcept")])
    monkeypatch.setattr(eps, "RESEARCH_DB", db)
    payload = json.dumps(
        {"kind_distribution": {"method": 1, "architecture": 2, "task": 3, "paradigm": 4}}
    )
    eps._sanitize_check(payload)  # must not raise


def test_non_ascii_literal_fallback_still_aborts(tmp_path, monkeypatch):
    # Korean has no word boundaries; a synthetic Korean concept name
    # embedded as a substring must still be caught (literal fallback).
    db = _synthetic_db(tmp_path, [("zz_kr", "합성개념가짜")])
    monkeypatch.setattr(eps, "RESEARCH_DB", db)
    payload = json.dumps({"x": "이것은합성개념가짜입니다"}, ensure_ascii=False)
    with pytest.raises(SystemExit):
        eps._sanitize_check(payload)
