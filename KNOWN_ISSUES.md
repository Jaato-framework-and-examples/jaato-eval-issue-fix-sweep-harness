# Known issues

Framework behaviour that bites this harness. Each row points at a jaato issue,
so when the fix lands the row can be deleted rather than quietly becoming a
lie. The README deliberately says none of this — it describes how to run the
harness, not what is wrong with the framework this week.

**Check the linked issues before trusting a row.** A closed issue means the
row is stale, not that the workaround is still needed.

| # | What bites | Upstream | Status |
|---|---|---|---|
| 1 | `JAATO_PROVIDER_TRACE` is a **path**, not a switch | [#775](https://github.com/Jaato-framework-and-examples/jaato/issues/775) | fix on branch, not yet merged |
| 2 | A source edit under a running daemon splits the session | [#790](https://github.com/Jaato-framework-and-examples/jaato/issues/790) | open |
| 3 | Arms with a prefetch persona cannot be revived at all | [#787](https://github.com/Jaato-framework-and-examples/jaato/issues/787) | open — blocks interrogation |
| 4 | `det` counts DISTINCT payload hashes, so a cell can report 100% agreement from one observation | [#798](https://github.com/Jaato-framework-and-examples/jaato/issues/798) | open — the page shows no agreement column |
| 5 | A results file cannot express cache economics: the runner sums last-response cache readings | [#800](https://github.com/Jaato-framework-and-examples/jaato/issues/800) | open — the page's cache-hit column reads "not observed" |

---

## 1. `JAATO_PROVIDER_TRACE` is a path, not a switch

Set through a profile's `env:` block, `"1"` is accepted — `env` is
`Dict[str, str]` and `"1"` is a valid string. Every session then wrote its
provider trace to a file literally named `1`, in whatever directory that
session resolved a relative path against. Including arm workspaces, which
contaminated the very trees the comparative judge diffs.

**Until #775 merges:** give it a real filename. A *relative* path is correct
and deliberate — the runner joins it onto `JAATO_WORKSPACE_ROOT`, so each arm
traces into its own workspace:

```yaml
env:
  JAATO_PROVIDER_TRACE: provider_trace.log
```

**When #775 merges** this becomes a typed `trace:` block that rejects
`1`/`true`/`yes`/`on` at parse time with a message naming the distinction.
Delete this section then.

## 2. A source edit under a running daemon splits the session

Editing framework source while the daemon runs leaves some modules the edited
ones and some the versions the pre-warm pool template imported at start. It
surfaces as an unrelated `TypeError` far from the edit — observed as
`create_provider() got an unexpected keyword argument 'session_id'`, where
`jaato_session` had the edit and `jaato_runtime` did not.

**Workaround:** restart the daemon after any framework change, from a neutral
directory so it does not inherit a workspace `.env`:

```bash
cd ~ && python -m server --stop
python -m server --ipc-socket /tmp/jaato.sock --daemon
```

Only happens with the pre-warm pool, which is the default;
`JAATO_RUNNER_POOL_ENABLED=false` cold-spawns and cannot split, at the cost of
~30s per session.

## 3. Arms cannot be interrogated at all yet

`tools/interrogate/` is shipped and its profile is correct, but reviving any
arm of this harness fails in bootstrap:

    session.bootstrap: dynamic-instructions abort:
      checkout_worktree.py: RuntimeError: input.agent_params must carry both
      'repo' and 'issue_id' — the task.yaml for this arm is missing one

The task file is fine and the params were present when the session was
created. They are not persisted, so revival re-runs the persona's mandatory
prefetch with an empty `agent_params`.

**No workaround** that keeps the arm's own history. Delete this section when
#787 closes; the interrogation profile is already ready for it.

### Resolved: the inherited acceptance processor (#791, merged)

The interrogate profile used to inherit the sweep's acceptance gate with no way
to decline it. `suppress_inherited_processors` now removes it, verified:
schema `{}`, **0 processors**, with `budget_control`, `max_turns: 15`,
`runtime_limits` and all six plugins still inherited.

## 4. `det` reports agreement that was never observed

`jaato_eval/report.py` computes the `det` column as `1.0 / len(payload_hashes)`
over a **set**, and `build_cells` skips arms that produced no payload. Two
consequences, and the first is already visible in this repo's own corpus:

* An arm with no payload shrinks numerator and denominator together, so run22's
  `openrouter_gemini25flash` cell prints `100%` — footnoted "byte-identical
  across repeats" — on the strength of **one** observed payload, the other arm
  having died at `max_tokens`. A missing payload is counted as a matching one.
* Frequencies are discarded, so three arms with two agreeing print 50% where
  the documented modal share is 67%. It coincides with the docstring only when
  every hash is equally frequent, which is why nothing published so far is
  visibly wrong: every existing cell has at most 2 arms.

**Until #798 merges:** `tools/site/collect.py` reports no agreement percentage
at all. It emits `payloads: {arms, produced, absent, distinct}` instead, and
the page renders those counts. Delete this section when the formula has one
definition, and the percentage can then sit on top of counts that were already
honest.

## 5. A results file cannot express cache economics

`jaato-sdk`'s `events.py` is explicit that `cache_read_tokens` /
`cache_creation_tokens` are **the last response's** figures, while
`spend_cache_read_tokens` / `spend_cache_creation_tokens` are the traffic
billed across the turn — "the distinction is load-bearing for a session using
`model_tiers`". `jaato_eval/runner.py` puts the *level* fields in
`_SUMMED_USAGE` and adds them across turns, and records neither spend field.
The result is neither a level nor a spend.

The tell is in this repo's corpus: `openrouter_gemini25flash` reports
`cache_creation_tokens` exactly equal to `cache_read_tokens` in three of four
arms (97590/97590, 32530/32530, 32584/32584). That is one reading copied into
two fields; two billed sums over multiple responses would not agree to the
token.

**Until #800 merges:** the site's cache-hit figure —
`spend_cache_read_tokens / spend_total_tokens`, both spend figures of the same
shape — reads "not observed" for every archived arm. It deliberately does NOT
fall back to the summed level, which would publish an artifact under a label
claiming to mean something else. The first run produced by a fixed runner
populates the column with no change to the collector.

---

## Not framework issues — harness facts that read like bugs

**`budget:` in `task.yaml` is a shared POOL, not a per-arm ceiling.** This is
[documented framework design](https://github.com/Jaato-framework-and-examples/jaato/blob/main/jaato-eval/jaato_eval/manifest.py)
(`BudgetSpec`), not a defect. Three arms once drew on one $6.00 pool and spent
$6.0140; the last was killed mid-work and looked like a model failure. A
per-arm ceiling is `budget_control:` in the arm's own profile — a session
carrying one is on its own books and never draws on the pool.

**A restart immediately after `--stop` once failed silently.** Observed once,
mechanism never established. The socket is *not* the cause — `ipc.py` unlinks
a stale socket unconditionally when it is a socket. The shutdown that preceded
it was logging `_run_threadsafe called from the event-loop thread it targets`,
so "started before teardown finished" is the better guess, but it is a guess.
Recorded here so nobody repeats the `rm /tmp/jaato.sock` cargo-cult; if it
recurs, capture the daemon log and file it.
