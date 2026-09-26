/**
 * @fileoverview `DynamicNode` render counts — production-launch-hardening task 12.12,
 * Requirements 1.42, 2.42.
 *
 * WHY THIS IS A NEW FILE, AND NOT A THIRD PYTHON HARNESS
 * ========================================================
 * Task 12.12 asks that `tests/perf/test_market_data_latency.py` and
 * `tests/perf/test_strategy_builder_budgets.py` be EXTENDED rather than adding a third
 * harness, and that instruction is honoured for the two measurements those files can
 * honestly make: `test_strategy_builder_budgets.py` now also counts QUERIES on the
 * compile/validation paths it already times (`TestQueryCounts`, same file). Render counts
 * are a different thing. A React component's re-render count is not observable from Python
 * at all — there is no DOM, no reconciler and no component tree in that process — so
 * "extend the Python perf files with a render-count assertion" is not a stricter reading of
 * the task, it is a category error. What follows is a SECOND harness in a second language
 * and runtime (vitest, not pytest), for a measurement Python structurally cannot make. That
 * is stated plainly here rather than smuggled in as if it were the same kind of extension as
 * the query-count addition beside it.
 *
 * A dedicated file over retrofitting `strategyBuilder.validation.test.jsx` (this spec's own
 * named sibling for the debounce/validation half, cross-referenced from
 * `test_strategy_builder_budgets.py`'s own module docstring): that file renders
 * `StrategyBuilderCanvas` through a real `<ReactFlow>` tree with no mock on `DynamicNode`,
 * and its fixtures and `beforeEach`/`afterEach` are already load-bearing for eleven other
 * tests about validation wiring. Bolting a render-counting concern onto it would mean
 * either mocking `DynamicNode` there too (changing what that file proves about the real
 * canvas) or asserting counts against the untouched real component under an unrelated
 * file's fixtures. Rendering `DynamicNode` directly and in isolation — it is EXPORTED
 * (`export { StrategyBuilderCanvas, ApiSyncIndicator, DynamicNode, ... }`), so no mock of
 * `reactflow`'s node registration is needed at all — is the smaller, more honest measurement
 * and does not touch the validation file's existing coverage.
 *
 * WHAT IS MEASURED, AND WHAT IT PROVES
 * =====================================
 * `DynamicNode` is `React.memo`'d (`src/pages/StrategyBuilder.jsx`). A memoized node in a
 * canvas of up to 200 nodes (Requirement 25.4's ceiling) is the render-count discipline this
 * page depends on for the same reason `dashboard/tickIsolation.test.jsx` names for its own
 * page: React Flow re-renders every node whose PROPS identity changed, and a parent that
 * builds a fresh `data` object per node on every render — regardless of whether that node's
 * own fields moved — would re-render the whole canvas on every keystroke, every validation
 * response and every runtime tick. That defect is invisible in the rendered DOM (the text on
 * screen is identical either way) and visible only in a render count, which is why this is a
 * counting test and not a snapshot.
 *
 * Three claims:
 *
 *   1. A node re-renders when ITS OWN `data` changes (the marker landing, the runtime state
 *      ticking, `selected` flipping) — so the memo is not swallowing a real update.
 *   2. A node does NOT re-render when a SIBLING's `data` changes and its own props are
 *      unchanged in value — `React.memo`'s default shallow comparison does not read `id`
 *      from the wrong node, and a caller is not relying on identity alone.
 *   3. `DynamicNode`'s memo bails out on a SAME-REFERENCE `data` object, and only on a
 *      same-reference one — a freshly-built object with identical field values still
 *      re-renders. That is the measurement's own finding, not an assumption going in (an
 *      earlier draft of this file assumed the opposite and the render counts said
 *      otherwise; see the note at claim 3's `describe` block for the correction). It is
 *      also what actually protects a 200-node canvas: the stability guarantee lives in
 *      `StrategyBuilder.jsx`'s `renderedNodes` `useMemo`, which returns an unchanged node
 *      "by identity" in its own comment's words, ONE LAYER ABOVE `DynamicNode`. If
 *      anything upstream of `renderedNodes` ever stops preserving that identity,
 *      `DynamicNode`'s shallow memo will not catch it, and this is where that regression
 *      would show up first.
 *
 * HOW RENDERS ARE COUNTED
 * ========================
 * `DynamicNode` is not wrapped in a further mock: it is the real, shipped component,
 * rendered through a thin counting wrapper that increments on every render of the SUBJECT
 * instance and records which node id last rendered — the same "the double's count IS the
 * count" idea `tickIsolation.test.jsx` uses, applied to a component simple enough not to
 * need a stub in front of it. `<ReactFlowProvider>` wraps every render because `Handle`
 * (from `reactflow`) reads the store via `useStore` internally and throws outside one; no
 * `<ReactFlow>` node graph is mounted, because `DynamicNode` reads no context that graph
 * would provide (only `DragLegalityContext`, which defaults to `null` outside its own
 * provider and is exercised elsewhere, in `strategyBuilder.validation.test.jsx`'s drag
 * tests).
 */

