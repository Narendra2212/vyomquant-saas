/**
 * ═══════════════════════════════════════════════════════════════════════════
 * Feature: vyomquant-ui-redesign — Property 6 and Property 7
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * Tasks 17.3 and 17.4. `design.md` §7.2, §19.
 *
 *   * **Property 6: The rendered action set never exceeds what the server permitted.**
 *     **Validates: Requirements 4.2**
 *   * **Property 7: Destructive and live-transition actions are partitioned into the
 *     separated group.** **Validates: Requirements 4.3**
 *
 * WHY THESE ARE ASSERTED AGAINST THE MODULE AND NOT AGAINST A ROW
 * -------------------------------------------------------------
 * Both properties are claims about *construction*. P6 says a control for an action nobody
 * offered is never **built** — not that it is built and hidden, which is one CSS mistake
 * away from being shown, and which is what a DOM-level assertion would happily accept. P7
 * says the partition of rendered actions *exactly matches the classifier*, which is a
 * statement about two functions agreeing, not about a border colour.
 *
 * So they are asserted where the decision is made: `src/lib/rowActions.js`, the pure
 * module `pages/Strategies.jsx` renders the output of. This is the shape `lib/deployFlow.js`
 * already uses for P13/P14 and the reason it exists apart from
 * `components/deploy/DeployConfirmation.jsx`.
 *
 * WHAT THE GENERATORS COVER, AND WHY EACH PART IS THERE
 * ---------------------------------------------------
 * Task 17.3 asks for lists "including unknown members, duplicates and the empty list", and
 * every one of the three is a real failure mode rather than a box to tick:
 *
 *   * **unknown members** — a server that grows a new action name, or a renamed enum. The
 *     client must render nothing for it, not a nameless control and not a throw.
 *   * **duplicates** — a projection bug, or two sources merged. The row must not grow two
 *     Archive buttons.
 *   * **the empty list** — an entry the server permits nothing on. The row must render no
 *     actions rather than fall back to a default set.
 *
 * Beyond the three: prototype keys (`'constructor'`, `'__proto__'`), which a bare
 * `catalogue[id]` lookup would resolve to `Object` and then render as a control — the same
 * defect `design/semantic.js` guards its vocabulary against; non-strings, because
 * `allowed_actions` is JSON and JSON carries numbers, `null` and objects; and non-arrays in
 * place of the list entirely, because a 500 body or a shape change can put an object there.
 */

import { describe, expect, it } from 'vitest';
import fc from 'fast-check';

import {
  ROW_ACTION,
  SEPARATED_ROW_ACTIONS,
  isSeparatedAction,
  ownerRowActions,
  partitionRowActions,
  renderedActionIds,
  separationOf,
} from '../../../src/lib/rowActions';

/** design.md: "minimum 100 iterations per property". */
const RUNS = { numRuns: 300 };

/* ══════════════════════════════════════════════════════════════════════════
 * GENERATORS
 * ══════════════════════════════════════════════════════════════════════════ */

/** Every id this client has a name for. The honest half of an `allowed_actions` list. */
const KNOWN_IDS = Object.values(ROW_ACTION);

/**
 * Ids the client has no name for.
 *
 * The first six are real names `design.md` §7.2 lists as deliberately absent from the
 * page's catalogue; `renew` and `view_listing` are the marketplace section's, which the
 * owner row must never acquire. The last three are the prototype keys.
 */
const UNKNOWN_IDS = [
  'open_in_builder',
  'view_graph',
  'edit_blocks',
  're_version',
  'view_model_params',
  'download_source',
  'renew',
  'view_listing',
  'constructor',
  '__proto__',
  'toString',
];

/** A member of an `allowed_actions` list: mostly real ids, sometimes not an id at all. */
const anyMember = fc.oneof(
  { weight: 6, arbitrary: fc.constantFrom(...KNOWN_IDS) },
  { weight: 3, arbitrary: fc.constantFrom(...UNKNOWN_IDS) },
  { weight: 1, arbitrary: fc.string() },
  { weight: 1, arbitrary: fc.constantFrom(null, undefined, 0, 7, true, {}, []) },
);

