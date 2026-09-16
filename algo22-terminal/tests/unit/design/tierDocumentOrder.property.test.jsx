/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Feature: vyomquant-ui-redesign — Property 4
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Task 16.3b, over the registry task 16.3a built in `tests/unit/design/tierRegistry.jsx`.
 * design.md §7, §7.4, §7.6, §18.
 *
 *   * **Property 4: Declared priority tier determines document order.**
 *     **Validates: Requirements 3.1, 3.2, 3.4, 6.2, 6.3, 7.1, 7.2, 7.3, 10.1, 10.2**
 *
 * Two clauses, both about a rendered DOM and neither about a stylesheet:
 *
 *   1. every tier-*n* element precedes every tier-*(n+1)* element in document order, and
 *   2. no tier-1 element renders outside the page's single tier-1 container.
 *
 * The payloads are generated: for every field a page reads, an arbitrary reading drawn from
 * absent / `null` / `0` / wrongly-typed. That is the point of the property. A page whose tiers
 * are ordered correctly on the healthy fixture is what `portfolio-rendering.test.jsx` and
 * `tierRegistry.test.jsx` already assert; what nobody has asserted is that the ordering survives
 * a server that omitted a figure, sent `null` for it, sent a real `0`, or sent a string.
 *
 * WHAT IS SPELLED HERE AND WHAT IS CONSUMED
 * -----------------------------------------
 * The three attributes the clauses are decided by — `data-page`, `data-page-tier`,
 * `data-metric-tier` — are written out as literals below and cross-checked against
 * `design/pageHierarchy`'s declaration in the first test. Every selector in the property is
 * built from the literals, so a run cannot pass by agreeing with a renamed attribute. There is
 * no class name anywhere in this file: a refactor that swaps a utility class must not fail
 * Property 4, and one that drops a tier attribute must.
 *
 * What is consumed rather than restated is the registry: `TIER_REGISTRY` supplies
 * `{page, hierarchy, requiredMocks, payloadFields, payload, mount}` per page, this file iterates
 * it and branches on nothing. Today that is Portfolio alone — tasks 19.5, 20.4 and 23.5 add
 * Dashboard, Live Trading and Backtester, and the coverage test below names them so "Property 4
 * covers one page" cannot read as "Property 4 covers every tiered page".
 *
 * REGISTERING A PAGE HERE COSTS ONE `vi.mock`
 * ------------------------------------------
 * `vi.mock` is hoisted per file, so an entry's `requiredMocks` cannot be installed by the
 * registry — it can only be declared there and honoured here, at file scope. {@link INSTALLED_MOCKS}
 * is that list, and it is asserted equal to the union of every entry's declaration, so a page
 * added to the registry without its mock fails on a named expectation instead of on whatever
 * jsdom does when recharts asks for layout.
 *
 * FOUR DECISIONS TASK 16.3a MADE, HONOURED HERE
 * --------------------------------------------
 * 1. **The generator is split at `payload()`.** The value domain — absent, `null`, zero,
 *    wrongly-typed — is the property's and is identical for all four pages. The PLACEMENT is the
 *    entry's: only Portfolio's entry knows `investedCapital` is read at `overview.used_balance`,
 *    and only Live Trading's will know which of its four bodies a field is on. So the generator
 *    here is `fc.dictionary(fc.constantFrom(...entry.payloadFields), readingArb)` and this file
 *    holds no page knowledge at all. Zero is generated in its own right and not folded in with
 *    `null`: `0` is a READABLE reading that must still render in its tier, and a generator over
 *    only absent/`null` would never distinguish "renders 0" from "renders the em-dash".
 * 2. **Clause 1 is over tier containers and `[data-metric-tier]` elements — not one element per
 *    declared field.** A tier field is not a figure outside tier 1: Portfolio's tier 2 declares
 *    `openPositions` and `positionLiquidationPrice`, which are a `DataTable` and one of its
 *    *columns*, and tier 3's three keys are charts. "One rendered element per declared field"
 *    fails Portfolio on the healthy payload, before a single reading is generated.
 * 3. **Clause 2 is over `[data-metric-tier]`, never over a region attribute.** Live Trading's
 *    untiered `deploymentStopped` renders INSIDE its tier-1 `connectionState` element and
 *    deliberately carries no `Metric tier={1}`; a clause stated over regions reads that line as a
 *    stray tier-1 element outside the tier-1 container and fails a page that is correct.
 * 4. **The vacuous case is real, and is asserted rather than skipped.** `mount` resolves on
 *    "settled" — no `[data-panel-state="loading"]` — and not on "tier 1 appeared, because §7.4
 *    and Requirement 6.6 say an unreadable tier-1 row renders NO FIGURES AT ALL. So a generated
 *    payload may legitimately produce a page with no tiers, and on those runs both clauses hold
 *    over an empty node list. Skipping them would let a generator that destroys the page every
 *    time pass in green, so instead each run is counted and the counts are asserted after the
 *    property: at least one non-vacuous render, and every reading kind reached.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup } from '@testing-library/react';