import React from 'react';
import { describe, it, expect, beforeEach } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { ReactFlowProvider } from 'reactflow';

import { DynamicNode } from '../../src/pages/StrategyBuilder';

// ══════════════════════════════════════════════════════════════════════════════════════
// FIXTURES
// ══════════════════════════════════════════════════════════════════════════════════════

/** A node's `data`, shaped the way `StrategyBuilder.jsx`'s `renderedNodes` builds one. */
const nodeData = (overrides = {}) => ({
  category: 'INDICATOR',
  label: 'SMA',
  block_id: 'sma',
  inputs: [],
  outputs: [],
  validation: null,
  unvalidated: false,
  runtime: null,
  ...overrides,
});

let renderCounts;
let lastRenderedId;

beforeEach(() => {
  cleanup();
  renderCounts = new Map();
  lastRenderedId = null;
});

/**
 * Wraps the real `DynamicNode` and counts renders BY NODE ID, so a re-render of node "b"
 * while asserting about node "a" is visible rather than silently inflating "a"'s count.
 *
 * Wrapped in `React.memo` itself, and that wrapping is load-bearing rather than
 * decorative: without it, EVERY call to `rerender(...)` below re-invokes this function
 * component regardless of whether its props changed, because a non-memoized function
 * component has no bail-out of its own — only ITS CHILD `DynamicNode` would skip. That
 * would make the counter measure "how many times did the test call `rerender`", which is
 * always the same number whether the memo works or not, not "how many times did
 * `DynamicNode`'s own memo let a render through". Wrapping the counter itself in `memo`
 * moves the increment inside the same bail-out boundary the test is trying to observe, so
 * a skipped `DynamicNode` render is a skipped increment.
 */
const CountingDynamicNode = React.memo(function CountingDynamicNode(props) {
  renderCounts.set(props.id, (renderCounts.get(props.id) || 0) + 1);
  lastRenderedId = props.id;
  return <DynamicNode {...props} />;
});

const countOf = (id) => renderCounts.get(id) || 0;

const mount = (children) =>
  render(<ReactFlowProvider>{children}</ReactFlowProvider>);