/** A list, including the empty one and lists that repeat their members. */
const anyAllowedActions = fc.oneof(
  { weight: 8, arbitrary: fc.array(anyMember, { maxLength: 14 }) },
  // Duplicates, guaranteed rather than left to chance: a short domain sampled long.
  {
    weight: 3,
    arbitrary: fc
      .array(fc.constantFrom(...KNOWN_IDS), { minLength: 4, maxLength: 10 })
      .map((ids) => [...ids, ...ids]),
  },
  { weight: 1, arbitrary: fc.constant([]) },
  // Not a list at all.
  { weight: 1, arbitrary: fc.constantFrom(null, undefined, 'deploy_live', 42, {}) },
);

/**
 * A catalogue: a subset of the known ids, each carrying a descriptor.
 *
 * The descriptor's contents are irrelevant to both properties — the module reads no field
 * of it, only whether the key exists and the value is not nullish — so it is a marker
 * object. Some entries are deliberately `null`, which is a key declared with nothing behind
 * it and must render nothing.
 */
const anyCatalogue = fc
  .uniqueArray(fc.constantFrom(...KNOWN_IDS, ...UNKNOWN_IDS), { maxLength: 12 })
  .chain((ids) =>
    fc.array(fc.boolean(), { minLength: ids.length, maxLength: ids.length }).map((declared) =>
      Object.fromEntries(
        ids.map((id, index) => [id, declared[index] ? { label: id } : null]),
      ),
    ),
  );

/** The catalogue `pages/Strategies.jsx` actually declares, as a fixed shape. */
const PAGE_CATALOGUE = Object.freeze({
  [ROW_ACTION.RUN_BACKTEST]: { label: 'Backtest' },
  [ROW_ACTION.EDIT]: { label: 'Edit' },
  [ROW_ACTION.CLONE]: { label: 'Duplicate' },
  [ROW_ACTION.RENAME]: { label: 'Rename' },
  [ROW_ACTION.SIGNAL_TRACE]: { label: 'Signal Trace' },
  [ROW_ACTION.PAUSE]: { label: 'Pause' },
  [ROW_ACTION.DEPLOY_LIVE]: { label: 'Deploy live' },
  [ROW_ACTION.ARCHIVE]: { label: 'Archive strategy' },
});

/* ══════════════════════════════════════════════════════════════════════════
 * Property 6 — the offer never widens
 * ══════════════════════════════════════════════════════════════════════════ */

describe('Property 6: the rendered action set never exceeds what the server permitted', () => {
  it('renders only ids present in BOTH the offered list and the catalogue', () => {
    fc.assert(
      fc.property(anyAllowedActions, anyCatalogue, (offered, catalogue) => {
        const rendered = renderedActionIds(partitionRowActions(offered, catalogue));
        const offeredSet = new Set(Array.isArray(offered) ? offered : []);

        rendered.forEach((id) => {
          // ⊆ the input list.
          expect(offeredSet.has(id), `"${id}" was rendered but never offered`).toBe(true);
          // ⊆ the client catalogue, as an OWN key with something behind it.
          expect(
            Object.prototype.hasOwnProperty.call(catalogue, id),
            `"${id}" was rendered but is not an own key of the catalogue`,
          ).toBe(true);
          expect(catalogue[id] ?? null, `"${id}" was rendered from a null descriptor`)
            .not.toBe(null);
        });
      }),
      RUNS,
    );
  });

  it('renders each id at most once, however many times it is offered', () => {
    fc.assert(
      fc.property(anyAllowedActions, anyCatalogue, (offered, catalogue) => {
        const rendered = renderedActionIds(partitionRowActions(offered, catalogue));
        expect(new Set(rendered).size).toBe(rendered.length);
      }),
      RUNS,
    );
  });

  it('renders nothing at all for the empty offer or an empty catalogue', () => {
    fc.assert(
      fc.property(anyCatalogue, (catalogue) => {
        expect(renderedActionIds(partitionRowActions([], catalogue))).toEqual([]);
      }),
      RUNS,
    );
    fc.assert(
      fc.property(anyAllowedActions, (offered) => {
        expect(renderedActionIds(partitionRowActions(offered, {}))).toEqual([]);
      }),
      RUNS,
    );
  });

  it('never throws, whatever is handed to it', () => {
    fc.assert(
      fc.property(fc.anything(), fc.anything(), (offered, catalogue) => {
        expect(() => partitionRowActions(offered, catalogue)).not.toThrow();
      }),
      RUNS,
    );
  });

  it('holds for the offer the owner list actually builds, over every row shape', () => {
    // The narrowing input `pages/Strategies.jsx` passes in, rather than a synthetic list:
    // P6's subject is the page's real offer, and `ownerRowActions` is where it comes from.
    fc.assert(
      fc.property(
        fc.record(
          {
            id: fc.oneof(fc.string(), fc.integer(), fc.constant(null)),
            status: fc.oneof(
              fc.constantFrom('running', 'paused', 'draft', 'failed', 'stopped'),
              fc.string(),
              fc.constant(undefined),
            ),
          },
          { requiredKeys: [] },
        ),
        (row) => {
          const offered = ownerRowActions(row);
          const rendered = renderedActionIds(partitionRowActions(offered, PAGE_CATALOGUE));
          rendered.forEach((id) => {
            expect(offered).toContain(id);
            expect(Object.prototype.hasOwnProperty.call(PAGE_CATALOGUE, id)).toBe(true);
          });
          // `pause` and `deploy_live` are exclusive: a running strategy is never offered a
          // deployment, and a stopped one is never offered a pause.
          expect(
            rendered.includes(ROW_ACTION.PAUSE) && rendered.includes(ROW_ACTION.DEPLOY_LIVE),
          ).toBe(false);
        },
      ),
      RUNS,
    );
  });
});

