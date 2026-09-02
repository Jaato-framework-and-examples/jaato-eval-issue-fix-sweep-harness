# Acceptance criteria for jaato #694 — a server-supplied Retry-After overrides
# the local backoff cap with no ceiling.
#
# BEHAVIOURAL, and built as a PAIR that a plausible wrong answer cannot both
# satisfy.  The obvious fix — clamp Retry-After to `max_delay` — bounds the
# absurd hint and breaks the ordinary one, and the issue says so in its own
# words: "When a provider says wait 90 seconds for a genuine rate-limit reset,
# retrying at 30s just burns another 429 and can extend the limit.  Honouring
# the server is usually correct; honouring it *without limit* is not."
#
# So: 90 must survive, 3600 must not.  Every number here is the issue's, not
# mine — 3600 is its own example of the stall, 90 its own example of the
# nuance to keep.
#
# WHAT IS DELIBERATELY NOT CHECKED.  The issue's suggested shape names a config
# knob (`AI_RETRY_MAX_SERVER_DELAY`), a log line, and an event.  None is
# asserted here: the knob's NAME is the arm's to choose, and the other two need
# a live session to observe.  An arm is graded on the behaviour the defect
# describes, never on having guessed an identifier.
#
# Contract: REPO and PYBIN are exported by acceptance.sh.
set -uo pipefail

PP="$REPO/jaato-server:$REPO/jaato-sdk"

# Prove the import from the ARM'S TREE first.  Without this a broken edit
# fails silently into an empty result and every assertion below passes
# vacuously — the failure mode that produced a false PASS on 2026-09-01
# before #782's guard existed.
if ! (cd "$REPO" && PYTHONPATH="$PP" "$PYBIN" -c \
        "import shared.retry_utils" >/dev/null 2>&1); then
    echo "cannot import shared.retry_utils from the arm's tree with ${PYBIN##*/} — the check cannot run, so it cannot pass."
    exit 1
fi

out="$(cd "$REPO" && PYTHONPATH="$PP" "$PYBIN" - <<'PY' 2>&1
import json
from shared.retry_utils import calculate_backoff, RetryConfig

cfg = RetryConfig()
# Jitter is random, so anything asserting a bound samples rather than trusting
# one draw.  The bound is computed FROM THE CONFIG, never hardcoded: an arm may
# legitimately change the defaults, and a check that pinned 30.0 would grade
# the constant instead of the behaviour.
CEILING = cfg.max_delay * (1 + cfg.jitter_factor)
SAMPLES = 25
verdict = {}

# 1. THE DEFECT.  An hour-long server hint is honoured verbatim today.
try:
    delay = calculate_backoff(1, cfg, 3600.0)
    verdict["absurd_hint_bounded"] = isinstance(delay, (int, float)) and delay < 3600.0
    verdict["absurd_hint_value"] = delay
except Exception as exc:
    # "either wait the ceiling or fail the attempt" — the issue allows both,
    # so a refusal is a bound, not a crash.
    verdict["absurd_hint_bounded"] = True
    verdict["absurd_hint_value"] = f"raised {type(exc).__name__}"

# 2. THE NUANCE THE FIX MUST PRESERVE, and where clamping to max_delay dies.
try:
    delay = calculate_backoff(1, cfg, 90.0)
    verdict["modest_hint_honoured"] = abs(delay - 90.0) < 1e-9
    verdict["modest_hint_value"] = delay
except Exception as exc:
    verdict["modest_hint_honoured"] = False
    verdict["modest_hint_value"] = f"raised {type(exc).__name__}"

# 3. REGRESSION: our own exponential is still capped by max_delay.  Passes on
#    the unfixed tree, so it is not the defect — it is the thing a fix must
#    not trade away while bounding the other branch.
try:
    ours = [calculate_backoff(20, cfg) for _ in range(SAMPLES)]
    verdict["own_backoff_still_capped"] = all(0 < d <= CEILING for d in ours)
    verdict["own_backoff_max"] = max(ours)
except Exception as exc:
    verdict["own_backoff_still_capped"] = False
    verdict["own_backoff_max"] = f"raised {type(exc).__name__}"

# 4. REGRESSION: a negative hint is not authoritative.  Also passes today —
#    `-5 > delay` is simply False — and an arm that starts trusting the
#    server's number without checking its sign would break it.
try:
    neg = [calculate_backoff(1, cfg, -5.0) for _ in range(SAMPLES)]
    verdict["negative_hint_not_authoritative"] = all(0 < d <= CEILING for d in neg)
    verdict["negative_hint_min"] = min(neg)
except Exception as exc:
    verdict["negative_hint_not_authoritative"] = False
    verdict["negative_hint_min"] = f"raised {type(exc).__name__}"

print(json.dumps(verdict))
PY
)"

fail=0
check() {  # key, human-readable failure
    if ! echo "$out" | grep -q "\"$1\": true"; then
        echo "$2"
        fail=1
    fi
}
check absurd_hint_bounded \
    "calculate_backoff(1, cfg, 3600) still returns 3600 — a server-supplied Retry-After is honoured with no ceiling, so one upstream response parks a session for an hour."
check modest_hint_honoured \
    "calculate_backoff(1, cfg, 90) no longer returns 90 — a genuine 90s rate-limit reset must still be waited out. Clamping the server's value to max_delay is not the fix: retrying at 30s burns another 429."
check own_backoff_still_capped \
    "our own exponential backoff is no longer bounded by max_delay * (1 + jitter_factor) — the cap that already worked must survive the fix."
check negative_hint_not_authoritative \
    "a negative Retry-After now reaches the delay — a nonsense value must be treated as absent, not obeyed."

if [ $fail -ne 0 ] && ! echo "$out" | grep -q '^{'; then
    echo "(the probe produced no verdict at all; raw output: ${out:0:200})"
fi
exit $fail
