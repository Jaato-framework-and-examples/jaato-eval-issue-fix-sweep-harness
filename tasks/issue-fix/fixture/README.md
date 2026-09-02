# Scratch workspace

Copied fresh for every arm by jaato_eval's fixture step (a plain
`shutil.copytree`, no git anywhere in it). The mandatory prefetch
`.jaato/scripts/checkout_worktree.py` runs before the agent's first turn
and materialises the REAL git checkout at `repo/` inside this copy — a
`git worktree add` against a shared bare mirror, on a branch unique to
this arm. Nothing here is a git repo until that script runs.
