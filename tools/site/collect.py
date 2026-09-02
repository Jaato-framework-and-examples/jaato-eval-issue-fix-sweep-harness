#!/usr/bin/env python3
"""Consolidate every archived sweep into one document the Pages site renders.

WHY THIS EXISTS AT ALL.  ``jaato_eval.cli report`` renders ONE results file
and pivots it on ``(task_id, profile_set)``.  The question this site answers
is a different one — "every model that has ever attempted issue #782, across
every run" — and no cross-file view exists upstream to reuse.

WHY IT DOES NOT IMPORT ``jaato_eval``.  Results are produced locally and
pushed; CI only publishes them.  Importing the framework would put
``jaato-sdk`` and ``jaato-eval`` git installs into the publishing path, and
make the published bytes a function of a sibling repo's HEAD.  So the
aggregation rules below are COPIED from ``jaato_eval/report.py`` rather than
imported, and the copy is deliberate — see the two rules under ``Cell``.

INPUT CONTRACT, and it is the whole contract:

    sweeps/<owner>__<repo>/<issue>/<run>.jsonl

Every file under that shape is a valid run — the corpus is curated by
EXCLUSION (sweeps/README.md keeps 2 of 23 runs; the other 21 are absent, not
flagged), so there is no trust field to read and none to write.  The path is
the only metadata: ``<owner>__<repo>`` unslugs to the repo (the inverse of
checkout_worktree.py's ``_slug``) and ``<issue>`` is the issue number, which
together give the issue URL without a network call at build time.

A file that does not parse, or a row missing a key this document is built
from, ABORTS the build.  A sweep that cannot be read must not quietly vanish
from the page — same reasoning as acceptance.sh refusing to grade an issue it
has no criteria for.

RANGES, NOT MEANS.  Every figure is reported as [min, max] with the number of
observations behind it.  A mean would claim a central tendency across runs
that may have executed under different framework versions; a range claims
only observed extent, which is what the corpus supports.  Two consequences
this module is careful about:

* The POPULATION DIFFERS BY FIGURE.  Cost, duration, turns, tokens and nudges
  are per ARM — each arm produced its own value.  Pass rate is per RUN: an
  arm's pass rate is 0 or 1, which is not a rate.  Each range carries its own
  ``n`` so the page can never imply one population for both.
* ``n`` COUNTS CONTRIBUTING OBSERVATIONS.  A run in which every arm was
  BLOCKED contributes no pass rate at all, and a provider that reported no
  cost contributes no cost.  ``n`` is what was observed; ``of`` is what
  existed.  The gap between them is itself information.

Rendering decisions are NOT made here.  ``n == 1`` is emitted as a range like
any other and the page decides to print it as a bare value; this module's job
is to be the arithmetic, once, in a place that can be tested.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# The three verdict states, copied from jaato_eval/verdict.py rather than
# imported (see the module docstring on why nothing here imports the
# framework).  A state outside this set is a corpus this code was not written
# for, and is refused rather than counted as BLOCKED by default.
PASS, FAIL, BLOCKED = "PASS", "FAIL", "BLOCKED"
STATES = {PASS, FAIL, BLOCKED}


class CorpusError(Exception):
    """A results file this build refuses to publish rather than misreport."""


# ── the path IS the metadata ─────────────────────────────────────────────

def unslug_repo(slug: str) -> str:
    """``Owner__name`` -> ``Owner/name``, the inverse of checkout_worktree's
    ``_slug`` (``repo.replace("/", "__")``).

    Refuses anything that does not split into exactly two parts rather than
    guessing: a name containing ``__`` would make the mapping ambiguous, and
    an issue URL built from a guess points somewhere real and wrong.
    """
    parts = slug.split("__")
    if len(parts) != 2 or not all(parts):
        raise CorpusError(
            f"directory {slug!r} is not <owner>__<repo> — the issue URL is "
            "derived from it, so an ambiguous name cannot be resolved by "
            "guessing")
    return "/".join(parts)


@dataclass(frozen=True)
class RunFile:
    """One results file, fully identified by where it sits."""
    repo: str
    issue: str
    run: str
    path: Path


def discover(sweeps_dir: Path) -> List[RunFile]:
    """Every ``<owner>__<repo>/<issue>/<run>.jsonl`` under ``sweeps_dir``.

    A ``.jsonl`` at any other depth is an ERROR.  Skipping it silently is how
    a sweep gets dropped from the page by a typo in a directory name, and the
    page would look complete while missing a run.
    """
    if not sweeps_dir.is_dir():
        raise CorpusError(f"no sweeps directory at {sweeps_dir}")

    found: List[RunFile] = []
    for path in sorted(sweeps_dir.rglob("*.jsonl")):
        rel = path.relative_to(sweeps_dir)
        if len(rel.parts) != 3:
            raise CorpusError(
                f"{rel} is not at <owner>__<repo>/<issue>/<run>.jsonl — the "
                "path is the only metadata a run carries, so a file outside "
                "that shape cannot be attributed to an issue")
        repo_slug, issue, filename = rel.parts
        found.append(RunFile(repo=unslug_repo(repo_slug), issue=issue,
                             run=filename[:-len(".jsonl")], path=path))
    if not found:
        raise CorpusError(f"no .jsonl results under {sweeps_dir}")
    return found


def load_rows(run: RunFile) -> List[Dict[str, Any]]:
    """Parse one results file, refusing anything this document cannot render."""
    rows: List[Dict[str, Any]] = []
    for lineno, line in enumerate(run.path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CorpusError(f"{run.path}:{lineno}: not valid JSON: {exc}") from exc
        for key in ("arm_id", "profile_set", "state"):
            if not row.get(key):
                raise CorpusError(f"{run.path}:{lineno}: row has no {key!r}")
        if row["state"] not in STATES:
            raise CorpusError(
                f"{run.path}:{lineno}: unknown state {row['state']!r} "
                f"(expected one of {sorted(STATES)})")
        rows.append(row)
    if not rows:
        raise CorpusError(f"{run.path}: no rows — an empty run cannot be published "
                          "as a run that happened")
    return rows


# ── ranges ───────────────────────────────────────────────────────────────

def build_range(observations: Sequence[Tuple[Optional[float], str]],
                of: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """[min, max] over ``(value, label)`` pairs, with what held each end.

    ``None`` values are DROPPED, not coerced.  A provider that reported no
    cost is not an arm that cost nothing (report.py's own note: a dash "does
    not mean free"), and a run where nothing was exercised has no pass rate
    rather than a pass rate of zero.

    Returns ``None`` when nothing was observed — which the page renders as
    "not observed", never as 0.
    """
    seen = [(float(v), label) for v, label in observations
            if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not seen:
        return None
    lo = min(seen, key=lambda pair: pair[0])
    hi = max(seen, key=lambda pair: pair[0])
    return {
        "min": lo[0], "min_by": lo[1],
        "max": hi[0], "max_by": hi[1],
        "n": len(seen),
        "of": len(observations) if of is None else of,
    }


# ── aggregation ──────────────────────────────────────────────────────────

@dataclass
class Cell:
    """Arms sharing one grouping key, and the tallies that survive pooling.

    Two rules copied verbatim from ``jaato_eval/report.py`` because they
    encode judgement rather than convenience:

    **BLOCKED is never in a denominator.**  Pass rate is PASS / (PASS + FAIL).
    An arm that did not run is not evidence about the configuration.

    **BLOCKED is always visible.**  It keeps its own count, because a cell
    showing 100% over two arms with eight blocked is a broken runner, not a
    result.
    """
    passed: int = 0
    failed: int = 0
    blocked: int = 0
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def add(self, row: Dict[str, Any]) -> None:
        state = row["state"]
        if state == PASS:
            self.passed += 1
        elif state == FAIL:
            self.failed += 1
        else:
            self.blocked += 1
        self.rows.append(row)

    @property
    def exercised(self) -> int:
        return self.passed + self.failed

    @property
    def pass_rate(self) -> Optional[float]:
        """``None`` when nothing was exercised — not ``0.0``.

        Zero says "it always failed"; the truth is "we never found out", and
        the two must not print the same.
        """
        return (self.passed / self.exercised) if self.exercised else None


def _usage(row: Dict[str, Any], key: str) -> Optional[float]:
    value = (row.get("usage") or {}).get(key)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _sum_present(values: Iterable[Optional[float]]) -> Optional[float]:
    """Total of the values actually reported, or ``None`` if none were.

    A total over a partly-unreported column is a floor, not a total — the
    page labels it with the same ``n`` the range carries, so a reader can see
    how much of the column it covers.
    """
    present = [v for v in values if v is not None]
    return sum(present) if present else None


#: The per-arm figures, as (json key, how to read it off a row).  Every one is
#: an arm-level observation, which is what makes them shareable between the
#: model level and the issue level: both are just different groupings of arms.
_ARM_FIGURES = (
    ("cost_usd", lambda r: _usage(r, "cost_usd")),
    ("tokens", lambda r: _usage(r, "spend_total_tokens")),
    ("duration_seconds", lambda r: r.get("duration_seconds")),
    ("turns", lambda r: r.get("turns")),
    ("completion_nudges", lambda r: r.get("completion_nudges")),
)


def _figures(rows: Sequence[Dict[str, Any]], label: str) -> Dict[str, Any]:
    """Per-arm ranges for a group of arms, each labelled by ``label``.

    ``label`` names the FIELD that attributes an endpoint — ``arm_id`` when
    comparing arms of one model, ``profile_set`` when comparing models of one
    issue.  The endpoint attribution is the point: a 21x cost spread across an
    issue is about model pricing, and a range that cannot say which model held
    each end reads as variance in the task.
    """
    out: Dict[str, Any] = {}
    for key, read in _ARM_FIGURES:
        out[key] = build_range([(read(r), str(r.get(label, "?"))) for r in rows])
    out["cost_usd_total"] = _sum_present(_usage(r, "cost_usd") for r in rows)
    out["tokens_total"] = _sum_present(_usage(r, "spend_total_tokens") for r in rows)
    return out


def _pass_rate_range(per_run: Dict[str, Cell]) -> Optional[Dict[str, Any]]:
    """Pass rate ranges over RUNS, never over arms — an arm's is 0 or 1."""
    return build_range([(cell.pass_rate, run) for run, cell in sorted(per_run.items())],
                       of=len(per_run))


def _payloads(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """What is actually known about output agreement, without asserting it.

    Deliberately NOT a determinism percentage.  ``jaato_eval``'s ``det``
    counts DISTINCT hashes and skips arms that produced none, so run22's
    gemini25flash cell reports 100% "byte-identical across repeats" from a
    single observed payload, the other arm having died at max_tokens (jaato
    #798).  Until that has one definition, this reports the three counts a
    reader can draw their own conclusion from — and an absent payload is
    counted as absent, never as a matching one.
    """
    hashes = [r.get("payload_hash") for r in rows]
    produced = [h for h in hashes if h]
    return {
        "arms": len(hashes),
        "produced": len(produced),
        "absent": len(hashes) - len(produced),
        "distinct": len(set(produced)),
    }


def _distinct(rows: Sequence[Dict[str, Any]], *path: str) -> List[str]:
    """Sorted distinct values of a (possibly nested) field, nulls dropped.

    Emitted as a LIST rather than a single value because a profile set can
    legitimately be repointed between runs — a model rename, a newer SDK — and
    collapsing that to one value would hide the confound inside a range whose
    endpoints then came from different conditions.
    """
    values = set()
    for row in rows:
        cursor: Any = row
        for key in path:
            cursor = (cursor or {}).get(key) if isinstance(cursor, dict) else None
        if cursor:
            values.add(str(cursor))
    return sorted(values)


def _arm(row: Dict[str, Any], run: str) -> Dict[str, Any]:
    """One arm, flattened for the page's deepest level."""
    return {
        "arm_id": row["arm_id"],
        "run": run,
        "repeat": row.get("repeat"),
        "state": row["state"],
        "session_id": row.get("session_id"),
        "model": row.get("model"),
        "upstream_provider": row.get("upstream_provider"),
        "turns": row.get("turns"),
        "cost_usd": _usage(row, "cost_usd"),
        "tokens": _usage(row, "spend_total_tokens"),
        "duration_seconds": row.get("duration_seconds"),
        "finish_reason": row.get("finish_reason"),
        "native_finish_reason": row.get("native_finish_reason"),
        "payload_hash": row.get("payload_hash"),
        "completion_nudges": row.get("completion_nudges"),
        "budget_ceiling": row.get("budget_ceiling"),
        "pool_limits": row.get("pool_limits"),
        "sdk_version": (row.get("provenance") or {}).get("jaato_sdk_version"),
        # blocked_reason and error are DIFFERENT facts and both travel: the
        # first means there was nothing to grade, the second that the arm
        # produced evidence and ended badly anyway (jaato #773).
        "blocked_reason": row.get("blocked_reason"),
        "error": row.get("error"),
        "verdicts": [
            {"grader_id": v.get("grader_id"), "state": v.get("state"),
             "detail": v.get("detail"), "blocked_reason": v.get("blocked_reason")}
            for v in (row.get("verdicts") or [])
        ],
    }


def build(sweeps_dir: Path) -> Dict[str, Any]:
    """The whole document: issues -> models -> arms."""
    runs = discover(sweeps_dir)

    # (repo, issue) -> run -> rows.  Grouped by run first because pass rate is
    # a per-run figure at BOTH levels above it.
    corpus: Dict[Tuple[str, str], Dict[str, List[Dict[str, Any]]]] = defaultdict(dict)
    for run in runs:
        rows = load_rows(run)
        if run.run in corpus[(run.repo, run.issue)]:
            raise CorpusError(f"two files claim run {run.run!r} for issue {run.issue}")
        corpus[(run.repo, run.issue)][run.run] = rows

    issues = []
    for (repo, issue), by_run in sorted(corpus.items()):
        all_rows = [dict(row, _run=run) for run, rows in by_run.items() for row in rows]

        issue_cell = Cell()
        for row in all_rows:
            issue_cell.add(row)

        models = []
        for profile_set in sorted({r["profile_set"] for r in all_rows}):
            model_rows = [r for r in all_rows if r["profile_set"] == profile_set]
            model_cell = Cell()
            for row in model_rows:
                model_cell.add(row)

            per_run: Dict[str, Cell] = {}
            for row in model_rows:
                per_run.setdefault(row["_run"], Cell()).add(row)

            models.append({
                "profile_set": profile_set,
                "models": _distinct(model_rows, "model"),
                "providers": _distinct(model_rows, "provider"),
                "upstream_providers": _distinct(model_rows, "upstream_provider"),
                "sdk_versions": _distinct(model_rows, "provenance", "jaato_sdk_version"),
                "runs": sorted(per_run),
                "arms": len(model_rows),
                "passed": model_cell.passed,
                "failed": model_cell.failed,
                "blocked": model_cell.blocked,
                "pass_rate": _pass_rate_range(per_run),
                "payloads": _payloads(model_rows),
                **_figures(model_rows, "arm_id"),
                "arm_detail": [_arm(r, r["_run"]) for r in
                               sorted(model_rows, key=lambda r: (r["_run"], r["arm_id"]))],
            })

        # The issue's own pass-rate range is over runs, pooling every model in
        # that run — the same unit the model level uses, one grouping up.
        issue_per_run: Dict[str, Cell] = {}
        for row in all_rows:
            issue_per_run.setdefault(row["_run"], Cell()).add(row)

        issues.append({
            "issue": issue,
            "repo": repo,
            "url": f"https://github.com/{repo}/issues/{issue}",
            "runs": sorted(by_run),
            "arms": len(all_rows),
            "passed": issue_cell.passed,
            "failed": issue_cell.failed,
            "blocked": issue_cell.blocked,
            "pass_rate": _pass_rate_range(issue_per_run),
            "payloads": _payloads(all_rows),
            "sdk_versions": _distinct(all_rows, "provenance", "jaato_sdk_version"),
            # Endpoints attributed to the MODEL here, not the arm: at issue
            # level the question a spread raises is which model held each end.
            **_figures(all_rows, "profile_set"),
            "models": models,
        })

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "issues": issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sweeps", type=Path, default=Path("sweeps"),
                        help="corpus root (default: sweeps)")
    parser.add_argument("--out", type=Path, default=Path("site/data.json"),
                        help="where to write the consolidated document")
    args = parser.parse_args()

    # A refused corpus is an ordinary build failure, not a crash: exit 1 with
    # the reason on stderr, so CI shows the sentence rather than a traceback
    # whose last line happens to contain it.
    try:
        document = build(args.sweeps)
    except CorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")

    arms = sum(i["arms"] for i in document["issues"])
    models = {m["profile_set"] for i in document["issues"] for m in i["models"]}
    runs = sum(len(i["runs"]) for i in document["issues"])
    print(f"{args.out}: {len(document['issues'])} issues, {runs} runs, "
          f"{len(models)} models, {arms} arms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
