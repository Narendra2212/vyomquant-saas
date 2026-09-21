/**
 * ═══════════════════════════════════════════════════════════════════════════
 * src/design/semantic.js — the ONE state → colour mapping
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign §4.1, §4.2, §8.1, §9.1.
 * Requirements 1.4, 4.1, 7.4, 8.5, 12.3.
 *
 * This is the **only** module in the application permitted to turn a state into a
 * colour. Every `ds/*` primitive derives its colour from a *state* prop through one of
 * the functions below; none of them accepts a colour prop. That is what makes
 * Requirement 1.4's "exclusively through that mapping" structural rather than a rule
 * people have to remember.
 *
 * Every colour here comes from `./tokens`, which is generated from
 * `src/styles/tokens.css`. There is no colour literal in this file and there must never
 * be one — the `no-colour-literals` guard (task 1.10) and a re-run of
 * `npm run tokens` are both premised on `tokens.css` being the single source.
 */

import { token } from './tokens';

/*
 * ── The eight semantic groups ──────────────────────────────────────────────
 *
 * Six are named by Requirement 1.4. `neutral` is added because an unknown or idle
 * state must resolve to *something* rather than to `undefined`. `guidance` is a
 * separate named token from `warning` even though `tokens.css` currently gives them
 * the same hex: Requirement 5.5 needs invalid-connection feedback on the Strategy
 * Builder canvas to be distinguishable from an error on saved data, and separating the
 * name now means a future decision to split the values is a one-line change in
 * `tokens.css` rather than a hunt through call sites.
 *
 * Each `token.status.*` entry is `{fg, wash}`; that shape is spread into the result and
 * is what consumers destructure.
 *
 * KNOWN TOKEN COLLISION (see the report on task 5.1, and Property 1 in §19):
 * `tokens.css` deliberately gives `status.live`, `status.connected` and `status.profit`
 * one green (#26A69A) and `status.loss` and `status.error` one red (#EF5350). So the six
 * requirement-named groups are distinct *tokens* and distinct `group` values, but they
 * are NOT pairwise distinct in `fg`/`wash` — there are two hue families, not six. This
 * module reports the group name so a consumer can always tell them apart; making the
 * *values* pairwise distinct would require a `tokens.css` change, which this task does
 * not own.
 */
const GROUP = Object.freeze({
  live: token.status.live,
  connected: token.status.connected,
  profit: token.status.profit,
  loss: token.status.loss,
  error: token.status.error,
  warning: token.status.warning,
  guidance: token.status.guidance,
  neutral: token.status.neutral,
});

/** The six groups Requirement 1.4 names, in requirement order. */
export const REQUIRED_STATUS_GROUPS = Object.freeze([
  'live',
  'connected',
  'profit',
  'loss',
  'error',
  'warning',
]);

/** Every group this module can return, including the two additions. */
export const STATUS_GROUPS = Object.freeze(Object.keys(GROUP));

/**
 * The §4.1 vocabulary table, flattened and keyed lowercase.
 *
 * Keys are the server values the in-scope pages actually receive — deployment states,
 * order states, connection states, exchange-health grades and side names all share this
 * one table on purpose, because a trader reads "rejected" the same way whichever
 * subsystem said it.
 *
 * Total by construction: `statusToken` falls back to `neutral` for anything absent, so
 * a value added to the backend tomorrow renders calmly instead of rendering
 * `undefined`.
 */
const VOCABULARY = Object.freeze({
  // live / running
  live: 'live',
  running: 'live',
  active: 'live',
  started: 'live',
  deployed: 'live',
  healthy: 'live',

  // connected / paired
  connected: 'connected',
  paired: 'connected',
  open: 'connected',
  ok: 'connected',

  // profitable / buy
  profit: 'profit',
  buy: 'profit',
  long: 'profit',
  up: 'profit',
  filled: 'profit',
  gain: 'profit',

  // loss / sell
  loss: 'loss',
  sell: 'loss',
  short: 'loss',
  down: 'loss',

  // error / disconnected
  error: 'error',
  failed: 'error',
  disconnected: 'error',
  rejected: 'error',
  closed: 'error',
  blocked: 'error',
  critical: 'error',
  stale: 'error',

  // warning
  warning: 'warning',
  paused: 'warning',
  pending: 'warning',
  degraded: 'warning',
  connecting: 'warning',
  reconnecting: 'warning',
  partially_filled: 'warning',

  // neutral — not a Requirement 1.4 group, but a real set of states
  idle: 'neutral',
  stopped: 'neutral',
  draft: 'neutral',
  cancelled: 'neutral',
  unknown: 'neutral',
});

