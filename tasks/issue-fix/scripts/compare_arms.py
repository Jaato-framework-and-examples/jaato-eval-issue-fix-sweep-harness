#!/usr/bin/env python3
"""Comparative judge — ranks every arm of an issue-fix sweep against the
others, from their real diffs.

WHY THIS IS NOT A task.yaml `judge` GRADER.  jaato_eval's per-arm judge
adapter (jaato_eval/graders/judge.py) opens one session per arm and its
GraderContext carries exactly that arm's workspace_path — there is no
hook for a grader to see a sibling arm's output.  Ranking arms against
each other therefore has to happen OUTSIDE the manifest, once, after
every arm in the sweep has finished:

    python -m jaato_eval run tasks/issue-fix --profile-set \
        openrouter_gpt5mini,openrouter_gemini25flash \
        --keep-workspaces --out results.jsonl
    python tasks/issue-fix/scripts/compare_arms.py results.jsonl

``--keep-workspaces`` is required: without it jaato_eval discards every
arm's workspace the moment its own graders finish (jaato_eval/fixture.py
``discard()``), and there would be nothing left on disk for THIS script
to diff.

WHICH JUDGE PROFILE.  Reads `--profile-set` from the CLI (default
openrouter_gpt5mini) — the judge is one model, not one per arm; ranking
N arms against each other is a single comparison, not N separate ones.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_results(results_path: Path) -> List[Dict[str, Any]]:
    records = []
    with results_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _issue_text(workspace_root: Path, arm_ids: List[str]) -> str:
    """Best-effort: read the issue title/body straight off the FIRST arm's
    kept worktree branch name / commit history is not reliable, so this
    reads it from the same source every worker did — .base_commit sits
    beside repo/, but the issue TEXT itself was only ever in the worker's
    prompt, not written to disk.  Rather than re-deriving it here (a
    second source that could drift from what the worker actually saw),
    the judge is handed the arms' diffs only when this returns empty; a
    non-empty value is opportunistic context, not a dependency.
    """
    return ""


def _arm_workspace(workspace_root: Path, arm_id: str) -> Optional[Path]:
    """jaato_eval sanitises arm_id -> directory name identically in
    fixture.materialise: '/' and '#' become '_' (see runner.py's
    ``workspace_root / spec.arm_id.replace("/", "_").replace("#", "_")``).
    """
    slug = arm_id.replace("/", "_").replace("#", "_")
    candidate = workspace_root / slug
    return candidate if candidate.is_dir() else None


#: Set by :func:`_ask_judge` so the cost of the judge turn survives the
#: session closing.  A module-level dict rather than a return value because
#: the payload is what callers want and threading a tuple through would
#: make every caller handle the accounting.
_JUDGE_SESSION: Dict[str, Any] = {}


def _judge_cost(workspace: Path) -> Optional[Dict[str, float]]:
    """Read the judge session's own BudgetTracker snapshot.

    The same source `jaato_eval` uses for a cut arm (jaato #727): the daemon
    persists per-response usage into the session record under the session's
    workspace, so it is readable after the session has gone.  Without this
    the judge reports nothing and a sweep's total is only its arms — which
    for a three-invocation comparison is a material undercount.

    Returns ``None`` when the record is absent or unreadable; a missing
    figure must be reported as unknown rather than as zero, because zero is
    a number someone will add up.
    """
    sid = _JUDGE_SESSION.get("id")
    if not sid:
        return None
    record = workspace / ".jaato" / "sessions" / f"{sid}.json"
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    usage = data.get("budget_usage")
    if isinstance(usage, dict) and usage:
        return usage

    # No tracker snapshot.  That is EXPECTED for the judge: the profile
    # declares no `budget_control`, and persistence uses tracker_only=True,
    # which deliberately writes nothing rather than a fallback shape that
    # would overwrite a real snapshot on the next load.
    #
    # `turn_accounting` still carries per-turn token counts, so tokens are
    # recoverable.  Cost is NOT — nothing in the record prices them, and the
    # per-token rate depends on the model and its cache split.  Report the
    # tokens and leave cost absent rather than deriving a number that would
    # be added to measured ones.
    turns = data.get("turn_accounting") or []
    total = sum(t.get("total") or 0 for t in turns if isinstance(t, dict))
    if not total:
        return None
    return {
        "tokens": float(total),
        "prompt_tokens": float(sum(t.get("prompt") or 0 for t in turns)),
        "output_tokens": float(sum(t.get("output") or 0 for t in turns)),
        "cache_read": float(sum(t.get("cache_read") or 0 for t in turns)),
        "turns": float(len(turns)),
    }


async def _ask_judge(*, profile: str, agent: str, config_root: Path,
                     arms: List[Dict[str, str]], socket_path: Optional[str],
                     workspace_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    from jaato_sdk.client.ipc import IPCClient

    kwargs: Dict[str, Any] = {
        "profile": profile,
        "agent": agent,
        # A real workspace, holding one symlink per arm to that arm's
        # worktree, so the judge can read the code around a change and not
        # only the rendered diff.
        "config_root": str(config_root),
        # agent_params is Dict[str, str] on the wire
        # (SessionInitEnvelope.agent_params, and IPCClient.create_session's
        # own signature).  Passing a list of dicts through it does not fail
        # loudly — the entries arrive stringified, and the prefetch dies with
        # "string indices must be integers", four layers from the cause.
        # Serialise deliberately and parse on the far side.
        "agent_params": {"arms": json.dumps(arms), "issue_text": ""},
    }
    if workspace_path is not None:
        kwargs["workspace_path"] = str(workspace_path)
        # Resolved relative to workspace_path, exactly as an arm's is.
        kwargs["env_file"] = ".env"
    if socket_path:
        kwargs["socket_path"] = socket_path

    async with IPCClient.session(**kwargs) as session:
        payload = await session.complete(
            "Rank every arm shown to you in your system prompt, best fix first.")
        # Remember which session this was, so the caller can price it after
        # the context manager has closed.  A comparison harness that cannot
        # price its own judge under-reports every sweep it runs.
        _JUDGE_SESSION["id"] = getattr(session, "session_id", None) or \
            getattr(session, "sid", None)
        return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", help="results.jsonl from `jaato_eval run --keep-workspaces`")
    parser.add_argument("--task-dir", default=None,
                        help="task directory (default: parent of this script)")
    parser.add_argument("--workspaces", default=".jaato-eval-workspaces",
                        help="parent dir for per-arm scratch workspaces "
                             "(must match the --workspaces used for the run)")
    parser.add_argument("--profile-set", default="openrouter_gpt5mini",
                        help="which model set judges — one judge for the whole sweep")
    parser.add_argument("--socket", default=None, help="daemon IPC socket path")
    args = parser.parse_args(argv)

    task_dir = Path(args.task_dir) if args.task_dir else Path(__file__).resolve().parent.parent
    config_root = task_dir / ".jaato"
    workspace_root = Path(args.workspaces)

    records = _load_results(Path(args.results))
    if not records:
        print(f"no records in {args.results}", file=sys.stderr)
        return 2

    arms: List[Dict[str, str]] = []
    for r in records:
        arm_id = r["arm_id"]
        ws = _arm_workspace(workspace_root, arm_id)
        if ws is None:
            print(f"skipping {arm_id}: no kept workspace under {workspace_root} "
                  f"(was the sweep run with --keep-workspaces?)", file=sys.stderr)
            continue
        repo_path = ws / "repo"
        if not repo_path.is_dir():
            print(f"skipping {arm_id}: no repo/ under {ws} — the worktree "
                  f"prefetch never ran or failed for this arm", file=sys.stderr)
            continue
        # Carry the graders' verdicts to the judge.
        #
        # Without them the judge ranks on diff text alone, and a diff that
        # reads well scores well — which is what a language model is best at
        # detecting and what correctness least depends on.  Twice now that
        # produced a wrong ranking: once favouring an arm whose file would
        # not even import, once ranking first an arm whose feature was
        # unreachable while the runner-up's half actually worked.
        #
        # Whether the code runs is a FACT the judge cannot obtain by
        # reading, and the harness already computed it.  Withholding it was
        # the mistake.
        verdicts = []
        for v in (r.get("verdicts") or []):
            state = v.get("state") or "?"
            claim = (v.get("claim") or "").strip().replace("\n", " ")
            detail = (v.get("detail") or "").strip().replace("\n", " ")
            verdicts.append(f"{state}: {claim[:120]}" + (f" -> {detail[:80]}" if detail else ""))
        arms.append({"arm_id": arm_id, "repo_path": str(repo_path),
                     "verdicts": verdicts})

    if not arms:
        print("no arm had a usable kept worktree — nothing to compare", file=sys.stderr)
        return 2

    # The judge needs a workspace of its own, and it must CONTAIN the repos
    # it is comparing.
    #
    # An earlier version passed no workspace_path at all, reasoning that the
    # judge mutates nothing and receives the diffs through its prefetch.
    # That was wrong twice over.  A session without a workspace has no place
    # to stand: path-containment, the workspace venv and the session record
    # all key off it, and `session.new` hung for the client's full 60s
    # timeout rather than refusing (jaato #730).  And a judge that can only
    # see rendered diff text cannot go and look at the code around a change
    # — which is most of what distinguishes a good fix from a plausible one.
    #
    # So: a dedicated directory holding one symlink per arm, pointing at
    # that arm's real worktree.  Symlinks rather than copies because the
    # worktrees are full checkouts of the subject repo and there may be many
    # arms; the judge only reads them.
    judge_ws = workspace_root / "_judge"
    judge_ws.mkdir(parents=True, exist_ok=True)
    for arm in arms:
        slug = arm["arm_id"].replace("/", "_").replace("#", "_")
        link = judge_ws / slug
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(Path(arm["repo_path"]).resolve(), target_is_directory=True)
        # Handed to the persona as the in-workspace path, so the judge reads
        # the same location it is told about.
        arm["workspace_path"] = str(link)

    # Select the profile SET the same way jaato_eval does for an arm:
    # fixture.materialise writes JAATO_PROFILE_SET into the arm workspace's
    # .env, and the daemon reads that file.  Setting it in THIS process's
    # environment (as an earlier version did) cannot work — profile
    # discovery runs daemon-side, in a different process, so the daemon
    # never saw it and `profile: "judge"` had no set to resolve against.
    #
    # The arms worked precisely because their workspace .env carried it.
    # Now the judge has a workspace of its own, it can carry it the same
    # way, through the same mechanism, rather than a second one.
    (judge_ws / ".env").write_text(
        f"JAATO_PROFILE_SET={args.profile_set}\n", encoding="utf-8")

    payload = asyncio.run(_ask_judge(
        profile="judge", agent="judge", config_root=config_root,
        arms=arms, socket_path=args.socket, workspace_path=judge_ws))

    # Price the comparison itself.  Printed to stderr so it never
    # contaminates a ranking someone is parsing off stdout.
    cost = _judge_cost(judge_ws)
    if cost:
        usd = cost.get("usd")
        price = f"${usd:.4f}" if isinstance(usd, (int, float)) else "cost unpriced"
        print(f"judge: {price}, {int(cost.get('tokens') or 0):,} tokens "
              f"({int(cost.get('cache_read') or 0):,} cache-read), "
              f"{int(cost.get('turns') or 0)} turn(s)", file=sys.stderr)
    else:
        # Explicitly unknown, not zero — zero is a number someone adds up.
        print("judge: cost unknown (no session record found)", file=sys.stderr)

    if payload is None:
        print("judge returned no typed payload (no completion_payload_schema "
              "reached, or it answered in prose)", file=sys.stderr)
        return 2

    errors = payload.get("errors") or []
    if errors:
        print("judge could not complete the comparison:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 2

    ranking = payload.get("ranking") or []
    reasoning = payload.get("reasoning") or {}
    print(f"Comparative ranking ({len(ranking)} arm(s), best first):\n")
    for i, arm_id in enumerate(ranking, start=1):
        why = reasoning.get(arm_id, "")
        print(f"{i}. {arm_id}")
        if why:
            print(f"   {why}")
    for w in payload.get("warnings") or []:
        print(f"\nwarning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