import fc from 'fast-check';

import {
  ABSENT,
  PENDING_TIER_PAGES,
  TIER_REGISTRY,
  assertTierEntry,
  declaredTiersOf,
} from './tierRegistry';
import {
  METRIC_TIER_ATTRIBUTE,
  TIER_ATTRIBUTE,
  TIER_PAGE_ATTRIBUTE,
  TIERS,
  tierSelector,
} from '../../../src/design/pageHierarchy';
import { PAGES } from '../../../src/design/pageFields';

/*
 * Every entry's declared `requiredMocks`, installed at FILE scope because `vi.mock` is hoisted
 * per file. Copied verbatim from `tierRegistry.test.jsx`, which is the shape the registry's
 * docblock prescribes for a consumer. Nothing here asserts anything about a chart's interior —
 * that is `tests/unit/ds/Chart.test.jsx`'s subject; what tier 3 owes Property 4 is an element
 * carrying a tier, in the right place.
 */
vi.mock('../../../src/components/ds/Chart', () => {
  const Stub = (props) => <figure data-testid="chart" data-chart-kind={props.kind} />;
  return { __esModule: true, Chart: Stub, default: Stub };
});

/** The mocks installed above, by the module id each entry names. Asserted complete below. */
const INSTALLED_MOCKS = Object.freeze(['src/components/ds/Chart']);

/**
 * 100 runs, deliberately, and not this repo's usual 300.
 *
 * Task 16.3 and design.md §18 both state the requirement as "minimum 100 iterations per
 * property", so 100 is the spec floor and this property meets it. 300 is the convention the other
 * properties here follow, and it is the right number FOR THEM: every one of them asserts over a
 * pure function (`semantic.property.test.js`, `signalTraceStages.property.test.js`) or over a
 * single component, where a run costs microseconds and 300 is free.
 *
 * This property is the first one whose unit of work is a PAGE. One run installs four API reads,
 * mounts a real `Portfolio`, waits it out of its loading state, renders a lazy chart subtree and
 * unmounts — measured on this machine, seconds per run rather than milliseconds. At 300 that is
 * upwards of ten minutes of wall clock for one green run, which is long enough that the suite
 * stops being run and a property nobody runs asserts nothing.
 *
 * So this is a considered wall-clock decision, not a quiet weakening, and it is the ONLY relaxation
 * in this file: no assertion, no witness and no clause is softened to buy it, and the non-vacuity
 * witnesses below still demand that all four reading kinds were generated and that a tier-1 figure
 * actually rendered. 100 draws over a dictionary of up to eight keys from a four-value domain
 * reaches every kind comfortably; that is asserted after `fc.assert`, not assumed. Raise this to
 * 300 the day a run gets cheap — nothing else in the file has to change.
 */
const RUNS = { numRuns: 100 };

/**
 * Per-test timeout, well over the config's 15s default.
 *
 * Not a hang and not hiding one. Every run here installs four reads, mounts a real page,
 * `waitFor`s it out of its loading state, renders a lazy chart subtree and then unmounts it, and
 * jsdom charges real time for all of that; a cold Vite transform of the page's module graph is
 * charged to the first run on top. Generous on purpose — the timeout is not the lever this file
 * uses to control wall clock, {@link RUNS} is, and a `SLOW` tight enough to be interesting would
 * only convert a slow machine into a red suite. `signalTraceExpansion.property.test.jsx` sizes
 * its `SLOW` the same way.
 */
const SLOW = 600_000;

