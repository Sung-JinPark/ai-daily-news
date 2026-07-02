"""Paper-level trend layer — joins the private papers.db with the
public news corpus so the paper can treat *papers themselves* as
trend entities (velocity, topic profile, hot list).

Inputs (never mutated):
  * ``data/papers_private/papers.db`` — ``papers`` + ``paper_mentions``
    accumulated by ``pipeline.collect_papers``
  * ``data/YYYY-MM-DD/articles.json`` — tags per mentioning article

Outputs (all under ``data/research_private/paper_trends/``, gitignored):
  * ``paper_velocity.parquet``  — (arxiv_id, day, count, velocity)
  * ``paper_topics.parquet``    — (arxiv_id, tag, count)
  * ``hot_papers-<as_of>.json`` — top-N papers by 7-day mention
                                   acceleration proxy

Determinism contract (verified by running twice and diffing):
  * ``as_of`` is the **latest day present in paper_mentions**, never
    wall-clock, so the same DB state always produces the same output.
  * No generated-at timestamps inside any output file.
  * All orderings carry an explicit tiebreak — score desc, then
    arxiv_id ascending — so equal scores can't shuffle between runs.

Velocity is gap-aware exactly like ``trend_metrics.daily_counts``:
each paper's series is reindexed over the full calendar-day range of
the corpus (``pd.date_range``), so a day the pipeline missed shows an
honest zero instead of silently compressing two days into one diff
step.

Usage:
    python -m pipeline.research.paper_trends [--top 10]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")
PAPERS_DB = DATA_DIR / "papers_private" / "papers.db"
OUT_DIR = DATA_DIR / "research_private" / "paper_trends"

HOT_WINDOW_DAYS = 7
DEFAULT_TOP_N = 10


# ---------- loading ----------


def load_mentions(db_path: Path = PAPERS_DB) -> pd.DataFrame:
    """Return paper_mentions as a DataFrame
    ``(arxiv_id, day, article_id, cluster_id, source_id, importance,
    mention_kind)``. Empty frame when the DB is absent (machine
    without the private corpus). Pre-v2 DBs (no mention_kind column)
    are read as all-primary."""
    cols = ["arxiv_id", "day", "article_id", "cluster_id", "source_id",
            "importance", "mention_kind"]
    if not db_path.exists():
        return pd.DataFrame(columns=cols)
    conn = sqlite3.connect(db_path)
    try:
        have_kind = any(
            row[1] == "mention_kind"
            for row in conn.execute("PRAGMA table_info(paper_mentions)")
        )
        kind_expr = "mention_kind" if have_kind else "'primary' AS mention_kind"
        return pd.read_sql_query(
            f"SELECT arxiv_id, day, article_id, cluster_id, source_id, importance, {kind_expr} "
            "FROM paper_mentions ORDER BY arxiv_id, day, article_id",
            conn,
        )
    finally:
        conn.close()


def load_paper_titles(db_path: Path = PAPERS_DB) -> dict[str, str]:
    """arxiv_id -> title (may be the news headline until enriched)."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT arxiv_id, COALESCE(title, '') FROM papers").fetchall()
    finally:
        conn.close()
    return {aid: title for aid, title in rows}


def load_article_tags(days: list[str]) -> dict[str, list[str]]:
    """article_id -> tags, read from each day's articles.json."""
    out: dict[str, list[str]] = {}
    for day in days:
        path = DATA_DIR / day / "articles.json"
        if not path.exists():
            continue
        try:
            articles = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for a in articles:
            aid = a.get("id")
            if aid:
                out[aid] = a.get("tags") or []
    return out


# ---------- derivations ----------


def paper_velocity(mentions: pd.DataFrame) -> pd.DataFrame:
    """Gap-aware per-paper daily mention counts + signed velocity.

    Returns long-format ``(arxiv_id, day, count, velocity,
    primary_count, reference_count)`` where every paper has one row per
    calendar day of the corpus range. ``count`` and ``velocity`` keep
    their v1 semantics (all mentions); the two kind columns are an
    additive schema extension (C1) — count == primary + reference on
    every row. First-day velocity equals the count (baseline-from-zero,
    matching ``trend_metrics.compute_velocity``).
    """
    out_cols = ["arxiv_id", "day", "count", "velocity", "primary_count", "reference_count"]
    if mentions.empty:
        return pd.DataFrame(columns=out_cols)
    counts = (
        mentions.groupby(["arxiv_id", "day"]).size().rename("count").reset_index()
    )
    kind_counts = (
        mentions.groupby(["arxiv_id", "day", "mention_kind"]).size().rename("n").reset_index()
        .pivot_table(index=["arxiv_id", "day"], columns="mention_kind", values="n", fill_value=0)
        .reset_index()
    )
    for col in ("primary", "reference"):
        if col not in kind_counts.columns:
            kind_counts[col] = 0
    counts = counts.merge(
        kind_counts[["arxiv_id", "day", "primary", "reference"]],
        on=["arxiv_id", "day"], how="left",
    ).fillna({"primary": 0, "reference": 0})
    observed = pd.to_datetime(sorted(counts["day"].unique()))
    full_range = pd.date_range(observed.min(), observed.max(), freq="D")
    frames: list[pd.DataFrame] = []
    for aid, sub in counts.groupby("arxiv_id"):
        idx = pd.to_datetime(sub["day"])
        series = sub.set_index(idx)["count"].reindex(full_range, fill_value=0)
        prim = sub.set_index(idx)["primary"].reindex(full_range, fill_value=0)
        ref = sub.set_index(idx)["reference"].reindex(full_range, fill_value=0)
        velocity = series.diff()
        velocity.iloc[0] = series.iloc[0]
        frames.append(pd.DataFrame({
            "arxiv_id": aid,
            "day": series.index.strftime("%Y-%m-%d"),
            "count": series.values.astype(int),
            "velocity": velocity.values.astype(float),
            "primary_count": prim.values.astype(int),
            "reference_count": ref.values.astype(int),
        }))
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["arxiv_id", "day"]).reset_index(drop=True)


