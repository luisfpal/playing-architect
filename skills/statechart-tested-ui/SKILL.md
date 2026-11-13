---
name: statechart-tested-ui
description: Use when building an interactive UI with modes (loading, orbit, walk, paused, error). The modes live in one table that a test compares with the documented chart.
---
# Statechart-tested UI

1. Draw the modes and events as a Mermaid `stateDiagram-v2` in the docs. That diagram is the spec.
2. Implement it as one table (`MACHINE = { state: { event: nextState } }`) and one `send(event)` function. `send` is the
   only writer of the mode; events not in the table are ignored and return false. No `isWalking` flags elsewhere.
3. A test parses the Mermaid block and the table and fails if they differ, so the diagram cannot drift from the code.
4. Drive every transition in a headless browser with real input and assert the visible result (HUD text, pointer lock,
   camera position), not only the internal state.
5. Ignored events are tested too: send each event from every state where it is not drawn and expect no change.
