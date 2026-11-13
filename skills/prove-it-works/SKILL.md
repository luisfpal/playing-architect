---
name: prove-it-works
description: Use before declaring any task done. Check the result the way its user will meet it, and prove the checks can fail.
---
# Prove it works

1. State the claim in one sentence a user would care about ("the player can walk from the street into every room").
2. Test that claim through the real interface: real key, mouse and touch events in a real browser on the real asset,
   not a unit test of a helper.
3. Prove the check can fail. Break the thing on purpose (a mutant model with a wall across a door, a missing file)
   and confirm the check goes red. A check that has never failed proves nothing.
4. Keep the evidence: logs, screenshots, the exact command. Someone else must be able to re-run it.
5. Report what was not tested, plainly. "Not tested on Safari" is useful; silence is not.
6. If a test was wrong (not the product), fix the test, say so, and show the product behaviour it now checks.
