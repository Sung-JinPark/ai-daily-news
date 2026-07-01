@echo off
REM Scheduled wrapper for the private research pipeline.
REM Registered in Windows Task Scheduler (daily, KST 20:00 — after the
REM evening CI run finishes ~19:00 and its data commit lands on main).
REM
REM Freshness: the research snapshot reads git-tracked data under
REM data\, so pull first. --ff-only keeps this safe: if the local
REM tree has diverged or is mid-work, the pull is skipped and the
REM snapshot runs on whatever is local (idempotent per day, so the
REM next successful pull+run self-heals).
REM
REM Never wire this into GitHub Actions — outputs are gitignored on
REM purpose and must not leak into the public repository.

setlocal
cd /d "%~dp0"

set LOGDIR=data\research_private\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOGFILE=%LOGDIR%\scheduled-%date:~0,4%%date:~5,2%%date:~8,2%.log

echo ==== scheduled run %date% %time% ==== >> "%LOGFILE%"

git pull --ff-only >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [WARN] git pull --ff-only failed - running on local data. >> "%LOGFILE%"
)

call run-research.bat >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [ERROR] run-research.bat failed - see above. >> "%LOGFILE%"
    endlocal
    exit /b 1
)

echo ==== done %date% %time% ==== >> "%LOGFILE%"
endlocal
