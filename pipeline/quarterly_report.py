"""Generate a quarterly narrative report from the accumulated corpus.

Reads:
  * weekly digests whose ISO week falls in the quarter
  * cross-day themes archived for those weeks
  * entity mentions aggregated over the quarter
  * predictions still pending at quarter's end
  * category mix from articles.json

Sends a single Haiku call and writes:
  * `data/reports/<quarter>.json`  (structured payload)
  * `data/reports/<quarter>.md`    (rendered markdown)

Idempotent — re-runs replace both files atomically.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

load_dotenv()

from pipeline.summarize import DATA_DIR, MODEL, RETRYABLE
from pipeline.utils.prompts import (
    QUARTERLY_REPORT_SYSTEM_PROMPT,
    QUARTERLY_REPORT_USER_TEMPLATE,
)

log = logging.getLogger(__name__)
MAX_OUTPUT_TOKENS = 2200
REPORTS_DIR = DATA_DIR / "reports"

CATEGORY_KO = {
    "model_research": "모델/연구",
    "business": "비즈니스/투자",
    "policy": "정책/규제",
    "product": "제품/툴",
    "hardware": "하드웨어/인프라",
    "community": "커뮤니티",
}


def quarter_bounds(quarter: str) -> tuple[date, date]:
    m = re.match(r"^(\d{4})-Q([1-4])$", quarter)
    if not m:
        raise ValueError(f"invalid quarter: {quarter}")
    year, q = int(m.group(1)), int(m.group(2))
    start_month = (q - 1) * 3 + 1
    start = date(year, start_month, 1)
    if q == 4:
        end = date(year, 12, 31)
    else:
        next_start = date(year, start_month + 3, 1)
        end = next_start - timedelta(days=1)
    return start, end


def current_quarter(d: date | None = None) -> str:
    d = d or date.today()
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def previous_completed_quarter(d: date | None = None) -> str:
    """Return the most recent fully-completed quarter."""
    d = d or date.today()
    q = (d.month - 1) // 3 + 1
    if q == 1:
        return f"{d.year - 1}-Q4"
    return f"{d.year}-Q{q - 1}"


def _list_days_in(start: date, end: date) -> list[str]:
    if not DATA_DIR.exists():
        return []
    out: list[str] = []
    for p in DATA_DIR.iterdir():
        if not p.is_dir():
            continue
        if len(p.name) != 10 or p.name[4] != "-" or p.name[7] != "-":
            continue
        try:
            d = date.fromisoformat(p.name)
        except ValueError:
            continue
        if start <= d <= end:
            out.append(p.name)
    return sorted(out)


def _iso_week_in_quarter(week_str: str, start: date, end: date) -> bool:
    m = re.match(r"^(\d{4})-W(\d{1,2})$", week_str)
    if not m:
        return False
    year, wk = int(m.group(1)), int(m.group(2))
    try:
        monday = date.fromisocalendar(year, wk, 1)
        sunday = monday + timedelta(days=6)
    except ValueError:
        return False
    return not (sunday < start or monday > end)


def _load_json(path: Path, fallback):
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _gather(quarter: str) -> dict:
    start, end = quarter_bounds(quarter)
    days = _list_days_in(start, end)

    # Weekly recaps.
    weekly_dir = DATA_DIR / "weekly"
    weekly_recaps: list[str] = []
    if weekly_dir.exists():
        for f in sorted(weekly_dir.glob("*.json")):
            digest = _load_json(f, None)
            if not digest:
                continue
            if not _iso_week_in_quarter(digest.get("week", ""), start, end):
                continue
            recap = str(digest.get("theme_recap_ko", "")).strip()
            if recap:
                weekly_recaps.append(f"[{digest.get('week')}] {recap}")

    # Themes archived in the quarter.
    themes_dir = DATA_DIR / "themes"
    themes: list[str] = []
    if themes_dir.exists():
        for f in sorted(themes_dir.glob("*.json")):
            payload = _load_json(f, None)
            if not payload:
                continue
            week = payload.get("week")
            if not week or not _iso_week_in_quarter(week, start, end):
                continue
            for t in payload.get("themes", []) or []:
                nm = t.get("name")
                th = t.get("thesis_ko")
                if nm and th:
                    themes.append(f"[{week}] {nm}: {th}")

    # Entity mentions in the quarter.
    entity_counts: Counter[str] = Counter()
    ment_path = DATA_DIR / "aggregates" / "entity_mentions.jsonl"
    if ment_path.exists():
        for line in ment_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            day = obj.get("day", "")
            if day < start.isoformat() or day > end.isoformat():
                continue
            ent = obj.get("entity")
            if ent:
                entity_counts[ent] += 1
    top_entities = entity_counts.most_common(20)

    # Pending predictions.
    reg = _load_json(DATA_DIR / "predictions" / "registry.json", None)
    pending: list[str] = []
    if reg:
        for p in reg.get("predictions", []) or []:
            if p.get("status") in ("pending", "still_pending"):
                claim = p.get("claim_ko", "")
                who = p.get("who", "")
                horizon = p.get("horizon", "unspecified")
                pending.append(f"[{who} · 만기 {horizon}] {claim}")

    # Category mix + article total.
    n_articles = 0
    cat_counter: Counter[str] = Counter()
    for day in days:
        arts = _load_json(DATA_DIR / day / "articles.json", [])
        n_articles += len(arts)
        for a in arts:
            cat_counter[a.get("category", "")] += 1
    category_mix = [
        (CATEGORY_KO.get(c, c or "기타"), n) for c, n in cat_counter.most_common()
    ]

    # Coverage disclosure — quarter is 89~92 days but the archive may
    # only contain a fraction of them (esp. the very first report of the
    # project's life). Reporting this ratio prevents readers from
    # mistaking a partial-window synthesis for a full-quarter view.
    quarter_total_days = (end - start).days + 1
    coverage_ratio = round(len(days) / quarter_total_days, 3) if quarter_total_days else 0.0

    return {
        "quarter": quarter,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "n_days": len(days),
        "n_articles": n_articles,
        "quarter_total_days": quarter_total_days,
        "coverage_days": len(days),
        "coverage_ratio": coverage_ratio,
        "weekly_recaps": weekly_recaps,
        "themes": themes,
        "entities": top_entities,
        "pending_predictions": pending,
        "category_mix": category_mix,
    }


def _format_prompt(g: dict) -> str:
    def _block(items: list[str], empty: str = "(없음)") -> str:
        if not items:
            return empty
        return "\n".join(f"- {s}" for s in items[:20])
    weekly_block = _block(g["weekly_recaps"])
    themes_block = _block(g["themes"])
    entities_block = "\n".join(f"- {ent}: {n}건" for ent, n in g["entities"]) or "(없음)"
    pending_block = _block(g["pending_predictions"], empty="(대기 중 예측 없음)")
    category_block = "\n".join(f"- {label}: {n}건" for label, n in g["category_mix"]) or "(없음)"
    return QUARTERLY_REPORT_USER_TEMPLATE.format(
        quarter=g["quarter"],
        start=g["start"],
        end=g["end"],
        n_articles=g["n_articles"],
        n_days=g["n_days"],
        weekly_recaps=weekly_block,
        themes=themes_block,
        entities=entities_block,
        pending_predictions=pending_block,
        category_mix=category_block,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=2, max=20),
    retry=retry_if_exception_type(RETRYABLE),
)
def _call_haiku(client: anthropic.Anthropic, user: str) -> dict[str, Any]:
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=QUARTERLY_REPORT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"no JSON in quarterly response: {text[:200]!r}")
    return json.loads(m.group(0))


def _validate(parsed: dict) -> dict | None:
    try:
        title = str(parsed["title_ko"]).strip()
        summary = str(parsed["exec_summary_ko"]).strip()
        narratives_raw = parsed.get("top_narratives_ko", [])
        movers_raw = parsed.get("top_movers_ko", [])
        questions_raw = parsed.get("open_questions_ko", [])
        closing = str(parsed.get("closing_ko", "")).strip()
    except (KeyError, TypeError):
        return None
    if not title or not summary:
        return None
    def _list_of_dicts(v, keys):
        if not isinstance(v, list):
            return []
        out = []
        for item in v:
            if not isinstance(item, dict):
                continue
            row = {k: str(item.get(k, "")).strip() for k in keys}
            if all(row.values()):
                out.append(row)
        return out
    narratives = _list_of_dicts(narratives_raw, ("name", "summary_ko"))
    movers = _list_of_dicts(movers_raw, ("entity", "movement_ko"))
    questions = [str(q).strip() for q in (questions_raw or []) if str(q).strip()]
    return {
        "title_ko": title,
        "exec_summary_ko": summary,
        "top_narratives_ko": narratives,
        "top_movers_ko": movers,
        "open_questions_ko": questions,
        "closing_ko": closing,
    }


def _render_markdown(payload: dict, meta: dict) -> str:
    lines: list[str] = []
    lines.append(f"# {meta['quarter']} · {payload['title_ko']}")
    lines.append("")
    lines.append(f"*{meta['start']} ~ {meta['end']} · 기사 {meta['n_articles']}건 · 활동 {meta['n_days']}일*")
    lines.append("")
    coverage_days = meta.get("coverage_days", meta.get("n_days", 0))
    total_days = meta.get("quarter_total_days")
    ratio = meta.get("coverage_ratio")
    if total_days and ratio is not None:
        pct = int(round(ratio * 100))
        lines.append(f"> **커버리지 고지**: 이 리포트는 분기 {total_days}일 중 {coverage_days}일({pct}%)의 데이터를 반영합니다.")
        if ratio < 0.6:
            lines.append("> ")
            lines.append("> ⚠️ **부분 커버리지** — 분기의 절반 미만이 관측된 상태에서 종합했습니다. 후속 분기에 전체 관점이 완성됩니다.")
        lines.append("")
    lines.append("## 요약")
    lines.append(payload["exec_summary_ko"])
    lines.append("")
    if payload["top_narratives_ko"]:
        lines.append("## 주요 서사")
        for n in payload["top_narratives_ko"]:
            lines.append(f"### #{n['name']}")
            lines.append(n["summary_ko"])
            lines.append("")
    if payload["top_movers_ko"]:
        lines.append("## 이번 분기의 무버")
        for m in payload["top_movers_ko"]:
            lines.append(f"- **{m['entity']}** — {m['movement_ko']}")
        lines.append("")
    if payload["open_questions_ko"]:
        lines.append("## 지켜볼 것")
        for q in payload["open_questions_ko"]:
            lines.append(f"- {q}")
        lines.append("")
    if payload["closing_ko"]:
        lines.append("## 맺음")
        lines.append(payload["closing_ko"])
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--quarter", help="YYYY-Qn, e.g. 2026-Q2")
    parser.add_argument(
        "--auto",
        action="store_true",
        help="only run for the most recent completed quarter and skip if the file already exists",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    quarter = args.quarter or previous_completed_quarter()
    if args.auto and (REPORTS_DIR / f"{quarter}.json").exists():
        log.info("auto: %s report already exists, skipping", quarter)
        return 0

    gathered = _gather(quarter)
    log.info(
        "quarterly %s: %d/%d days (%.1f%% coverage), %d articles, %d weekly recaps, %d themes, %d entities, %d pending predictions",
        quarter, gathered["coverage_days"], gathered["quarter_total_days"],
        gathered["coverage_ratio"] * 100,
        gathered["n_articles"], len(gathered["weekly_recaps"]),
        len(gathered["themes"]), len(gathered["entities"]),
        len(gathered["pending_predictions"]),
    )
    if gathered["n_articles"] < 20:
        log.warning("too little data (%d articles) — skipping", gathered["n_articles"])
        return 0
    if args.dry_run:
        log.info("dry-run: skipping LLM call")
        return 0

    user_prompt = _format_prompt(gathered)
    client = anthropic.Anthropic()
    try:
        parsed = _call_haiku(client, user_prompt)
    except Exception as exc:  # noqa: BLE001
        log.error("LLM call failed: %s", exc)
        return 1
    payload = _validate(parsed)
    if not payload:
        log.error("schema validation failed: %s", parsed)
        return 1

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    meta = {
        "quarter": quarter,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "start": gathered["start"],
        "end": gathered["end"],
        "n_days": gathered["n_days"],
        "n_articles": gathered["n_articles"],
        "quarter_total_days": gathered["quarter_total_days"],
        "coverage_days": gathered["coverage_days"],
        "coverage_ratio": gathered["coverage_ratio"],
    }
    (REPORTS_DIR / f"{quarter}.json").write_text(
        json.dumps({**meta, **payload}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (REPORTS_DIR / f"{quarter}.md").write_text(_render_markdown(payload, meta), encoding="utf-8")
    log.info("wrote %s.json + %s.md", quarter, quarter)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
