# Known issues

Framework behaviour that bites this harness. Each row points at a jaato issue,
so when the fix lands the row can be deleted rather than quietly becoming a
lie. The README deliberately says none of this — it describes how to run the
harness, not what is wrong with the framework this week.

**Check the linked issues before trusting a row.** A closed issue means the
row is stale, not that the workaround is still needed.

| # | What bites | Upstream | Status |
|---|---|---|---|
| 1 | A source edit under a running daemon splits the session | [#790](https://github.com/Jaato-framework-and-examples/jaato/issues/790) | open |
| 2 | Arms with a prefetch persona cannot be revived at all | [#787](https://github.com/Jaato-framework-and-examples/jaato/issues/787) | open — blocks interrogation |

---

## 1. A source edit under a running daemon splits the session

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

## 2. Arms cannot be interrogated at all yet

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
