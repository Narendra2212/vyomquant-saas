/**
 * tests/unit/builder/enginePaths.test.jsx - production-launch-hardening task 1, CLUSTER D.
 *
 * Requirements 1.25, 1.26, 1.27 / 2.25, 2.26, 2.27. `design.md` §Hypothesized Root Cause
 * (cluster D, wave 4). This is the regression test task 9.2 names.
 *
 * WHAT THIS FILE IS
 * -----------------
 * **Bug condition exploration tests.** Sections 1-4 are EXPECTED TO FAIL against the current
 * tree (`F`). The failure is the deliverable: it is the counterexample that proves the defect
 * exists, and the same assertion is what validates the fix in task 11.1. Nothing here is a
 * symptom patch and no assertion has been weakened to make a run green.
 *
 * THE DEFECT, IN ONE SENTENCE
 * ---------------------------
 * Three documented builder capabilities raise `ReferenceError: post is not defined` before
 * they reach the network, and the route each of them was aiming at does not exist either.
 *
 * The three sites, with the identifier each one reaches for and the route it names:
 *
 * | # | Site | Function | Calls | On `F` |
 * |---|---|---|---|---|
 * | 1 | `src/contexts/IndicatorEngineContext.jsx:66` | `computeIndicator` | `post('/indicator/compute', …)` | `ReferenceError: post is not defined` |
 * | 2 | `src/contexts/LogicEngineContext.jsx:55` | `evaluateLogic` | `post('/logic/evaluate', …)` | `ReferenceError: post is not defined` |
 * | 3 | `src/contexts/StrategyEngineContext.jsx:283` | `executeStrategy` | `post('/strategy/execute', …)` | `ReferenceError: post is not defined` |
 *
 * TWO INDEPENDENT DEFECTS PER SITE
 * --------------------------------
 * **(a) `post` is not imported.** All three modules import `{ endpoints }` from `../api` and
 * nothing else from it. `src/api/index.js:19` does re-export `post` - the binding exists and is
 * one word away - so this is an omitted import, not a missing client. Nothing in module scope,
 * nothing in the provider closure and nothing on `globalThis` supplies `post`, so the call
 * expression throws on evaluation. Section 2 asserts this per module.
 *
 * **(b) None of the three routes exists.** `/indicator/compute`, `/logic/evaluate` and
 * `/strategy/execute` appear nowhere under `backend_app/` - not as a decorator path, not as a
 * router prefix, not as a string. Section 3 asserts this per path. `tests/test_route_contract.py`
 * (task 9.1) owns the canonical OpenAPI sweep; this file's check is the narrow one that keeps
 * cluster D's second defect from being taken on trust.
 *
 * The two are independent: importing `post` correctly would turn each site's
 * `ReferenceError` into a 404, and adding the routes without importing `post` would change
 * nothing at all. That is why task 9.2's disposition is "decide per capability", and why this
 * file records both halves rather than only the one that raises.
 *
 * WHY A "DEFINED OUTCOME" ASSERTION ALONE WOULD PASS ON `F`
 * --------------------------------------------------------
 * This is the load-bearing subtlety of cluster D, and the reason section 4 has two assertions
 * per site rather than one.
 *
 * The `post(...)` call sits **inside** each function's `try`, so the `ReferenceError` is caught
 * by that function's own `catch` and translated through `design/errorCopy.js` -
 * `errorLine(err, 'builder')`. A `ReferenceError` carries no `code`, no `reasons`, no `channel`
 * and no `category`, so `translateError` falls all the way through to `fromContext('builder')`
 * and produces `CONTEXT_COPY.builder`:
 *
 *     "Could not load the strategy builder. Nothing is shown rather than a partial reading. Try again."
 *
 * So on `F` every one of the three already produces a *defined* outcome in the letter of the
 * clause: a handled failure carrying authored copy. Two consequences. First, a test that
 * asserted only "no exception escapes and the message is authored" would be **green on `F`**
 * while all three capabilities are dead - the same one-sided-test failure mode
 * `design.md §Testing Strategy` rule 1 records for 1.24. Second, the defect is invisible on
 * screen: a programming error renders as "could not load the builder, try again", so retrying
 * is the one action the copy suggests and the one action that cannot work.
 *
 * Section 4 therefore asserts the two halves separately. The `ReferenceError` half fails on `F`
 * at all three sites; the defined-outcome half passes on `F` for the wrong reason, and says so
 * in its own docblock rather than being omitted - because the fix must keep it true.
 *
 * TASK 9.2's DISPOSITION: IS THE CAPABILITY COMPUTED LOCALLY TODAY?  (section 6)
 * -----------------------------------------------------------------------------
 * `tasks.md` task 9.2 says *"Removing is the likelier correct answer. The builder already
 * computes indicators locally via `DataPipelineContext` and `utils/engineHelpers`"*. Measured
 * against `F`, **that premise does not hold**, and section 6 is the evidence rather than the
 * claim:
 *
 * | Capability | Computed locally? | What the named module actually does |
 * |---|---|---|
 * | Indicator values | **No** | `utils/engineHelpers.js` exports four functions and none of them touches a candle: `getMinDataLength` returns a candle *count*, `validateConditions` returns a list of shape errors, `getTimeframeMs` and `getMaxHistoryForTimeframe` convert an interval label. `DataPipelineContext` *fetches* OHLCV (`fetchOHLCV`, `get('/data/historical')`) and an orderbook imbalance; it publishes no compute function. There is no RSI, EMA, MACD or Bollinger implementation anywhere in `src/`. |
 * | Logic evaluation | **No** | `validateConditions` checks that a condition names a type, an operator, both operands and a known indicator key. It never compares two values, so no signal is produced locally. |
 * | Strategy execution | **Partly, and only the planning half** | `parseGraphToExecutionPlan` (same module, `:32-263`) really does build the whole execution plan locally, with no network. What does not exist locally is the part that *runs* it. |
 *
 * So for all three the choice is not "remove the dead call and let the local computation
 * stand" - there is no local computation to stand. The honest dispositions are *remove the
 * capability* (the three providers have **no consumer**: nothing in `src/` calls
 * `useIndicatorEngine`, `useLogicEngine` or `useStrategyEngine`, which is measured in section 6
 * and is why these calls have never been missed) or *implement the routes*. Section 6 states
 * this as assertions so task 9.2 rests on measurement.
 *
 * REACHABILITY  (section 1)
 * -------------------------
 * All three providers are mounted, nested, in `src/pages/StrategyBuilder.jsx:4590-4603`:
 * `DataPipelineProvider` (`:4590`) → `IndicatorEngineProvider` (`:4591`) →
 * `LogicEngineProvider` (`:4592`) → `StrategyEngineProvider` (`:4593`). Section 1 reads the
 * file and asserts the nesting, so "all three are reachable" is checked rather than assumed -
 * a provider that had quietly been dropped from the wrapper would make one of these a dead
 * clause instead of a defect.
 *
 * HARNESS
 * -------
 * `@testing-library/react` with the three providers mounted in the wrapper's own order and a
 * probe component capturing the three context values - the same pattern
 * `tests/unit/builderRealtime.test.jsx` uses. Two module mocks, both documented at their
 * definition:
 *
 * * `src/api` is replaced by a stub whose `post` **would answer correctly** for all three
 *   paths. That matters: it means the fixed state these tests describe is reachable, and that
 *   a failure here is the call path's, not the stub's. It also lets the file record that the
 *   transport is never reached at all.
 * * `src/design/errorLine` is wrapped by a **pass-through recorder** - it records the error it
 *   was handed and then calls the real implementation. It is an observer, not a substitute:
 *   the real copy path still runs and section 5 asserts its output. Without it the caught
 *   `ReferenceError` is unobservable from outside the provider, which is exactly the hole
 *   described above.
 */

