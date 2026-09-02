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

## What the arms get, beyond the issue text

### A language server — and why that is a measurement decision

`tasks/issue-fix/fixture/.lsp.json` ships a `pylsp` configuration, and the
worker profile declares the `lsp` plugin. Together they give arms **diagnostics
at authoring time instead of grading time**: `file_edit`'s write tools carry
`TRAIT_FILE_WRITER`, the lsp plugin subscribes to tool-result enrichment, and
the diagnostics for a file the arm just wrote are appended to the result the
model reads.

Without it an arm writes Python blind. One did: 547 lines of `explain.py` with
escaped quotes where a docstring belonged, never saw the `SyntaxError`, and
committed a file that would not import. It failed on a mistake it could have
seen instantly.

The `extraPaths` entries are not decoration:

```json
"extraPaths": ["${workspaceRoot}/repo",
               "${workspaceRoot}/repo/jaato-server",
               "${workspaceRoot}/repo/jaato-sdk"]
```

The arm edits a repository checked out *inside* its workspace, so without these
jedi cannot resolve a single first-party import and every file comes back
buried in false "unresolved import" diagnostics — worse than none, because the
real error is somewhere in the noise.

**This is the part to be deliberate about:** if `pylsp` is not on `PATH`, the
plugin degrades to no diagnostics rather than failing the session. That is the
right behaviour, and it means **a host without a language server measures
something different** — the same model, the same issue, and a silently harder
task. Sweeps are not comparable across hosts that differ here. If you are
reproducing a run in `sweeps/`, install `pylsp` first:

    pip install python-lsp-server        # then: command -v pylsp

## Running it

    cp .env.example .env        # then set JAATO_CONFIG_ROOT to this checkout
    python -m jaato_eval.cli run tasks \
        --profile-set openrouter_glm53,openrouter_gpt5mini,openrouter_gemini25flash \
        --out results.jsonl --concurrency 1 --arm-timeout 3600 --keep-workspaces

`repeats:` in `task.yaml` applies to every profile set in one invocation, so a
run with different repeat counts per model is two invocations appending to one
results file.

## The consolidated view

<https://jaato-framework-and-examples.github.io/jaato-eval-issue-fix-sweep-harness/>

Every arm that has ever attempted each issue, on one page: an issue row expands
to the models that attempted it, and a model row expands to its individual
arms. Published by `.github/workflows/pages.yml` on any push touching
`sweeps/**` — drop a results file in, and the page has it.

The build installs **nothing**. `tools/site/collect.py` is stdlib-only, so
publishing never pulls in `jaato-sdk` or `jaato-eval` — a git install of the
framework in CI would make the published bytes a function of a sibling repo's
HEAD, and the site could then change without a commit here.

**Every figure is a range, never a mean.** A mean would claim a central
tendency across runs that may have executed under different framework
versions; `[min … max]` with its `n` claims only observed extent. Three
consequences worth knowing before reading a row:

* **The population differs by figure.** Cost, duration, turns and cache hit
  range over *arms*. Pass rate ranges over *runs* — an arm's pass rate is 0 or
  1, which is not a rate.
* **`n` is what was observed, `of` is what existed.** An unreported cost is
  dropped rather than counted as zero; a run in which every arm was BLOCKED
  contributes no pass rate at all. BLOCKED never enters a denominator and is
  always shown.
* **Two columns are deliberately absent**, and say so on the page rather than
  rendering a blank cell the reader has to explain: cache hit reads "not
  observed", and there is no agreement percentage — payload counts are shown
  instead, where an arm that produced no payload is counted as absent rather
  than as one that agreed. Both are waiting on framework behaviour, so both
  are rows in [KNOWN_ISSUES.md](KNOWN_ISSUES.md) with the issue that retires
  them.

A corpus the collector cannot render **fails the publish**: an unparseable
file, an unknown verdict state, a `.jsonl` at the wrong depth. A sweep that
cannot be read must not quietly vanish from a page that still looks complete.

To see it locally:

    python tools/site/collect.py && python -m http.server -d site
    python -m unittest discover -s tools/site

## Tools

The sweep measures **quantitatively** — verdicts, costs, pass rates,
determinism. `python -m jaato_eval.cli report` is the rest of that: the same numbers,
per arm instead of per configuration, with the session id that joins a row to the
provider's own record of it.

`tools/interrogate/` measures nothing. It wakes a finished arm and asks it, in
prose, **why** it did what it did — the **qualitative** half, and the only way
to tell apart two arms whose verdicts are identical. Run 22 produced three
PASSes; reading the sessions showed one arm had verified its own work
exhaustively and another had run no tests at all and passed only because the
completion gate caught it. No column carries that distinction.

Use the report to find the arm worth reading; use interrogation to ask it what
the report cannot. Both, and the contract trap that makes an interrogation
return a single sentence, are in [tools/README.md](tools/README.md).

## Known framework issues

Framework behaviour that bites this harness lives in
[KNOWN_ISSUES.md](KNOWN_ISSUES.md), each row linked to the jaato issue that
will close it. Read it before your first run — and check the linked issues,
because a closed one means the row is stale rather than that the workaround is
still needed.
