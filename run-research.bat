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
echo Done. Artifacts written under data\research_private\snapshots\
endlocal