/**
 * The attributes the two clauses are decided by, spelled out.
 *
 * Cross-checked against `pageHierarchy`'s declaration in the first test and used as literals
 * everywhere else. Reading them out of the module instead would make this property agree with
 * any renaming of them, including one that silently stopped matching the pages' markup —
 * `tests/unit/lib/signalTraceStages.property.test.js`'s convention.
 */
const ATTRIBUTE = Object.freeze({
  page: 'data-page',
  tier: 'data-page-tier',
  metricTier: 'data-metric-tier',
});

/** The tier domain §18 gives. No tier 0, no tier 4. */
const EXPECTED_TIERS = Object.freeze([1, 2, 3]);

/** The pages this property covers today, and the tasks that add the rest. */
const EXPECTED_COVERAGE = Object.freeze(['portfolio']);
const EXPECTED_PENDING = Object.freeze(['dashboard:19.5', 'live-trading:20.4', 'backtester:23.5']);

/* ══════════════════════════════════════════════════════════════════════════
 * THE VALUE DOMAIN — page-independent, by construction
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * One wrongly-typed reading: anything a JSON body can carry that is not the number the page
 * expects.
 *
 * `undefined` is in here and is NOT the same generator as {@link ABSENT}: a key present with an
 * `undefined` value and a key that is not on the body are different objects, and a page reading
 * `Object.hasOwn` tells them apart. `NaN` is here because `JSON.parse` cannot produce it but
 * arithmetic on a garbage field can, and it is the value most likely to reach a formatter intact.
 */
const wronglyTypedArb = fc.oneof(
  fc.string(),
  fc.boolean(),
  fc.constant(Number.NaN),
  fc.constant(undefined),
  fc.constant([]),
  fc.constant({}),
  fc.constant('12.5'),
);

/**
 * One reading for one field: absent, `null`, zero, or wrongly-typed.
 *
 * Equal weights, so {@link RUNS} draws over a dictionary of up to eight keys reach every kind many
 * times over — witnessed, not assumed, at the end of the property.
 */
const readingArb = fc.oneof(
  fc.constant(ABSENT),
  fc.constant(null),
  fc.constant(0),
  wronglyTypedArb,
);

/**
 * The readings for one page: an arbitrary SUBSET of its `payloadFields`, each with an arbitrary
 * reading.
 *
 * A subset and not a full record, because a body where every figure is broken is one point in
 * the input space and the interesting ones are mixed: seven readable figures and one `null` is
 * the payload that tempts a page into rendering a partial tier 1 in the wrong place. An empty
 * dictionary is the healthy fixture, which `payload()` builds for `{}`.
 */
const readingsArb = (entry) =>
  fc.dictionary(fc.constantFrom(...entry.payloadFields), readingArb);

/**
 * Which of the four kinds a generated reading is, for the witness counters.
 *
 * Total over the domain above: {@link wronglyTypedArb} generates no numbers other than `NaN`, so
 * nothing it produces can be mistaken for the zero case.
 */
const readingKind = (value) => {
  if (value === ABSENT) return 'absent';
  if (value === null) return 'null';
  if (value === 0) return 'zero';
  return 'wrong-type';
};

/* ══════════════════════════════════════════════════════════════════════════
 * READING THE PAGE THE WAY THE CLAUSES ARE STATED
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * This page's tier containers, in document order, as `{tier, element}`.
 *
 * `querySelectorAll` returns document order, which is the whole subject of clause 1. Scoped to
 * `[data-page="…"]` so that a second registered page mounted in the same run could not lend this
 * one a container.
 */
const tierContainersOf = (page) =>
  [...document.querySelectorAll(`[${ATTRIBUTE.page}="${page}"][${ATTRIBUTE.tier}]`)].map(
    (element) => ({ tier: Number(element.getAttribute(ATTRIBUTE.tier)), element }),
  );

/** Every tiered figure on the page, in document order. The registry's `metrics()`, spelled. */
const tieredElementsOf = () =>
  [...document.querySelectorAll(`[${ATTRIBUTE.metricTier}]`)].map((element) => ({
    tier: Number(element.getAttribute(ATTRIBUTE.metricTier)),
    element,
  }));

/** `[a, b]` for the first offending pair in document order, or `null` if the tiers ascend. */
const firstInversion = (tiers) => {
  for (let index = 1; index < tiers.length; index += 1) {
    if (tiers[index] < tiers[index - 1]) return [index - 1, index];
  }
  return null;
};