/** The declared vocabulary, for tests and for the dev-time audit in `ds/StatusBadge`. */
export const STATUS_VOCABULARY = Object.freeze(Object.keys(VOCABULARY));

/**
 * The only way a colour is chosen from a state anywhere in the app.
 *
 * Total over every input. An unrecognised value, the empty string, `null`, `undefined`
 * and any non-string all resolve to `neutral` — never to `undefined`, so no caller has
 * to guard the result before spreading it.
 *
 * @param {unknown} state
 * @returns {{ group: string, fg: string, wash: string }}
 */
export function statusToken(state) {
  const key = typeof state === 'string' ? state.trim().toLowerCase() : '';
  const group = Object.prototype.hasOwnProperty.call(VOCABULARY, key)
    ? VOCABULARY[key]
    : 'neutral';
  return { group, ...GROUP[group] };
}

/**
 * A signed number → profit / loss / neutral.
 *
 * **Zero is NEUTRAL, not profit.** This is a deliberate correction of
 * `ui-legacy/primitives.jsx`'s `PnLBadge`, which tests `value >= 0` and therefore
 * renders a flat position in profit green today. A position that has made nothing has
 * not made a profit, and colouring it as one overstates the account on every panel that
 * shows it.
 *
 * Non-finite input (`NaN`, `Infinity`, `null`, `undefined`, a string) is neutral too: an
 * unreadable figure is not a gain. Rendering "not available" rather than `0` is
 * `ds/Metric`'s job (Requirement 14.5) — this function only decides the hue.
 *
 * @param {unknown} value
 * @returns {{ group: string, fg: string, wash: string }}
 */
export function pnlToken(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value === 0) {
    return { group: 'neutral', ...GROUP.neutral };
  }
  return value > 0
    ? { group: 'profit', ...GROUP.profit }
    : { group: 'loss', ...GROUP.loss };
}

/*
 * ── Environment treatment ─────────────────────────────────────────────────
 * §4.2 and §8.2. Requirements 7.4, 8.5, 12.2, 12.3.
 *
 * Exactly three values. The three differ on FOUR independent axes — hue, label text,
 * icon and border style — so a trader who cannot distinguish the hues still cannot
 * mistake a live badge for a paper one. Requirement 12.3 read strictly asks for this,
 * and Property 22 asserts it.
 *
 * `--color-env-live` reuses the loss hue on purpose (see the comment in `tokens.css`):
 * live is the state a trader must never mistake, so it takes the most alarming hue in
 * the palette. That is safe because the badge always carries text *and* an icon, never
 * colour alone, and it never sits inside a numeric figure.
 *
 * `icon` is a lucide export *name*, not a component. This module stays free of React so
 * it can be imported by tests, guards and non-component code; `ds/TradingEnvironmentBadge`
 * (task 6.7) resolves the name to a component.
 */
export const ENVIRONMENT = Object.freeze({
  LIVE: Object.freeze({
    id: 'LIVE',
    label: 'LIVE',
    long: 'Live — real funds, real orders',
    ...token.env.live,
    icon: 'Radio',
    border: 'solid',
  }),
  PAPER: Object.freeze({
    id: 'PAPER',
    label: 'PAPER TRADING',
    long: 'Simulated — no live order is ever placed',
    ...token.env.paper,
    icon: 'FlaskConical',
    border: 'dashed',
  }),
  BACKTEST: Object.freeze({
    id: 'BACKTEST',
    label: 'BACKTEST',
    long: 'Simulated on historical data',
    ...token.env.backtest,
    icon: 'History',
    border: 'dotted',
  }),
});

/** The three ids, in escalating-consequence order. */
export const ENVIRONMENT_IDS = Object.freeze(['BACKTEST', 'PAPER', 'LIVE']);

