"""Aggregation-only monthly research insight notes (RDB-6).

Writes ``notes/insights/YYYY-MM.md`` — every number is a pure
aggregate from research.db with a source footnote; interpretation is
explicitly left blank for the researcher. Sections with insufficient
data say so (brief honesty-line convention). Monthly gate (1st of
month KST) lives inside; --force bypasses.

Usage: python -m pipeline.research.monthly_insight [--force]
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.research.research_db import DB_FILE, _atomic_write

INSIGHTS_DIR = Path("data") / "research_private" / "notes" / "insights"
KST = timezone(timedelta(hours=9))


def build(conn: sqlite3.Connection) -> tuple[str, str]:
    anchor = conn.execute("SELECT MAX(day) FROM latest_mentions").fetchone()[0]
    month = anchor[:7]
    L: list[str] = [f"# 월간 연구 인사이트 · {month} (데이터 기준일 {anchor} · 집계 전용, 해석은 하단 메모에)", ""]

    # ① 이달의 신규 개념
    new_c = conn.execute(
        "SELECT concept_id, MIN(day) fs FROM latest_mentions GROUP BY concept_id "
        "HAVING fs LIKE ? ORDER BY fs, concept_id", (month + "%",)).fetchall()
    L += ["## ① 이달의 신규 개념 (첫 등장)", ""]
    if new_c:
        L += [f"- {c} — 첫 등장 {d}" for c, d in new_c]
    else:
        L.append("_이달 첫 등장 개념 없음._")
    L.append("")

    # ② 재부상
    rev = conn.execute(
        "SELECT concept_id, dormant_days, revived_day, revived_source_type FROM revival_events "
        "WHERE revived_day LIKE ? ORDER BY revived_day", (month + "%",)).fetchall()
    L += ["## ② 재부상 (revival)", ""]
    if rev:
        L += [f"- {c} — {d}일 휴면 후 {rd} 재등장 ({st})" for c, d, rd, st in rev]
    else:
        L.append("_재부상 이벤트 없음 — 코퍼스 ~1개월로 dormancy(21일) 관측에 이르며, 3개월+ 축적 후 유의미. [^lc]_")
    L.append("")

    # ③ 횡/수직 무버
    bd = conn.execute(
        "SELECT concept_id, new_partners, repeat_intensity FROM breadth_depth WHERE month=? "
        "ORDER BY new_partners DESC, concept_id LIMIT 5", (month,)).fetchall()
    L += ["## ③ 횡/수직 무버 상위 5 (이달)", ""]
    if bd:
        L += ["| 개념 | 신규 파트너(횡) | 반복 강도(수직) |", "|---|---:|---:|"]
        L += [f"| {c} | {n} | {r} |" for c, n, r in bd]
    else:
        L.append("_이달 breadth_depth 데이터 없음._")
    L.append("")

    # ④ paper↔news 시차
    lag = conn.execute(
        "SELECT concept_id, first_news_day, first_paper_day, news_minus_paper_days FROM media_lag "
        "WHERE news_minus_paper_days IS NOT NULL ORDER BY ABS(news_minus_paper_days) DESC, concept_id LIMIT 5"
    ).fetchall()
    L += ["## ④ paper↔news 시차 관찰 (|양쪽 관측| 상위 5)", ""]
    if lag:
        L += ["| 개념 | news 첫 | paper 첫 | news−paper(일) |", "|---|---|---|---:|"]
        L += [f"| {c} | {n} | {p} | {int(d):+d} |" for c, n, p, d in lag]
        L.append("")
        L.append("_주의: paper 초록 커버리지가 아직 증분 중(enrich 드레인) — 시차 부호는 확정으로 읽지 말 것. [^dl]_")
    else:
        L.append("_양 코퍼스 모두에서 관측된 개념 없음._")
    L.append("")

    # ⑤ 렉시콘 변경 이력
    vers = conn.execute("SELECT version, created_at, note, concept_count FROM lexicon_versions ORDER BY version").fetchall()
    L += ["## ⑤ 렉시콘 변경 이력", "", "| ver | 일시(UTC) | 개념 수 | 비고 |", "|---|---|---:|---|"]
    L += [f"| {v} | {ca} | {cc} | {nt} |" for v, ca, nt, cc in vers]
    L.append("")

    # ⑥ 연구자 메모
    L += ["## ⑥ 연구자 메모 (수기)", "", "_(해석·가설 후보를 여기에 직접 기입 — hypotheses.md로 승격)_", "",
          "---", "",
          "[^lc]: concept_features/revival_events.parquet · 상수 근거 decisions.md",
          "[^dl]: notes/DATASET.md 알려진 한계 절", ""]
    return month, "\n".join(L)


def run(force: bool = False) -> Path | None:
    today = datetime.now(KST)
    if today.day != 1 and not force:
        print(f"[insight] not the 1st (KST day={today.day}) - skipping. --force to override.")
        return None
    conn = sqlite3.connect(DB_FILE)
    try:
        month, body = build(conn)
    finally:
        conn.close()
    out = INSIGHTS_DIR / f"{month}.md"
    _atomic_write(out, body)
    print(f"[insight] wrote {out}")
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    run(force=a.force)
