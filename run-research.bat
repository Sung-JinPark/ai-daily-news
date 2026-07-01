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
python -m pipeline.collect_papers --sleep 3
if errorlevel 1 (
    echo [WARN] Paper corpus step reported errors, continuing anyway.
)

echo.
echo Done. Artifacts written under data\research_private\ and data\papers_private\
endlocal
