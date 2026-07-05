@echo off
REM Local trigger for the private research snapshot pipeline.
REM Never wire this into GitHub Actions — outputs are gitignored on
REM purpose and must not leak into the public repository.
REM
REM Freshness (AUD-024): this script does NOT git pull — run it
REM directly and it consumes whatever is in the local tree. The
REM scheduled wrapper (run-research-scheduled.bat) pulls --ff-only
REM first; prefer it, or pull manually before ad-hoc runs.

setlocal
cd /d "%~dp0"

echo === Health check (pre-run staleness, AUTO-1) ===
REM Non-fatal report of how far the private ledger has drifted from the
REM public frontier BEFORE this run. exit 1 here only means it was stale
REM (a missed run) — this run is about to remedy it (backfill is idempotent).
python -m pipeline.research.health_check

echo.
echo === Research snapshot (private) ===
python -m pipeline.research.snapshot %*
if errorlevel 1 (
    echo [ERROR] Snapshot failed.
    exit /b 1
)

echo.
echo === Paper corpus (private, gitignored) ===
REM Auto-collect arXiv paper metadata from today's news into
REM data\papers_private\papers.db. Non-fatal: a failed enrich (offline,
REM arXiv 429, etc.) MUST NOT break the local session — the collect
REM step itself is offline (reads articles.json) and always succeeds.
REM --limit-enrich 50: nightly cap so the backlog drains gently within
REM arXiv's comfort zone (~470 pending clears in ~10 nightly runs);
REM run manually without the cap to drain faster.
python -m pipeline.collect_papers --sleep 3 --limit-enrich 50
if errorlevel 1 (
    echo [WARN] Paper corpus step reported errors, continuing anyway.
)

echo.
echo === Papers DB cold export (private) ===
REM C4-2: consistent SQLite checkpoint copy into research_private\
REM db_exports\ — gcs_sync walks that tree, so the nightly backup
REM includes the export automatically. Non-fatal.
python -m pipeline.research.export_papers_db
if errorlevel 1 (
    echo [WARN] Papers DB export reported errors, continuing anyway.
)

echo.
echo === Paper trends (private, offline) ===
REM Z2 join layer: paper velocity/topics/hot list from papers.db +
REM article tags. Pure aggregation, no network. Non-fatal.
python -m pipeline.research.paper_trends
if errorlevel 1 (
    echo [WARN] Paper trends step reported errors, continuing anyway.
)

echo.
echo === Weekly brief (Mondays only, self-gated) ===
REM Z3: aggregation-only Korean brief. The script itself skips unless
REM it's Monday KST, so calling it unconditionally is safe. Non-fatal.
python -m pipeline.research.weekly_brief
if errorlevel 1 (
    echo [WARN] Weekly brief step reported errors, continuing anyway.
)

echo.
echo === Lexicon candidates (1st of month only, self-gated) ===
REM RDB-4: deterministic emergent-term candidates for the private
REM lexicon. Script skips unless it's the 1st (KST). Non-fatal.
python -m pipeline.research.lexicon_candidates
if errorlevel 1 (
    echo [WARN] Lexicon candidates step reported errors, continuing anyway.
)

echo.
echo === Concept ledger + monthly insight (private) ===
REM RDB-2/6: incremental concept extraction (idempotent upserts pick
REM up new days and newly enriched abstracts) + monthly insight note
REM (1st of month KST, self-gated). Non-fatal.
REM F1: EN corpus ledger from committed bodies.jsonl (+raw teaser days).
python -m pipeline.research.en_corpus --backfill
if errorlevel 1 (
    echo [WARN] EN corpus step reported errors, continuing anyway.
)
REM Full backfill is idempotent and structurally covers F4's
REM late-arriving-corpus window (superset of any N-day rescan).
python -m pipeline.research.concept_extract --backfill
if errorlevel 1 (
    echo [WARN] Concept extract step reported errors, continuing anyway.
)
python -m pipeline.research.monthly_insight
if errorlevel 1 (
    echo [WARN] Monthly insight step reported errors, continuing anyway.
)

echo.
echo === Public stats export (sanitized aggregates) ===
REM Writes data\research_stats.json (git-tracked) for the site's
REM /stats page. Aggregates only - a hard guard aborts if any lexicon
REM term leaks into the payload. Non-fatal.
python -m pipeline.research.export_public_stats
if errorlevel 1 (
    echo [WARN] Public stats export reported errors, continuing anyway.
)

echo.
echo === Local dashboard (private HTML) ===
REM Stats view over papers.db + research.db — the private replacement
REM for the removed public /research pages. Non-fatal.
python -m pipeline.research.dashboard
if errorlevel 1 (
    echo [WARN] Dashboard step reported errors, continuing anyway.
)

echo.
echo === GCS backup (no-op until credentials configured) ===
REM D-4: mirrors data\research_private\ to gs://%%GCS_BUCKET%%. Reads
REM GCS_BUCKET + GOOGLE_APPLICATION_CREDENTIALS from .env; prints a
REM skip notice and exits cleanly when they are absent. Non-fatal.
python -m pipeline.research.gcs_sync
if errorlevel 1 (
    echo [WARN] GCS backup step reported errors, continuing anyway.
)

echo.
echo === Heartbeat (AUTO-1) ===
REM Stamp the full-run heartbeat so the staleness guard knows the run
REM reached completion (data\research_private\health\last_success.json).
python -m pipeline.research.health_check --stamp-success

echo.
echo Done. Artifacts written under data\research_private\ and data\papers_private\
endlocal
