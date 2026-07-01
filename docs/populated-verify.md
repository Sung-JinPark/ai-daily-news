# populated-state 검증 하니스

## 목적

F12 (`/themes`) · F13 (`/predictions`) · F14 (`/compare`) · M6 (`/reports`) 페이지는 empty-state만 검증되고 실제 데이터 렌더는 CI 첫 런 이후 확인되던 QA 공백을 메움.

## 실행

```
node scripts/verify-populated.mjs
```

## 동작

1. `fixtures/`의 합성 데이터를 `data/`에 오버레이 (기존 파일은 `.verify-backup/`에 백업)
2. `cd site && npm run build`
3. `site/dist/`의 특정 HTML 파일에 예상 문자열이 있는지 assertion
4. 성공/실패와 무관하게 `finally`에서 원본 복원 + 백업 폴더 삭제

## 어서션 목록

- `themes/index.html` — "테스트 서사", "이번 주 흐름"
- `predictions/index.html` — "테스트 예측", "확인됨", "반박됨"
- `compare/index.html` — "TestModel-GPT5-v1", "TestModel-Claude-v1"
- `reports/index.html` — "테스트 리포트 헤드라인"
- `reports/2026-Q2/index.html` — "커버리지", "부분 커버리지" (N1의 커버리지 배너 검증)

## 실패 시

- assertion 실패 메시지가 stderr에 출력됨
- exit code 1
- **원본 데이터는 이미 복원됨** — 재실행 시 아무 부작용 없음

## 주의

- CI 게이팅 대상이 아님 (로컬 QA 도구). CI 통합은 후속.
- `fixtures/*.json`의 스키마가 실제 파이프라인 출력과 일치해야 함 — 스키마 변경 시 fixtures도 업데이트 필수.
