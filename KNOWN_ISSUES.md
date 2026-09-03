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
| 2 | A language server outlives its session; nothing reaps it | [#806](https://github.com/Jaato-framework-and-examples/jaato/issues/806) | open — reap by hand between sweeps |
| 3 | A session outlives its client, ungradeable and unstoppable | [#812](https://github.com/Jaato-framework-and-examples/jaato/issues/812) | open — check for a live session before touching a workspace |

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

## 2. A language server outlives its session; nothing reaps it

`pylsp` survives the session that spawned it, survives `reset_for_next_session`,
and survives the slot's return to the idle pool. On this harness it indexes the
subject monorepo — `.lsp.json` points jedi at `repo/`, `repo/jaato-server` and
`repo/jaato-sdk` so first-party imports resolve — and one server reached
**3.2 GB**.

It ended a sweep. Run 24's first attempt measured ONE arm before the host
reached 11 GiB used / 474 MiB available with swap full; the remaining four came
back `can't start new thread` and `SessionNotConfirmed`. Neither count was
binding — threads 32k against a 94k max, processes 571 against a 47k ulimit —
memory was. Three servers were holding 3.7 GB with no live session at all.

**Workaround: reap by hand, AFTER a sweep ends and never during one.**

```bash
ps -eo args | grep "[j]aato_eval" | grep -v "bash -c"   # must print nothing
for p in $(pgrep -f "[p]ylsp"); do
  pp=$(ps -o ppid= -p $p | tr -d ' ')
  case "$(ps -o args= -p $pp)" in *server.runner*) kill -TERM $p;; esac
done
```

The first line is the load-bearing one: a `pylsp` under a `server.runner`
parent belongs to whichever arm is running *now* if one is, and killing it
would break a live measurement.

Keeping the `lsp` plugin is a deliberate trade — without it arms write Python
blind, which the README explains — so the cost is this manual step between
sweeps, not a config change.

## 3. A session outlives its client, ungradeable and unstoppable

Killing the `jaato_eval` client does not end the sessions it created. The
daemon-side session keeps working: no grader survives to score it, `--arm-timeout`
is enforced client-side so it never applies, and the task pool's `seconds` is
reconciled only when a session ends. Only a profile's own `budget_control`
stops it.

Observed: a session ran seven minutes past its client's death, reached
`signal_completion`, and terminated at **$2.5198** — its full per-arm ceiling —
having produced nothing that was ever recorded.

It also cannot be stopped from outside. Nothing records which runner serves a
session: `~/.jaato/session_workspace_index.json` maps the session to its
workspace and names no pid or slot, and the session record has no runner-shaped
key. The only options are killing a runner identified circumstantially on a
shared daemon, or waiting for the budget to burn.

**Workaround: never treat a workspace as inert because its client died.**
Before deleting or reusing one, prove nothing is writing to it:

```bash
s1=$(stat -c%s "$W/provider_trace.log"); sleep 6
[ "$s1" = "$(stat -c%s "$W/provider_trace.log")" ] || echo "LIVE — do not touch"
```

Skipping that check cost $2.52 and destroyed a running arm's worktree: the
agent kept working from memory against files that no longer existed until its
budget ran out.

## Resolved: interrogation, and the gate it used to inherit

Two things that used to block interrogating an arm are both fixed upstream.

A revive now wakes from persisted state rather than re-running the persona's
mandatory prefetch against an empty `agent_params`, which is what made every
arm of this harness unrevivable — `worker.md` carries exactly such a prefetch.

And the interrogate profile used to inherit the sweep's acceptance gate with no
way to decline it. `suppress_inherited_processors` now removes it, verified:
schema `{}`, **0 processors**, with `budget_control`, `max_turns: 15`,
`runtime_limits` and all six plugins still inherited.

Neither has been exercised end to end on an arm of this repo yet.

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
