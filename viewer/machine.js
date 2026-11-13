// Viewer modes. Spec: docs/statecharts.md chart 1; test_viewer.py requires this table to equal the chart.
// MACHINE[state][event] = next state. An event not listed for the current state is ignored.
export const MACHINE = {
  loading: { loaded: 'orbit', failed: 'error' },
  orbit: { enterWalk: 'walk', teleport: 'walk' },
  walk: { pause: 'paused', exitWalk: 'orbit', teleport: 'walk', unstick: 'walk' },
  paused: { resume: 'walk', teleport: 'walk', exitWalk: 'orbit' },
  error: {},
};

export function createMachine(onChange) {
  let state = 'loading';
  const trace = ['loading'];
  return {
    get state() { return state; },
    trace,
    // Only writer of the mode. Returns false when the event is not drawn from the current state.
    send(event, arg) {
      const next = MACHINE[state][event];
      if (next === undefined) return false;
      const prev = state;
      state = next;
      trace.push(`${event}:${next}`);
      onChange(next, prev, event, arg);
      return true;
    },
  };
}
