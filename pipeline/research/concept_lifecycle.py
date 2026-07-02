"""Method-agnostic lifecycle & direction features (RQ1-4 raw material).

Derives from the concept_mentions ledger (latest lexicon version):

  concept_spans     — first/last seen, active spans, dormancy gaps (RQ1/2)
  revival_events    — re-emergence after dormancy (RQ1)
  novelty_series    — monthly new vs went-dormant concept counts (RQ2)
  concept_pairs     — co-mention ledger: pairs matched in the SAME
                      source document (RQ3 raw)
  breadth_depth     — concept x month: new co-occurrence partners
                      (lateral) vs repeat-partner intensity (vertical) (RQ3)
  media_lag         — per concept: paper-first vs news-first day gap (RQ4)

Constants (rationale in CODEBOOK/decisions.md): ACTIVE_WINDOW_DAYS=7
(collection cadence), DORMANCY_MIN_DAYS=21 (3x active window; initial
value, revisit after 3+ months of accumulation).

HONESTY: with ~1 month of corpus most dormancy/revival outputs are
EMPTY — that is correct, not a bug. Minimum accumulation for
meaningful output: revival/dormancy >= 3 months; breadth_depth >= 2
months; media_lag usable now but sparse (paper abstracts still
draining). Empty-feature list is logged every run.

Outputs: research.db derived tables + parquet mirrors under
data/research_private/concept_features/ (deterministic: sorted,
no timestamps). Idempotent: derived tables are rebuilt atomically
(DROP+CREATE inside one transaction) from the ledger each run.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from pipeline.research.research_db import DB_FILE

OUT_DIR = Path("data") / "research_private" / "concept_features"
ACTIVE_WINDOW_DAYS = 7
DORMANCY_MIN_DAYS = 21


def _mentions(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT concept_id, source_type, source_id, day FROM latest_mentions "
        "ORDER BY concept_id, day, source_type, source_id", conn)


def concept_spans(m: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cid, sub in m.groupby("concept_id"):
        days = sorted(set(sub["day"]))
        d = pd.to_datetime(days)
        gaps = d.to_series().diff().dt.days.fillna(0)
        spans, dorms = [], []
        span_start = days[0]
        for i in range(1, len(days)):
            if gaps.iloc[i] > ACTIVE_WINDOW_DAYS:
                spans.append((span_start, days[i - 1]))
                if gaps.iloc[i] >= DORMANCY_MIN_DAYS:
                    dorms.append((days[i - 1], days[i], int(gaps.iloc[i])))
                span_start = days[i]
        spans.append((span_start, days[-1]))
        rows.append({
            "concept_id": cid, "first_seen": days[0], "last_seen": days[-1],
            "n_active_days": len(days), "n_spans": len(spans),
            "active_spans": ";".join(f"{a}..{b}" for a, b in spans),
            "n_dormancies": len(dorms),
            "dormancy_periods": ";".join(f"{a}..{b}({g}d)" for a, b, g in dorms),
        })
    return pd.DataFrame(rows).sort_values("concept_id").reset_index(drop=True)


def revival_events(m: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for cid, sub in m.groupby("concept_id"):
        days = sorted(set(sub["day"]))
        d = pd.to_datetime(days)
        for i in range(1, len(days)):
            gap = (d[i] - d[i - 1]).days
            if gap >= DORMANCY_MIN_DAYS:
                revived_types = sorted(set(sub[sub["day"] == days[i]]["source_type"]))
                rows.append({"concept_id": cid, "dormant_days": gap,
                             "revived_day": days[i],
                             "revived_source_type": "+".join(revived_types)})
    cols = ["concept_id", "dormant_days", "revived_day", "revived_source_type"]
    return (pd.DataFrame(rows, columns=cols)
            .sort_values(["revived_day", "concept_id"]).reset_index(drop=True))


def novelty_series(m: pd.DataFrame, spans: pd.DataFrame) -> pd.DataFrame:
    first = spans.assign(month=spans["first_seen"].str[:7]).groupby("month").size().rename("new_concepts")
    # went-dormant month = month of a span end that was followed by a dormancy
    dorm_months = []
    for _, r in spans.iterrows():
        for part in filter(None, r["dormancy_periods"].split(";")):
            dorm_months.append(part.split("..")[0][:7])
    dormant = pd.Series(dorm_months).value_counts().rename("went_dormant") if dorm_months else pd.Series(dtype=int, name="went_dormant")
    out = pd.concat([first, dormant], axis=1).fillna(0).astype(int).reset_index().rename(columns={"index": "month"})
    return out.sort_values("month").reset_index(drop=True)


def concept_pairs(m: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (st, sid), sub in m.groupby(["source_type", "source_id"]):
        cids = sorted(set(sub["concept_id"]))
        day = sub["day"].iloc[0]
        for i in range(len(cids)):
            for j in range(i + 1, len(cids)):
                rows.append({"concept_a": cids[i], "concept_b": cids[j],
                             "source_type": st, "source_id": sid, "day": day})
    cols = ["concept_a", "concept_b", "source_type", "source_id", "day"]
    return (pd.DataFrame(rows, columns=cols)
            .sort_values(cols).reset_index(drop=True))


def breadth_depth(pairs: pd.DataFrame) -> pd.DataFrame:
    if pairs.empty:
        return pd.DataFrame(columns=["concept_id", "month", "new_partners", "repeat_intensity"])
    long = pd.concat([
        pairs.rename(columns={"concept_a": "concept_id", "concept_b": "partner"}),
        pairs.rename(columns={"concept_b": "concept_id", "concept_a": "partner"}),
    ])[["concept_id", "partner", "day"]]
    long["month"] = long["day"].str[:7]
    rows = []
    for cid, sub in long.groupby("concept_id"):
        seen: set = set()
        for month, msub in sub.sort_values("month").groupby("month"):
            partners = set(msub["partner"])
            new = partners - seen
            repeat_hits = len(msub[msub["partner"].isin(seen)])
            rows.append({"concept_id": cid, "month": month,
                         "new_partners": len(new), "repeat_intensity": repeat_hits})
            seen |= partners
    return pd.DataFrame(rows).sort_values(["concept_id", "month"]).reset_index(drop=True)


def media_lag(m: pd.DataFrame) -> pd.DataFrame:
    first = m.groupby(["concept_id", "source_type"])["day"].min().unstack()
    for col in ("news", "paper"):
        if col not in first.columns:
            first[col] = None
    out = first.reset_index()[["concept_id", "news", "paper"]]
    out.columns = ["concept_id", "first_news_day", "first_paper_day"]
    lag = []
    for _, r in out.iterrows():
        if r["first_news_day"] and r["first_paper_day"]:
            lag.append((pd.to_datetime(r["first_news_day"]) - pd.to_datetime(r["first_paper_day"])).days)
        else:
            lag.append(None)
    out["news_minus_paper_days"] = lag
    return out.sort_values("concept_id").reset_index(drop=True)


TABLES = ["concept_spans", "revival_events", "novelty_series",
          "concept_pairs", "breadth_depth", "media_lag"]


def run() -> dict:
    conn = sqlite3.connect(DB_FILE)
    try:
        m = _mentions(conn)
        spans = concept_spans(m)
        outputs = {
            "concept_spans": spans,
            "revival_events": revival_events(m),
            "novelty_series": novelty_series(m, spans),
            "concept_pairs": concept_pairs(m),
        }
        outputs["breadth_depth"] = breadth_depth(outputs["concept_pairs"])
        outputs["media_lag"] = media_lag(m)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        with conn:  # single transaction: derived tables rebuilt atomically
            for name, df in outputs.items():
                conn.execute(f"DROP TABLE IF EXISTS {name}")
                df.to_sql(name, conn, index=False)
        for name, df in outputs.items():
            df.to_parquet(OUT_DIR / f"{name}.parquet", index=False)

        empty = [n for n, df in outputs.items() if df.empty]
        stats = {n: len(df) for n, df in outputs.items()}
        print(f"[lifecycle] rows: {stats}")
        print(f"[lifecycle] currently-empty features (expected with ~1mo corpus): {empty}")
        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    run()
