/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/lib/rowActions.js — the row's action offer, and its destructive partition
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 17.2. `design.md` §7.2. Requirements 4.2, 4.3, 7.6.
 * Properties P6, P7.
 *
 * WHY THE PARTITION LIVES APART FROM THE ROW THAT RENDERS IT
 * ---------------------------------------------------------
 * The two properties this module carries are both about *construction*, not appearance:
 *
 *   * **P6** — the set of action ids rendered is a subset of both the offered list and
 *     the client catalogue, so an action the server did not return can never be offered.
 *   * **P7** — every action the destructive-or-live classifier accepts appears in the
 *     separated group and no action it rejects appears there.
 *
 * Neither is a statement about a `<td>`. Both are statements about a function from
 * (offered ids, catalogue) to two lists, and both are only honestly assertable if that
 * function exists apart from anything that renders it. A cell that filtered a fixed
 * button list at render time would satisfy neither: the control for an action the server
 * never mentioned would have been *built* and merely not painted, and "visually
 * separated" would be a CSS fact rather than a structural one.
 *
 * This is the separation `lib/deployFlow.js` already uses for P13/P14, and that
 * `components/shell/navigation.js`, `design/errorLine.js` and `lib/strategyHealth.js`
 * use for the same class of pure decision. `pages/Strategies.jsx` renders what this
 * module decides and adds nothing to it.
 *
 * ⚠️ HOW THE OFFER IS STRUCTURALLY INCAPABLE OF WIDENING ⚠️
 * -------------------------------------------------------
 * {@link partitionRowActions} iterates the **catalogue's own keys** and emits one only
 * when the offered list contains it. So each emitted id is, by the shape of the loop:
 *
 *   1. an own key of the catalogue — a key reached through the prototype chain is not
 *      an own key, so `'constructor'` and `'toString'` in an `allowed_actions` list
 *      resolve to nothing rather than to `Object` (the defect a bare `catalogue[id]`
 *      lookup has, and the reason `design/semantic.js` guards its vocabulary the same
 *      way); and
 *   2. a member of the offered list.
 *
 * There is no branch that adds an id from anywhere else, and no default arm. An unknown
 * id, a duplicate, a non-string, `null`, a non-array and the empty list are all valid
 * inputs — every one of them narrows the offer or leaves it alone, and none of them
 * throws. A function that can throw here is a row that renders no actions at all.
 *
 * WHAT THIS MODULE DOES NOT DECIDE
 * -------------------------------
 * **Whether an action is permitted.** `allowed_actions` is an affordance list, never an
 * authorisation: each restricted route enforces its own 403 (Requirement 12.7), and this
 * module has no opinion about any of them. It also holds no label, no icon, no handler,
 * no endpoint and no colour — the catalogue the page passes in owns all five, so nothing
 * here can change what a request looks like or where it goes.
 *
 * PURITY
 * ------
 * No React, no `window`, no network, no timers, no colour. Every export is deterministic
 * given its arguments and every returned value is frozen.
 *
 * @module lib/rowActions
 */

/* ══════════════════════════════════════════════════════════════════════════
 * Vocabulary
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The action ids this client understands, spelled as the wire spells them.
 *
 * The snake_case names are `backend_app/backend/marketplace/library_entries.py`'s own —
 * `run_backtest`, `deploy_live`, `start_paper` are already the vocabulary
 * `pages/Strategies.jsx`'s `ACTION_CATALOG` reads from `entry.allowed_actions`, and
 * §7.2's note lists `edit`, `re_version` and `delete` among the ones that catalogue
 * deliberately has no entry for. They are named here rather than restated at each call
 * site so that the classifier and a catalogue cannot disagree about how an action is
 * spelled — a disagreement would silently drop the action, since an id absent from the
 * catalogue renders nothing.
 *
 * A row's *offer* is a subset of these; nothing requires a catalogue to describe all of
 * them, and nothing requires a server to return only these.
 */
