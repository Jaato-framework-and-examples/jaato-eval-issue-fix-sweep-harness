# Acceptance criteria for jaato #782 — updateFile with only `path` truncates
# the file to zero bytes and reports success.
#
# BEHAVIOURAL, not grep-based, and deliberately so.  #715's criteria were two
# greps over CLI output, and three arms passed them with materially different
# quality (one omitted subcommands, one omitted the drill-down line, none
# wrote the guard the issue asked for).  A grep cannot tell those apart.  The
# defect here is an observable state change — a file's bytes — so the check
# asserts the bytes.
#
# Contract: REPO and PYBIN are exported by acceptance.sh.
set -uo pipefail

PP="$REPO/jaato-server:$REPO/jaato-sdk"

# The plugin must import from the arm's tree.  Proving this first means a
# broken import fails LOUDLY here rather than silently passing the assertions
# below on an empty result (the failure mode that produced a vacuous PASS on
# 2026-09-01 before this guard existed).
if ! (cd "$REPO" && PYTHONPATH="$PP" "$PYBIN" -c \
        "import shared.plugins.file_edit.plugin" >/dev/null 2>&1); then
    echo "cannot import shared.plugins.file_edit from the arm's tree with ${PYBIN##*/} — the check cannot run, so it cannot pass."
    exit 1
fi

out="$(cd "$REPO" && PYTHONPATH="$PP" "$PYBIN" - <<'PY' 2>&1
import json, tempfile, os
from shared.plugins.file_edit.plugin import FileEditPlugin

ws = tempfile.mkdtemp()
target = os.path.join(ws, "victim.txt")
ORIGINAL = "line one\nline two\nline three\n"
open(target, "w").write(ORIGINAL)

p = FileEditPlugin()
p.initialize({"workspace_root": ws, "backup_dir": os.path.join(ws, ".backups")})
p.set_workspace_path(ws)

verdict = {}

# 1. The defect itself: only `path`, no old/new, no new_content.
try:
    res = p._execute_update_file({"path": "victim.txt"})
except Exception as e:
    res = {"error": f"raised {type(e).__name__}: {e}"}
after = open(target).read()
verdict["bytes_preserved"] = (after == ORIGINAL)
verdict["reported_error"] = bool(isinstance(res, dict) and res.get("error"))
verdict["did_not_claim_success"] = not (isinstance(res, dict) and res.get("success"))

# 2. A deliberate truncation must STILL work — the fix must distinguish
#    "omitted" from "explicitly empty", not ban empty content.
open(target, "w").write(ORIGINAL)
try:
    res2 = p._execute_update_file({"path": "victim.txt", "new_content": ""})
    verdict["explicit_empty_still_allowed"] = (open(target).read() == "")
except Exception:
    verdict["explicit_empty_still_allowed"] = False

# 3. A normal targeted edit must be unaffected.
open(target, "w").write(ORIGINAL)
try:
    p._execute_update_file({"path": "victim.txt", "old": "line two", "new": "LINE TWO"})
    verdict["targeted_edit_unaffected"] = ("LINE TWO" in open(target).read())
except Exception:
    verdict["targeted_edit_unaffected"] = False

print(json.dumps(verdict))
PY
)"

fail=0
check() {  # name, human-readable failure
    if ! echo "$out" | grep -q "\"$1\": true"; then
        echo "$2"
        fail=1
    fi
}
check bytes_preserved            "updateFile(path=...) with no old/new/new_content still changed the file — its bytes must be preserved when the call names no content."
check reported_error             "updateFile(path=...) with no content returned no error — a call that cannot be carried out must say so, not report success."
check did_not_claim_success      "updateFile(path=...) with no content still reported success:true — that is what leaves an agent unable to detect the loss."
check explicit_empty_still_allowed "an explicit new_content='' no longer truncates — the fix must distinguish OMITTED from DELIBERATELY EMPTY, not forbid empty files."
check targeted_edit_unaffected   "a normal targeted old/new edit stopped working — the fix must not disturb the targeted path."

if [ $fail -ne 0 ] && ! echo "$out" | grep -q '^{'; then
    echo "(the probe produced no verdict at all; raw output: ${out:0:200})"
fi
exit $fail
