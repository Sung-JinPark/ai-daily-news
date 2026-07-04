"""trending.py switched to English-only tokenization (Korean particle
stripping was removed). These tests exercise the current tokenizer."""
from pipeline.trending import tokenize


def test_tokenize_keeps_english_terms_over_two_chars():
    tokens = tokenize("Transformer architecture improves reasoning benchmarks")
    assert "transformer" in tokens
    assert "architecture" in tokens
    assert "reasoning" in tokens
    assert "benchmarks" in tokens


def test_tokenize_drops_stopwords_and_generic_fillers():
    tokens = tokenize("The new AI model shows strong results")
    # stopwords / generic fillers removed
    assert "the" not in tokens
    assert "new" not in tokens
    assert "ai" not in tokens
    assert "model" not in tokens
    assert "shows" not in tokens
    # content words kept
    assert "strong" in tokens
    assert "results" in tokens


def test_tokenize_is_english_only_and_lowercases_hyphenated():
    tokens = tokenize("한국어 텍스트 GPT-5 Mixture-of-Experts")
    # Korean tokens are dropped (no ASCII-letter start); hyphenated names survive
    assert tokens == ["gpt-5", "mixture-of-experts"]


def test_tokenize_short_and_numeric_only_tokens_dropped():
    # a bare "AI" is 2 chars (below min 3) and also a filler; digits alone never match
    tokens = tokenize("AI is 42 ok GPU")
    assert "ai" not in tokens
    assert "42" not in tokens
    assert "gpu" in tokens
