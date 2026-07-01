"""Detect sources that have gone silent (0 items for N consecutive days).

Reads ``data/aggregates/source_health.jsonl`` (produced by
``pipeline.collect``) and emits an anomaly record + prepared GitHub
issue body for the audit-weekly workflow (X4). Sources first observed
in the last ``GRACE_DAYS`` days are excluded so a freshly-added feed
that hasn't hit the CI yet doesn't get flagged as dead.

Design mirrors ``pipeline.audit_cluster_merge`` so the audit-weekly
workflow can reuse the same gh-issue create-or-comment plumbing.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
HEALTH_FILE = DATA_DIR / "aggregates" / "source_health.jsonl"

ZERO_STREAK_DAYS = 3     # 3 consecutive days at 0 items
GRACE_DAYS = 3           # first observation must be older than this to alert


def _load_events() -> list[dict]:
    if not HEALTH_FILE.exists():
        return []
    out: list[dict] = []
    for line in HEALTH_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def detect_dead_sources() -> list[dict]:
    """Return one record per source whose most recent ZERO_STREAK_DAYS
    days are all present in source_health.jsonl AND all reported 0
    items. Sources with fewer than ZERO_STREAK_DAYS observations, or
    first observed within GRACE_DAYS, are excluded.
    """
    events = _load_events()
    if not events:
        return []
    by_source: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        sid = e.get("source_id", "")
        if not sid:
            continue
        by_source[sid].append(e)

    today = date.today()
    anomalies: list[dict] = []
    for sid, rows in by_source.items():
        rows.sort(key=lambda r: r.get("day", ""))
        if not rows:
            continue
        # Grace: don't alert on sources first seen very recently.
        try:
            first_seen = date.fromisoformat(rows[0].get("day", ""))
        except (ValueError, TypeError):
            continue
        if (today - first_seen).days < GRACE_DAYS:
            continue
        # Pick the tail — most recent ZERO_STREAK_DAYS distinct days.
        recent = rows[-ZERO_STREAK_DAYS:]
        if len(recent) < ZERO_STREAK_DAYS:
            continue
        if all((r.get("items", 0) or 0) == 0 for r in recent):
            errors = [r.get("error", "") for r in recent if r.get("error")]
            anomalies.append({
                "source_id": sid,
                "streak_days": ZERO_STREAK_DAYS,
                "days": [r.get("day", "") for r in recent],
                "sample_errors": errors[:3],
                "first_seen": first_seen.isoformat(),
            })
    return anomalies


def _format_issue(anomalies: list[dict]) -> tuple[str, str]:
    title = f"[audit] {len(anomalies)}개 소스가 {ZERO_STREAK_DAYS}일 연속 0건"
    lines: list[str] = []
    lines.append(f"`pipeline/audit_sources.py`가 {ZERO_STREAK_DAYS}일 연속 0건인 소스를 감지했습니다.")
    lines.append("")
    lines.append(f"- 대상 소스 수: **{len(anomalies)}**")
    lines.append(f"- Grace: 최초 관측 후 {GRACE_DAYS}일 미만인 소스는 제외")
    lines.append("")
    for i, a in enumerate(anomalies, 1):
        lines.append(f"### {i}. `{a['source_id']}`")
        lines.append(f"- 연속 0건 일자: {', '.join(a['days'])}")
        lines.append(f"- 최초 관측: {a['first_seen']}")
        if a["sample_errors"]:
            lines.append(f"- 최근 에러 샘플:")
            for e in a["sample_errors"]:
                lines.append(f"    - `{e[:200]}`")
        lines.append("")
    lines.append("---")
    lines.append(
        "확인 후 sources.yaml에서 해당 소스를 조정(URL 수정 · enabled: false · type 변경)"
        "하고 이 이슈를 close 해주세요. 자동 close는 없음."
    )
    return title, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--anomaly-out", help="JSON output path for CI")
    parser.add_argument("--issue-body-out", help="Issue body markdown output path for CI (only written when anomalies present)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    anomalies = detect_dead_sources()
    log.info("audit_sources: %d dead source(s) (streak=%d, grace=%d)",
             len(anomalies), ZERO_STREAK_DAYS, GRACE_DAYS)
    for a in anomalies:
        log.info("  dead: %s (%s..%s)", a["source_id"], a["days"][0], a["days"][-1])

    if args.anomaly_out:
        p = Path(args.anomaly_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(anomalies, ensure_ascii=False, indent=2), encoding="utf-8")

    if anomalies and args.issue_body_out:
        title, body = _format_issue(anomalies)
        p = Path(args.issue_body_out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(title + "\n\n" + body, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