import React from 'react';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render } from '@testing-library/react';

// ---------------------------------------------------------------------------
// The api stub. Hoisted, because all three contexts import `endpoints` at module scope.
//
// `post` resolves with a well-formed response for each of the three paths, so the fixed state
// these tests assert is actually reachable through this harness: a site that imported `post`
// and pointed at a live route would return a computed result here. A stub that rejected would
// make section 4 unfalsifiable.
// ---------------------------------------------------------------------------

const { mockPost, mockGet, mockApi } = vi.hoisted(() => {
  const exchange = { getAccounts: vi.fn() };
  const market = { getSymbols: vi.fn(), getMarketData: vi.fn() };
  return {
    mockPost: vi.fn(),
    mockGet: vi.fn(),
    mockApi: { exchange, market, user: {} },
  };
});

vi.mock('../../../src/api', () => ({
  api: mockApi,
  endpoints: mockApi,
  default: mockApi,
  get: mockGet,
  post: mockPost,
  put: vi.fn(),
  del: vi.fn(),
  publicGet: vi.fn(),
}));

// ---------------------------------------------------------------------------
// The pass-through recorder over `errorLine`.
//
// `importOriginal` keeps the real implementation: every call below goes through
// `design/errorCopy.js` exactly as it does in production, and `translated` is a side record of
// what each site handed it. This is the only way to see the error a `catch` swallowed without
// changing what that `catch` does.
// ---------------------------------------------------------------------------

const { translated } = vi.hoisted(() => ({ translated: [] }));

vi.mock('../../../src/design/errorLine', async (importOriginal) => {
  const actual = await importOriginal();
  const errorLine = (error, context) => {
    translated.push({ error, context });
    return actual.errorLine(error, context);
  };
  return { ...actual, errorLine, default: errorLine };
});

import { IndicatorEngineProvider, useIndicatorEngine } from '../../../src/contexts/IndicatorEngineContext';
import { LogicEngineProvider, useLogicEngine } from '../../../src/contexts/LogicEngineContext';
import { StrategyEngineProvider, useStrategyEngine } from '../../../src/contexts/StrategyEngineContext';
import { DataPipelineProvider, useDataPipeline } from '../../../src/contexts/DataPipelineContext';
import * as engineHelpers from '../../../src/utils/engineHelpers';
import { CATEGORY_COPY, CODE_COPY, CONTEXT_COPY, containsForbidden } from '../../../src/design/errorCopy';
import { resetRegistryClient } from '../../../src/lib/registryClient';

// ---------------------------------------------------------------------------
// Repo paths. Resolved from this file rather than from `process.cwd()`, because vitest's root
// is `algo22-terminal` and sections 2, 3 and 6 read both trees.
// ---------------------------------------------------------------------------

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND_ROOT = join(HERE, '..', '..', '..'); // algo22-terminal
const REPO_ROOT = join(FRONTEND_ROOT, '..');
const SRC = join(FRONTEND_ROOT, 'src');
const BACKEND_ROOT = join(REPO_ROOT, 'backend_app');

const readSource = (absolutePath) => readFileSync(absolutePath, 'utf8');

/**
 * `source` with every `//` and block comment replaced by spaces, string and template literals
 * left intact.
 *
 * Sections 2 and 3 ask what a module *calls* and what address it *names*. Both are claims about
 * code, and both were originally checked against the raw file - which worked only for as long as
 * no module documented the call it used to make. Task 9.2's three headers describe the removed
 * `post('/indicator/compute', …)` expressions by name, in prose, which a raw `includes` reads as
 * the call still being there. The same trap `tests/test_route_contract.py` and
 * `tests/unit/guards/api-surface.js` both strip comments to avoid: `src/api/modules/orders.js`
 * quotes `@router.post("/cancel-all")` in a docblock, and a scan that read it would invent a call
 * site that does not exist.
 *
 * Newlines are preserved so a line number taken off the result is the real one.
 */
