---
description: pytest 규칙 — 순수 함수 단위 테스트, 네트워크·LLM 호출 금지
paths:
  - "tests/**"
  - "pipeline/**"
---

# 테스트

- 러너는 **pytest** (`pyproject.toml`의 `[tool.pytest.ini_options]`: `testpaths=["tests"]`,
  `pythonpath=["."]`). 프로젝트 루트에서 `pytest`로 실행한다.
- 파일명 `tests/test_<module>.py`, 테스트 이름은 **동작을 서술하는 문장**으로:
  `test_score_prioritizes_larger_clusters_when_importance_equal`.
- import는 테스트 대상 함수만 콕 집어서: `from pipeline.rank import freshness_hours, score`.
- 순수 함수(스코어링, 중복 제거, 정규화, 파싱) 중심으로 테스트한다. 어서션은 `assert` 하나로 명확하게.

## 하지 말 것
- **네트워크 호출 금지** — 외부 HTTP·Anthropic·Voyage 호출을 테스트에서 하지 않는다.
  필요하면 `pytest-mock`의 `mocker.patch`로 경계를 막는다.
- **실데이터 의존 금지** — `data/YYYY-MM-DD/`의 실제 날짜 디렉터리를 읽지 않는다.
  고정 입력은 `fixtures/`를 쓰거나 테스트 안에서 dict로 만든다.
- **사설 코퍼스 금지** — `data/*_private/`, `bodies.jsonl`을 테스트가 건드리지 않는다.
  연구 쪽 테스트의 개념명은 **합성 이름**을 쓴다 (실제 렉시콘 항목 금지).

## 변경 시
- 파이프라인 스코어링·중복 제거·트렌딩 로직을 고치면 같은 커밋에 테스트를 추가/수정한다.
- 누출 방지 로직(`test_sanitize_guard.py` 등)은 회귀 방지선이다. 깨지면 우회하지 말고 원인을 고친다.
