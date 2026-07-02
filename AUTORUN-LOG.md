# Autonomous research-layer run log (cycle 2)

- **Started:** 2026-07-02T08:54+09:00 (KST)
- **Branch:** `auto/research-20260702-0854` (base: `main` @ `6defa0b`)
- **Budget:** ~5 hours
- **Rules:** cycle-1 protocol in full (local commits only, no push/merge, leak gate every commit, 1 task = 1 commit, 2-fail skip, STOP on completion) + cycle-2 reinforcements: (8) this log is append-only — new entries go at the END of the file only; (9) review-package diffs saved UTF-8 via `git show <hash> --output=<file>`; (10) allowed surfaces are `pipeline/research/`, `pipeline/collect_papers.py`, `data/*_private/`, `research/notebooks/`, `run-*.bat` only — no public site or public data changes.

## Queue

- [ ] Z1 — trust_flag backward-compat + retroactive backfill (derive from stored nodes/edges, NOT snapshot re-run)
- [ ] Z2 — paper_trends.py: papers.db ↔ news tags join layer (velocity / topics / hot_papers)
- [ ] Z3 — weekly_brief.py: aggregation-only Korean weekly brief
- [ ] Z4 — finish arXiv enrichment backlog + notebook example cells

## Progress

### [DONE] Z1 — trust_flag backward-compat + retroactive backfill

- New `pipeline/research/backfill_trust_flag.py` derives the flag from each snapshot's **stored** nodes/edges (thresholds imported from `network_metrics` — cannot drift). Deliberately does NOT re-run snapshots for past days: `snapshot.py` always reads the full current corpus regardless of `--day`, so a re-run would falsify history. This also auto-satisfies the "no field other than trust_flag changes" requirement.
- `report.py` Δ section now shows `- trust: <today> (prior: <prior>)` via `.get(..., "unknown")` — tolerant of pre-P2 snapshots.
- Notebook 02 Δ cell filters to numeric keys before subtraction (string trust_flag would have broken it) and prints trust separately.
- Verified: dry-run → 1 candidate (2026-06-30); real run added `trust_flag=ok`; second run 0 backfilled (idempotent). Installed matplotlib 3.11.0; `jupyter nbconvert --execute` on both notebooks exit 0. Regenerated 2026-07-01 delta report renders the trust line.

### [DONE] Z2 — paper_trends.py: papers.db ↔ news tags join layer

- New `pipeline/research/paper_trends.py`: `paper_velocity` (gap-aware `pd.date_range` reindex per paper, first-day velocity = count, matching trend_metrics), `paper_topics` (paper_mentions.article_id ↔ articles.json tags → frequency vector, sorted (arxiv_id, count desc, tag)), `hot_papers` (score = last-7d − prior-7d mentions, anchored at **latest data day** not wall-clock, explicit 3-key ordering score desc / recent desc / arxiv_id asc).
- Outputs under `data/research_private/paper_trends/`: `paper_velocity.parquet` (12,663 rows = 469 papers × 27 calendar days), `paper_topics.parquet` (772 rows), `hot_papers-2026-06-30.json` (10 papers). No timestamps inside outputs.
- Verified determinism: run twice → `DataFrame.equals=True` AND byte-identical parquet + JSON for all three artifacts. Current corpus has each paper mentioned exactly once (arXiv feed one-shots), so hot scores are all +1 with visible arxiv_id-ascending tiebreak — correct per spec; scores will differentiate once cross-source paper mentions accumulate.
