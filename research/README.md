# AI Trend Micro-Dynamics — Research Corpus

This folder is the paper's **methodology + reproducibility** home. Analysis code, definitions, notes, and notebooks live here so that the paper's raw output can be re-derived by any reader who clones the public repo. The actual accumulated data lives in `data/research_private/` (gitignored) and stays private until publication.

## Paper direction

**Working title**: *Micro-dynamics of AI industry attention: velocity, acceleration, and network evolution in the daily news cycle*

**Corpus**: this repository's `data/aggregates/` streams (entity mentions, co-occurrence pairs, source health) accumulated by an automated daily scraper across ~20 sources (TechCrunch, arXiv cs.AI/LG/CL, OpenAI/Anthropic/DeepMind blogs, HN AI filter, etc.). Article-level LLM-summarized in Korean, tagged from a controlled vocabulary of models · labs · themes.

## Three working hypotheses

1. **H1 (Micro-velocity)** — Entity-level attention (daily mention counts) exhibits sharp asymmetric bursts around discrete events (model releases, funding, regulation) that are quantifiable as signed velocity and detectable before mainstream aggregators register them.

2. **H2 (Acceleration precedes commentary)** — For frontier models, mention *acceleration* peaks 1–3 days before the same entity's *volume* peak, i.e. rate-of-change is a leading indicator of narrative crystallization.

3. **H3 (Network fragmentation)** — The co-mention graph exhibits recurring community-restructuring events: as new frontier models launch, the model × lab × technique triangle re-partitions into new modularity clusters within ~2 weeks, then stabilizes.

## Data dictionary (public streams)

| Stream | Location | Row schema | Purpose |
|---|---|---|---|
| Entity mentions | `data/aggregates/entity_mentions.jsonl` | `{day, entity_type, entity, article_id, cluster_id, source_id, importance_score, category}` | Time series of who/what was talked about |
| Tag co-occurrence | `data/aggregates/tag_cooccurrence.jsonl` | `{day, tag_a, tag_b, cluster_id, article_id, category}` | Raw edge list for the tag graph |
| Entity co-occurrence | `data/aggregates/entity_cooccurrence.jsonl` | Same shape, typed (`entity_a_type`, `entity_b_type`) | Model × lab / lab × lab / model × tag edges |
| Source health | `data/aggregates/source_health.jsonl` | `{day, source_id, items, capped, error}` | Coverage completeness auditing |
| Merge events | `data/aggregates/merge_events.jsonl` | `{day, cluster_id, kind, hamming, gap_days}` | SimHash cluster-merge diagnostics |
| Manifest | `data/aggregates/manifest.json` | schema_version + sha256 + line counts | Version compatibility for downstream consumers |

Full schema version and integrity hashes live in `data/aggregates/manifest.json` per Y2.

### Schema versioning contract (Y2)

Every `data/aggregates/*.jsonl` row set has a `schema_version` recorded in the sidecar manifest — currently `1` for all five streams. The row bodies themselves stay clean (no per-line version noise); the manifest is the single source of truth for what shape a consumer should expect. Producers (`pipeline.collect` for `source_health`, `pipeline.dedupe` for `merge_events`, `pipeline.entity_index` for the three entity streams) call `pipeline.aggregates_manifest.update_files` at the tail of each run so `sha256`, `lines`, and `bytes` stay honest. Consumers (`pipeline.build_db._check_aggregates_manifest`, plus any external notebook) compare the recorded `schema_version` to what they were built for and log a **warning, never a failure** if they drift — that keeps partial deploys (pipeline advances first, downstream lags) buildable while still surfacing the drift. Bump `STREAM_SCHEMA_VERSIONS` in `pipeline/aggregates_manifest.py` whenever a row-level field shape actually changes, and bump the matching `EXPECTED_AGGREGATE_SCHEMA` in `pipeline/build_db.py` in the same PR.

## Derived (private) artifacts

Not in git; generated locally under `data/research_private/snapshots/YYYY-MM-DD/`:

- `entity_mentions.parquet` — the full mention log through the snapshot day
- `entity_velocity.parquet` — day-over-day change per entity
- `entity_acceleration.parquet` — day-over-day change of the change
- `network_edges.parquet` — co-mention edges snapshot
- `network_metrics.json` — density, avg clustering, community count, etc.
- `report.md` — auto-generated Δ report describing what moved that day

Rolled up in `data/research_private/timeseries/{daily,weekly,monthly}.parquet` for direct pandas / R consumption.

## How to reproduce

```
# 1. Ensure the public daily pipeline has run for the days of interest.
#    Aggregates live under data/aggregates/*.jsonl (git-tracked).

# 2. Install analysis dependencies once (Phase 2 onward will pin these).
pip install pandas pyarrow networkx

# 3. Generate the snapshot for today (or a back-date via --day YYYY-MM-DD).
python -m pipeline.research.snapshot

# 4. Open a notebook in research/notebooks/ and point it at
#    data/research_private/. Everything downstream flows from there.
```

## Related documents

- `methodology.md` — mathematical definitions of every metric emitted
- `notes/` — chronological research journal; each session adds one dated entry
- `notebooks/` — starter analyses used to draft the paper's figures

## Public/private boundary contract (D-1)

The public `/research/*` pages and the private paper layer draw from the
same corpus, so every addition to a public page must be checked against
this contract:

- **Public-allowed**: coarse aggregates recomputable at build time from
  the git-tracked `data/aggregates/*.jsonl` streams — raw mention
  counts, rolling 7-day-window count/weight diffs ("주간 변화"), typed
  co-occurrence weights. Rationale: these are trivial rearrangements of
  already-public data and reveal none of the paper's contributions.
- **Private-only until publication**: EMA-smoothed daily velocity and
  acceleration, Kleinberg burst z-scores, Louvain communities and their
  evolution (hypothesis H3), and every paper-level trend derived from
  the private `papers.db`.
- When adding a new metric to a public page, compare it against this
  list first; if it resembles anything in the private-only list at
  *daily* granularity or with *smoothing*, keep it private.

## What NOT to modify from this folder

- `data/aggregates/*` — raw streams owned by the daily pipeline; treat as read-only
- `site/src/pages/research/*.astro` — public site views; deliberately kept coarser than paper analyses so nothing pre-publication leaks (see the boundary contract above)
