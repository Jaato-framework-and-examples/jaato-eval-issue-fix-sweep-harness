#!/usr/bin/env bash
# Acceptance checks for ONE arm, as one deterministic script.
#
# Two callers share it, and that sharing is the point:
#
#   * the manifest's `script` graders, once per arm AFTER the session ends;
#   * the worker's completion processor, DURING the session — which blocks
#     signal_completion until these pass.
#
# One file, one verdict.  If the two drifted, an arm could satisfy the
# in-session gate and still be graded a failure.
#
# ── TWO TIERS, and the split is load-bearing ────────────────────────────
#
# `committed` and `compiles` are ISSUE-AGNOSTIC: they reference only
# .base_commit and the files this arm changed, so they are correct for any
# repo and any issue.
#
# `compliant` is ISSUE-SPECIFIC and is NOT defined here.  It is delegated to
# acceptance/<issue_id>.sh, because `issue_id` is a task INPUT: change it
# from 715 to 716 and a `compliant` check hardcoded here would silently grade
# the new issue against the old issue's criteria — reporting FAIL for arms
# that fixed the requested issue correctly, with a verdict naming a feature
# nobody asked for.  That is a wrong answer wearing a measurement's clothes.
#
# So a missing acceptance script FAILS LOUDLY rather than passing.  An
# ungradeable arm is not a passing arm.
#
# Usage:  bash acceptance.sh <check> [issue_id]      (from the WORKSPACE ROOT)
#         bash acceptance.sh --all  [issue_id]
#
# issue_id may also arrive as $ISSUE_ID.  --all prints every failure.
set -uo pipefail

WS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$WS/repo"
BASE_FILE="$WS/.base_commit"

# The interpreter MUST have the subject repo's dependencies.  Bare `python3`
# does not: it lacks pydantic, so `python3 -m shared.scaffold ...` exits with
# ModuleNotFoundError and a traceback.  A check that greps that traceback for
# a feature string reads a crash as a verdict — half passing vacuously (the
# string it looks for cannot appear in a traceback) and half failing
# spuriously.  That is exactly what happened here for every run until it was
# caught, so the interpreter is resolved explicitly and its absence is fatal.
PYBIN="${ACCEPTANCE_PYTHON:-$HOME/.local/share/jaato/venv/bin/python}"

check_committed() {
    [ -f "$BASE_FILE" ] || { echo "no .base_commit — the worktree prefetch did not run."; return 1; }
    local base head
    base="$(cat "$BASE_FILE")"
    head="$(git -C "$REPO" rev-parse HEAD 2>/dev/null)" || { echo "repo/ is not a git worktree."; return 1; }
    [ "$head" != "$base" ] || { echo "no commit on top of the base ($base)."; return 1; }
}

check_compiles() {
    [ -f "$BASE_FILE" ] || { echo "no .base_commit — cannot tell which files this arm changed."; return 1; }
    local changed out rc
    # `2>/dev/null` + "empty means nothing changed" made a git FAILURE
    # indistinguishable from a clean pass.  Observed 2026-09-01: this
    # returned exit 0 on an arm whose committed explain.py had an
    # unterminated string literal, because git produced no output five
    # seconds after the arm's final commit.  An error and a pass must not
    # share a representation — check git's status, not just its output.
    changed="$(git -C "$REPO" diff --name-only "$(cat "$BASE_FILE")"..HEAD -- '*.py' 2>&1)"; rc=$?
    if [ $rc -ne 0 ]; then
        echo "git could not list the changed files (exit $rc): ${changed//$'\n'/ }. The check could not run, so it cannot pass."
        return 1
    fi
    # Genuinely no Python touched — a real pass, distinct from the above.
    [ -n "$changed" ] || return 0
    # shellcheck disable=SC2086
    out="$(cd "$REPO" && "$PYBIN" -m py_compile $changed 2>&1)" \
        || { echo "a changed Python file does not parse: ${out//$'\n'/ }"; return 1; }
}

# Delegates to the issue's own criteria.  Never define behaviour here.
# Named `compliant` rather than `works` deliberately: `works` asserts an
# absolute property, which is the one claim this check cannot make — it does
# not know what "working" means until an issue_id says so.  Compliance is
# relational, so the name itself asks "with what?" — which is the argument.
check_compliant() {
    local issue="${1:-${ISSUE_ID:-}}"
    if [ -z "$issue" ]; then
        echo "no issue_id given — cannot select acceptance criteria. Pass it as \$2 or \$ISSUE_ID."
        return 1
    fi
    local script="$WS/acceptance/${issue}.sh"
    if [ ! -f "$script" ]; then
        echo "no acceptance criteria for issue ${issue} (expected ${script#$WS/}). An arm that cannot be graded is not a passing arm."
        return 1
    fi
    if [ ! -x "$PYBIN" ]; then
        echo "acceptance interpreter not found at ${PYBIN} — set \$ACCEPTANCE_PYTHON. Refusing to grade with an interpreter that may lack the repo's dependencies."
        return 1
    fi
    REPO="$REPO" PYBIN="$PYBIN" bash "$script"
}

CHECKS=(committed compiles compliant)

case "${1:---all}" in
    --all)
        rc=0
        for c in "${CHECKS[@]}"; do
            reason="$("check_$c" "${2:-}")" || { echo "$c: $reason"; rc=1; }
        done
        exit "$rc" ;;
    committed|compiles) "check_$1" ;;
    compliant)          check_compliant "${2:-}" ;;
    *) echo "unknown check: $1 (expected: ${CHECKS[*]}, or --all)" >&2; exit 2 ;;
esac
