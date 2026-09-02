# sweeps

Completed sweeps, kept as evidence. Organised `<owner>__<repo>/<issue>/`, so a
second target repo or a second issue is a new directory rather than a naming
convention argument.

    sweeps/
      Jaato-framework-and-examples__jaato/
        715/run22.jsonl
        782/run23.jsonl

Render one with the per-arm report:

    python -m jaato_eval.cli report \
        sweeps/Jaato-framework-and-examples__jaato/782/run23.jsonl \
        --html run23.html

## What is here, and what deliberately is not

Twenty-three sweeps were run against these two issues. **Two are archived.**

Runs 1-21 were graded by a harness with three defects since fixed, and their
verdicts cannot be trusted:

* **jaato #767** — arms were graded on the first `finish=stop`, while the
  daemon was still re-prompting the agent. One arm was graded two minutes
  before it wrote the commit that made it pass, and recorded FAIL.
* **jaato #773** — an arm that exhausted its nudge budget was recorded BLOCKED
  ("nothing to grade") while its workspace held a gradeable tree. Blocked arms
  are excluded from the pass-rate denominator, so a genuine failure silently
  *improved* the model's score.
* **jaato #766** — a bare `finish_reason="error"` was reported as
  `Provider returned an error` with the cause discarded, and was terminal
  rather than retried. Two arms died that way; the cause
  (`MALFORMED_FUNCTION_CALL`) was only recoverable by querying the provider's
  API out of band.

Runs 22 and 23 are the first two on a fully merged stack. Keeping the earlier
21 would mean shipping a corpus whose verdicts I know to be wrong, next to two
whose verdicts I trust, in one directory.

## Session attribution

Every row carries `session_id`, `model`, `provider`, `upstream_provider`,
`nudges` and `budget`, per jaato #777.

Both runs predate #777's merge, so the runner did not record those columns.
They were reconstructed **once**, from the arms' own kept workspaces, and every
value is a fact read off disk rather than an estimate:

* `session_id` — from the session log's filename, cross-checked against the
  run's time window (a session id *is* its creation timestamp).
* `nudges` — by counting `COMPLETION_NUDGE` lines in that session's log.
* `model` / `provider` / `upstream_provider` — from the profile the arm ran
  under, and the gateway's own record of which upstream served it.

**Sweeps recorded from now on need none of this** — a current server records
the columns natively. The reconstruction is not a tool and is not shipped; it
was a one-off to make this corpus usable.

## Reproducing a run

Arms in both runs had `pylsp` on `PATH`, so they received live diagnostics on
every file they wrote. A host without a language server runs the same models
against a silently harder task — see the README's note on why that is a
measurement decision, not a convenience. Install it before comparing:

    pip install python-lsp-server

## The corpus in one line each

**715 / run22** — three PASSes and two FAILs, and the PASSes are the
interesting part: the three arms verified their own work to completely
different degrees, and `results.jsonl` cannot show it. See
[tools/README.md](../tools/README.md) on why that needs interrogation rather
than another column.

**782 / run23** — all five arms PASS. The criteria are behavioural (they
assert a file's bytes, plus two regressions the fix must not cause) rather
than greps over output, which is the difference this harness exists to
demonstrate.
