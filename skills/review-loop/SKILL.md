---
name: review-loop
description: Use when a change needs review before it merges. A bounded loop of independent review, fix, and re-check.
---
# Review loop

1. Work on a branch. Merge with merge commits; never rewrite shared history.
2. Ask a second agent (a different model if possible, with no memory of the work) to review the diff against the
   stated goal. Give it the claim, the diff and the evidence, not your reasoning.
3. Sort findings: real bug, real risk, taste, or wrong. Fix the first two; record why the others were not taken.
4. Re-run the full checks after the fixes. A fix without a re-run is not a fix.
5. Stop after three rounds. If it is still not clean, the problem is the design: write that down and escalate.
