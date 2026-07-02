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

### [DONE] Z1-follow-up — canonicalize pre-P2 community labels (found during Z3 verification)

The first weekly brief reported **47 community moves** between 2026-06-30 → 2026-07-01. Investigation: both snapshots were built from identical corpus state, but 06-30 was written by pre-P2 code (arbitrary Louvain label order) and 07-01 by post-P2 canonicalized code — the 47 "moves" were pure label permutation, the exact artifact P2 exists to kill. New `pipeline/research/backfill_community_labels.py` re-derives each old snapshot's communities **from that snapshot's own stored `network_edges.parquet`** (history preserved) and overwrites only when the partition signature (label-independent set of member-sets) is identical — genuine partition differences are reported, never overwritten. Verified: dry-run flagged exactly 47 nodes; real run relabeled with `partition unchanged`; rerun → 0 (idempotent); regenerated brief now reports **0 community moves**.

### [DONE] Z3 — weekly_brief.py: aggregation-only Korean weekly brief

- New `pipeline/research/weekly_brief.py`: 4 sections — entity velocity top/bottom (recent-7d vs prior-7d, anchored at latest data day), community diff between two newest snapshots (joined/left/moved with table), hot papers (Z2 functions called in-process), low-trust snapshot days (`trust_flag != ok`). Every number footnoted with its source file path. Korean, table-first.
- Monday gate lives **inside** the script (KST weekday check, `--force` bypass) so `run-research.bat` calls it unconditionally; also added a `paper_trends` step to the bat per the original Z2 spec ("매 실행 스냅샷"). Both steps non-fatal.
- Verified: no-flag run on Thursday correctly skipped; `--force` wrote `briefs/2026-W27.md`; spot-check of 3 numbers against raw sources PASSED (OpenAI 47/24, 저작권 24/5 from entity_mentions.jsonl; 2606.24625 recent=1 prior=0 from papers.db).

### [PARTIAL] Z4 — enrichment FAILED (external), notebook examples DONE

- **Enrichment: FAILED after 2 attempts** — attempt 1 (`--sleep 3`, all 466): every batch got HTTP 429 from export.arxiv.org; attempt 2 (`--sleep 6 --limit-enrich 100`): read timeouts. arXiv is rate-limiting or degraded right now; this is external, not a code defect. Per protocol, stopped after 2 failures. **Self-healing by design**: rows stay `enriched=0` and the scheduled `run-research.bat` (daily 20:00 KST, registered this session) retries automatically. `check-papers-db.mjs` still 12/12 PASS (papers 469 / mentions 469 / enriched 3).
- **Notebook examples: DONE** — appended a paper_trends section (markdown + code cell) to notebook 01 via a programmatic ipynb edit (no hand-editing). Cells load `paper_velocity.parquet` / `paper_topics.parquet`, display the newest `hot_papers-*.json`, and rank the most-attached tags. `jupyter nbconvert --execute` exit 0 with the new cells.
