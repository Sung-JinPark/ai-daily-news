# BASELINE — 2026-08-31, chore/dead-code-20260831 @ 080a661

## Scope
- Python: `pipeline/**/*.py` EXCLUDING `pipeline/research/**` — 34 files, 8170 LOC
- Site: `site/src/**` — 57 tracked files (astro + ts)
- `pipeline/research/**` explicitly excluded this run (see CONTEXT.md)

## Python
- `python -m pytest tests/ -q` → **115 passed**, 0 failed, 4 warnings (statsmodels convergence warnings in test_trend_model.py, pre-existing/unrelated to dead code)
- No pre-existing failures.

## Site
- `npm run build` (in `site/`) → **1605 page(s) built**, exit 0, "Build Complete"
- No typecheck/lint command configured (ESLint not installed — not adding it this run, see CONTEXT.md P6 note)

## Dependency counts
- Python: 17 runtime deps declared in `pyproject.toml` `[project.dependencies]`
- Site: 5 deps in `site/package.json` (`astro`, `@astrojs/tailwind`, `@astrojs/rss`, `tailwindcss`, `fuse.js`)

## Reproduce
```
git checkout chore/dead-code-20260831
python -m pytest tests/ -q
cd site && npm run build
```