/**
 * A server-supplied environment string → its treatment.
 *
 * Returns `null` when the server did not say. `null` is a real state, not an error: it
 * is what makes "the server did not report an execution environment" renderable instead
 * of guessed. `ds/TradingEnvironmentBadge` renders `ENVIRONMENT UNCONFIRMED` (or
 * `SIMULATED · SERVER LABEL UNAVAILABLE`, the copy `pages/TradeHistory.jsx` already
 * uses) for that case, in the `neutral` group — reachable from here as
 * `statusToken(null)`, so the null arm still takes its colour from this module.
 *
 * @param {unknown} value
 * @returns {typeof ENVIRONMENT.LIVE | null}
 */
export function environmentTreatment(value) {
  const key = typeof value === 'string' ? value.trim().toUpperCase() : '';
  return Object.prototype.hasOwnProperty.call(ENVIRONMENT, key)
    ? ENVIRONMENT[key]
    : null;
}

/**
 * Resolve the trading environment from server-supplied fields only (§8.1).
 *
 * Each step reads a real server field and **there is no default branch**. The
 * environment is never inferred from a route, from a toggle position, or from the
 * absence of a field:
 *
 *   1. a backtest context — the caller is rendering historical results
 *   2. a named server environment (`positions[].environment`, `execution_environment`,
 *      a deployment's mode)
 *   3. `isSimulated === true` — the server said simulated but not which kind
 *   4. otherwise `null`
 *
 * Defaulting to `LIVE` would be alarmist; defaulting to `PAPER` would be dangerous.
 * Saying "unconfirmed" is the only honest option.
 *
 * `isSimulated === true` is an identity check on purpose: a truthy non-boolean
 * (`"false"`, `1`, `{}`) is not the server saying simulated.
 *
 * @param {{serverEnvironment?: unknown, isSimulated?: unknown, backtestContext?: unknown}} [input]
 * @returns {typeof ENVIRONMENT.LIVE | null}
 */
export function resolveEnvironment(input) {
  const { serverEnvironment, isSimulated, backtestContext } = input || {};
  if (backtestContext) return ENVIRONMENT.BACKTEST;
  const named = environmentTreatment(serverEnvironment);
  if (named) return named;
  if (isSimulated === true) return ENVIRONMENT.PAPER;
  return null;
}

/*
 * ── Strategy Builder stage bands ──────────────────────────────────────────
 * §9.1. Requirement 5.1.
 *
 * The backend serves SEVEN `BlockCategory` values
 * (`backend_app/backend/strategy_dag/schema.py`: DATA INDICATOR MATH LOGIC
 * FEATURE_ENGINEERING ML_DL ACTION); Requirement 5.1 names FIVE data-flow stages. The
 * stage band sits on top of the seven categories rather than replacing them: the five
 * stages are authoritative for *layout* (lane order, lane headers, stage number), the
 * seven categories stay authoritative for *identity* (name, icon).
 *
 * Colour is spent on the stage band edge and on validation markers only — every node
 * body is `surface.raised` whatever its stage (Requirement 1.5). Stage 4 shares the
 * neutral hue with stage 1 and carries a distinct border instead of a new hue, so the
 * palette does not grow a sixth decorative colour.
 */
