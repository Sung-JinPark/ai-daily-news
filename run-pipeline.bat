@echo off
cd /d "%~dp0"
echo === collect ===
python -m pipeline.collect
echo === dedupe ===
python -m pipeline.dedupe
echo === summarize (Haiku API call) ===
python -m pipeline.summarize
echo === rank ===
python -m pipeline.rank
echo === trending ===
python -m pipeline.trending
echo === themes (cross-day narratives) ===
python -m pipeline.themes
echo === predictions (extract + resolve) ===
python -m pipeline.predict_extract
echo === model facts (per-model comparison table) ===
python -m pipeline.model_facts
echo === index ===
python -m pipeline.index_latest
echo.
echo Done. Data updated in data\YYYY-MM-DD\
pause