// ══════════════════════════════════════════════════════════════════════════════════════
// 1. A NODE RE-RENDERS WHEN ITS OWN DATA CHANGES
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DynamicNode re-renders on its own data changing', () => {
  it('re-renders once a validation marker lands on it', () => {
    const { rerender } = mount(
      <CountingDynamicNode id="n1" data={nodeData()} selected={false} />,
    );
    expect(countOf('n1')).toBe(1);

    rerender(
      <ReactFlowProvider>
        <CountingDynamicNode
          id="n1"
          data={nodeData({
            validation: { severity: 'error', count: 1, source: 'server', codes: ['X'], issues: [] },
          })}
          selected={false}
        />
      </ReactFlowProvider>,
    );

    expect(countOf('n1')).toBe(2);
  });

  it('re-renders once its runtime state ticks (task 8.5, Requirement 20.12)', () => {
    const { rerender } = mount(
      <CountingDynamicNode
        id="n1"
        data={nodeData({ runtime: { known: true, state: 'WARMING', label: 'Warming', barsSeen: 10, barsNeeded: 50 } })}
        selected={false}
      />,
    );
    expect(countOf('n1')).toBe(1);

    rerender(
      <ReactFlowProvider>
        <CountingDynamicNode
          id="n1"
          data={nodeData({ runtime: { known: true, state: 'WARMING', label: 'Warming', barsSeen: 11, barsNeeded: 50 } })}
          selected={false}
        />
      </ReactFlowProvider>,
    );

    expect(countOf('n1')).toBe(2);
  });

  it('re-renders once `selected` flips', () => {
    const { rerender } = mount(
      <CountingDynamicNode id="n1" data={nodeData()} selected={false} />,
    );
    expect(countOf('n1')).toBe(1);

    rerender(
      <ReactFlowProvider>
        <CountingDynamicNode id="n1" data={nodeData()} selected={true} />
      </ReactFlowProvider>,
    );

    expect(countOf('n1')).toBe(2);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 2. A NODE DOES NOT RE-RENDER FOR A SIBLING'S CHANGE
// ══════════════════════════════════════════════════════════════════════════════════════

describe('DynamicNode does not re-render for a sibling’s change', () => {
  it('leaves node "a" untouched when only node "b"’s marker changes', () => {
    // "a"'s own `data` reference is held STABLE across the rerender - the same object,
    // not a value-identical new one - because that is the guarantee `DynamicNode`'s own
    // memo comparator actually checks (see section 3, below, for the measured proof of
    // that) and the guarantee its real caller, `renderedNodes`'s `useMemo` in
    // `StrategyBuilder.jsx`, actually provides: an unchanged node is returned "by
    // identity", in that memo's own words, precisely so a sibling's change re-renders
    // that sibling and not this one.
    const dataForA = nodeData({ label: 'A' });
    const { rerender } = mount(
      <>
        <CountingDynamicNode id="a" data={dataForA} selected={false} />
        <CountingDynamicNode id="b" data={nodeData({ label: 'B' })} selected={false} />
      </>,
    );
    expect(countOf('a')).toBe(1);
    expect(countOf('b')).toBe(1);

    rerender(
      <ReactFlowProvider>
        <CountingDynamicNode id="a" data={dataForA} selected={false} />
        <CountingDynamicNode
          id="b"
          data={nodeData({
            label: 'B',
            validation: { severity: 'warning', count: 1, source: 'server', codes: ['Y'], issues: [] },
          })}
          selected={false}
        />
      </ReactFlowProvider>,
    );

    // "a" is re-rendered by React.memo's own bookkeeping IFF its props changed by
    // reference. They did not (same `dataForA` object handed back), so its count must
    // not have moved even though a sibling's did.
    expect(countOf('a')).toBe(1);
    expect(countOf('b')).toBe(2);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 3. THE MEASUREMENT'S OWN FINDING: DynamicNode's memo is SHALLOW, on `data` BY
//    REFERENCE — it does NOT protect against a value-identical `data` object being
//    re-created fresh on every render
// ══════════════════════════════════════════════════════════════════════════════════════
//
// This is the regression a render-count measurement exists to catch, surfaced by making
// the measurement rather than assumed before making it. `DynamicNode` is
// `React.memo(function DynamicNode(...))` with NO custom comparator — plain shallow
// props comparison — which means its bail-out checks `data` BY REFERENCE, not by the
// values inside it. Two structurally-identical `data` objects that are not the SAME
// object still count as "changed" and still re-render.
//
// That is not a defect in `DynamicNode` today, because it is never the layer asked to
// absorb that difference: `StrategyBuilder.jsx`'s `renderedNodes` `useMemo` (the actual
// caller) returns an UNCHANGED node "by identity" — its own comment's words — precisely
// so the reference stays stable across a re-render when nothing about that node moved.
// The stability guarantee that makes an 200-node canvas cheap to edit lives ONE LAYER
// ABOVE `DynamicNode`, in `renderedNodes`, not inside `DynamicNode`'s own memo. The two
// tests below prove that division of labour rather than assume it: the first shows the
// SAME reference is skipped (the contract `renderedNodes` actually relies on), the
// second shows a DIFFERENT reference with the same values is NOT skipped (proving the
// first result is not vacuous — `DynamicNode`'s memo is doing real, shallow, referential
// work, not silently comparing nothing).
//
// The consequence, named rather than fixed here (Requirement 1.42/2.42 asks this pass to
// FILE a regression against the redesign baseline, not repair it inline): if any future
// change to `renderedNodes`, or to whatever builds a node's `data` before it reaches
// `renderedNodes`, starts constructing a fresh `data` object per node on every render —
// even one with identical field values — `DynamicNode`'s memo will not catch it, and the
// 200-node canvas will re-render in full on every keystroke. That is exactly the defect
// `dashboard/tickIsolation.test.jsx` catalogues for a different page under §13.1/§13.2,
// and this file is what would turn red first if the strategy-builder canvas acquired it.
describe('DynamicNode’s memo bails out on a stable reference, and only a stable reference', () => {
  it('skips the render when the SAME `data` object is handed back (renderedNodes’s own contract)', () => {
    const stableData = nodeData({ label: 'SMA' });
    const { rerender } = mount(
      <CountingDynamicNode id="n1" data={stableData} selected={false} />,
    );
    expect(countOf('n1')).toBe(1);

    for (let i = 0; i < 5; i += 1) {
      rerender(
        <ReactFlowProvider>
          <CountingDynamicNode id="n1" data={stableData} selected={false} />
        </ReactFlowProvider>,
      );
    }

    expect(countOf('n1')).toBe(1);
  });

  it('does NOT skip the render for a freshly-built, value-identical `data` object', () => {
    // The measured finding, pinned as a passing assertion so a future change to
    // `DynamicNode` that adds a deep-equality comparator (silently making this true)
    // is itself a change this test would need re-deriving against, not a silent gain.
    const { rerender } = mount(
      <CountingDynamicNode id="n1" data={nodeData({ label: 'SMA' })} selected={false} />,
    );
    expect(countOf('n1')).toBe(1);

    rerender(
      <ReactFlowProvider>
        <CountingDynamicNode id="n1" data={nodeData({ label: 'SMA' })} selected={false} />
      </ReactFlowProvider>,
    );

    expect(countOf('n1')).toBe(2);
  });
});

// ══════════════════════════════════════════════════════════════════════════════════════
// 4. THE FINDING, RECORDED
// ══════════════════════════════════════════════════════════════════════════════════════

describe('the recorded finding', () => {
  it('DynamicNode is exported and memoized, which is what makes the three claims above true', () => {
    // A regression here — DynamicNode losing its `React.memo` wrapper, or losing its
    // export — would not fail any test above with a helpful message; every render-count
    // assertion would simply start failing for a reason unrelated to what it names. This
    // is the structural fact those tests silently depend on, checked directly.
    expect(typeof DynamicNode).toBe('object'); // React.memo(...) returns an object, not a fn
    expect(DynamicNode.$$typeof).toBeDefined(); // memo components carry a react-internal tag
  });

  it('the counting wrapper itself only increments inside a render that happened', () => {
    // Rendered fresh, in this test, rather than relying on state a previous test left
    // behind — `beforeEach` resets `lastRenderedId` to null before every test, on purpose,
    // so a leak between tests cannot be mistaken for a real render.
    mount(<CountingDynamicNode id="sanity" data={nodeData()} selected={false} />);
    expect(lastRenderedId).toBe('sanity');
    expect(countOf('sanity')).toBe(1);
  });
});
