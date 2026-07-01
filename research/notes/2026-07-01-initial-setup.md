# 2026-07-01 — Research folder scaffolded

**Session context**: 46-commit day, from ranking bugfix through F/M/R/P/N/X/Y/Z/ZE. Semantic infrastructure just landed (ZE1–ZE4). Research folder created as the paper's methodology home; private data will accumulate under `data/research_private/` (gitignored) starting from Phase 2.

## What's ready as of today

- Public daily pipeline produces `data/aggregates/*.jsonl` (5 streams, sha256-tracked via `manifest.json` since Y2).
- 28 days of archive · 1,786 articles · 1,747 clusters · 2,131 entity mentions · 834 typed entity co-occurrences.
- Semantic embeddings pipeline (ZE1) awaits `VOYAGE_API_KEY`; not required for velocity/network analyses but useful for later "topic drift" work.
- Public `/research/{evolution,network,completeness}` pages summarize aggregates at daily granularity; deliberately coarser than what this folder will produce so the site does not leak paper-grade signal.

## What the paper needs that the site cannot show

- Signed velocity + acceleration per entity per day (not just count sparklines)
- Rolling burst detection with per-entity baselines
- Full snapshot graphs with node-level centrality (site limited to top-22 radial)
- Community assignments over time
- Δ reports for narrative reconstruction

All of the above go under `data/research_private/snapshots/YYYY-MM-DD/`.

## Open questions to test in later notes

1. Does mention acceleration for a frontier model (say `Claude 4.7`) precede its own volume peak by 1–3 days as H2 predicts? — Needs a labelled release calendar to align against.
2. Do Louvain communities restructure within 14 days of a major model launch? — Needs at least 60 days of archive; revisit around 2026-08-30.
3. Are arXiv-heavy days vs. blog-heavy days materially different in velocity distributions? — Compare category mix.

## Immediate follow-up (Phase 2 items)

- Implement `pipeline/research/snapshot.py`
- Wire `pipeline/research/trend_metrics.py` with pandas
- Write `run-research.bat`
- Verify a first snapshot on today's aggregates
- Sanity check that `git status` shows nothing under `data/research_private/`