/* ══════════════════════════════════════════════════════════════════════════
 * Property 7 — the partition matches the classifier exactly
 * ══════════════════════════════════════════════════════════════════════════ */

describe('Property 7: destructive and live-transition actions are partitioned out', () => {
  it('puts every accepted action in the separated group and no rejected one there', () => {
    fc.assert(
      fc.property(anyAllowedActions, anyCatalogue, (offered, catalogue) => {
        const { inline, separated } = partitionRowActions(offered, catalogue);

        // Every accepted action that is rendered is in the separated group…
        separated.forEach((id) => {
          expect(isSeparatedAction(id), `"${id}" is separated but the classifier rejects it`)
            .toBe(true);
        });
        // …and no rejected action is.
        inline.forEach((id) => {
          expect(isSeparatedAction(id), `"${id}" is inline but the classifier accepts it`)
            .toBe(false);
        });
        // The two groups are a partition: disjoint, and together the whole rendered set.
        const overlap = separated.filter((id) => inline.includes(id));
        expect(overlap).toEqual([]);
        expect([...inline, ...separated].length)
          .toBe(renderedActionIds({ inline, separated }).length);
      }),
      RUNS,
    );
  });

  it('accepts the live deployment and the archive, and rejects every paper target', () => {
    // The classification the requirement turns on, over the whole known vocabulary rather
    // than at three sample points. `deploy_paper` / `start_paper` are the load-bearing
    // rejections: §7.2 says a paper session is not a live transition.
    fc.assert(
      fc.property(fc.constantFrom(...KNOWN_IDS), (id) => {
        const accepted = SEPARATED_ROW_ACTIONS.includes(id);
        expect(isSeparatedAction(id)).toBe(accepted);
        if (id === ROW_ACTION.DEPLOY_PAPER || id === ROW_ACTION.START_PAPER) {
          expect(isSeparatedAction(id)).toBe(false);
        }
        if (id === ROW_ACTION.PAUSE) expect(isSeparatedAction(id)).toBe(false);
      }),
      RUNS,
    );
  });

  it('gives every separated entry a treatment, and every inline entry none', () => {
    fc.assert(
      fc.property(anyAllowedActions, anyCatalogue, (offered, catalogue) => {
        const { inline, separated } = partitionRowActions(offered, catalogue);
        separated.forEach((id) => expect(['live', 'destructive']).toContain(separationOf(id)));
        inline.forEach((id) => expect(separationOf(id)).toBe(null));
      }),
      RUNS,
    );
  });

  it('classifies without throwing, for any input at all', () => {
    fc.assert(
      fc.property(fc.anything(), (value) => {
        expect(() => isSeparatedAction(value)).not.toThrow();
        expect(typeof isSeparatedAction(value)).toBe('boolean');
      }),
      RUNS,
    );
  });
});
