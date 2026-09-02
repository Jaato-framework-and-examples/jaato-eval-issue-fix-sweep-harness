#!/usr/bin/env python3
"""Render a sweep's results as the per-arm HTML report (and optionally PDF).

A WRAPPER, deliberately, not a copy.  The renderer is
``jaato_eval.report_html``; vendoring it here would fork on the framework's
first change and this repo would quietly render an old layout from stale
columns.  What lives here is the harness-specific part: where the results are,
and the backfill below.

WHEN TO RUN IT
  After a sweep, before reading verdicts.  The markdown pivot the CLI already
  prints answers "which configuration won"; this answers "what happened to arm
  3", which is the question a FAIL actually raises.

  It is also the join onto the provider's own record: the session id column is
  what OpenRouter's console groups by, so a row here leads straight to the
  generations, upstream provider and per-request cost for that arm.

BACKFILL
  Results written before jaato #777 carry none of the per-arm columns — no
  session id, model, upstream provider or nudge count — so the report renders
  them blank.  ``--backfill`` reconstructs what is recoverable from the kept
  workspaces: the session id from the log filename, the nudge count by
  counting COMPLETION_NUDGE lines.  It cannot invent the upstream provider,
  which only the gateway knows.

  Backfilled values are real facts gathered by hand, not estimates — but they
  are gathered from disk, so an arm whose workspace was not kept
  (``--keep-workspaces``) stays blank.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def _backfill(rows: list[dict], workspaces: Path) -> int:
    """Fill session_id and nudges from kept workspaces.  Returns rows touched."""
    touched = 0
    for row in rows:
        if row.get("session_id"):
            continue
        arm = row["arm_id"].split("@")[1].replace("#", "_")
        # workspaces may have been renamed aside between runs; take the newest
        candidates = sorted(glob.glob(str(workspaces / f"*{arm}")))
        if not candidates:
            continue
        logs = sorted(glob.glob(os.path.join(candidates[-1], ".jaato/logs/session_*.log")))
        if not logs:
            continue
        m = re.search(r"session_(\d{8}_\d{6})_", os.path.basename(logs[0]))
        if m:
            row["session_id"] = m.group(1)
        with open(logs[0], errors="ignore") as fh:
            row["nudges"] = sum(1 for line in fh if "COMPLETION_NUDGE" in line)
        touched += 1
    return touched


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="results JSONL from a sweep")
    ap.add_argument("--html", default="report.html", help="output HTML path")
    ap.add_argument("--pdf", help="also render PDF (needs jaato-eval[report])")
    ap.add_argument("--backfill", action="store_true",
                    help="reconstruct per-arm columns for pre-#777 results")
    ap.add_argument("--workspaces", default=".jaato-eval-workspaces",
                    help="where kept arm workspaces live (for --backfill)")
    args = ap.parse_args()

    src = Path(args.results)
    if not src.is_file():
        print(f"no such results file: {src}", file=sys.stderr)
        return 2

    if args.backfill:
        rows = [json.loads(line) for line in src.read_text().splitlines() if line.strip()]
        n = _backfill(rows, Path(args.workspaces))
        src = src.with_suffix(".backfilled.jsonl")
        src.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"backfilled {n} arm(s) -> {src}", file=sys.stderr)

    cmd = [sys.executable, "-m", "jaato_eval.cli", "report", str(src), "--html", args.html]
    if args.pdf:
        cmd += ["--pdf", args.pdf]
    # --pdf without the optional renderer exits 2 and writes nothing; that is
    # deliberate upstream behaviour, so pass the code straight through.
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
