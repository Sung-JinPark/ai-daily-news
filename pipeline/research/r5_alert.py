"""R5 READY alert decision — surfacing only, NEVER analysis.

Reads ``notes/news_density.json`` (written by news_density_monitor) and decides whether
to raise the one-time "R5 READY" alert: the R5 gate is GREEN, an alert is pending, and no
alert has been issued yet. It **decides and formats** the alert; the workflow's
github-script step actually creates the GitHub issue and sets ``r5.alert_issued=true``
(dedup) after a successful create.

★UNATTENDED-ANALYSIS GUARD: this module reads state and emits an alert decision ONLY. It
imports no analysis module (h3_decide, changepoint, …) and runs no analysis. A human
reviews the issue and manually runs ``prompts/R5_cross_lingual.md``. The alert body names
only the gate and a file path — no concept names.

Usage:
    python -m pipeline.research.r5_alert                 # print decision (human)
    python -m pipeline.research.r5_alert --github-output # also write emit/title/body to $GITHUB_OUTPUT
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DENSITY_JSON = REPO / "data" / "research_private" / "notes" / "news_density.json"

TITLE = "R5 READY — review prompts/R5_cross_lingual.md (manual run)"


def decide(density: dict | None) -> dict:
    """Pure decision. emit iff R5 gate GREEN & alert pending & not yet issued."""
    r5 = (density or {}).get("r5", {}) or {}
    emit = (r5.get("status") == "GREEN"
            and bool(r5.get("alert_pending"))
            and not bool(r5.get("alert_issued")))
    body = (
        f"News-density gate `R5_cross_lingual` is GREEN (first ready {r5.get('first_ready_at')}). "
        "A HUMAN should review and run `data/research_private/prompts/R5_cross_lingual.md`. "
        "Do NOT auto-run analysis — findings require human review (review zip) before the paper."
    )
    return {"emit": bool(emit), "title": TITLE, "body": body,
            "r5_status": r5.get("status"), "first_ready_at": r5.get("first_ready_at")}


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _readiness_line(density: dict) -> str:
    out = []
    for name, g in (density.get("readiness") or {}).items():
        out.append(f"{name}={g.get('status')}(streak {g.get('streak_weeks')}/{g.get('weeks_required')})")
    return " ".join(out)


def _write_github_output(d: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"emit={'true' if d['emit'] else 'false'}\n")
        f.write(f"title={d['title']}\n")
        f.write("body<<__R5BODY__\n" + d["body"] + "\n__R5BODY__\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", default=str(DENSITY_JSON))
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args()
    density = _load(Path(args.json))
    d = decide(density)
    print("READINESS:", _readiness_line(density) or "(none)")
    print(f"R5 alert decision: emit={d['emit']} (status={d['r5_status']})")
    if d["emit"]:
        print("★ would raise issue:", d["title"])
    if args.github_output:
        _write_github_output(d)


if __name__ == "__main__":
    main()
