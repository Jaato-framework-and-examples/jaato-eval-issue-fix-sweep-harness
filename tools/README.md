# tools

Two things you reach for *after* a sweep, and they measure different kinds of
thing. The sweep itself is a **quantitative** instrument: it produces verdicts,
costs, token counts, pass rates, determinism hashes. `jaato_eval.cli report` is the
rest of that instrument — it makes the numbers legible per arm instead of per
configuration. `interrogate/` is not part of that instrument at all. It
produces **prose**, and it is the only way to ask an arm a question the numbers
cannot answer.

| | kind | question it answers | cost |
|---|---|---|---|
| `jaato_eval.cli report` | quantitative | what happened to each arm | free |
| `interrogate/` | qualitative | *why* it did that, and what it saw | one model turn |

## Why the numbers are not enough

Run 22 is the clearest case. Three arms, three PASSes, indistinguishable in
`results.jsonl` beyond cost:

    glm53      PASS  $1.57
    gpt5mini#0 PASS  $0.20
    gpt5mini#1 PASS  $0.29

Reading the sessions showed two opposite pieces of engineering behind those
identical verdicts. One arm ran the acceptance script itself before claiming
completion, then ran the full 120-test suite for regressions, and never
triggered the completion gate. The other never ran a single test — eleven of
its thirteen shell commands were `ls`/`grep` exploration and the other two were
`git commit` — and passed only because the gate refused its first
`signal_completion` and handed it the failures.

Both are PASS. On a task without a completion gate, one of them ships.

**No column can carry that.** You could add "ran the acceptance script" as a
boolean and it would be gamed the moment it mattered; what you actually want to
know is whether the model *understood* that verifying was its job, and that is
a reading of its narration, not a measurement of its output.

So: use the report to find the arm worth reading, and `interrogate/` to ask it
the question the report raised. The report tells you *which* arm to be
suspicious of; only the session can tell you whether the suspicion is fair.

---

# the per-arm report

There is no wrapper script — the framework's CLI is the interface:

    python -m jaato_eval.cli report results.jsonl --html report.html

Writes a self-contained HTML document (no dependencies, print CSS included, so
a browser is the PDF renderer). `--pdf out.pdf` renders directly but needs
`pip install 'jaato-eval[report]'`, and **fails loudly** rather than silently
writing HTML only — an unattended run cannot report success without the PDF.

The **exit code is the verdict**: `0` all passed, `1` some arm FAILed, `2` some
arm was BLOCKED. A CI wrapper can act on it.

Archived sweeps live in [`sweeps/`](../sweeps/) and render the same way.

## When

Before reading verdicts, not after. The markdown pivot the CLI prints answers
*"which configuration won"*; this answers *"what happened to arm 3"*, which is
the question a FAIL actually raises.

It is also the join onto the provider's own record: the **session id** column
is what OpenRouter's console groups by. A row here leads straight to that arm's
generations, upstream provider and per-request cost — which is how a
`MALFORMED_FUNCTION_CALL` was diagnosed when the framework's own error said
only `Provider returned an error`.

---

# interrogate

Wake a **finished** arm and ask it to account for something. Vendored from
`prime-agents-vs-jaato/tools/interrogate`; re-copy rather than diverge.

    python tools/interrogate/interrogate.py \
        <session_id> <workspace> <env_file> <question.md> [config_root]

Session ids are the filenames under `<workspace>/.jaato/sessions/`, and the
report's session-id column is the same value.

## When it is worth a turn

When the verdict does not explain itself, and the answer is only inside the
session. Three cases that recur:

* **A PASS you distrust.** An arm passed every grader and you want to know
  whether it verified anything or got lucky. One arm ran the acceptance script
  itself before claiming completion; another never ran a test and was caught
  only by the completion gate. Same verdict, different foundations, and
  nothing in `results.jsonl` distinguishes them.
* **A wrong self-diagnosis.** An arm blamed its own parameter passing four
  times for what turned out to be a framework bug. Asking *what did you see*
  is how you find out whether the model could have known better.
* **A guard the arm never ran.** Two arms in two runs shipped a complexity
  ratchet violation because they ran the plugin suite and not `shared/tests`.
  Asking *what would have told you it applied to you* is a question about the
  repo, and only the session can answer it.

## THE CONTRACT TRAP — read this before writing a question

A revived session answers under **its own completion contract**, and that
decides how many turns it gets.

**Removing the completion schema gives the session exactly one turn.**
Declaring a schema is what exposes `signal_completion` at all; without one, the
session ends as soon as the model returns text with no tool call. That answers
a question and is useless for anything else — an interrogation asking for a fix
produced one sentence, *"First, reproduce the audit failure exactly as
described"*, and ended with no commit and a clean tree.

