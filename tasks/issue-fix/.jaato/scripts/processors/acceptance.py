"""Block the worker's completion until the fix actually passes acceptance.

Wired from ``profiles/_base_worker.yaml`` as::

    completion_processors:
      - script: scripts/processors/acceptance.py
        on_error: fail_completion

``validate`` runs after ``jsonschema.validate`` accepts the payload.  A
non-empty return list blocks ``signal_completion``: the framework hands the
agent back a ``validation_failed`` result carrying every string below, and
the agent retries inside its own ``max_turns``
(``shared/lifecycle_tools.py``).  So this IS the fix-until-it-passes loop,
and the error strings are written as instructions for the retry rather than
as a report for a human.

Why here rather than in the harness: the same loop built as an
eval-runner feature would re-prompt through a whole extra session
round-trip, would need a second retry budget alongside ``max_turns``, and
would only ever work for ``jaato_eval`` callers.  A profile-declared
processor costs no new framework code and works for any driver.

The checks themselves are NOT reimplemented here — they live in
``acceptance.sh`` in the arm's workspace, which the manifest's graders run
too.  One verdict for the in-session gate and the scoreboard; see the
header of that script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List
import subprocess

# The agent gets several goes at the fix, but a script that cannot finish
# is an environment fault, not a wrong answer — fail it loudly instead of
# hanging the arm until the sweep's own wall-clock budget aborts it.
_TIMEOUT_SECONDS = 300

# How many times this processor will REFUSE a completion before it stops
# blocking and lets the graders have the last word.
#
# Without a bound the loop is unbounded: signal_completion returns a
# self-correction prompt, the agent re-claims completion, forever.  Observed
# on 2026-09-01 — seven refusals in 156 seconds, some 9 seconds apart, every
# one reporting the SAME two errors, with no commits between them.  The arm
# ended BLOCKED having spent its budget on the loop, where the run before it
# had reached a graded verdict.
#
# Three, matching the framework's own bounded retry loops
# (TRUNCATION_RECOVERY_BUDGET and MAX_COMPLETION_NUDGES both default to 2;
# one more here because a refusal is cheaper than a truncation).
#
# On exhaustion this returns [] — it ALLOWS the completion rather than
# failing the session.  That is right for an eval arm and not obviously
# right elsewhere: an arm that completes with unmet criteria is graded FAIL
# by the same checks a moment later, which is a better outcome than BLOCKED,
# because a verdict carries information and a blocked arm carries none.
_MAX_REFUSALS = 3

# Module-level state is viable because ``LifecycleTools`` loads processors
# ONCE per session and caches them (``lifecycle_tools.py:787``,
# ``if self._processors_loaded is None``).  The module therefore survives
# across signal_completion calls.  That is load-bearing and undocumented —
# see jaato #765 — so it is asserted by the counter simply working; if the
# framework ever reloads per call this silently becomes a no-op bound.
_refusals = 0


def validate(payload: Dict[str, Any], context: Any) -> List[str]:
    """Return acceptance failures; empty means the completion stands."""
    workspace = Path(getattr(context, "workspace_path", None) or ".")
    script = workspace / "acceptance.sh"
    if not script.is_file():
        return [
            f"acceptance.sh is missing from the workspace ({script}). This is "
            "an environment fault, not something your fix can address — stop "
            "and report it in errors[] rather than retrying."
        ]

    # The issue id selects the acceptance criteria, exactly as it does for
    # the manifest's graders.  It comes from the same agent_params the task
    # declares, so the in-session gate and the post-hoc graders cannot end
    # up grading different issues — which is the whole reason both callers
    # share one script.
    #
    # Omitting it does not fail open: acceptance.sh refuses without an id.
    # But it fails for the WRONG reason — "no issue_id given" is an
    # environment fault the model cannot act on, so every completion would
    # be blocked by a message that tells the agent to fix something outside
    # its reach.  It would burn the arm's whole budget without ever
    # producing a verdict.
    issue_id = str((getattr(context, "agent_params", None) or {}).get("issue_id", "")).strip()
    if not issue_id:
        return [
            "the task declares no issue_id, so the acceptance criteria "
            "cannot be selected. This is an environment fault, not "
            "something your fix can address — report it in errors[] "
            "rather than retrying."
        ]

    try:
        proc = subprocess.run(
            ["bash", str(script), "--all", issue_id],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [
            f"the acceptance checks did not finish within {_TIMEOUT_SECONDS}s. "
            "Something your fix introduced is most likely hanging — look for a "
            "blocking call on an import path the checks exercise."
        ]

    if proc.returncode == 0:
        return []

    # --all prints one `<check>: <reason>` line per failure and nothing on
    # success, so stdout IS the error list.  An empty stdout with a non-zero
    # exit means the script itself broke; surface stderr instead of
    # returning an empty list, which would silently WAVE THE COMPLETION
    # THROUGH on a broken gate.
    failures = [line for line in proc.stdout.splitlines() if line.strip()]
    if not failures:
        detail = (proc.stderr or "").strip() or f"exit status {proc.returncode}"
        return [
            "the acceptance checks failed without reporting which one: "
            f"{detail}. This is an environment fault, not something your fix "
            "can address — report it in errors[] rather than retrying."
        ]

    global _refusals
    _refusals += 1
    if _refusals >= _MAX_REFUSALS:
        # Stop blocking.  The checks still fail, and the graders will say so
        # with the same script — but an arm that cannot be graded teaches
        # nothing, and repeating an unheeded message is not feedback.
        return []

    remaining = _MAX_REFUSALS - _refusals
    return [
        "Your fix does not pass this task's acceptance checks yet. Fix the "
        "underlying cause of each failure below in repo/, commit again, then "
        "call signal_completion again. Do not report success while any of "
        "these still fails."
        f" You have {remaining} further attempt(s) before this completion is "
        "accepted unfinished and graded as it stands — re-sending the same "
        "claim without changing anything spends one."
    ] + failures
