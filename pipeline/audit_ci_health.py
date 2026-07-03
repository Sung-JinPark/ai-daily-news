"""CI health detector — repeated step-reds behind green runs (E2).

AUDIT-2 made enhancement-layer steps ``continue-on-error``: a Voyage
or Anthropic hiccup no longer blocks the deploy, but the failure only
shows as a red step icon inside a green run — nobody opens the
Actions tab daily. This module closes that loop: it scans the last N
Daily runs via the GitHub API, counts per-step ``failure``
conclusions (KEY DISTINCTION: with continue-on-error the RUN is
success but the STEP conclusion stays "failure"), and flags any step
red R+ times.

Threshold rationale: R=3 over N=14 runs (~7 days at 2 runs/day).
The E1 retro scan found only 4 Daily runs in retention (young repo),
3 of them failed on 2 distinct steps — with so little history a
lower R would page on every transient; 3 repeats in a week means a
persistent condition, not a blip.

Run weekly from audit-weekly.yml (issue creation) or locally:

    python -m pipeline.audit_ci_health --dry-run
    python -m pipeline.audit_ci_health --runs-json mock.json  # unit test

Output contract (stdout JSON + optional issue body file): see
``detect()``. The issue step in audit-weekly.yml reuses the N5
create-or-comment pattern with label ``ci-health``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "Sung-JinPark/ai-daily-news")
API = f"https://api.github.com/repos/{REPO}"
N_RUNS = 14
R_THRESHOLD = 3
WORKFLOW_NAME = "Daily AI News Pipeline"
# Steps whose failure already fails the run loudly are excluded — this
# detector exists for the continue-on-error (silent) layer, but we
# count everything and let the threshold speak; no exclusion list to
# drift.


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ai-daily-news-ci-health",
        **({"Authorization": f"Bearer {os.environ['GH_TOKEN']}"}
           if os.environ.get("GH_TOKEN") else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_recent_daily_runs(n: int = N_RUNS) -> list[dict]:
    """Return the last n Daily runs as [{run_url, jobs:[{steps:[...]}]}]."""
    runs = _get(f"{API}/actions/runs?per_page=50").get("workflow_runs", [])
    daily = [r for r in runs if r["name"] == WORKFLOW_NAME][:n]
    out = []
    for r in daily:
        jobs = _get(r["jobs_url"])
        out.append({
            "html_url": r["html_url"],
            "created_at": r["created_at"],
            "conclusion": r["conclusion"],
            "jobs": jobs.get("jobs", []),
        })
    return out


def detect(runs: list[dict], threshold: int = R_THRESHOLD) -> dict:
    """Count per-step failures across runs; flag >= threshold.

    Deterministic: steps sorted by (count desc, name asc); run URLs in
    input order (newest first).
    """
    counts: dict[str, int] = defaultdict(int)
    urls: dict[str, list[str]] = defaultdict(list)
    for run in runs:
        seen_this_run: set[str] = set()
        for job in run.get("jobs", []):
            for step in job.get("steps", []):
                name = step.get("name", "")
                if step.get("conclusion") == "failure" and name not in seen_this_run:
                    seen_this_run.add(name)
                    counts[name] += 1
                    urls[name].append(run.get("html_url", ""))
    flagged = [
        {"step": name, "failures": counts[name], "window": len(runs),
         "run_urls": urls[name][:5]}
        for name in sorted(counts, key=lambda k: (-counts[k], k))
        if counts[name] >= threshold
    ]
    return {"window_runs": len(runs), "threshold": threshold,
            "all_step_failures": dict(sorted(counts.items())),
            "flagged": flagged}


def issue_body(result: dict) -> str:
    lines = [f"# [ci-health] 반복 스텝 실패 감지 ({result['window_runs']}런 창)", ""]
    for f in result["flagged"]:
        lines.append(f"## {f['step']} — {f['failures']}/{f['window']} red")
        lines += [f"- {u}" for u in f["run_urls"]]
        lines.append("")
    lines.append("_continue-on-error 스텝은 run이 green이어도 스텝 red가 남습니다 — "
                  "이 이슈는 그 반복을 감지한 것입니다 (E2)._")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--runs-json", type=Path, default=None,
                   help="mocked runs payload for unit testing (list of runs)")
    p.add_argument("--threshold", type=int, default=R_THRESHOLD)
    p.add_argument("--dry-run", action="store_true", help="print verdict only")
    p.add_argument("--issue-body-out", type=Path, default=None)
    a = p.parse_args()

    runs = (json.loads(a.runs_json.read_text(encoding="utf-8"))
            if a.runs_json else fetch_recent_daily_runs())
    result = detect(runs, a.threshold)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["flagged"] and a.issue_body_out and not a.dry_run:
        a.issue_body_out.parent.mkdir(parents=True, exist_ok=True)
        a.issue_body_out.write_text(issue_body(result), encoding="utf-8")
        print(f"[ci-health] issue body -> {a.issue_body_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
