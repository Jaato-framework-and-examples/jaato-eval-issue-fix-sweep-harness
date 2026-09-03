# sweeps

Completed sweeps, kept as evidence. Organised `<owner>__<repo>/<issue>/`, so a
second target repo or a second issue is a new directory rather than a naming
convention argument.

    sweeps/
      Jaato-framework-and-examples__jaato/
        694/run24.jsonl
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
`completion_nudges` and `budget_ceiling`, per jaato #777.

Both runs predate #777's merge, so the runner did not record those columns.
They were reconstructed **once**, from the arms' own kept workspaces, and every
value is a fact read off disk rather than an estimate:

* `session_id` — from the session log's filename, cross-checked against the
  run's time window (a session id *is* its creation timestamp).
* `completion_nudges` — by counting `COMPLETION_NUDGE` lines in that session's log.
* `model` / `provider` / `upstream_provider` — from the profile the arm ran
  under, and the gateway's own record of which upstream served it.

**Sweeps recorded from now on need none of this** — a current server records
the columns natively. The reconstruction is not a tool and is not shipped; it
was a one-off to make this corpus usable.

### The keys were normalised afterwards

That reconstruction wrote two of its columns under names the runner does not
use: `nudges` and `budget` where `ArmResult.to_dict` writes `completion_nudges`
and `budget_ceiling` (jaato-eval `arm.py`). The values were right and present,
but `report_html.py` reads the canonical names, so both columns rendered `—` on
this corpus — data that was there, reported as data that was missing.

Both files were rewritten to the canonical key set. Renames only, with every
other value byte-identical:

* `nudges` -> `completion_nudges`
* `budget: {usd, source}` -> `budget_ceiling: {usd}`. The `source` string is not
  lost — in the canonical schema *which key* carries the ceiling is what names
  the gate: `budget_ceiling` is the arm's own `budget_control`, `pool_limits`
  is the shared task pool. All ten arms were on their own books.
* `native_finish_reason`, `pool_limits`, `pool_on_arrival` added as explicit
  `null`, per `arm.py`'s rule that a null means "this engine could not
  establish it" while an absent key means "a newer engine added it". These runs
  predate jaato #766, so `null` is the true value rather than a placeholder.

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

**694 / run24** — five models, one arm each, four PASS and one FAIL. The
first run whose criteria were validated against simulated arms BEFORE it ran
(a correct fix passes; clamping to `max_delay` fails the 90s check; bounding
without a sign check fails the negative-hint check), and the FAIL is the case
they were built for: gpt5mini committed a `max_server_delay` knob and a
sanity-checker, wrote in its own docstring that the ceiling "is applied in
`calculate_backoff`", and never applied it there.

The four passes converged independently on the same shape — `max_server_delay`
via `AI_RETRY_MAX_SERVER_DELAY`, defaulting to 300.0 — which no criterion asked
for; only the issue text suggested "2-5 minutes".

This run is also the first on a runner that records `spend_cache_read_tokens`,
so it is the first with a cache-hit figure at all: 35%-95% across the five.

### Read run24's provenance before comparing it to 22 and 23

Its arms did not all run under the same host conditions, and that is visible
rather than hidden:

* The first attempt measured ONE arm. The host exhausted 11 GiB of RAM and the
  other four were BLOCKED with `can't start new thread` / `SessionNotConfirmed`
  — an unreaped `pylsp` had reached 3.2 GB and was never released between arms
  (see KNOWN_ISSUES.md).
  Those four BLOCKED rows were pruned and the arms re-run; `--resume` is
  state-blind, so a BLOCKED row left in place would never have been retried.
* `glm53` therefore ran in the first attempt and the other four in later ones,
  after ~4 GB was reclaimed. Same task, same criteria, same profiles — but not
  the same memory pressure.
* One session was orphaned by operator error (its workspace was deleted while
  it was still live), spent its full $2.50 ceiling and produced nothing. It is
  not in this file: no client survived to grade it, so it was never recorded.

None of that touches the five verdicts here, each of which was graded by the
same three checks against a tree the arm committed. It is recorded because a
run assembled across three invocations is not the same artefact as one that
ran start to finish, and a reader comparing cost or duration to run 23 should
know which.
