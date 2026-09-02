# Comparative fix-attempt judge

Several independent agents each attempted to fix the SAME GitHub issue,
each in their own git worktree. You are shown every attempt's diff
below, at once, and must RANK them against each other — not score any
one in isolation.

## Every arm's diff

{{!py:scripts/render_arms_for_judge.py}}

## How to judge

1. Compare the diffs above to each other and to the issue they were all
   trying to fix (quoted in the same section).
2. An arm with no diff (`(no changes)` below) did not attempt a fix —
   rank it last, and say so in `reasoning`.
3. Rank on whether the change plausibly fixes the issue, not on style.
   A smaller correct diff beats a larger one that does not address the
   issue.
4. Quote the bytes you are judging (a line from the diff, a file name)
   in `reasoning` for every arm — a ranking nobody can check against the
   diffs is not a ranking.
5. If anything prevented you from comparing the arms, put it in `errors`
   and the harness will record this run as unjudged rather than as a
   ranking.
