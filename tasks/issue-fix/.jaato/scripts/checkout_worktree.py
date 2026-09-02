"""Mandatory prefetch: materialise this arm's OWN git worktree, and fetch
the issue text via ``gh``.

WHY THIS IS A PREFETCH, NOT A TOOL CALL.  jaato_eval's fixture step is a
plain ``shutil.copytree`` (see ``jaato_eval/fixture.py``) with no git
hook, and it runs BEFORE any session exists.  Putting the checkout here
instead means:

- It is a FACT, not a model choice.  Per the framework's dynamic-
  instructions doctrine (see .jaato/README.md and jaato-eval's own
  README on the judge's artefact prefetch), a step every arm must have
  is unreliable when routed through a model's discretion and reliable
  when the harness just does it.  Every worker gets a real worktree and
  real issue text before its first turn, unconditionally.
- ``{{!py:}}`` (no ``?``) is MANDATORY: a failure here raises
  DynamicInstructionsError and aborts session-prep, so an arm that could
  not be checked out never starts and is never mistaken for a FAIL.

ISOLATION.  Each arm's ``workspace_path`` is a name the framework already
guarantees is unique across the whole sweep: ``fixture.materialise``
names it ``workspace_root / arm_id`` with ``/`` and ``#`` sanitised to
``_`` (``arm_id`` is ``task@profile_set#repeat``, and profile_set is the
model — the model axis IS the arm axis by this task's own design). This
script worktree-checks-out into ``workspace_path/repo`` — never into
``workspace_path`` itself, because ``git worktree add`` refuses a
non-empty target and the fixture copy has already dropped a ``.env`` and
a README there — on branch ``issue-fix/<same sanitised name>``. Two arms
therefore can never collide on a worktree path or a branch name, and
each worktree is a REAL, independent git checkout: edits in one cannot
appear in another, because they are different directories with
different ``.git`` links.

SHARED MIRROR.  Cloning the same repo once per arm would multiply
network cost by N. Every arm shares one ``--bare`` mirror clone, cached
at ``<workspace_root>/../.repo-cache/<owner>__<name>.git`` (a sibling of
every arm's workspace, so it outlives no single arm and is shared by the
whole sweep). ``git worktree add`` against that mirror is what actually
gives each arm its own checkout; the mirror itself is never an arm's
workspace and no arm ever writes to it directly.  Concurrent arms racing
to create the mirror take an atomic ``os.mkdir`` lock; the loser polls
for a done-marker rather than double-cloning.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

_CLONE_TIMEOUT_S = 120
_LOCK_WAIT_S = 600


def _slug(repo: str) -> str:
    return repo.replace("/", "__")


def _run(cmd, **kwargs):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=kwargs.pop("timeout", 60), **kwargs)


def _clone_url(repo: str) -> str:
    """Resolve an ``owner/repo`` identifier to a cloneable URL via ``gh``.

    ``repo`` is a `gh`-style identifier (per this task's own
    contract — the worker fetches issue text with
    ``gh issue view <issue_id> --repo <repo>``), not a git remote URL.
    ``git clone`` on that bare string fails outright:
    ``fatal: repository 'owner/repo' does not exist`` — verified live
    against this task's own repo before this function existed.  `gh`
    already knows the mapping (and already carries the auth needed to
    resolve a private repo), so ask it rather than guessing at
    https://github.com/<repo>.git.
    """
    result = _run(["gh", "repo", "view", repo, "--json", "url"], timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"gh repo view {repo} failed: {result.stderr.strip()}")
    import json as _json
    return _json.loads(result.stdout)["url"]


def _ensure_mirror(repo: str, cache_root: Path) -> Path:
    """Return a shared ``--bare`` mirror of ``repo``, cloning it once."""
    cache_root.mkdir(parents=True, exist_ok=True)
    mirror_dir = cache_root / f"{_slug(repo)}.git"
    done_marker = cache_root / f"{_slug(repo)}.done"
    if done_marker.exists():
        return mirror_dir

    lock_dir = cache_root / f"{_slug(repo)}.lock"
    try:
        os.mkdir(lock_dir)
        won_race = True
    except FileExistsError:
        won_race = False

    if won_race:
        try:
            clone_url = _clone_url(repo)
            result = _run(["git", "clone", "--bare", clone_url, str(mirror_dir)],
                          timeout=_CLONE_TIMEOUT_S)
            if result.returncode != 0:
                raise RuntimeError(f"git clone --bare {clone_url} failed: {result.stderr.strip()}")
            done_marker.touch()
        finally:
            try:
                lock_dir.rmdir()
            except OSError:
                pass
    else:
        deadline = time.monotonic() + _LOCK_WAIT_S
        while not done_marker.exists():
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"timed out waiting for another arm's mirror clone of {repo}")
            time.sleep(1)
    return mirror_dir


def _default_branch(mirror_dir: Path) -> str:
    result = _run(["git", "-C", str(mirror_dir), "symbolic-ref", "--short", "HEAD"])
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return "main"


def _materialise_worktree(mirror_dir: Path, target: Path, branch: str, base_ref: str) -> None:
    if target.exists():
        raise RuntimeError(
            f"worktree target already exists: {target} — refusing to reuse it, "
            "since a stale checkout would let one arm see another's edits")
    # The mirror is REUSED across sweeps (it is the expensive part), but the
    # branch name is derived from the arm id and so repeats every run.  A
    # second sweep therefore met `fatal: a branch named '<arm>' already
    # exists` and every arm was BLOCKED before its first turn — the prefetch
    # was only ever idempotent by accident of nobody re-running it.
    #
    # Safe to clear here, and only here: we have just established above that
    # the target directory does NOT exist.  A worktree registration whose
    # directory is gone is dead by definition (that is exactly what `prune`
    # removes), and a branch bearing this arm's name with no worktree behind
    # it is last run's leftover.  Neither can be live work.
    _run(["git", "-C", str(mirror_dir), "worktree", "prune"], timeout=60)
    if _run(["git", "-C", str(mirror_dir), "rev-parse", "--verify",
             "--quiet", f"refs/heads/{branch}"]).returncode == 0:
        drop = _run(["git", "-C", str(mirror_dir), "branch", "-D", branch], timeout=60)
        if drop.returncode != 0:
            raise RuntimeError(
                f"could not delete the previous run's branch {branch!r}: "
                f"{drop.stderr.strip()}")

    result = _run(
        ["git", "-C", str(mirror_dir), "worktree", "add", "-b", branch, str(target), base_ref],
        timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"git worktree add failed: {result.stderr.strip()}")


def _issue_text(repo: str, issue_id: str) -> str:
    result = _run(
        ["gh", "issue", "view", str(issue_id), "--repo", repo, "--json", "title,body"],
        timeout=30)
    if result.returncode != 0:
        raise RuntimeError(
            f"gh issue view {issue_id} --repo {repo} failed: {result.stderr.strip()}")
    return result.stdout


def render(context, args) -> str:
    repo = (context.agent_params or {}).get("repo")
    issue_id = (context.agent_params or {}).get("issue_id")
    if not repo or issue_id is None:
        raise RuntimeError(
            "input.agent_params must carry both 'repo' and 'issue_id' — "
            "the task.yaml for this arm is missing one of them")

    # ABSOLUTE, always: `git -C <mirror_dir> worktree add <target>` resolves
    # a RELATIVE target against -C's directory, not this process's cwd.
    # Verified live: with jaato_eval's default --workspaces
    # (".jaato-eval-workspaces", itself relative), context.workspace_path
    # arrived relative too, and every worktree landed nested inside the
    # mirror's own .git directory instead of under workspace_root — `git
    # worktree list` showed
    # ".repo-cache/<repo>.git/.jaato-eval-workspaces/<arm>/repo", and the
    # session's own `repo/` never existed, so the worker's every git
    # command (and the script grader after it) failed with
    # "can't cd to repo".
    workspace_path = Path(context.workspace_path).resolve()
    arm_slug = workspace_path.name  # already sanitised by fixture.materialise
    cache_root = workspace_path.parent / ".repo-cache"

    mirror_dir = _ensure_mirror(str(repo), cache_root)
    base_ref = _default_branch(mirror_dir)
    branch = f"issue-fix/{arm_slug}"
    target = workspace_path / "repo"
    _materialise_worktree(mirror_dir, target, branch, base_ref)

    # Record the starting commit OUTSIDE repo/ (never committed, never
    # part of the diff under test) so the script grader can check
    # `git diff <base> HEAD` without guessing which ref is still "base" —
    # the mirror's default branch can move between arms of the same sweep.
    base_sha = _run(["git", "-C", str(target), "rev-parse", "HEAD"]).stdout.strip()
    (workspace_path / ".base_commit").write_text(base_sha + "\n")

    import json
    raw = _issue_text(str(repo), str(issue_id))
    issue = json.loads(raw)
    title = issue.get("title", "")
    body = issue.get("body", "") or "(no body)"

    return (
        f"Repository: `{repo}`  (checked out at `repo/`, branch `{branch}`, "
        f"based on `{base_ref}`)\n\n"
        f"### Issue #{issue_id}: {title}\n\n"
        f"{body}\n"
    )