So the shipped `interrogate` set does **not** remove the schema. It overrides
it with an empty one (`completion_payload_schema: {}`), which still exposes
`signal_completion` and keeps the session multi-turn, while dropping the
sweep's payload requirements that your question never involved.

The sweep's acceptance processor is **declined**, not inherited. That used to
be impossible — `completion_processors` concatenated with no opt-out, so an
interrogation carried the sweep's gate and asking a *failing* arm a question
made it try to fix its failures instead of answering. The
profile now names the processor in `suppress_inherited_processors`, and an
entry that matches nothing is an **error**, so a base that renames its script
fails loudly rather than quietly re-acquiring the gate.

## Selecting the interrogation contract — DO THIS FIRST

The profile set is shipped: `tasks/issue-fix/.jaato/profiles/interrogate/`.
It declares a `worker` that does **not** inherit `_base_worker`, so the
completion schema — and with it the processor that gates on acceptance
criteria — is absent.

    cp tools/interrogate/interrogate.env.example interrogate.env
    # edit JAATO_CONFIG_ROOT to <checkout>/tasks/issue-fix/.jaato

    python tools/interrogate/interrogate.py \
        20260901_215524 \
        .jaato-eval-workspaces/issue-fix_sweep@openrouter_glm53_0 \
        interrogate.env \
        question.md

`interrogate.env` carries the one line that does the work:

    JAATO_PROFILE_SET=interrogate

A profile set is a subdirectory of `profiles/` scanned **first**, winning on
name collision, so it shadows the sweep's `worker` without touching it. Omit
that line and the arm revives under the sweep's contract, answers your
question, then argues with a completion processor about criteria the question
never involved — burning turns on a gate irrelevant to what you asked.

**Do not** try to point `config_root` at a different directory instead. The
daemon's resolution order (`_resolve_restore_config_root`) prefers the
session's **saved** `config_root`, so the argument is only a fallback for
sessions that never persisted one. It is silently ignored, and you will spend
an hour finding out why. The profile set is the supported seam; the
`config_root` argument is not.

### Asking for work rather than an answer

Same set — the empty schema keeps the session multi-turn, so it can read, run
and commit. The difference is in the question, not the profile: say which
commands to run and to report their verbatim output, and expect the acceptance
processor to fire at the end (see the residual above).

## Reviving an arm of THIS harness

An arm's persona carries a mandatory prefetch (`{{!py:...}}` in `worker.md`),
and that used to make revival impossible: bootstrap re-ran the prefetch against
an empty `agent_params` and aborted before the session came up. A revive now
wakes from persisted state instead, so the prefetch is not re-run and the arm
comes back with the repo and issue it was created with.

Not yet exercised end to end on an arm of this repo — the first interrogation
here is also the test of it, so read the session log if it does not come up.

## Writing the question

`question-template.md` is the starting point. Its discipline, which is not
obvious:

1. **Say plainly whether anything is wrong.** If the work was accepted, open
   with that — an agent that thinks it is in trouble writes apologies instead
   of accounts.
2. **Quote the observation exactly.** Give the log line, the diff, the command
   output — not your reading of it. A session handed an interpretation agrees
   with it; one handed evidence goes and checks.
3. **Demand verbatim output including failures.** *"Do not work around a
   failure, I want the failure."* The instinct is to route around a broken
   thing and report success.
4. **End the turn `suspended`, not `finished`** when the session was created
   under a goal profile — it is not completing anything, only pausing again.

### Example: asking why a guard was missed

    Not a new goal — a question about work you have already done, and you are
    not in trouble. Your fix was accepted: all three acceptance tiers pass.

    THE OBSERVATION. Run from your own repo/ with
    PYTHONPATH=$PWD/jaato-server:$PWD/jaato-sdk:

        $ pytest -q jaato-server/shared/tests/test_cyclomatic_complexity_audit.py
        E   ...::_execute_update_file: 16 -> 17 (+1)
        1 failed, 3 passed

    THE ASK. Tell me, in prose: you ran the full file_edit suite during your
    original work and it passed. What would have had to be different for you
    to have run shared/tests too — is there something in the repo that would
    have told you it applied to you, or did nothing point at it?

    End this turn `suspended`, not `finished`.

### Example: asking it to fix something

Same shape, but the session **must keep its completion gate** — do not use an
`interrogate` profile set for this. Add to the ask:

    Fix it without raising the baseline number. Then re-run each of these and
    report the verbatim output, INCLUDING failures:

        pytest -q jaato-server/shared/tests/test_cyclomatic_complexity_audit.py
        pytest -q jaato-server/shared/plugins/file_edit/
        bash ../acceptance.sh --all 782

    Commit on the same branch.

## What it costs, and one caveat

One model turn on the arm's own model, at whatever that arm's rate was —
cents, not dollars. The question reaches the session as **untrusted content**
(the daemon wraps it), so what obliges the agent to answer is its persona, not
your text. A persona that says nothing about answering operators may decline.