const codeOf = (source) => {
  let out = '';
  let i = 0;
  while (i < source.length) {
    const ch = source[i];
    if (ch === '"' || ch === "'" || ch === '`') {
      let j = i + 1;
      while (j < source.length) {
        if (source[j] === '\\') { j += 2; continue; }
        if (source[j] === ch) { j += 1; break; }
        if (ch !== '`' && source[j] === '\n') break;
        j += 1;
      }
      out += source.slice(i, j);
      i = j;
      continue;
    }
    if (ch === '/' && source[i + 1] === '/') {
      const end = source.indexOf('\n', i);
      const stop = end === -1 ? source.length : end;
      out += ' '.repeat(stop - i);
      i = stop;
      continue;
    }
    if (ch === '/' && source[i + 1] === '*') {
      const end = source.indexOf('*/', i + 2);
      const stop = end === -1 ? source.length : end + 2;
      out += source.slice(i, stop).replace(/[^\n]/g, ' ');
      i = stop;
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
};

/** The code of the file at `absolutePath`, comments stripped. */
const readCode = (absolutePath) => codeOf(readSource(absolutePath));

/** Every `.py` file under `dir`, recursively. */
const pythonFiles = (dir) => {
  const found = [];
  const walk = (current) => {
    for (const entry of readdirSync(current)) {
      if (entry === '__pycache__' || entry === 'node_modules') continue;
      const full = join(current, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (full.endsWith('.py')) found.push(full);
    }
  };
  walk(dir);
  return found;
};

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/**
 * 40 deterministic candles. Long enough to clear `getMinDataLength('rsi', {period: 14})` = 15,
 * so `computeIndicator`'s own length gate passes and the call reaches the `post` expression
 * rather than being refused before it.
 */
const OHLCV = Object.freeze(
  Array.from({ length: 40 }, (_, index) => {
    const close = 100 + Math.sin(index / 3) * 5;
    return Object.freeze({
      timestamp: Date.UTC(2024, 0, 1) + index * 3_600_000,
      open: close - 0.5,
      high: close + 1,
      low: close - 1,
      close,
      volume: 1_000 + index,
    });
  }),
);

/** A condition `validateConditions` accepts, so `evaluateLogic` reaches its `post` expression. */
const CONDITIONS = Object.freeze([
  Object.freeze({
    type: 'comparison',
    left: { source: 'indicator', name: 'rsi', key: 'value' },
    operator: '>',
    right: { source: 'constant', value: 70 },
  }),
]);

/** The indicator series the condition references. Named `rsi` because the condition says so. */
const INDICATORS_DATA = Object.freeze({
  rsi: Object.freeze(OHLCV.map((candle, index) => 40 + ((index * 7) % 50))),
});

/**
 * A graph `parseGraphToExecutionPlan` accepts: one source carrying both required parameters,
 * one indicator, one logic node, one action, and edges connecting all four with no cycle.
 * Built as a real graph so the plan `executeStrategy` receives is the plan the builder would
 * hand it.
 */
const GRAPH = Object.freeze({
  nodes: Object.freeze([
    {
      id: 'n_source',
      type: 'source',
      data: { label: 'Market Data', params: { symbol: 'BTC/USDT', timeframe: '1h', start_date: '2024-01-01', end_date: '2024-02-01' } },
    },
    { id: 'n_rsi', type: 'indicator', data: { label: 'RSI', params: { window: 14, output: 'value' } } },
    {
      id: 'n_logic',
      type: 'logic',
      data: {
        label: 'Compare',
        params: {
          condition_type: 'comparison',
          left_indicator: 'rsi',
          left_output: 'value',
          operator: '>',
          right_type: 'constant',
          right_constant: 70,
          signal_on_true: 'BUY',
          signal_on_false: 'NONE',
          min_confidence: 0.6,
        },
      },
    },
    { id: 'n_action', type: 'action', data: { label: 'Market Buy', params: { size_pct: 10, order_type: 'MARKET' } } },
  ]),
  edges: Object.freeze([
    { source: 'n_source', target: 'n_rsi' },
    { source: 'n_rsi', target: 'n_logic' },
    { source: 'n_logic', target: 'n_action' },
  ]),
});

/** What each route would answer if it existed and the client reached it. */
const STUB_RESPONSES = Object.freeze({
  '/indicator/compute': {
    result: { value: INDICATORS_DATA.rsi },
    metadata: { engine: 'stub' },
  },
  '/logic/evaluate': {
    signal: 'BUY',
    confidence: 0.82,
    triggeredConditions: [0],
    metadata: { engine: 'stub' },
  },
  '/strategy/execute': {
    status: 'completed',
    signals: [{ timestamp: OHLCV[39].timestamp, signal: 'BUY' }],
    charts: {},
    metrics: { total_return_pct: 4.2 },
    trades: [],
    performance: { sharpe_ratio: 1.1 },
    version: 'stub-1',
    metadata: { engine: 'stub' },
  },
});

// ---------------------------------------------------------------------------
// "A defined outcome", stated once
// ---------------------------------------------------------------------------

/**
 * Every line `design/errorCopy.js` can author, in `errorLine`'s join form (`headline. detail`).
 *
 * Membership in this set is what "a handled failure that reaches `design/errorCopy.js`" means
 * operationally. Built from the tables rather than hardcoded, so re-wording an entry does not
 * silently narrow the check.
 */
const AUTHORED_LINES = new Set();
for (const table of [CODE_COPY, CATEGORY_COPY, CONTEXT_COPY]) {
  for (const entry of Object.values(table)) {
    AUTHORED_LINES.add(entry.detail ? `${entry.headline}. ${entry.detail}` : entry.headline);
  }
}

/** Run `thunk` and report how it settled, without letting a rejection end the test. */
const settle = async (thunk) => {
  try {
    return { status: 'fulfilled', value: await thunk() };
  } catch (reason) {
    return { status: 'rejected', reason };
  }
};

/** `Name: message` for an error, or a readable rendering of whatever else was thrown. */
const describeThrown = (thrown) =>
  thrown instanceof Error ? `${thrown.name}: ${thrown.message}` : `${typeof thrown}: ${String(thrown)}`;

/** True when `thrown` is an unbound-identifier failure, however it was wrapped. */
const isReferenceFailure = (thrown) =>
  thrown instanceof ReferenceError || /\bis not defined\b/.test(String(thrown?.message ?? ''));

/**
 * The claim: nothing on this call path raised a `ReferenceError`.
 *
 * Checked in three places, because a `ReferenceError` can leave a `useCallback` by three
 * routes: escaping to the caller, being handed to `errorLine` by the site's own `catch` (the
 * route `F` takes), or being swallowed into a `console.error`.
 */
const expectNoReferenceError = (outcome, site) => {
  const escaped = outcome.status === 'rejected' && isReferenceFailure(outcome.reason) ? [outcome.reason] : [];
  const swallowed = translated.filter(({ error }) => isReferenceFailure(error)).map(({ error }) => error);
  const found = [...escaped, ...swallowed].map(describeThrown);

  expect(
    found,
    `${site}: the call path raised ${found.length} ReferenceError(s) - ${found.join(' | ')}. ` +
      `The stubbed transport was reached ${mockPost.mock.calls.length} time(s), so the failure ` +
      'is before the network, not on it.',
  ).toEqual([]);
};

/**
 * The claim: the call settled into something a caller can act on.
 *
 * A computed result, or a handled failure whose words come from `design/errorCopy.js`. Not an
 * exception escaping a `useCallback`, and not `undefined`.
 */
const expectDefinedOutcome = (outcome, site, isComputedResult) => {
  if (outcome.status === 'rejected') {
    const message = String(outcome.reason?.message ?? outcome.reason);
    expect(
      AUTHORED_LINES.has(message),
      `${site}: rejected with ${describeThrown(outcome.reason)}, which is not a line ` +
        'design/errorCopy.js authors - so this is an exception escaping a useCallback, not a ' +
        'handled failure',
    ).toBe(true);
    expect(containsForbidden(message), `${site}: the message reaching the caller is not clean`).toBe(false);
    return { kind: 'handled failure', detail: message };
  }

  const { value } = outcome;
  expect(value, `${site}: resolved with nothing at all`).not.toBeUndefined();
  expect(value, `${site}: resolved with null`).not.toBeNull();

  const handledMessage = typeof value?.error === 'string' ? value.error : null;
  if (handledMessage !== null) {
    expect(
      AUTHORED_LINES.has(handledMessage),
      `${site}: resolved carrying error=${JSON.stringify(handledMessage)}, which is not a line ` +
        'design/errorCopy.js authors',
    ).toBe(true);
    expect(containsForbidden(handledMessage)).toBe(false);
    return { kind: 'handled failure', detail: handledMessage };
  }

  expect(
    isComputedResult(value),
    `${site}: resolved with ${JSON.stringify(value)}, which is neither a computed result nor a ` +
      'handled failure',
  ).toBe(true);
  return { kind: 'computed result', detail: JSON.stringify(value) };
};

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

let engines = null;

function EngineProbe() {
  engines = {
    indicator: useIndicatorEngine(),
    logic: useLogicEngine(),
    strategy: useStrategyEngine(),
  };
  return null;
}

/** The three providers in the wrapper's own nesting order (`StrategyBuilder.jsx:4591-4593`). */
const mountEngines = async () => {
  await act(async () => {
    render(
      <IndicatorEngineProvider>
        <LogicEngineProvider>
          <StrategyEngineProvider>
            <EngineProbe />
          </StrategyEngineProvider>
        </LogicEngineProvider>
      </IndicatorEngineProvider>,
    );
  });
  await act(async () => {});
};

beforeEach(() => {
  translated.length = 0;
  engines = null;

  mockPost.mockReset();
  mockPost.mockImplementation(async (path) => {
    if (Object.prototype.hasOwnProperty.call(STUB_RESPONSES, path)) return STUB_RESPONSES[path];
    throw new Error(`the api stub was asked for an unstubbed path`);
  });
  mockGet.mockReset();
  mockGet.mockResolvedValue([]);
  mockApi.exchange.getAccounts.mockReset();
  mockApi.exchange.getAccounts.mockResolvedValue([]);
  mockApi.market.getSymbols.mockReset();
  mockApi.market.getSymbols.mockResolvedValue([]);

  vi.spyOn(console, 'log').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
  vi.restoreAllMocks();
});

// ══════════════════════════════════════════════════════════════════════════
// 1. REACHABILITY  (`StrategyBuilder.jsx:4590-4603`)
// ══════════════════════════════════════════════════════════════════════════

describe('the three engine providers', () => {
  it('are all mounted by the StrategyBuilder wrapper', () => {
    /*
      MEASURED ON `F` - `src/pages/StrategyBuilder.jsx`, `StrategyBuilderWrapper` at `:4588`:

          4589  <ReactFlowProvider>
          4590    <DataPipelineProvider mode="backtest">
          4591      <IndicatorEngineProvider>
          4592        <LogicEngineProvider>
          4593          <StrategyEngineProvider>
          4594            <UndoRedoProvider>
          4595              <ValidationProvider>
          4596                <StrategyBuilderCanvas {...props} />

      `StrategyBuilder.jsx` is the default export of the builder route, so every one of the
      three providers is instantiated on every visit to it. That is what makes 1.25-1.27 three
      live defects rather than three dead clauses, and it is checked here rather than asserted
      in prose because a provider silently dropped from this wrapper would change the verdict.

      This test PASSES on `F`. It is the premise the rest of the file rests on.
    */
    const source = readSource(join(SRC, 'pages', 'StrategyBuilder.jsx'));
    const lines = source.split(/\r?\n/);

    const lineOf = (needle) => lines.findIndex((line) => line.includes(needle)) + 1;

    const nesting = [
      'DataPipelineProvider mode="backtest"',
      '<IndicatorEngineProvider>',
      '<LogicEngineProvider>',
      '<StrategyEngineProvider>',
    ].map((needle) => ({ needle, line: lineOf(needle) }));

    for (const { needle, line } of nesting) {
      expect(line, `${needle} is not mounted anywhere in StrategyBuilder.jsx`).toBeGreaterThan(0);
    }

    // Nested, in this order - so mounting the builder mounts all three.
    const linesInOrder = nesting.map(({ line }) => line);
    expect(
      linesInOrder,
      `the providers are opened at ${linesInOrder.join(', ')}, which is not the nesting order ` +
        'this file mounts them in',
    ).toEqual([...linesInOrder].sort((a, b) => a - b));

    expect(linesInOrder[0]).toBeGreaterThanOrEqual(4580);
    expect(linesInOrder[3]).toBeLessThanOrEqual(4610);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 2. DEFECT (a) - `post` IS NOT BOUND IN ANY OF THE THREE MODULES
// ══════════════════════════════════════════════════════════════════════════

const MODULES = Object.freeze([
  { file: 'src/contexts/IndicatorEngineContext.jsx', call: "post('/indicator/compute'" },
  { file: 'src/contexts/LogicEngineContext.jsx', call: "post('/logic/evaluate'" },
  { file: 'src/contexts/StrategyEngineContext.jsx', call: "post('/strategy/execute'" },
]);

describe('a module that calls post()', () => {
  it.each(MODULES)('imports it - $file', ({ file, call }) => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F` - the whole import surface of each of the three, and the
      one call each of them makes:

          IndicatorEngineContext.jsx:2   import { endpoints } from '../api';      -> calls post( at :66
          LogicEngineContext.jsx:2       import { endpoints } from '../api';      -> calls post( at :55
          StrategyEngineContext.jsx:2    import { endpoints } from '../api';      -> calls post( at :283

      `post` is imported in none of them, and `endpoints` - the only binding they do take from
      `../api` - is never used on any of the three call paths. `src/api/index.js:19` re-exports
      `post` by name, so the binding is one word away; this is an omitted import rather than an
      absent client.

      Checked statically as well as behaviourally because this is the half ESLint `no-undef`
      would have caught, and task 4.5 puts that check in CI for exactly this reason. Either
      disposition satisfies this assertion: importing `post` binds it, and deleting the dead
      call path removes the premise.

      Read off `readCode`, not the raw file: task 9.2's three headers name the expression they
      removed, and a raw `includes` would read that prose as the call still being there. See
      `codeOf`.
    */
    const source = readCode(join(FRONTEND_ROOT, file));
    if (!source.includes(call)) return; // the call is gone - removal disposition, nothing to bind

    const bindsPost =
      /^\s*import\s*\{[^}]*\bpost\b[^}]*\}\s*from\s*['"][^'"]*api['"]/m.test(source) ||
      /^\s*import\s*\{[^}]*\bpost\b[^}]*\}\s*from/m.test(source) ||
      /\b(const|let|var|function)\s+post\b/.test(source) ||
      /\bpost\s*[,}]?\s*\}\s*=\s*(useDataPipeline|api|endpoints)\b/.test(source);

    expect(
      bindsPost,
      `${file} evaluates ${call}…) but binds no identifier named post - so the expression ` +
        'raises ReferenceError before any request is built',
    ).toBe(true);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 3. DEFECT (b) - NONE OF THE THREE ROUTES EXISTS ON THE BACKEND
// ══════════════════════════════════════════════════════════════════════════

const ROUTES = Object.freeze(['/indicator/compute', '/logic/evaluate', '/strategy/execute']);

describe('the route each engine context names', () => {
  it.each(ROUTES)('is registered under backend_app/, or is named by no client source - %s', (route) => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F` - all 384 `.py` files under `backend_app/` searched for
      the literal path:

          /indicator/compute   -> 0 occurrences in 384 files
          /logic/evaluate      -> 0 occurrences in 384 files
          /strategy/execute    -> 0 occurrences in 384 files

      and no router declares a `/indicator`, `/logic` or `/strategy` prefix that a relative
      decorator could complete into one: the only `APIRouter(prefix=…)` in `backend_app/routers`
      is `dag_tasks.py:28`'s `/api/dag/tasks`.

      So on `F` each path existed in exactly one place in the repository - the frontend call
      site that named it. That was the second, independent defect: binding `post` correctly
      would have turned each `ReferenceError` into a 404.

      THE ASSERTION IS A DISJUNCTION, AND WAS NOT WEAKENED TO FIT THE FIX
      ------------------------------------------------------------------
      Requirements 2.25-2.27 are disjunctions in their own words: indicator computation "SHALL
      reach an implemented endpoint through an imported client, **or the client-side path SHALL
      be removed**". This test originally asserted only the first arm - that the route exists -
      which quietly encoded the implement disposition as the only acceptable one. Task 9.2 took
      the other arm, on the evidence in section 6: none of the three capabilities has a
      consumer, and there is no local computation for any of them to fall back on either.

      So the check now reads the clause as written. For each of the three paths, either a route
      answers it, or nothing under `src/` names it. What it will not accept is the state `F` was
      in - a client that names an address the backend does not serve - and that is still
      measured by exactly this assertion.

      The canonical both-directions sweep is `tests/test_route_contract.py` (task 9.1), which
      resolves every client address against the app's own OpenAPI schema with the method used.
      This check stays deliberately narrow - two greps, each settling a claim on its own - so
      cluster D's second defect is measured here as well as there.
    */
    const files = pythonFiles(BACKEND_ROOT);
    expect(files.length, 'the walk must have found backend sources for this to mean anything').toBeGreaterThan(50);

    const hits = files
      .filter((file) => readSource(file).includes(route))
      .map((file) => relative(REPO_ROOT, file));

    if (hits.length > 0) return; // first arm: a route answers it

    // Second arm: no client source names it. Asserted over the whole of `src/`, not just the
    // three contexts, so the address cannot reappear somewhere else in the bundle.
    const jsFiles = [];
    const walk = (dir) => {
      for (const entry of readdirSync(dir)) {
        if (entry === 'node_modules' || entry === 'archive') continue;
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.jsx?$/.test(full)) jsFiles.push(full);
      }
    };
    walk(SRC);
    expect(jsFiles.length).toBeGreaterThan(100);

    const callers = jsFiles
      .filter((file) => readCode(file).includes(route))
      .map((file) => relative(FRONTEND_ROOT, file));

    expect(
      callers,
      `${route} is named by no Python source under backend_app/ (${files.length} files ` +
        'searched), so no route answers it - and it is still named by client code, which is ' +
        'therefore addressing nothing. Point it at a route that exists, or remove the call ' +
        'path.',
    ).toEqual([]);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 4. THE THREE CALLS  (Requirements 1.25, 1.26, 1.27 / 2.25, 2.26, 2.27)
// ══════════════════════════════════════════════════════════════════════════

const SITES = Object.freeze([
  {
    name: 'IndicatorEngineContext.jsx:66 computeIndicator',
    capability: 'indicator computation',
    invoke: () => engines.indicator.computeIndicator('rsi', OHLCV, { period: 14 }, { symbol: 'BTC/USDT', timeframe: '1h' }),
    isComputedResult: (value) =>
      value?.indicator === 'rsi' && value?.data !== undefined && typeof value?.metadata === 'object',
    observedOnF:
      'rejected with Error("Could not load the strategy builder. Nothing is shown rather than a ' +
      'partial reading. Try again."), the translation of a swallowed ReferenceError',
  },
  {
    name: 'LogicEngineContext.jsx:55 evaluateLogic',
    capability: 'logic evaluation',
    invoke: () => engines.logic.evaluateLogic(CONDITIONS, INDICATORS_DATA),
    isComputedResult: (value) =>
      ['BUY', 'SELL', 'NONE'].includes(value?.signal) && typeof value?.confidence === 'number',
    observedOnF:
      'resolved with {signal: "NONE", confidence: 0, error: "Could not load the strategy ' +
      'builder. …"} - a handled failure describing the wrong cause',
  },
  {
    name: 'StrategyEngineContext.jsx:283 executeStrategy',
    capability: 'strategy execution',
    invoke: () => {
      const parsed = engines.strategy.parseGraphToExecutionPlan(GRAPH.nodes, GRAPH.edges, {
        strategyName: 'cluster D probe',
      });
      expect(parsed.valid, `the probe graph must parse for this to mean anything: ${JSON.stringify(parsed.errors)}`).toBe(true);
      return engines.strategy.executeStrategy(parsed.plan);
    },
    isComputedResult: (value) => typeof value?.executionId === 'string' && typeof value?.status === 'string',
    observedOnF:
      'rejected with Error("Could not load the strategy builder. …"), the translation of a ' +
      'swallowed ReferenceError',
  },
]);

/** Mount, call one site, and report how it settled. */
const callSite = async (site) => {
  await mountEngines();
  let outcome;
  await act(async () => {
    outcome = await settle(site.invoke);
  });
  return outcome;
};

describe.each(SITES)('$name', (site) => {
  it('raises no ReferenceError', async () => {
    /*
      COUNTEREXAMPLE OBSERVED ON `F` - the same one at all three sites, recorded off the error
      each site's own `catch` handed to `errorLine`:

          ReferenceError: post is not defined
          stubbed transport reached: 0 times

      Zero transport calls is the second half of the evidence: the `api` stub in this file
      would have answered all three paths, and it was never asked. The capability does not fail
      at the network, it fails one expression before it.

      This assertion FAILS on `F`. That is the deliverable.
    */
    const outcome = await callSite(site);
    expectNoReferenceError(outcome, site.name);
  });

  it('reaches a defined outcome - which on `F` it does for the wrong reason', async () => {
    /*
      OBSERVED ON `F` - this assertion PASSES, and the pass is the finding:

          indicator -> rejected  Error("Could not load the strategy builder. Nothing is shown
                                 rather than a partial reading. Try again.")
          logic     -> resolved  {signal: "NONE", confidence: 0, error: "Could not load the
                                 strategy builder. …"}
          strategy  -> rejected  Error("Could not load the strategy builder. …")

      Every one of those is a handled failure carrying authored copy from
      `design/errorCopy.js`, so the letter of the clause is already met - by the site's own
      `catch` translating a `ReferenceError` that has nothing to do with loading the builder.
      `translateError` reaches `CONTEXT_COPY.builder` because a `ReferenceError` carries no
      code, no reasons, no channel and no category, so every branch above the last-resort one
      declines it.

      Kept in the file rather than dropped for being green, for two reasons. It is the half the
      fix must not break - whichever disposition task 9.2 takes, a failure here must still
      arrive as authored copy and never as a raw message (Requirement 14.4). And it records why
      the ReferenceError assertion above cannot be replaced by an outcome assertion: on `F`
      this one is green while the capability is dead, which is the one-sided-test failure mode
      `design.md §Testing Strategy` rule 1 names.
    */
    const outcome = await callSite(site);
    const verdict = expectDefinedOutcome(outcome, site.name, site.isComputedResult);
    expect(['computed result', 'handled failure']).toContain(verdict.kind);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 5. PRESERVATION - what must still hold after either disposition
// ══════════════════════════════════════════════════════════════════════════

describe('preserved behaviour', () => {
  it('test_preserved_the_local_length_gate_still_refuses_short_input', async () => {
    /*
      Passes on `F` and must keep passing. `computeIndicator`'s own gate - `getMinDataLength`
      from `utils/engineHelpers` - refuses 5 candles for a 14-period RSI before any transport
      is involved, and it must still refuse them after the fix. The refusal arrives as authored
      copy, because the bare `throw new Error(…)` lands in the same `catch`.

      This is the one piece of real local computation on the indicator path, and it is a
      candle *count*, not a series. See section 6.
    */
    await mountEngines();

    let outcome;
    await act(async () => {
      outcome = await settle(() => engines.indicator.computeIndicator('rsi', OHLCV.slice(0, 5), { period: 14 }));
    });

    expect(outcome.status).toBe('rejected');
    expect(AUTHORED_LINES.has(String(outcome.reason.message))).toBe(true);
    expect(containsForbidden(String(outcome.reason.message))).toBe(false);
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('test_preserved_no_conditions_still_short_circuits_to_NONE', async () => {
    /*
      Passes on `F` and must keep passing. `evaluateLogic` answers an empty condition list
      locally, before its `try`, with `{signal: 'NONE', confidence: 0, reason: 'No conditions
      defined'}` - a computed result, not a failure, and the one path in the file that already
      satisfies 1.26 today.
    */
    await mountEngines();

    let result;
    await act(async () => {
      result = await engines.logic.evaluateLogic([], {});
    });

    expect(result).toEqual({ signal: 'NONE', confidence: 0, reason: 'No conditions defined' });
    expect(mockPost).not.toHaveBeenCalled();
  });

  it('test_preserved_the_execution_plan_is_still_built_locally', async () => {
    /*
      Passes on `F` and must keep passing - this is the part of strategy execution that really
      is local, and the part a removal disposition must leave standing.
      `parseGraphToExecutionPlan` builds the whole plan with no network: the data source, the
      indicator list, the logic conditions, the execution rules and the graph projection.

      Also pins SB-06, which task 3.9 and task 7.3 landed on this same function: an unset
      symbol or timeframe is an error naming the field, never a substituted default. A fix that
      re-pointed this file at a route must not reintroduce `symbol || 'BTC/USDT'`.
    */
    await mountEngines();

    const parsed = engines.strategy.parseGraphToExecutionPlan(GRAPH.nodes, GRAPH.edges, {
      strategyName: 'preservation probe',
    });

    expect(parsed.valid).toBe(true);
    expect(parsed.plan.pipeline.dataSource).toMatchObject({ symbol: 'BTC/USDT', timeframe: '1h' });
    expect(parsed.plan.pipeline.indicators).toHaveLength(1);
    expect(parsed.plan.pipeline.logic).toHaveLength(1);
    expect(parsed.plan.pipeline.executionRules).toHaveLength(1);
    expect(parsed.plan.executionId).toMatch(/^exec_/);
    expect(mockPost).not.toHaveBeenCalled();

    // The silent default is gone and must stay gone.
    const unset = engines.strategy.parseGraphToExecutionPlan(
      [{ id: 'n_bare', type: 'source', data: { label: 'Market Data', params: {} } }],
      [],
      {},
    );
    expect(unset.valid).toBe(false);
    expect(unset.errors.map((error) => error.field)).toEqual(['symbol', 'timeframe']);
  });

  it('test_preserved_a_failure_leaves_no_provider_stuck_computing', async () => {
    /*
      Passes on `F` and must keep passing: each site's `finally` clears its in-flight flag, so a
      failed call does not leave the builder showing a spinner for the rest of the session.
      Pinned because both dispositions rewrite the body of these `try` blocks.
    */
    await mountEngines();

    await act(async () => {
      await settle(() => engines.indicator.computeIndicator('rsi', OHLCV, { period: 14 }));
      await settle(() => engines.logic.evaluateLogic(CONDITIONS, INDICATORS_DATA));
    });

    expect(engines.indicator.isComputing).toBe(false);
    expect(engines.logic.isEvaluating).toBe(false);
    expect(engines.strategy.isExecuting).toBe(false);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// 6. TASK 9.2's DISPOSITION - IS THE CAPABILITY COMPUTED LOCALLY TODAY?
//
// These four tests PASS on `F` by design. They are measurements, not expectations: their job
// is to make the remove-vs-implement evidence reproducible instead of leaving it in prose.
// `tasks.md` task 9.2 asserts the builder "already computes indicators locally via
// DataPipelineContext and utils/engineHelpers". It does not, and this is where that is shown.
// ══════════════════════════════════════════════════════════════════════════

describe('measured: where each capability is computed today', () => {
  it('test_measured_engineHelpers_computes_no_indicator_series', () => {
    /*
      MEASURED ON `F` - `src/utils/engineHelpers.js`, complete export surface:

          getMinDataLength(indicator, params)        -> a NUMBER: the minimum candle count
          validateConditions(conditions, data)       -> an ARRAY of shape errors
          getTimeframeMs(tf)                         -> a NUMBER: interval in milliseconds
          getMaxHistoryForTimeframe(tf)              -> a NUMBER: days of history available

      Four functions, 70 lines, and not one of them reads a candle's `close`. The module is a
      table of limits and a shape validator. `getMinDataLength('rsi', {period: 14})` returns
      `15` - the number of candles an RSI needs, not an RSI.

      => The indicator capability is NOT computed locally. Task 9.2's premise is wrong on this
         point, and "remove the call path and let the builder's local computation stand" has no
         local computation to fall back on.
    */
    expect(Object.keys(engineHelpers).sort()).toEqual([
      'getMaxHistoryForTimeframe',
      'getMinDataLength',
      'getTimeframeMs',
      'validateConditions',
    ]);

    // A count, not a series.
    expect(engineHelpers.getMinDataLength('rsi', { period: 14 })).toBe(15);
    expect(typeof engineHelpers.getMinDataLength('macd', {})).toBe('number');

    // A list of shape errors, not a signal: a well-formed condition yields no verdict at all.
    const verdict = engineHelpers.validateConditions(CONDITIONS, INDICATORS_DATA);
    expect(Array.isArray(verdict)).toBe(true);
    expect(verdict).toEqual([]);
  });

  it('test_measured_DataPipelineContext_fetches_data_and_computes_nothing', async () => {
    /*
      MEASURED ON `F` - the complete key set `DataPipelineProvider` publishes on its context
      value, grouped by what it is for:

          mode, setMode, activeExchange, isLoadingExchange
          availableTimeframes, timeframeEntries, isLoadingTimeframes, timeframesError
          availableSymbols, symbolSearchQuery, setSymbolSearchQuery, isLoadingSymbols,
            symbolsError, loadMarkets
          fetchOHLCV, fetchHistoricalPaginated, fetchOrderbookImbalance, connectLiveData
          ohlcCache, isFetchingData, fetchProgress, clearCache
          liveData, isLiveConnected
          validateInputs, validationErrors, dataAvailability

      Twenty-six keys. Every function on it either FETCHES (`fetchOHLCV` ->
      `get('/data/historical')`, `fetchHistoricalPaginated`, `fetchOrderbookImbalance`,
      `loadMarkets`, `connectLiveData`) or VALIDATES (`validateInputs`). None computes an
      indicator, evaluates a condition or runs a plan.

      => The context named in task 9.2 as the local compute path is a data *transport* and
         cache. It has no compute surface to fall back to.

      This is the one test in the file that mounts `DataPipelineProvider`, so it is also the
      one that makes `registryClient.loadTimeframes()` run for real. With no server behind
      jsdom's XHR that request fails and the run prints an `AggregateError` stack from
      `jsdom/living/xhr` on stderr. It is the timeframe fetch failing closed - `loadTimeframes`
      never rejects, it sets an error snapshot - and it is not a failure of this test.
    */
    let pipeline = null;
    function PipelineProbe() {
      pipeline = useDataPipeline();
      return null;
    }

    await act(async () => {
      render(
        <DataPipelineProvider mode="backtest">
          <PipelineProbe />
        </DataPipelineProvider>,
      );
    });
    await act(async () => {});

    const functions = Object.entries(pipeline)
      .filter(([, value]) => typeof value === 'function')
      .map(([key]) => key)
      .sort();

    expect(functions).toEqual([
      'clearCache',
      'connectLiveData',
      'fetchHistoricalPaginated',
      'fetchOHLCV',
      'fetchOrderbookImbalance',
      'loadMarkets',
      'setMode',
      'setSymbolSearchQuery',
      'validateInputs',
    ]);

    // Nothing on it computes, evaluates or executes.
    expect(functions.filter((key) => /^(compute|evaluate|execute|calculate)/.test(key))).toEqual([]);
  });

  it('test_measured_no_indicator_implementation_exists_anywhere_in_src', () => {
    /*
      MEASURED ON `F` - every `.js`/`.jsx` file under `algo22-terminal/src` searched for an
      indicator implementation: a function whose body would have to walk a price series. Nothing
      matches. The only occurrences of `computeIndicator` in `src/` are its definition in
      `IndicatorEngineContext.jsx` and the batch wrapper immediately below it.

      Searched for, and not found: a moving-average loop, an RSI gain/loss accumulator, a
      standard-deviation pass, a MACD line. `src/lib/` holds `blockRegistry`,
      `canonicalGraph`, `registryClient` and friends - graph and vocabulary code, no maths on
      candles.

      => Indicator values are produced by the BACKEND in this application, everywhere they are
         produced at all. The builder's own charts render series the backend returned.
    */
    const jsFiles = [];
    const walk = (dir) => {
      for (const entry of readdirSync(dir)) {
        if (entry === 'node_modules' || entry === 'archive') continue;
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.jsx?$/.test(full)) jsFiles.push(full);
      }
    };
    walk(SRC);
    expect(jsFiles.length).toBeGreaterThan(100);

    // A local indicator engine would have to declare at least one of these.
    const implementationPattern =
      /\b(?:function|const|let)\s+(?:compute|calculate)(?:Rsi|RSI|Sma|SMA|Ema|EMA|Macd|MACD|Bollinger|Atr|ATR|Stochastic)\b/;

    const implementers = jsFiles
      .filter((file) => implementationPattern.test(readSource(file)))
      .map((file) => relative(FRONTEND_ROOT, file));

    expect(implementers).toEqual([]);
  });

  it('test_measured_none_of_the_three_capabilities_has_a_consumer', () => {
    /*
      MEASURED ON `F` - every `.js`/`.jsx` file under `src` searched for a call to each hook:

          useIndicatorEngine()  -> 0 call sites outside IndicatorEngineContext.jsx's own definition
          useLogicEngine()      -> 0 call sites outside LogicEngineContext.jsx's own definition
          useStrategyEngine()   -> 0 call sites outside StrategyEngineContext.jsx's own definition

      The two cross-imports that do exist are unused bindings:
      `StrategyEngineContext.jsx:10-11` imports `useIndicatorEngine` and `useLogicEngine` and
      calls neither; `IndicatorEngineContext.jsx:8` and `StrategyEngineContext.jsx:9` import
      `useDataPipeline` and call it nowhere. `StrategyEngineContext.jsx:329` already says so in
      a comment: *"nothing in `src/` calls `useStrategyEngine`"*.

      => This is why three broken capabilities have never been reported. The providers are
         mounted on every builder visit and their functions are never called, so the
         `ReferenceError` has never reached a user. It is also the strongest argument for the
         removal disposition: removing them removes three dead call paths and three phantom
         routes, and no screen changes.

      Recorded, not decided. The decision is task 9.2's.
    */
    const jsFiles = [];
    const walk = (dir) => {
      for (const entry of readdirSync(dir)) {
        if (entry === 'node_modules' || entry === 'archive') continue;
        const full = join(dir, entry);
        if (statSync(full).isDirectory()) walk(full);
        else if (/\.jsx?$/.test(full)) jsFiles.push(full);
      }
    };
    walk(SRC);

    const consumers = {};
    for (const hook of ['useIndicatorEngine', 'useLogicEngine', 'useStrategyEngine']) {
      const owner = join(SRC, 'contexts', `${hook.replace(/^use/, '')}Context.jsx`);
      consumers[hook] = jsFiles
        .filter((file) => file !== owner)
        .filter((file) => new RegExp(`\\b${hook}\\s*\\(`).test(readCode(file)))
        .map((file) => relative(FRONTEND_ROOT, file));
    }

    expect(consumers).toEqual({
      useIndicatorEngine: [],
      useLogicEngine: [],
      useStrategyEngine: [],
    });
  });
});
