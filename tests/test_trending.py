from pipeline.trending import strip_particles, tokenize


def test_strip_particles_collapses_inflections():
    assert strip_particles("성능을") == "성능"
    assert strip_particles("모델은") == "모델"
    assert strip_particles("데이터의") == "데이터"
    assert strip_particles("학습에서") == "학습"


def test_strip_particles_leaves_english_untouched():
    assert strip_particles("Google") == "Google"
    assert strip_particles("GPT-5") == "GPT-5"


def test_strip_particles_does_not_destroy_short_tokens():
    # "을" alone is shorter than 2, should be untouched (returned as-is)
    assert strip_particles("을") == "을"


def test_tokenize_drops_stopwords_and_strips_particles():
    text = "새로운 모델을 통해 성능이 향상되었습니다."
    tokens = tokenize(text)
    assert "모델" in tokens
    assert "성능" in tokens
    # stopwords
    assert "새로운" not in tokens
    assert "통해" not in tokens