export const ROW_ACTION = Object.freeze({
  /** Open the strategy in the builder. */
  EDIT: 'edit',
  /** §7.2's "Duplicate" — `POST /api/strategies/{id}/clone`. */
  CLONE: 'clone',
  /** `PUT /api/strategies/{id}/rename`. */
  RENAME: 'rename',
  /** §7.2's "Backtest". */
  RUN_BACKTEST: 'run_backtest',
  /** §7.2's "Signal Trace". */
  SIGNAL_TRACE: 'signal_trace',
  /** `POST /api/strategies/{id}/pause`. Neither destructive nor a live transition. */
  PAUSE: 'pause',
  /** The Live deployment. Separated. */
  DEPLOY_LIVE: 'deploy_live',
  /** A Paper session. **Not** separated — see {@link SEPARATION}. */
  START_PAPER: 'start_paper',
  /** The Paper deployment, under the spelling §7.2's prose uses. Not separated. */
  DEPLOY_PAPER: 'deploy_paper',
  /** §7.2's "Delete" — `DELETE /api/strategies/{id}`, which soft-archives. Separated. */
  DELETE: 'delete',
  /** The same operation under the name the endpoint actually performs. Separated. */
  ARCHIVE: 'archive',
});

/** How a separated action is treated. `ds/ConfirmDialog`'s `CONFIRM_INTENTS` vocabulary. */
export const SEPARATION = Object.freeze({
  /** Initiates a Live trading-environment transition → `env.live` (Requirement 4.3). */
  LIVE: 'live',
  /** A `Destructive_Action` → `status.error` (Requirement 4.3). */
  DESTRUCTIVE: 'destructive',
});

/**
 * The classification table: the ids Requirement 4.3 separates, and which treatment each
 * earns.
 *
 * Three entries, and the omissions are the load-bearing part:
 *
 *   * **`deploy_live`** initiates a Live transition. Separated, `live`.
 *   * **`delete` / `archive`** are the same `Destructive_Action` under two names — task
 *     5.1 rewired `DELETE /api/strategies/{id}` from a row delete into a soft archive, so
 *     the operation is reversible but it is still the one that takes the strategy out of
 *     the trader's library. Separated, `destructive`. (Reversibility is what decides
 *     whether §8.4 spends an *acknowledgement checkbox* on it, and it does not; it is not
 *     what decides whether the control sits with the harmless ones.)
 *   * **`start_paper` / `deploy_paper` are absent, deliberately.** §7.2: *"`Deploy paper`
 *     is not separated — it is not a live transition."* A paper session places no real
 *     order, so separating it would spend the trader's attention on the case that cannot
 *     cost them anything and would teach them to click past the treatment that matters.
 *   * **`pause` is absent.** Pausing a running strategy stops it placing new orders. That
 *     is the *safe* direction of a live transition and destroys nothing, so it stays with
 *     the inline controls.
 *
 * Frozen, and read through {@link separationOf}'s own-property guard.
 */
const SEPARATION_BY_ACTION = Object.freeze({
  [ROW_ACTION.DEPLOY_LIVE]: SEPARATION.LIVE,
  [ROW_ACTION.DELETE]: SEPARATION.DESTRUCTIVE,
  [ROW_ACTION.ARCHIVE]: SEPARATION.DESTRUCTIVE,
});

/** The ids the classifier accepts, in treatment order: the live one, then the destructive. */
export const SEPARATED_ROW_ACTIONS = Object.freeze(Object.keys(SEPARATION_BY_ACTION));

/* ══════════════════════════════════════════════════════════════════════════
 * The classifier
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The treatment one action id earns, or `null` when it earns none.
 *
 * Total over every input: a non-string, `null`, `undefined`, a number, an object and an
 * id this build has never heard of all answer `null`. Nothing is inferred from the shape
 * of a name — there is no `id.includes('delete')` here, because `undelete` and
 * `delete_draft` would both match it and neither is this operation.
 *
 * The id is trimmed and lower-cased before the lookup so that `'Deploy_Live'` and
 * `' deploy_live '` classify as what they plainly are. That tolerance cannot widen the
 * offer: {@link partitionRowActions} only ever emits an own key of the catalogue, so a
 * loosely-spelled id is dropped from the row entirely rather than promoted into the
 * inline group by a stricter comparison here. Erring toward `live`/`destructive` on a
 * near-miss spelling is also the safe direction — the worst it does is separate a
 * control that did not have to be separated.
 *
 * @param {unknown} id An action id, however it is spelled.
 * @returns {'live'|'destructive'|null}
 */
export function separationOf(id) {
  if (typeof id !== 'string') return null;
  const key = id.trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(SEPARATION_BY_ACTION, key)
    ? SEPARATION_BY_ACTION[key]
    : null;
}

/**
 * Whether this action is destructive or initiates a Live transition — P7's classifier.
 *
 * @param {unknown} id
 * @returns {boolean}
 */
export function isSeparatedAction(id) {
  return separationOf(id) !== null;
}

/* ══════════════════════════════════════════════════════════════════════════
 * The partition
 * ══════════════════════════════════════════════════════════════════════════ */

