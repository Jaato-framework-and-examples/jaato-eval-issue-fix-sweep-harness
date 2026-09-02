#!/usr/bin/env bash
# Acceptance criteria for issue #715 — "jaato-scaffold explain does not
# expose user-facing TUI commands".
#
# Selected by acceptance.sh from the task's `issue_id` input.  Receives
# $REPO (the arm's worktree) and $PYBIN (an interpreter that HAS the repo's
# dependencies — bare python3 does not, and grepping its ModuleNotFoundError
# traceback for a feature string is how this check silently tested nothing).
#
# #715 asked for two things.  Both are checked, and both report distinctly,
# because an arm that lands one and not the other is a different result from
# an arm that landed neither.
set -uo pipefail
PP="$REPO/jaato-server:$REPO/jaato-sdk"

# Every invocation must actually RUN.  A crash is not a verdict, so the
# import is proven before anything greps output.
if ! (cd "$REPO" && PYTHONPATH="$PP" "$PYBIN" -c "import shared.scaffold.explain" >/dev/null 2>&1); then
    echo "cannot import shared.scaffold from the arm's tree with ${PYBIN##*/} — the check cannot run, so it cannot pass."
    exit 1
fi

fail=0

# 1. a top-level `commands` scope, reachable from the dispatcher
out="$(cd "$REPO" && PYTHONPATH="$PP" "$PYBIN" -m shared.scaffold explain commands 2>&1)"
if echo "$out" | grep -qi "unknown explain scope"; then
    echo "\`explain commands\` answers 'unknown explain scope' — the scope is not wired into the dispatcher."
    fail=1
fi

# 2. the per-plugin block naming that plugin's user-facing commands
plug="$(cd "$REPO" && PYTHONPATH="$PP" "$PYBIN" -m shared.scaffold explain plugin permission 2>&1)"
if ! echo "$plug" | grep -qi "permissions"; then
    echo "\`explain plugin permission\` does not name the plugin's user-facing 'permissions' command."
    fail=1
fi

exit $fail
