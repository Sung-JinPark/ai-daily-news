"""Read-only diagnostic for cross-day cluster merges.

Purpose: before or after tightening SimHash thresholds, produce a
factual snapshot of what the current continuity behavior actually
yields — how many clusters span more than N days, which are the
biggest, and whether the top offenders look like real persistent
stories or accidental merges of unrelated headlines.

Does NOT modify pipeline logic. Emits a markdown report at
``reviews/cluster-merge-audit-<today>.md``.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR = Path("data")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# day_span bands used to bucket clusters in the report.
BANDS = [1, 3, 7, 14, 30, 60, 90]

# Long-span threshold — clusters spanning at least this many days get
# eyeballed in the report because they are the ones most likely to be
# affected by (or to signal) false cross-day merges.
LONG_SPAN_DAYS = 30


def _list_days() -> list[str]:
    if not DATA_DIR.exists():
        return []
    return sorted(
        p.name for p in DATA_DIR.iterdir()
        if p.is_dir() and DATE_RE.match(p.name)
    )


def _load_articles(day: str) -> list[dict]:
    p = DATA_DIR / day / "articles.json"
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _load_continuity() -> dict:
    p = DATA_DIR / "cluster_continuity.json"
    if not p.exists():
        return {"entries": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"entries": []}


def _day_gap(a: str, b: str) -> int:
    try:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days)
    except (ValueError, TypeError):
        return 0


def _bucket(span: int) -> str:
    prev = 0
    for b in BANDS:
        if span <= b:
            return f"{prev+1}~{b}일" if prev else f"{b}일"
        prev = b
    return f"{BANDS[-1]}일+"


def build_summary() -> dict:
    days = _list_days()
    # Group articles by cluster_id across all days.
    by_cluster: dict[str, list[dict]] = defaultdict(list)
    day_count = 0
    total_articles = 0
    for day in days:
        arts = _load_articles(day)
        if not arts:
            continue
        day_count += 1
        for a in arts:
            total_articles += 1
            cid = a.get("cluster_id") or a.get("id")
            a = {**a, "_day": day}
            by_cluster[cid].append(a)

    clusters = []
    for cid, arts in by_cluster.items():
        arts.sort(key=lambda a: a["_day"])
        first_day = arts[0]["_day"]
        last_day = arts[-1]["_day"]
        day_span = _day_gap(first_day, last_day) + 1
        outlets: set[str] = set()
        for a in arts:
            outlets.add(a.get("source_name", ""))
            outlets.update(a.get("also_covered_by", []) or [])
        # Merge signal per article: cluster_size ≥ 2 means multi-outlet
        # coverage on a single day; we count the max as a rough indicator.
        max_cluster_size = max((a.get("cluster_size", 1) or 1) for a in arts)
        clusters.append({
            "cluster_id": cid,
            "member_count": len(arts),
            "day_span": day_span,
            "first_day": first_day,
            "last_day": last_day,
            "outlets": sorted(outlets),
            "outlet_count": len(outlets),
            "max_cluster_size": max_cluster_size,
            "titles": [a.get("title_original", "") for a in arts],
            "categories": Counter(a.get("category", "") for a in arts),
            "sources": Counter(a.get("source_id", "") for a in arts),
        })

    # Day-span distribution.
    band_counts: Counter[str] = Counter()
    for c in clusters:
        band_counts[_bucket(c["day_span"])] += 1

    # Continuity file diagnostic: how many entries exist, how many carry
    # last_titles (post-R1), and how the last_seen dates are distributed.
    cont = _load_continuity()
    cont_entries = cont.get("entries", []) or []
    n_with_titles = sum(1 for e in cont_entries if e.get("last_titles"))
    now = datetime.now(timezone.utc).date().isoformat()
    cont_gap_counts: Counter[str] = Counter()
    for e in cont_entries:
        cont_gap_counts[_bucket(_day_gap(e.get("last_seen", ""), now))] += 1

    # Long-span clusters (day_span >= LONG_SPAN_DAYS) — likely candidates
    # for false-merge inspection.
    long_span = sorted(
        (c for c in clusters if c["day_span"] >= LONG_SPAN_DAYS),
        key=lambda c: c["day_span"],
        reverse=True,
    )
    top_by_span = long_span[:15]
    top_by_members = sorted(clusters, key=lambda c: c["member_count"], reverse=True)[:15]

    return {
        "total_days": day_count,
        "total_articles": total_articles,
        "total_clusters": len(clusters),
        "band_counts": dict(band_counts),
        "long_span_count": len(long_span),
        "long_span_sample": top_by_span,
        "top_by_members": top_by_members,
        "continuity": {
            "entries": len(cont_entries),
            "with_last_titles": n_with_titles,
            "gap_bucket_counts": dict(cont_gap_counts),
        },
    }


def _fmt_titles_block(titles: list[str], limit: int = 6) -> str:
    if not titles:
        return "(제목 없음)"
    shown = titles[:limit]
    extra = len(titles) - len(shown)
    lines = [f"    - {t}" for t in shown]
    if extra > 0:
        lines.append(f"    - … 외 {extra}건")
    return "\n".join(lines)


def _sources_block(sources: Counter, limit: int = 5) -> str:
    top = sources.most_common(limit)
    return ", ".join(f"{sid}×{n}" for sid, n in top) or "(없음)"


def render(summary: dict) -> str:
    lines: list[str] = []
    today = date.today().isoformat()
    lines.append(f"# Cross-day cluster merge audit — {today}\n")
    lines.append(
        "R1 (커밋 `00cf89a`)로 크로스데이 병합에 티어 임계값 + 제목 Jaccard "
        "게이트가 이미 붙어 있는 상태를 대상으로, 현재 90일 continuity 설정 "
        "아래에서 실제 오병합이 얼마나 감지되는지 데이터로 확인합니다.\n"
    )

    lines.append("## 스캔 요약\n")
    lines.append(f"- 대상 일수: **{summary['total_days']}일**")
    lines.append(f"- 기사 총계: **{summary['total_articles']:,}건**")
    lines.append(f"- 관측된 cluster_id 수: **{summary['total_clusters']:,}개**")
    lines.append(
        f"- day_span >= {LONG_SPAN_DAYS}일 클러스터: **{summary['long_span_count']}개**"
    )
    lines.append("")

    lines.append("## day_span 분포\n")
    lines.append("| 구간 | 클러스터 수 |")
    lines.append("|---|---|")
    for band in [f"{BANDS[0]}일"] + [f"{BANDS[i]+1}~{BANDS[i+1]}일" for i in range(len(BANDS)-1)] + [f"{BANDS[-1]}일+"]:
        n = summary["band_counts"].get(band, 0)
        lines.append(f"| {band} | {n} |")
    lines.append("")

    lines.append("## 연속성 인덱스 상태\n")
    cont = summary["continuity"]
    lines.append(f"- `data/cluster_continuity.json` 엔트리: **{cont['entries']:,}개**")
    lines.append(f"- `last_titles` 필드 있는 엔트리 (R1 이후 갱신됨): **{cont['with_last_titles']:,}개**")
    lines.append("- 각 엔트리의 `last_seen`부터 오늘까지 경과 일수 분포:")
    for band, n in cont["gap_bucket_counts"].items():
        lines.append(f"    - {band}: {n}개")
    lines.append("")

    lines.append(f"## day_span >= {LONG_SPAN_DAYS}일 상위 15개 (오병합 후보)\n")
    if not summary["long_span_sample"]:
        lines.append("_현재 아카이브에는 해당 범위의 클러스터가 없습니다._\n")
    else:
        for c in summary["long_span_sample"]:
            lines.append(
                f"### `{c['cluster_id']}` — span {c['day_span']}일 · "
                f"멤버 {c['member_count']} · 매체 {c['outlet_count']} · "
                f"카테고리 {dict(c['categories'])}"
            )
            lines.append(
                f"- 기간: {c['first_day']} → {c['last_day']}"
            )
            lines.append(
                f"- 소스별 카운트: {_sources_block(c['sources'])}"
            )
            lines.append(f"- 대표 제목 샘플:\n{_fmt_titles_block(c['titles'])}\n")

    lines.append("## 멤버 수 상위 15개 클러스터\n")
    lines.append("| cluster_id | 멤버 | day_span | 매체 | 카테고리 | 최신 제목 |")
    lines.append("|---|---|---|---|---|---|")
    for c in summary["top_by_members"]:
        cats = ", ".join(f"{k}×{v}" for k, v in c["categories"].most_common(2))
        latest_title = c["titles"][-1][:60] + ("…" if len(c["titles"][-1]) > 60 else "")
        lines.append(
            f"| `{c['cluster_id']}` | {c['member_count']} | {c['day_span']}일 | "
            f"{c['outlet_count']} | {cats} | {latest_title} |"
        )
    lines.append("")

    lines.append("## 판단\n")
    if not summary["long_span_sample"]:
        lines.append(
            "- `day_span >= 30`인 크로스데이 클러스터가 **아직 없다** — "
            "아카이브가 짧기 때문 (28~29일). "
            "**현 단계에서는 P1b 추가 방어가 불필요**하며 R1의 티어 임계값 "
            "+ 제목 Jaccard 게이트만으로 충분하다고 판단."
        )
        lines.append(
            "- 재감사 시점: 아카이브가 45일을 넘겼을 때 (2026-07-14 이후), "
            "그리고 90일을 넘겼을 때 (2026-08-30 이후) 이 스크립트를 재실행."
        )
    else:
        lines.append(
            "- `day_span >= 30`인 클러스터가 감지됨. 위 샘플의 제목 리스트를 "
            "육안 검수해 서로 다른 사건이 묶여있으면 P1b에서 추가 게이트 도입."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        default=str(Path("reviews") / f"cluster-merge-audit-{date.today().isoformat()}.md"),
        help="Output report path",
    )
    parser.add_argument("--print", action="store_true", help="print report to stdout instead")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    summary = build_summary()
    report = render(summary)
    log.info(
        "audit: %d days, %d clusters, %d long-span (>=%dd)",
        summary["total_days"], summary["total_clusters"],
        summary["long_span_count"], LONG_SPAN_DAYS,
    )
    if args.print:
        print(report)
        return 0
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    log.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
