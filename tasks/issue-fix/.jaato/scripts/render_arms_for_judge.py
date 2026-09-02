"""Mandatory prefetch: render EVERY arm's diff into the judge's prompt.

WHY THIS IS A PREFETCH, NOT A TOOL CALL.  jaato-eval's own rubric
(tasks/example-echo/.jaato/scripts/prefetch_artefact.py) reads a file
for the judge because "reading is a FACT, and a fact routed through a
model's discretion is unreliable" — measured there at roughly 1 run in 4
where the judge simply never made the call.  Collecting every arm's diff
is the same kind of fact, multiplied by N arms instead of one file, so
the same guard applies: the harness computes every diff and hands them
all over before the judge's first turn, unconditionally.  ``{{!py:}}``
(no ``?``) aborts session-prep on failure, so a judge that cannot get
the diffs never starts.

INPUT.  ``context.agent_params['arms']`` is a list of
``{"arm_id": str, "repo_path": str}`` — one entry per arm that actually
ran, passed in by scripts/compare_arms.py when it opens this judge
profile directly (NOT through jaato_eval's per-arm `judge` grader, which
only ever sees one arm; see .jaato/profiles/_base_judge.yaml).
``repo_path`` is each arm's kept workspace's ``repo/`` — the real git
worktree the worker committed into — reachable because the sweep was run
with ``--keep-workspaces``, per the harness's own README on why that flag
exists for exactly this purpose.

DIFF.  For each arm, ``git diff <base_commit> HEAD`` inside its
``repo/``, where ``base_commit`` is the sibling ``.base_commit`` file
checkout_worktree.py wrote before the worker's first turn — the same
mechanism the script grader in task.yaml uses, so the judge and the
grader agree on what "before" meant for that arm.
"""
from __future__ import annotations

import subprocess
import json
from pathlib import Path

_MAX_DIFF_BYTES = 20_000


def _run(cmd, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=30)


def _arm_diff(repo_path: Path) -> str:
    base_file = repo_path.parent / ".base_commit"
    if not base_file.is_file():
        return "(harness note: no .base_commit recorded — this arm never reached the checkout step)"
    base_sha = base_file.read_text().strip()
    result = _run(["git", "diff", f"{base_sha}..HEAD"], cwd=str(repo_path))
    if result.returncode != 0:
        return f"(harness note: git diff failed: {result.stderr.strip()})"
    diff = result.stdout
    if not diff.strip():
        return "(no changes)"
    if len(diff) > _MAX_DIFF_BYTES:
        diff = diff[:_MAX_DIFF_BYTES] + f"\n[truncated at {_MAX_DIFF_BYTES} bytes]"
    return diff


def render(context, args) -> str:
    # agent_params crosses the wire as Dict[str, str]
    # (SessionInitEnvelope.agent_params), so compare_arms.py sends the arm
    # list JSON-encoded.  Tolerate a real list too: a caller that hands one
    # directly, in-process, should not have to encode it first.
    raw = (context.agent_params or {}).get("arms") or []
    if isinstance(raw, str):
        try:
            arms = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"agent_params['arms'] is not valid JSON: {exc}. "
                f"compare_arms.py sends json.dumps([{{arm_id, repo_path}}, ...])"
            ) from exc
    else:
        arms = raw
    if not isinstance(arms, list) or any(not isinstance(a, dict) for a in arms):
        raise RuntimeError(
            f"agent_params['arms'] must decode to a list of "
            f"{{arm_id, repo_path}} dicts; got {type(arms).__name__} "
            f"containing {[type(a).__name__ for a in (arms or [])][:3]}. "
            f"Stringified entries mean the list was passed through the "
            f"Dict[str, str] wire without encoding."
        )
    if not arms:
        raise RuntimeError(
            "agent_params['arms'] is empty — compare_arms.py must pass at "
            "least one {arm_id, repo_path} entry")

    issue_text = (context.agent_params or {}).get("issue_text", "")
    sections = []
    if issue_text:
        sections.append(f"### The issue every arm was trying to fix\n\n{issue_text}\n")

    for entry in arms:
        arm_id = entry["arm_id"]
        repo_path = Path(entry["repo_path"])
        diff = _arm_diff(repo_path)
        # Verdicts BEFORE the diff, deliberately.  They are the facts the
        # judge cannot derive by reading, and putting them after several
        # hundred lines of diff invites skimming past them.  An arm whose
        # acceptance check FAILed has not produced a working fix, however
        # well its diff reads.
        block = [f"### Arm `{arm_id}`", ""]
        verdicts = entry.get("verdicts") or []
        if verdicts:
            block.append("**Harness verdicts** (these were EXECUTED, not read — "
                         "weigh them above how the diff looks):")
            block.extend(f"- {v}" for v in verdicts)
            block.append("")
        block.append(f"```diff\n{diff}\n```")
        sections.append("\n".join(block))

    return "\n\n".join(sections)