/* ══════════════════════════════════════════════════════════════════════════
 * THE REGISTRY THIS PROPERTY RUNS OVER
 * ══════════════════════════════════════════════════════════════════════════ */

describe('Property 4 runs over the declared tier registry', () => {
  it('decides its clauses by the attributes `pageHierarchy` declares', () => {
    // The literals this file selects by ARE the declaration. Checked once, here, so that the
    // property below can use the literals and still fail on a renamed attribute.
    expect(TIER_PAGE_ATTRIBUTE).toBe(ATTRIBUTE.page);
    expect(TIER_ATTRIBUTE).toBe(ATTRIBUTE.tier);
    expect(METRIC_TIER_ATTRIBUTE).toBe(ATTRIBUTE.metricTier);
    expect(TIERS).toEqual(EXPECTED_TIERS);
    expect(tierSelector(PAGES.PORTFOLIO, 1)).toBe('[data-page="portfolio"][data-page-tier="1"]');
  });

  it('covers every registered page, and names the tasks that register the rest', () => {
    expect(TIER_REGISTRY.map((entry) => entry.page)).toEqual([...EXPECTED_COVERAGE]);
    expect(PENDING_TIER_PAGES.map(({ page, task }) => `${page}:${task}`))
      .toEqual([...EXPECTED_PENDING]);

    // A mispaired entry selects tier containers for a page it did not render, and then every
    // clause below passes over an empty node list. Cheap to rule out before generating anything.
    for (const entry of TIER_REGISTRY) expect(() => assertTierEntry(entry)).not.toThrow();
  });

  it('installs every mock the registry declares, and no mock it does not', () => {
    // The one thing a consumer of the registry owes it. A page added to `TIER_REGISTRY` without
    // its file-scope `vi.mock` fails here, by name, instead of inside recharts.
    const declared = TIER_REGISTRY.flatMap((entry) => entry.requiredMocks.map((m) => m.module));
    expect([...new Set(declared)].sort()).toEqual([...INSTALLED_MOCKS].sort());
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Property 4 — one suite per registered page, branching on nothing
 * ══════════════════════════════════════════════════════════════════════════ */

for (const entry of TIER_REGISTRY) {
  describe(`Property 4: declared tier determines document order on ${entry.page}`, () => {
    const declaredTiers = declaredTiersOf(entry.hierarchy);

    afterEach(() => {
      // Belt and braces. Each run unmounts itself in a `finally`; this catches a mount that threw
      // before it could return a handle, so run n + 1 never inherits run n's DOM.
      cleanup();
      vi.restoreAllMocks();
    });

    it('orders every tier-n element before every tier-(n+1) element, and keeps tier 1 in its '
      + 'single container', async () => {
      /*
       * Witnesses, in the shape `signalTraceStages.property.test.js` uses: a green run that
       * reached none of the interesting readings, or that destroyed the page on every payload,
       * would be decoration. Counted here, asserted after `fc.assert`.
       */
      const kinds = new Set();
      let vacuousRuns = 0;
      let nonVacuousRuns = 0;
      let healthyFixtureRuns = 0;
      let tierOneRenderedRuns = 0;
      let partialTierOneRuns = 0;

      await fc.assert(
        fc.asyncProperty(readingsArb(entry), async (readings) => {
          for (const value of Object.values(readings)) kinds.add(readingKind(value));
          if (Object.keys(readings).length === 0) healthyFixtureRuns += 1;

          /*
           * `entry.payload(readings)` is the seam: the readings map is page-independent, the
           * body it becomes is the entry's business. The map is applied here rather than inside
           * the generator only so that a counterexample prints the eight-key readings map
           * instead of the whole dashboard body it expands into.
           */
          let mounted = null;
          try {
            mounted = await entry.mount(entry.payload(readings));

            const containers = tierContainersOf(entry.page);
            const elements = tieredElementsOf();
            const containerTiers = containers.map(({ tier }) => tier);
            const elementTiers = elements.map(({ tier }) => tier);

            // Every tier on screen is one this page declares. A `Metric tier={4}` or a container
            // marked with a tier the hierarchy never claims is a change to what the page means.
            for (const tier of [...containerTiers, ...elementTiers]) {
              expect(declaredTiers, `${entry.page} rendered tier ${tier}`).toContain(tier);
            }

            /*
             * ── CLAUSE 1, over the tier containers ────────────────────────────────
             * At most one container per tier, and their tiers ascend in document order. A tier
             * container that moved above a lower-numbered one shows up as [2, 1, 3] here.
             */
            expect(new Set(containerTiers).size, `${entry.page} tiers ${containerTiers}`)
              .toBe(containerTiers.length);
            expect(firstInversion(containerTiers), `container order ${containerTiers}`).toBeNull();

            /*
             * ── CLAUSE 1, over the tiered figures ─────────────────────────────────
             * The clause as stated: for consecutive declared tiers n and n + 1, the LAST tier-n
             * figure precedes the FIRST tier-(n + 1) figure. Stated over index bounds rather than
             * over the sorted list because that is the sentence the requirement is written in,
             * and then over the whole sequence too, which additionally rules out a tier-3 figure
             * above a tier-1 one.
             */
            for (let index = 1; index < declaredTiers.length; index += 1) {
              const lower = declaredTiers[index - 1];
              const upper = declaredTiers[index];
              const lastLower = elementTiers.lastIndexOf(lower);
              const firstUpper = elementTiers.indexOf(upper);
              if (lastLower !== -1 && firstUpper !== -1) {
                expect(
                  lastLower,
                  `${entry.page}: a tier-${upper} figure precedes a tier-${lower} figure `
                    + `(document order ${elementTiers})`,
                ).toBeLessThan(firstUpper);
              }
            }
            expect(firstInversion(elementTiers), `figure order ${elementTiers}`).toBeNull();

            /*
             * ── CLAUSE 2 ──────────────────────────────────────────────────────────
             * No tier-1 element outside the page's single tier-1 container. Decided over
             * `[data-metric-tier="1"]` and nothing else: an untiered element nested inside a
             * tier-1 figure is not a tier-1 element, by §7's own construction.
             */
            const tierOne = elements.filter(({ tier }) => tier === 1);
            const tierOneContainers = containers.filter(({ tier }) => tier === 1);
            if (tierOne.length > 0) {
              expect(tierOneContainers, `${entry.page} tier-1 containers`).toHaveLength(1);
              for (const { element } of tierOne) {
                expect(
                  tierOneContainers[0].element.contains(element),
                  `${entry.page}: a tier-1 figure rendered outside the tier-1 container`,
                ).toBe(true);
              }
            }

            // The vacuity bookkeeping. §7.4: a tier-1 row that cannot be read renders no figures
            // at all, so `elements.length === 0` is a legitimate outcome and both clauses above
            // just held over an empty list. Counted so that "every payload destroyed the page"
            // cannot pass as green.
            if (elements.length === 0) vacuousRuns += 1;
            else nonVacuousRuns += 1;
            if (tierOne.length > 0) tierOneRenderedRuns += 1;
            if (tierOne.length > 0 && tierOne.length < 8) partialTierOneRuns += 1;
          } finally {
            if (mounted !== null) mounted.unmount();
            else {
              cleanup();
              vi.restoreAllMocks();
            }
          }
        }),
        RUNS,
      );

      // Non-vacuity of the SAMPLE: every reading kind was generated, including zero on its own.
      expect([...kinds].sort()).toEqual(['absent', 'null', 'wrong-type', 'zero']);
      // …and the generator did not merely destroy the page on every run. Without this, a `mount`
      // that rendered nothing would satisfy both clauses every time.
      expect(nonVacuousRuns, `${entry.page} rendered no tiers on any of the runs`)
        .toBeGreaterThan(0);
      expect(tierOneRenderedRuns, `${entry.page} never rendered a tier-1 figure`)
        .toBeGreaterThan(0);
      expect(vacuousRuns + nonVacuousRuns).toBeGreaterThanOrEqual(RUNS.numRuns);
      // Reported rather than asserted on: a healthy `{}` draw is likely over {@link RUNS} draws
      // but is not something the property depends on, and a partial tier 1 is clause 2's shape.
      expect(healthyFixtureRuns).toBeGreaterThanOrEqual(0);
      expect(partialTierOneRuns).toBeGreaterThanOrEqual(0);
    }, SLOW);
  });
}