export const STAGE_BAND = Object.freeze({
  MARKET_DATA: Object.freeze({
    id: 'MARKET_DATA',
    order: 1,
    label: 'Market data',
    categories: Object.freeze(['DATA']),
    icons: Object.freeze({ DATA: 'Database' }),
    fg: token.content.secondary,
    wash: token.status.neutral.wash,
    border: 'solid',
  }),
  TRANSFORM: Object.freeze({
    id: 'TRANSFORM',
    order: 2,
    label: 'Transform',
    categories: Object.freeze(['INDICATOR', 'MATH', 'FEATURE_ENGINEERING']),
    icons: Object.freeze({
      INDICATOR: 'Activity',
      MATH: 'Sigma',
      FEATURE_ENGINEERING: 'Cpu',
    }),
    fg: token.brand.base,
    wash: token.brand.wash,
    border: 'solid',
  }),
  LOGIC: Object.freeze({
    id: 'LOGIC',
    order: 3,
    label: 'Logic',
    categories: Object.freeze(['LOGIC']),
    icons: Object.freeze({ LOGIC: 'GitBranch' }),
    fg: token.status.warning.fg,
    wash: token.status.warning.wash,
    border: 'solid',
  }),
  MODEL: Object.freeze({
    id: 'MODEL',
    order: 4,
    label: 'Model',
    categories: Object.freeze(['ML_DL']),
    icons: Object.freeze({ ML_DL: 'Brain' }),
    fg: token.status.neutral.fg,
    wash: token.status.neutral.wash,
    // §9.1: "a distinct border, not a new hue" — this is stage 4's fourth axis.
    border: 'dashed',
  }),
  ACTION: Object.freeze({
    id: 'ACTION',
    order: 5,
    label: 'Action',
    categories: Object.freeze(['ACTION']),
    icons: Object.freeze({ ACTION: 'Zap' }),
    fg: token.status.live.fg,
    wash: token.status.live.wash,
    border: 'solid',
  }),
  /*
   * The neutral sixth band. A category the frontend does not recognise resolves here
   * rather than being hidden, matching `lib/blockRegistry.js`'s `FALLBACK_PRESENTATION`
   * philosophy: a block the backend says exists must be drawable. Hiding it would make
   * a saved graph render with fewer nodes than it has, which is worse than drawing it
   * without a stage.
   */
  UNRESOLVED: Object.freeze({
    id: 'UNRESOLVED',
    order: 6,
    label: 'Unresolved',
    categories: Object.freeze([]),
    icons: Object.freeze({}),
    fg: token.status.neutral.fg,
    wash: token.status.neutral.wash,
    border: 'dotted',
  }),
});

/** The bands in lane order, including `UNRESOLVED`. For the lane header strip. */
export const STAGE_BANDS = Object.freeze([
  STAGE_BAND.MARKET_DATA,
  STAGE_BAND.TRANSFORM,
  STAGE_BAND.LOGIC,
  STAGE_BAND.MODEL,
  STAGE_BAND.ACTION,
  STAGE_BAND.UNRESOLVED,
]);

/** The five Requirement 5.1 stages, without the fallback band. */
export const DECLARED_STAGE_BANDS = Object.freeze(STAGE_BANDS.slice(0, 5));

/** Backend `BlockCategory` → stage band id. Derived from `STAGE_BAND`, so it cannot drift. */
export const BLOCK_CATEGORY_STAGE = Object.freeze(
  Object.freeze(Object.values(STAGE_BAND)).reduce((acc, band) => {
    band.categories.forEach((category) => {
      acc[category] = band.id;
    });
    return acc;
  }, {}),
);

/** The seven backend categories this map covers, in the backend's declaration order. */
export const BLOCK_CATEGORIES = Object.freeze([
  'DATA',
  'INDICATOR',
  'MATH',
  'LOGIC',
  'FEATURE_ENGINEERING',
  'ML_DL',
  'ACTION',
]);

/**
 * A backend `BlockCategory` → its stage band. Total: an unknown category, the empty
 * string, `null` and any non-string resolve to the neutral `UNRESOLVED` band.
 *
 * @param {unknown} category
 * @returns {typeof STAGE_BAND.MARKET_DATA}
 */
export function stageBandFor(category) {
  const key = typeof category === 'string' ? category.trim().toUpperCase() : '';
  const id = Object.prototype.hasOwnProperty.call(BLOCK_CATEGORY_STAGE, key)
    ? BLOCK_CATEGORY_STAGE[key]
    : 'UNRESOLVED';
  return STAGE_BAND[id];
}

/**
 * The lucide icon *name* for a category, from its band's icon set.
 *
 * `HelpCircle` for anything unresolved — the same "we do not know" icon §8.2 gives the
 * unconfirmed environment, so one unknown reads the same way everywhere.
 *
 * @param {unknown} category
 * @returns {string}
 */
export function stageIconFor(category) {
  const key = typeof category === 'string' ? category.trim().toUpperCase() : '';
  const band = stageBandFor(key);
  return band.icons[key] || 'HelpCircle';
}
