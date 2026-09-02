# issue-fix harness

A `jaato-eval` sweep that measures how well a model fixes a **real GitHub
issue**: N independent agents each attempt the same issue in their own git
worktree, and script graders decide whether the result actually meets the
issue's acceptance criteria.

    tasks/issue-fix/
      task.yaml                  the arms, the inputs, the graders, the budget
      fixture/acceptance.sh      generic checks: committed / compiles / compliant
      fixture/acceptance/<id>.sh the criteria for ONE issue — the interesting part
      .jaato/profiles/           one directory per model under test
      .jaato/agents/             worker and judge personas
      .jaato/scripts/            worktree checkout, judge rendering
      .jaato/scripts/processors/ the in-session completion gate

## The one thing worth understanding

`acceptance/<id>.sh` is where a sweep is won or lost. On issue #715 the
criteria were two greps over CLI output, and **three arms passed them with
materially different quality** — one omitted subcommands, one omitted the
drill-down line, none wrote the guard the issue asked for, and one pushed two
functions past the repo's complexity ratchet. A grep cannot separate those.

Issue #782's criteria are behavioural instead — they assert a file's *bytes*,
plus two regressions the fix must not cause (an explicit empty write must
still truncate; targeted edits must still work). Both of those pass on the
UNFIXED tree, so they are not vacuous: they only fire if an arm over-corrects.

Write criteria that a plausible-looking wrong answer fails.

## Running it

    cp .env.example .env        # then set JAATO_CONFIG_ROOT to this checkout
    python -m jaato_eval.cli run tasks \
        --profile-set openrouter_glm53,openrouter_gpt5mini,openrouter_gemini25flash \
        --out results.jsonl --concurrency 1 --arm-timeout 3600 --keep-workspaces

`repeats:` in `task.yaml` applies to every profile set in one invocation, so a
run with different repeat counts per model is two invocations appending to one
results file.

## Known framework issues

Framework behaviour that bites this harness lives in
[KNOWN_ISSUES.md](KNOWN_ISSUES.md), each row linked to the jaato issue that
will close it. Read it before your first run — and check the linked issues,
because a closed one means the row is stale rather than that the workaround is
still needed.
