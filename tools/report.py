#!/usr/bin/env python3
"""Render a sweep's results as the per-arm HTML report (and optionally PDF).

A WRAPPER, deliberately, not a copy.  The renderer is
``jaato_eval.report_html``; vendoring it here would fork on the framework's
next change and this repo would quietly render an old layout.

WHEN TO RUN IT
  After a sweep, before reading verdicts.  The markdown pivot the CLI already
  prints answers "which configuration won"; this answers "what happened to arm
  3", which is the question a FAIL actually raises.

  It is also the join onto the provider's own record: the session id column is
  what OpenRouter's console groups by, so a row here leads straight to that
  arm's generations, upstream provider and per-request cost -- which is how a
  MALFORMED_FUNCTION_CALL was diagnosed when the framework's own error said
  only "Provider returned an error".

EXIT CODE
  The verdict, passed straight through from the CLI: 0 all passed, 1 some arm
  FAILed, 2 some arm was BLOCKED.  A CI wrapper can act on it.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results", help="results JSONL from a sweep")
    ap.add_argument("--html", default="report.html", help="output HTML path")
    ap.add_argument("--pdf", help="also render PDF (needs jaato-eval[report])")
    args = ap.parse_args()

    src = Path(args.results)
    if not src.is_file():
        print(f"no such results file: {src}", file=sys.stderr)
        return 2

    cmd = [sys.executable, "-m", "jaato_eval.cli", "report", str(src),
           "--html", args.html]
    if args.pdf:
        cmd += ["--pdf", args.pdf]
    # --pdf without the optional renderer exits 2 and writes nothing; that is
    # deliberate upstream behaviour, so pass the code straight through.
    return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main())