def paper_topics(mentions: pd.DataFrame) -> pd.DataFrame:
    """Per-paper topic frequency vector from mentioning articles' tags.

    Returns ``(arxiv_id, tag, count)`` sorted by (arxiv_id, count desc,
    tag) so the output is stable across runs.
    """
    if mentions.empty:
        return pd.DataFrame(columns=["arxiv_id", "tag", "count"])
    days = sorted(mentions["day"].unique())
    tag_map = load_article_tags(days)
    rows: list[dict] = []
    for row in mentions.itertuples(index=False):
        for tag in tag_map.get(row.article_id, []):
            rows.append({"arxiv_id": row.arxiv_id, "tag": tag})
    if not rows:
        return pd.DataFrame(columns=["arxiv_id", "tag", "count"])
    df = pd.DataFrame(rows)
    agg = df.groupby(["arxiv_id", "tag"]).size().rename("count").reset_index()
    return (
        agg.sort_values(["arxiv_id", "count", "tag"], ascending=[True, False, True])
        .reset_index(drop=True)
    )


def hot_papers(
    velocity: pd.DataFrame,
    topics: pd.DataFrame,
    titles: dict[str, str],
    top_n: int = DEFAULT_TOP_N,
) -> dict:
    """Top-N papers by 7-day mention acceleration proxy.

    score = mentions(last 7 days) - mentions(prior 7 days), where the
    window is anchored at ``as_of`` = the latest day in the data (not
    wall-clock — keeps the output deterministic for a given DB state).
    Ties break on arxiv_id ascending.
    """
    if velocity.empty:
        return {"schema_version": 1, "as_of": None, "window_days": HOT_WINDOW_DAYS, "papers": []}
    as_of = velocity["day"].max()
    days = pd.to_datetime(velocity["day"])
    anchor = pd.to_datetime(as_of)
    recent_mask = (days > anchor - pd.Timedelta(days=HOT_WINDOW_DAYS)) & (days <= anchor)
    prior_mask = (
        (days > anchor - pd.Timedelta(days=2 * HOT_WINDOW_DAYS))
        & (days <= anchor - pd.Timedelta(days=HOT_WINDOW_DAYS))
    )
    recent = velocity[recent_mask].groupby("arxiv_id")["count"].sum()
    prior = velocity[prior_mask].groupby("arxiv_id")["count"].sum()
    recent_primary = velocity[recent_mask].groupby("arxiv_id")["primary_count"].sum()
    recent_reference = velocity[recent_mask].groupby("arxiv_id")["reference_count"].sum()
    merged = pd.DataFrame({
        "recent": recent, "prior": prior,
        "recent_primary": recent_primary, "recent_reference": recent_reference,
    }).fillna(0).astype(int)
    merged["score"] = merged["recent"] - merged["prior"]
    # Only papers that actually moved in the window.
    merged = merged[merged["recent"] > 0].reset_index().rename(columns={"index": "arxiv_id"})
    # Explicit three-key ordering: score desc, recent desc, arxiv_id asc —
    # equal scores can never shuffle between runs.
    top = merged.sort_values(
        ["score", "recent", "arxiv_id"], ascending=[False, False, True]
    ).head(top_n)

    top_tags = {
        aid: sub["tag"].head(3).tolist()
        for aid, sub in topics.groupby("arxiv_id")
    }
    papers = []
    for row in top.itertuples(index=False):
        papers.append({
            "arxiv_id": row.arxiv_id,
            "title": titles.get(row.arxiv_id, ""),
            "score": int(row.score),
            "recent_mentions": int(row.recent),
            "prior_mentions": int(row.prior),
            "recent_primary": int(row.recent_primary),
            "recent_reference": int(row.recent_reference),
            "top_tags": top_tags.get(row.arxiv_id, []),
        })
    return {
        "schema_version": 1,
        "as_of": as_of,
        "window_days": HOT_WINDOW_DAYS,
        "papers": papers,
    }


# ---------- orchestration ----------


def run(top_n: int = DEFAULT_TOP_N) -> dict:
    mentions = load_mentions()
    print(f"[paper_trends] {len(mentions):,} mentions / {mentions['arxiv_id'].nunique() if not mentions.empty else 0} papers from {PAPERS_DB}")

    velocity = paper_velocity(mentions)
    topics = paper_topics(mentions)
    titles = load_paper_titles()
    hot = hot_papers(velocity, topics, titles, top_n=top_n)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    velocity.to_parquet(OUT_DIR / "paper_velocity.parquet", index=False)
    topics.to_parquet(OUT_DIR / "paper_topics.parquet", index=False)
    hot_path = OUT_DIR / f"hot_papers-{hot['as_of']}.json" if hot["as_of"] else OUT_DIR / "hot_papers-empty.json"
    hot_path.write_text(
        json.dumps(hot, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8",
    )

    print(f"[paper_trends] velocity rows: {len(velocity):,}  topics rows: {len(topics):,}")
    print(f"[paper_trends] hot papers (as_of {hot['as_of']}): {len(hot['papers'])} → {hot_path}")
    for p in hot["papers"][:5]:
        print(f"  {p['score']:+d}  {p['arxiv_id']}  {p['title'][:60]}")
    return {"velocity_rows": len(velocity), "topics_rows": len(topics), "hot": hot}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--top", type=int, default=DEFAULT_TOP_N, help="hot papers list size")
    args = parser.parse_args()
    run(top_n=args.top)


if __name__ == "__main__":
    main()