/** An array, or the empty array. Never throws, never copies a non-array's keys. */
function list(value) {
  return Array.isArray(value) ? value : [];
}

/** A plain object usable as a lookup table, or an empty one. */
function table(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value : {};
}

/**
 * Split one row's offered actions into the inline group and the separated group.
 *
 * ORDER IS THE CATALOGUE'S, NOT THE SERVER'S. §7.2 draws a fixed inline order
 * (`Backtest · Edit · Duplicate · View · Signal Trace`) and a fixed pair below the
 * divider; iterating the catalogue is what makes the row's controls sit in the same
 * places on every row, whatever order the offer arrives in. A trader who has learnt where
 * `Archive` is should not have to look for it again on the next row.
 *
 * That iteration order is also what makes P6 true by construction rather than by
 * inspection — see the module docblock. Duplicates in the offer collapse for free,
 * because each catalogue key is visited exactly once.
 *
 * An entry whose catalogue value is `null` or `undefined` is dropped: a key declared with
 * nothing behind it describes no control, and rendering a nameless one would be worse
 * than rendering none (the reasoning `ds/ActionControl` gives for returning `null`).
 *
 * @param {unknown} offered The `allowed_actions` list, or any garbage in its place.
 * @param {unknown} catalogue The page's own `{ [actionId]: descriptor }` table.
 * @returns {{inline: ReadonlyArray<string>, separated: ReadonlyArray<string>}} Frozen.
 */
export function partitionRowActions(offered, catalogue) {
  const wanted = new Set(list(offered).filter((id) => typeof id === 'string'));
  const declared = table(catalogue);

  const inline = [];
  const separated = [];

  Object.keys(declared).forEach((id) => {
    if (!wanted.has(id)) return;
    const descriptor = declared[id];
    if (descriptor === null || descriptor === undefined) return;
    (isSeparatedAction(id) ? separated : inline).push(id);
  });

  return Object.freeze({
    inline: Object.freeze(inline),
    separated: Object.freeze(separated),
  });
}

/**
 * Every id a partition renders, in render order — the set P6 asserts about.
 *
 * Exported so the property test reads one implementation of "what this row renders"
 * instead of restating `[...inline, ...separated]` and being right by coincidence.
 *
 * @param {{inline?: ReadonlyArray<string>, separated?: ReadonlyArray<string>}} partition
 * @returns {ReadonlyArray<string>} Frozen.
 */
export function renderedActionIds(partition) {
  const source = partition ?? {};
  return Object.freeze([...list(source.inline), ...list(source.separated)]);
}

/* ══════════════════════════════════════════════════════════════════════════
 * The owner list's offer
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * The actions this client offers for one strategy the trader **owns**.
 *
 * ⚠️ `GET /api/strategies` RETURNS NO `allowed_actions`. ⚠️ That field belongs to
 * `GET /api/library/my-strategies`, the marketplace ownership read, and §7.2's "map
 * `entry.allowed_actions` through `ACTION_CATALOG`" describes that section. The owner
 * list has no server-side affordance projection, so this function is the honest
 * equivalent: the client's own statement of what it can do with a strategy the trader
 * owns, derived from the row and from nothing else.
 *
 * It is a *narrowing* input to {@link partitionRowActions} exactly like a server list
 * would be — an id absent from what this returns is never iterated over and its control
 * is never constructed — so P6 holds over this section on the same mechanism. When the
 * listing endpoint grows an `allowed_actions` projection, this function is what that
 * field replaces, and nothing else about the row changes.
 *
 * THE ONE CONDITION: `pause` XOR `deploy_live`. A running strategy is offered `Pause`
 * and is not offered a deployment; anything else is offered the deployment and not
 * `Pause`. That is the card grid's own behaviour, moved unchanged — a "Deploy" beside a
 * strategy that is already running asks the trader to start a second one.
 *
 * @param {unknown} row A normalised row from `pages/Strategies.jsx`.
 * @returns {ReadonlyArray<string>} Frozen, in §7.2's inline-then-separated order.
 */
export function ownerRowActions(row) {
  const running = (row === null || typeof row !== 'object' ? {} : row).status === 'running';

  return Object.freeze([
    ROW_ACTION.RUN_BACKTEST,
    ROW_ACTION.EDIT,
    ROW_ACTION.CLONE,
    ROW_ACTION.RENAME,
    ROW_ACTION.SIGNAL_TRACE,
    running ? ROW_ACTION.PAUSE : ROW_ACTION.DEPLOY_LIVE,
    ROW_ACTION.ARCHIVE,
  ]);
}
