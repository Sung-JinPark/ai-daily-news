"""Local-only research stats dashboard (private HTML, gitignored).

The public /research pages were removed — this is their private
replacement, oriented at managing the paper DBs. Generates a single
self-contained HTML file at

    data/research_private/dashboard.html

covering papers.db (collection/enrichment/mentions/refs pipe),
research.db (lexicon, concept ledger, top movers), lifecycle features,
and backup state. Open it in a browser; run-research.bat regenerates
it nightly. Nothing here is served or committed.

Usage: python -m pipeline.research.dashboard
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pipeline.research.research_db import DB_FILE as RESEARCH_DB
from pipeline.research.research_db import _atomic_write

PAPERS_DB = Path("data") / "papers_private" / "papers.db"
OUT = Path("data") / "research_private" / "dashboard.html"
KST = timezone(timedelta(hours=9))


def q1(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def stat_cards(items: list[tuple[str, str, str]]) -> str:
    cells = "".join(
        f'<div class="card"><div class="k">{html.escape(k)}</div>'
        f'<div class="v">{html.escape(str(v))}</div>'
        f'<div class="s">{html.escape(s)}</div></div>'
        for k, v, s in items
    )
    return f'<div class="grid">{cells}</div>'


def table(headers: list[str], rows: list[tuple]) -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    trs = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>"
        for r in rows
    )
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def papers_section() -> str:
    if not PAPERS_DB.exists():
        return "<p>papers.db 없음</p>"
    c = sqlite3.connect(PAPERS_DB)
    n = q1(c, "SELECT COUNT(*) FROM papers")
    enr = q1(c, "SELECT COUNT(*) FROM papers WHERE enriched=1")
    kinds = dict(c.execute("SELECT mention_kind, COUNT(*) FROM paper_mentions GROUP BY mention_kind"))
    span = c.execute("SELECT MIN(day), MAX(day) FROM paper_mentions").fetchone()
    recent_cats = c.execute(
        "SELECT primary_category, COUNT(*) n FROM papers WHERE enriched=1 AND primary_category IS NOT NULL "
        "GROUP BY primary_category ORDER BY n DESC LIMIT 8").fetchall()
    c.close()
    refs_days = 0
    refs_rows = 0
    for d in sorted(Path("data").glob("2???-??-??"))[-7:]:
        f = d / "arxiv_refs.json"
        if f.exists():
            refs_days += 1
            try:
                refs_rows += len(json.loads(f.read_text(encoding="utf-8"))["refs"])
            except Exception:
                pass
    out = stat_cards([
        ("논문", f"{n:,}", f"멘션 기간 {span[0]} ~ {span[1]}"),
        ("보강(enriched)", f"{enr} ({enr / n * 100:.0f}%)", "야간 50/일 드레인"),
        ("멘션", f"{sum(kinds.values()):,}", f"primary {kinds.get('primary', 0)} · reference {kinds.get('reference', 0)}"),
        ("refs 파이프 (7일)", f"{refs_days}일 / {refs_rows}건", "CI 영속 파일 커버리지"),
    ])
    if recent_cats:
        out += "<h3>보강된 논문의 주 분류</h3>" + table(["category", "n"], recent_cats)
    return out


def concepts_section() -> str:
    if not RESEARCH_DB.exists():
        return "<p>research.db 없음</p>"
    c = sqlite3.connect(RESEARCH_DB)
    ver = q1(c, "SELECT COALESCE(MAX(version),0) FROM lexicon_versions")
    n_c = q1(c, "SELECT COUNT(*) FROM concepts WHERE status='active'")
    by_src = dict(c.execute("SELECT source_type, COUNT(*) FROM latest_mentions GROUP BY source_type"))
    top = c.execute(
        "SELECT concept_id, COUNT(*) n FROM latest_mentions GROUP BY concept_id ORDER BY n DESC, concept_id LIMIT 12"
    ).fetchall()
    newest = c.execute(
        "SELECT concept_id, MIN(day) fs FROM latest_mentions GROUP BY concept_id ORDER BY fs DESC, concept_id LIMIT 6"
    ).fetchall()
    try:
        pairs = q1(c, "SELECT COUNT(*) FROM concept_pairs")
        revivals = q1(c, "SELECT COUNT(*) FROM revival_events")
        both_sides = q1(c, "SELECT COUNT(*) FROM media_lag WHERE news_minus_paper_days IS NOT NULL")
    except sqlite3.Error:
        pairs = revivals = both_sides = "—"
    c.close()
    out = stat_cards([
        ("개념 (렉시콘)", f"{n_c} (v{ver})", "active"),
        ("멘션 원장", f"news {by_src.get('news', 0):,} · paper {by_src.get('paper', 0):,}", "latest version"),
        ("공동 매칭 쌍", f"{pairs:,}" if isinstance(pairs, int) else pairs, "concept_pairs"),
        ("재부상 / 양매체", f"{revivals} / {both_sides}", "revival · media_lag 관측"),
    ])
    out += "<h3>상위 개념</h3>" + table(["concept", "mentions"], top)
    out += "<h3>최근 첫 등장</h3>" + table(["concept", "first seen"], newest)
    return out


def backups_section() -> str:
    rows = []
    for d in sorted((Path("data") / "research_private" / "db_exports").glob("*.db")):
        rows.append((d.name, f"{d.stat().st_size:,} B"))
    briefs = sorted((Path("data") / "research_private" / "briefs").glob("*.md"))
    insights = sorted((Path("data") / "research_private" / "notes" / "insights").glob("*.md"))
    out = table(["콜드 백업", "크기"], rows) if rows else "<p>백업 없음</p>"
    out += "<p class='links'>브리프: " + " · ".join(b.stem for b in briefs[-4:])
    out += " | 월간 노트: " + " · ".join(i.stem for i in insights[-4:]) + "</p>"
    return out


def build() -> str:
    gen = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    return f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>연구 DB 대시보드 (private)</title>
<style>
body{{font-family:'Segoe UI',system-ui,sans-serif;color:#0a0a0a;max-width:900px;margin:2rem auto;padding:0 1rem;line-height:1.5}}
h1{{font-size:1.5rem;border-bottom:3px solid #0a0a0a;padding-bottom:.5rem}}
h2{{font-size:1.05rem;letter-spacing:.08em;text-transform:uppercase;color:#be1622;margin-top:2.2rem}}
h3{{font-size:.85rem;color:#555;margin-top:1.4rem}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.8rem;margin:.8rem 0}}
.card{{border:1px solid #ddd;padding: .8rem}}
.card .k{{font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:#888}}
.card .v{{font-size:1.35rem;font-weight:800;margin:.15rem 0}}
.card .s{{font-size:.72rem;color:#999}}
table{{border-collapse:collapse;width:100%;font-size:.82rem;margin:.4rem 0}}
th{{text-align:left;color:#888;font-size:.7rem;text-transform:uppercase;letter-spacing:.08em;border-bottom:1px solid #ddd;padding:.3rem .5rem}}
td{{border-bottom:1px solid #f0f0f0;padding:.3rem .5rem}}
.meta,.links{{font-size:.75rem;color:#999}}
</style></head><body>
<h1>연구 DB 대시보드 <span style="font-size:.8rem;color:#be1622">PRIVATE</span></h1>
<p class="meta">생성 {gen} · 로컬 전용 (gitignored) · 재생성: run-research.bat 또는 python -m pipeline.research.dashboard</p>
<h2>① 논문 DB (papers.db)</h2>{papers_section()}
<h2>② 개념 원장 (research.db)</h2>{concepts_section()}
<h2>③ 백업 · 산출물</h2>{backups_section()}
</body></html>"""


def main() -> None:
    _atomic_write(OUT, build())
    print(f"[dashboard] wrote {OUT} — open in a browser")


if __name__ == "__main__":
    main()
