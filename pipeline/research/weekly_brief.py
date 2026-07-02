"""Weekly private research brief — pure aggregation, zero LLM cost.

Renders the accumulated private metrics into a human-readable Korean
markdown brief at ``data/research_private/briefs/<ISO-week>.md``:

  1. 엔티티 velocity 상위/하위 (최근 7일, gap-aware)
  2. 커뮤니티 변화 — 최신 두 스냅샷의 Louvain 파티션 대조
     (cycle-1 P2 이후 커뮤니티 id가 결정적이므로 신뢰 가능)
  3. Hot papers — Z2 ``paper_trends`` 레이어 직접 호출
  4. 저신뢰 일자 — ``trust_flag != ok`` 스냅샷 명시

Every number carries a footnote pointing at the exact source file so
a reader can audit any claim against the raw artifacts.

Scheduling: the script gates itself to Monday (KST) so
``run-research.bat`` can call it unconditionally; ``--force``
bypasses the gate for manual runs and verification.

Usage:
    python -m pipeline.research.weekly_brief [--force]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from pipeline.research import paper_trends, trend_metrics

PRIVATE_ROOT = Path("data") / "research_private"
BRIEFS_DIR = PRIVATE_ROOT / "briefs"
SNAPSHOTS_DIR = PRIVATE_ROOT / "snapshots"

WINDOW_DAYS = 7
TOP_K = 8

KST = timezone(timedelta(hours=9))


# ---------- section builders ----------


def _entity_velocity_section(lines: list[str]) -> str | None:
    """Top/bottom entity velocity over the trailing window.

    Returns the anchor day (latest data day) or None when no data.
    """
    mentions = trend_metrics.load_mentions_df()
    if mentions.empty:
        lines.append("_엔티티 데이터 없음 — `data/aggregates/entity_mentions.jsonl` 미생성._")
        return None
    counts = trend_metrics.daily_counts(mentions)
    as_of = counts["day"].max()
    anchor = pd.to_datetime(as_of)
    days = pd.to_datetime(counts["day"])
    window = counts[(days > anchor - pd.Timedelta(days=WINDOW_DAYS)) & (days <= anchor)]
    prior = counts[
        (days > anchor - pd.Timedelta(days=2 * WINDOW_DAYS))
        & (days <= anchor - pd.Timedelta(days=WINDOW_DAYS))
    ]
    w = window.groupby(["entity", "entity_type"])["count"].sum().rename("recent")
    p = prior.groupby(["entity", "entity_type"])["count"].sum().rename("prior")
    merged = pd.concat([w, p], axis=1).fillna(0).astype(int).reset_index()
    merged["delta"] = merged["recent"] - merged["prior"]
    merged = merged.sort_values(
        ["delta", "recent", "entity"], ascending=[False, False, True]
    )

    lines.append(f"기준일: **{as_of}** · 윈도우: 최근 {WINDOW_DAYS}일 vs 직전 {WINDOW_DAYS}일 [^mentions]")
    lines.append("")
    lines.append("**상승 상위**")
    lines.append("")
    lines.append("| 엔티티 | 유형 | 최근 7일 | 직전 7일 | Δ |")
    lines.append("|---|---|---:|---:|---:|")
    for row in merged.head(TOP_K).itertuples(index=False):
        lines.append(f"| {row.entity} | {row.entity_type} | {row.recent} | {row.prior} | {row.delta:+d} |")
    lines.append("")
    lines.append("**하강 상위**")
    lines.append("")
    lines.append("| 엔티티 | 유형 | 최근 7일 | 직전 7일 | Δ |")
    lines.append("|---|---|---:|---:|---:|")
    for row in merged.tail(TOP_K).sort_values(["delta", "entity"]).itertuples(index=False):
        lines.append(f"| {row.entity} | {row.entity_type} | {row.recent} | {row.prior} | {row.delta:+d} |")
    lines.append("")
    return as_of


def _community_section(lines: list[str]) -> None:
    """Membership diff between the two newest snapshots."""
    if not SNAPSHOTS_DIR.exists():
        lines.append("_스냅샷 없음._")
        return
    snap_dirs = sorted(p for p in SNAPSHOTS_DIR.iterdir() if p.is_dir())
    with_comm = [p for p in snap_dirs if (p / "network_communities.parquet").exists()]
    if len(with_comm) < 2:
        lines.append("_커뮤니티 스냅샷이 2개 미만 — 다음 스냅샷부터 대조 가능._")
        return
    prev_dir, curr_dir = with_comm[-2], with_comm[-1]
    prev = pd.read_parquet(prev_dir / "network_communities.parquet")
    curr = pd.read_parquet(curr_dir / "network_communities.parquet")
    prev_map = dict(zip(prev["entity"], prev["community_id"]))
    curr_map = dict(zip(curr["entity"], curr["community_id"]))
    joined = set(curr_map) - set(prev_map)
    left = set(prev_map) - set(curr_map)
    moved = sorted(
        (e, prev_map[e], curr_map[e])
        for e in set(prev_map) & set(curr_map)
        if prev_map[e] != curr_map[e]
    )
    lines.append(
        f"대조: {prev_dir.name} → {curr_dir.name} · "
        f"커뮤니티 수 {prev['community_id'].nunique()} → {curr['community_id'].nunique()} [^communities]"
    )
    lines.append("")
    lines.append(f"- 신규 진입 노드: **{len(joined)}**" + (f" — {', '.join(sorted(joined)[:10])}" if joined else ""))
    lines.append(f"- 이탈 노드: **{len(left)}**" + (f" — {', '.join(sorted(left)[:10])}" if left else ""))
    lines.append(f"- 커뮤니티 이동: **{len(moved)}**")
    if moved:
        lines.append("")
        lines.append("| 엔티티 | 이전 | 현재 |")
        lines.append("|---|---:|---:|")
        for e, a, b in moved[:TOP_K]:
            lines.append(f"| {e} | {a} | {b} |")
    lines.append("")


def _paper_db_status_line(lines: list[str]) -> None:
    """One-line papers.db health so enrichment stalls are visible in
    the brief (C2): papers N · enriched E (pct) · reference mentions M."""
    import sqlite3
    db = paper_trends.PAPERS_DB
    if not db.exists():
        return
    conn = sqlite3.connect(db)
    try:
        n_papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        n_enriched = conn.execute("SELECT COUNT(*) FROM papers WHERE enriched=1").fetchone()[0]
        cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_mentions)")}
        n_ref = 0
        if "mention_kind" in cols:
            n_ref = conn.execute(
                "SELECT COUNT(*) FROM paper_mentions WHERE mention_kind='reference'"
            ).fetchone()[0]
    finally:
        conn.close()
    pct = (n_enriched / n_papers * 100) if n_papers else 0.0
    lines.append(
        f"논문 DB 상태: papers **{n_papers}** · enriched **{n_enriched}** ({pct:.0f}%) · "
        f"reference 멘션 **{n_ref}** [^papers]"
    )
    lines.append("")


def _hot_papers_section(lines: list[str]) -> None:
    mentions = paper_trends.load_mentions()
    if mentions.empty:
        lines.append("_papers.db 없음 또는 비어있음 — `python -m pipeline.collect_papers` 먼저._")
        return
    _paper_db_status_line(lines)
    velocity = paper_trends.paper_velocity(mentions)
    topics = paper_trends.paper_topics(mentions)
    titles = paper_trends.load_paper_titles()
    hot = paper_trends.hot_papers(velocity, topics, titles, top_n=TOP_K)
    lines.append(f"기준일: **{hot['as_of']}** · 스코어 = 최근 7일 − 직전 7일 멘션 · P=피드 원샷, R=본문 참조 [^papers]")
    lines.append("")
    # Honesty note (C3): when every score is identical the ranking is
    # just the arxiv_id tiebreak — say so instead of implying signal.
    scores = {p["score"] for p in hot["papers"]}
    if len(hot["papers"]) > 1 and len(scores) == 1:
        lines.append(
            "> ⚠ 이번 주 멘션 스코어는 전부 동일(차별화 불충분) — 아래 순위는 arxiv_id 순입니다."
        )
        lines.append("")
    lines.append("| arXiv | 제목 | 스코어 | 최근 | P/R | 태그 |")
    lines.append("|---|---|---:|---:|---:|---|")
    for p in hot["papers"]:
        # Placeholder rows (reference-discovered, not yet enriched)
        # have no title — show the id explicitly rather than a blank.
        title = p["title"] or f"arXiv:{p['arxiv_id']} (미보강)"
        title = (title[:52] + "…") if len(title) > 52 else title
        tags = ", ".join(p["top_tags"]) if p["top_tags"] else "—"
        pr = f"{p.get('recent_primary', 0)}/{p.get('recent_reference', 0)}"
        lines.append(
            f"| {p['arxiv_id']} | {title} | {p['score']:+d} | {p['recent_mentions']} | {pr} | {tags} |"
        )
    lines.append("")


def _trust_section(lines: list[str]) -> None:
    """Call out snapshot days whose network metrics are low-trust."""
    if not SNAPSHOTS_DIR.exists():
        lines.append("_스냅샷 없음._")
        return
    flagged: list[tuple[str, str]] = []
    scanned = 0
    for day_dir in sorted(p for p in SNAPSHOTS_DIR.iterdir() if p.is_dir()):
        mf = day_dir / "network_metrics.json"
        if not mf.exists():
            continue
        scanned += 1
        try:
            metrics = json.loads(mf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            flagged.append((day_dir.name, "unparseable"))
            continue
        flag = metrics.get("trust_flag", "unknown")
        if flag != "ok":
            flagged.append((day_dir.name, flag))
    if not flagged:
        lines.append(f"저신뢰 스냅샷 없음 — {scanned}일 전부 `trust_flag=ok`. [^trust]")
    else:
        lines.append(f"다음 {len(flagged)}일은 네트워크 지표 신뢰도가 낮음 — 시계열 분석에서 제외 권장: [^trust]")
        lines.append("")
        for day, flag in flagged:
            lines.append(f"- {day} — `{flag}`")
    lines.append("")


# ---------- orchestration ----------


def build_brief() -> tuple[str, str]:
    """Return (iso_week, markdown_body)."""
    lines: list[str] = []
    lines.append("")
    lines.append("## 1. 엔티티 velocity (뉴스 멘션)")
    lines.append("")
    as_of = _entity_velocity_section(lines)
    lines.append("## 2. 커뮤니티 변화 (co-mention 그래프)")
    lines.append("")
    _community_section(lines)
    lines.append("## 3. Hot papers")
    lines.append("")
    _hot_papers_section(lines)
    lines.append("## 4. 데이터 신뢰도")
    lines.append("")
    _trust_section(lines)
    lines.append("---")
    lines.append("")
    lines.append("[^mentions]: `data/aggregates/entity_mentions.jsonl` → `pipeline.research.trend_metrics.daily_counts` (gap-aware).")
    lines.append("[^communities]: `data/research_private/snapshots/<day>/network_communities.parquet` — Louvain seed 42, 사이즈·최소멤버 정렬로 id 결정적.")
    lines.append("[^papers]: `data/papers_private/papers.db` → `pipeline.research.paper_trends.hot_papers`.")
    lines.append("[^trust]: `data/research_private/snapshots/<day>/network_metrics.json` 의 `trust_flag`.")
    lines.append("")

    # ISO week from the data anchor (falls back to today KST when empty).
    anchor = as_of or datetime.now(KST).strftime("%Y-%m-%d")
    iso = datetime.strptime(anchor, "%Y-%m-%d").isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    header = [
        f"# 주간 리서치 브리프 · {week}",
        "",
        f"데이터 기준일: {anchor} · 생성 방식: 순수 집계 (LLM 미사용) · 소스: private corpus",
        "",
    ]
    return week, "\n".join(header + lines)


def run(force: bool = False) -> Path | None:
    today = datetime.now(KST)
    if today.weekday() != 0 and not force:
        print(f"[brief] not Monday (KST weekday={today.weekday()}) - skipping. Use --force to override.")
        return None
    week, body = build_brief()
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    out = BRIEFS_DIR / f"{week}.md"
    out.write_text(body, encoding="utf-8")
    print(f"[brief] wrote {out}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--force", action="store_true", help="run even when not Monday")
    args = parser.parse_args()
    run(force=args.force)


if __name__ == "__main__":
    main()
