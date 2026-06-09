@echo off
cd /d "%~dp0site"
start "" http://localhost:4321/ai-daily-news/
npm run dev
pause
