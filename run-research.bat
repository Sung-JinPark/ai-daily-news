@echo off
REM Local trigger for the private research snapshot pipeline.
REM Never wire this into GitHub Actions — outputs are gitignored on
REM purpose and must not leak into the public repository.

setlocal
cd /d "%~dp0"

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
echo === GCS backup (no-op until credentials configured) ===
REM D-4: mirrors data\research_private\ to gs://%%GCS_BUCKET%%. Reads
REM GCS_BUCKET + GOOGLE_APPLICATION_CREDENTIALS from .env; prints a
REM skip notice and exits cleanly when they are absent. Non-fatal.
python -m pipeline.research.gcs_sync
if errorlevel 1 (
    echo [WARN] GCS backup step reported errors, continuing anyway.
)

echo.
echo Done. Artifacts written under data\research_private\ and data\papers_private\
endlocal
