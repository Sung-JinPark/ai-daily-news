import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/audit-weekly.yml").read_text(encoding="utf-8")


def test_every_issue_label_is_created_before_use():
    """gh issue create는 없는 라벨이면 실패한다 (2026-09-07~11-02 감사 9주 연속 실패)."""
    used = set(re.findall(r'gh issue create .*--label "([^"]+)"', WORKFLOW))
    created = set(re.findall(r"gh label create (\S+)", WORKFLOW))
    assert used and used <= created


def test_issue_dedupe_does_not_search_by_title():
    """제목에 건수가 들어가므로 제목 검색은 건수가 바뀔 때마다 중복 이슈를 만든다."""
    assert "in:title" not in WORKFLOW


def test_ci_health_scan_runs_after_an_earlier_step_fails():
    """앞 이슈 단계 실패로 CI 헬스 스캔이 9주간 건너뛰어졌다."""
    step = WORKFLOW.split("- name: CI health scan", 1)[1].split("run: |", 1)[0]
    assert "!cancelled()" in step
