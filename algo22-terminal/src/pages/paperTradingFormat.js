/**
 * paperTradingFormat.js — the pure formatting, parsing and frame-reduction helpers
 * `PaperTrading.jsx` renders through.
 *
 * Separated from the page for one reason: every rule in here is a rule about MONEY or about
 * ABSENCE, and both are asserted rather than trusted. None of it touches React, `api`, the DOM
 * or a clock, so each function is directly exercisable.
 *
 * TWO KINDS OF MONEY REACH THIS PAGE, AND THEY ARE NOT INTERCHANGEABLE
 * -------------------------------------------------------------------
 * * **Integer Minor_Units.** `paper_sessions.initial_capital_minor`, `paper_orders.fee_minor`,
 *   `paper_orders.slippage_minor`, `paper_trades.fee_minor` — `BIGINT` columns holding an exact
 *   whole number of minor units. {@link formatMinorUnits} shifts the decimal point by moving
 *   characters, so `1998` becomes `19.98` without a division ever happening.
 * * **Exact decimal major units.** `paper_accounts` / `paper_equity_snapshots` /
 *   `paper_metrics` / `paper_positions` / `paper_trades` hold `NUMERIC(28,10)`, which arrives as
 *   a decimal string or a JSON number. {@link formatMoneyDecimal} renders the digits it was
 *   given — it never rounds and never truncates, so a figure that arrived exact is displayed
 *   exact.
 *
 * Nothing here adds, subtracts, multiplies or divides a monetary value. The only numeric
 * conversion is {@link toChartNumber}, which exists solely to place a pixel and is never used
 * for a displayed figure.
 *
 * ABSENCE IS A VALUE
 * ------------------
 * Every formatter returns `null` for an input it cannot render exactly — an unparseable string,
 * a `null`, a currency the platform holds no Minor_Units exponent for. `null` means "there is
 * no figure to show", and the page renders that as an explicit "not computed" / "not reported"
 * rather than as a zero. `GET /sessions/{id}/metrics` answering `metrics: null, computed: false`
 * and a `null` `win_rate` are both this case (Requirements 18.10, 28.5).
 */

// ═══════════════════════════════════════════════════════════════════════════
// CURRENCY
// ═══════════════════════════════════════════════════════════════════════════

/**
 * ISO 4217 minor-unit exponent per supported currency.
 *
 * The client half of `backend_app/backend/marketplace/money.MINOR_UNIT_EXPONENT`, which is the
 * platform's one persisted exponent table. A currency absent here has **no** exponent as far as
 * this page is concerned, and every function below refuses rather than assuming 2: assuming an
 * exponent is how a JPY balance becomes a hundredth of itself.
 *
 * @type {Readonly<Record<string, number>>}
 */
export const MINOR_UNIT_EXPONENT = Object.freeze({ USD: 2, INR: 2 });

/** The currencies a Paper_Account may be opened in, in the order the control offers them. */
export const SUPPORTED_CURRENCIES = Object.freeze(Object.keys(MINOR_UNIT_EXPONENT));

/** `marketplace.money.MAX_AMOUNT_MINOR` — the top of the inclusive Minor_Units domain. */
export const MAX_AMOUNT_MINOR = 99999999999;

/** `paper_repository.DEFAULT_CURRENCY`. */
export const DEFAULT_CURRENCY = 'USD';

/** A currency label as the exponent table keys it, or `''`. */
export const normaliseCurrency = (currency) => String(currency ?? '').trim().toUpperCase();

/**
 * The Minor_Units exponent for `currency`, or `null` when the platform holds none.
 *
 * @param {string} currency
 * @returns {number|null}
 */
export function minorUnitExponent(currency) {
  const code = normaliseCurrency(currency);
  const exponent = MINOR_UNIT_EXPONENT[code];
  return exponent === undefined ? null : exponent;
}

// ═══════════════════════════════════════════════════════════════════════════
// EXACT DECIMAL RENDERING — STRING OPERATIONS ONLY
// ═══════════════════════════════════════════════════════════════════════════

/** `1234567` -> `'1,234,567'`. Grouping is inserted into the digit string, not computed. */
const groupDigits = (whole) => whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');

/** Strip leading zeros while keeping at least one digit. */
const stripLeadingZeros = (digits) => digits.replace(/^0+(?=\d)/, '');

/**
 * `value` split into sign, integer digits and fraction digits, or `null` when it is not a plain
 * decimal.
 *
 * A JSON number is accepted because PostgREST may serialise `NUMERIC` either way, and
 * `String(number)` is exact for every value that round-trips as one. Exponent notation is
 * refused rather than expanded: `1e21` reaching here means the value already lost its exactness
 * upstream, and rendering it as if it had not would be the fabrication this page exists to
 * avoid.
 *
 * @param {string|number|null|undefined} value
 * @returns {{negative: boolean, whole: string, fraction: string}|null}
 */
export function splitDecimalText(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  const text = (typeof value === 'string' ? value : String(value)).trim();
  if (!/^[+-]?\d+(\.\d+)?$/.test(text)) return null;
  const negative = text.startsWith('-');
  const [whole, fraction = ''] = text.replace(/^[+-]/, '').split('.');
  return { negative, whole: stripLeadingZeros(whole), fraction };
}

/**
 * The three parts back as one display string.
 *
 * Trailing fraction zeros are dropped and then re-padded up to `minFractionDigits`. Digits
 * beyond that are **kept**: this function shortens nothing that carries information, so it
 * cannot display a figure that differs from the one it was handed.
 *
 * @param {{negative: boolean, whole: string, fraction: string}} parts
 * @param {number} [minFractionDigits=0]
 * @returns {string}
 */
export function renderDecimalParts(parts, minFractionDigits = 0) {
  let fraction = parts.fraction.replace(/0+$/, '');
  if (fraction.length < minFractionDigits) {
    fraction = fraction.padEnd(minFractionDigits, '0');
  }
  const whole = groupDigits(parts.whole || '0');
  const body = fraction ? `${whole}.${fraction}` : whole;
  const zero = /^[0.,]*$/.test(body);
  return `${parts.negative && !zero ? '-' : ''}${body}`;
}

/**
 * One `NUMERIC(28,10)` monetary value as text, at least to its currency's precision.
 *
 * The accounting engine quantizes every persisted monetary figure to the currency's Minor_Units
 * precision, so the digits past that point are trailing zeros in the normal case — and where
 * they are not, they are shown rather than hidden.
 *
 * @param {string|number|null|undefined} value
 * @param {string} [currency]
 * @returns {string|null} `null` when there is no figure to render.
 */
export function formatMoneyDecimal(value, currency = DEFAULT_CURRENCY) {
  const parts = splitDecimalText(value);
  if (parts === null) return null;
  // An unknown currency loses only the minimum-digit padding. No exponent is invented.
  const exponent = minorUnitExponent(currency);
  return renderDecimalParts(parts, exponent === null ? 0 : exponent);
}

/**
 * One integer Minor_Units amount as major-unit text.
 *
 * The decimal point is inserted by slicing the digit string, so `1998` USD renders `19.98` with
 * no division. A currency with no persisted exponent returns `null` — there is no exact
 * conversion to make, and a guessed one would misstate a balance by two orders of magnitude.
 *
 * @param {number|string|null|undefined} minor
 * @param {string} [currency]
 * @returns {string|null}
 */
export function formatMinorUnits(minor, currency = DEFAULT_CURRENCY) {
  const exponent = minorUnitExponent(currency);
  if (exponent === null) return null;
  if (minor === null || minor === undefined || typeof minor === 'boolean') return null;
  const text = (typeof minor === 'string' ? minor : String(minor)).trim();
  if (!/^-?\d+$/.test(text)) return null;
  const negative = text.startsWith('-');
  const digits = (negative ? text.slice(1) : text).padStart(exponent + 1, '0');
  const cut = digits.length - exponent;
  return renderDecimalParts(
    {
      negative,
      whole: stripLeadingZeros(digits.slice(0, cut)),
      fraction: digits.slice(cut),
    },
    exponent,
  );
}

/** A price as text. At least two fraction digits, every digit beyond them preserved. */
export function formatPrice(value) {
  const parts = splitDecimalText(value);
  return parts === null ? null : renderDecimalParts(parts, 2);
}

/** A quantity as text, exactly as recorded — no padding, no rounding. */
export function formatQuantity(value) {
  const parts = splitDecimalText(value);
  return parts === null ? null : renderDecimalParts(parts, 0);
}

/**
 * Move `value`'s decimal point `places` to the right by repositioning it in the digit string.
 *
 * This is how a fraction becomes a percentage without a multiplication: `0.62500` shifted two
 * places is `62.500`, character for character.
 *
 * @param {string|number|null|undefined} value
 * @param {number} places
 * @param {number} [minFractionDigits=0]
 * @returns {string|null}
 */
export function shiftDecimalRight(value, places, minFractionDigits = 0) {
  const parts = splitDecimalText(value);
  if (parts === null) return null;
  let digits = `${parts.whole}${parts.fraction}`;
  const point = parts.whole.length + places;
  while (digits.length < point) digits += '0';
  return renderDecimalParts(
    {
      negative: parts.negative,
      whole: stripLeadingZeros(digits.slice(0, point) || '0'),
      fraction: digits.slice(point),
    },
    minFractionDigits,
  );
}

/**
 * A fraction in `[0, 1]` — `paper_metrics.win_rate`, `max_drawdown_fraction` — as a percentage.
 *
 * @param {string|number|null|undefined} value
 * @param {number} [minFractionDigits=2]
 * @returns {string|null}
 */
export function formatFractionAsPercent(value, minFractionDigits = 2) {
  const shifted = shiftDecimalRight(value, 2, minFractionDigits);
  return shifted === null ? null : `${shifted}%`;
}

/**
 * A value already expressed in percent — `paper_metrics.total_return_pct` — as text.
 *
 * No shift: the column's name states its unit, and re-scaling a figure whose unit is already
 * percent would report a hundredth of the return.
 *
 * @param {string|number|null|undefined} value
 * @returns {string|null}
 */
export function formatPercentValue(value) {
  const parts = splitDecimalText(value);
  if (parts === null) return null;
  const body = renderDecimalParts(parts, 2);
  return `${parts.negative || body.startsWith('-') ? '' : '+'}${body}%`;
}

/** An integer count as text, or `null`. A count is not money and needs no exponent. */
export function formatCount(value) {
  if (value === null || value === undefined || typeof value === 'boolean') return null;
  const text = (typeof value === 'string' ? value : String(value)).trim();
  if (!/^-?\d+$/.test(text)) return null;
  const negative = text.startsWith('-');
  return `${negative ? '-' : ''}${groupDigits(stripLeadingZeros(negative ? text.slice(1) : text))}`;
}

/**
 * An ISO-8601 instant as a local, readable string, or `null` when there is no instant.
 *
 * `null` rather than a placeholder date, because "no timestamp was reported" and "the epoch" are
 * different facts.
 *
 * @param {string|null|undefined} value
 * @returns {string|null}
 */
export function formatInstant(value) {
  if (!value) return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** An ISO-8601 instant as a short clock label for a chart axis, or `''`. */
export function formatClock(value) {
  if (!value) return '';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

/** Milliseconds as text with one fraction digit, or `null`. */
export function formatLatency(value) {
  const parts = splitDecimalText(value);
  return parts === null ? null : `${renderDecimalParts(parts, 1)} ms`;
}

/**
 * `value` as a JavaScript number **for pixel placement only**.
 *
 * The one lossy conversion in this module, and it is confined to chart geometry: a chart point
 * is a position on a canvas, not a figure a trader reads. Every figure a trader reads goes
 * through one of the exact string formatters above. `null` for anything unparseable, so a chart
 * gaps rather than plotting a fabricated zero.
 *
 * @param {string|number|null|undefined} value
 * @returns {number|null}
 */
export function toChartNumber(value) {
  const parts = splitDecimalText(value);
  if (parts === null) return null;
  const parsed = Number(`${parts.negative ? '-' : ''}${parts.whole || '0'}.${parts.fraction || '0'}`);
  return Number.isFinite(parsed) ? parsed : null;
}

// ═══════════════════════════════════════════════════════════════════════════
// THE CAPITAL CONTROL — MAJOR UNITS IN, EXACT MINOR_UNITS OUT
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Convert the capital field's text to the exact integer `initial_capital_minor` the start body
 * carries, or refuse it with a sentence naming what is wrong.
 *
 * `POST /api/paper/sessions` declares `initial_capital_minor` as `strict=True`, so `100000.5`,
 * `"100000"` and `true` are each a 422 there rather than a value rounded into a balance. This
 * function is the same rule applied one step earlier, and it works the same way: the digits are
 * **concatenated**, never multiplied. `'1234.5'` at exponent 2 becomes `'1234' + '50'` =
 * `123450`. There is no `parseFloat(text) * 100` anywhere, because `19.99 * 100` is
 * `1998.9999999999998` and the whole point is that it never gets the chance.
 *
 * A value carrying more decimal places than the currency holds is **refused**, not rounded. A
 * rounded balance is a balance the trader did not ask for, and every figure the session goes on
 * to report is computed from it.
 *
 * @param {string|number} text - What the user typed, in major units. Thousands separators are
 *   tolerated; nothing else is.
 * @param {string} [currency]
 * @returns {{ok: true, minor: number}|{ok: false, message: string}}
 */
export function parseCapitalToMinor(text, currency = DEFAULT_CURRENCY) {
  const code = normaliseCurrency(currency);
  const exponent = minorUnitExponent(code);
  if (exponent === null) {
    return {
      ok: false,
      message: `The platform holds no Minor_Units exponent for ${code || 'that currency'}, so the amount cannot be converted exactly. Choose ${SUPPORTED_CURRENCIES.join(' or ')}.`,
    };
  }

  const raw = String(text ?? '').trim().replace(/,/g, '');
  if (!raw) {
    return { ok: false, message: 'Enter the initial simulated capital.' };
  }
  if (!/^\d+(\.\d*)?$/.test(raw)) {
    return {
      ok: false,
      message:
        'Enter a plain positive amount — digits with at most one decimal point. A sign, a space or exponent notation is not accepted.',
    };
  }

  const [whole, fraction = ''] = raw.split('.');
  if (fraction.length > exponent) {
    return {
      ok: false,
      message: `${code} is held to ${exponent} decimal place${exponent === 1 ? '' : 's'}. ${raw} is not an exact whole number of minor units, and it will not be rounded into a balance — enter at most ${exponent} decimal place${exponent === 1 ? '' : 's'}.`,
    };
  }

  const digits = stripLeadingZeros(`${whole}${fraction.padEnd(exponent, '0')}`);
  if (digits.length > String(MAX_AMOUNT_MINOR).length) {
    return {
      ok: false,
      message: `That is above the maximum of ${formatMinorUnits(MAX_AMOUNT_MINOR, code)} ${code}.`,
    };
  }

  const minor = Number(digits);
  if (!Number.isSafeInteger(minor)) {
    return {
      ok: false,
      message: 'That amount is too large to send as an exact whole number of minor units.',
    };
  }
  if (minor < 1) {
    return { ok: false, message: 'The initial simulated capital must be greater than zero.' };
  }
  if (minor > MAX_AMOUNT_MINOR) {
    return {
      ok: false,
      message: `That is above the maximum of ${formatMinorUnits(MAX_AMOUNT_MINOR, code)} ${code}.`,
    };
  }
  return { ok: true, minor };
}

// ═══════════════════════════════════════════════════════════════════════════
// THE FEED AND SESSION VOCABULARIES
// ═══════════════════════════════════════════════════════════════════════════

/**
 * `paper_sessions.feed_state`, rendered.
 *
 * The five values of `paper_market_feed.FEED_STATES`. `009_paper_trading.sql` places no CHECK on
 * the column, so an unrecognised value is displayed verbatim rather than folded into a state it
 * is not.
 */
export const FEED_STATE_COPY = Object.freeze({
  PENDING: { label: 'Pending', tone: 'muted', note: 'The session exists; no validated market event has arrived yet.' },
  HEALTHY: { label: 'Healthy', tone: 'good', note: 'Validated events are arriving over the websocket transport.' },
  FALLBACK_REST: { label: 'Fallback (REST)', tone: 'warn', note: 'Market data is arriving over REST. The feed is recorded and working; execution is not admitted on it.' },
  TRANSPORT_UNKNOWN: { label: 'Transport unknown', tone: 'warn', note: 'The delivery path could not be identified, so freshness has not been demonstrated.' },
  DEGRADED: { label: 'Degraded', tone: 'bad', note: 'The subscription dropped. A price observed before the drop is not a current price.' },
});

/** `paper_market_feed.TRADEABLE_FEED_STATES` — execution is admitted on `HEALTHY` alone. */
export const TRADEABLE_FEED_STATES = Object.freeze(['HEALTHY']);

/** `paper_sessions.session_state` — `chk_paper_session_state`'s four values, rendered. */
export const SESSION_STATE_COPY = Object.freeze({
  CREATED: { label: 'Created', tone: 'muted' },
  RUNNING: { label: 'Running', tone: 'good' },
  PAUSED: { label: 'Paused', tone: 'warn' },
  STOPPED: { label: 'Stopped', tone: 'muted' },
});

/** `paper_order_state.TERMINAL` — an order in one of these is not an open order. */
export const TERMINAL_ORDER_STATES = Object.freeze(['FILLED', 'CANCELLED', 'REJECTED']);

/** The five order-lifecycle frame types, in lifecycle order. */
export const ORDER_EVENT_TYPES = Object.freeze([
  'paper_order_created',
  'paper_order_accepted',
  'paper_order_partially_filled',
  'paper_order_filled',
  'paper_order_rejected',
]);

/** The four session-lifecycle frame types. */
export const SESSION_EVENT_TYPES = Object.freeze([
  'paper_session_started',
  'paper_session_paused',
  'paper_session_resumed',
  'paper_session_stopped',
]);

/** Whether an order row is still open — i.e. its state is not one of the three terminals. */
export const isOpenOrder = (order) =>
  !TERMINAL_ORDER_STATES.includes(String(order?.order_state ?? '').toUpperCase());

// ═══════════════════════════════════════════════════════════════════════════
// THE FRAME REDUCTION
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Reduce a list of Paper_Channel frames to the series the page renders from them.
 *
 * The frames are `GET /api/paper/sessions/{id}/events`' — the persisted `paper_events` log, put
 * back through the same envelope a live event is built with. They are the same frames the socket
 * serves, from the same `paper_channel.replay`, which is why one reduction serves both
 * transports: task 32.4 appends live frames to the same list and calls this again.
 *
 * Deduplicated on `event_id` and applied in ascending `sequence`, which is the rule Requirement
 * 19.8 states for the socket and is applied here so the REST path cannot behave differently.
 *
 * Nothing is derived that the frames do not carry. In particular no price is carried forward: a
 * `market_tick` whose `close` is absent contributes no point rather than repeating the last one.
 *
 * @param {Array<Object>} frames - Envelopes with `type`, `sequence`, `event_id`, `emitted_at`,
 *   `payload`.
 * @returns {{
 *   ordered: Object[], ticks: Object[], latestTick: (Object|null), signals: Object[],
 *   executions: Object[], pnlPoints: Object[], drawdownPoints: Object[],
 *   fills: Object[], errors: Object[], lastSequence: number, lastPriceAt: (string|null)
 * }}
 */
export function deriveFromFrames(frames) {
  const seen = new Set();
  const ordered = [];
  for (const frame of Array.isArray(frames) ? frames : []) {
    if (!frame || typeof frame !== 'object') continue;
    const id = frame.event_id;
    if (id !== undefined && id !== null) {
      if (seen.has(id)) continue;
      seen.add(id);
    }
    ordered.push(frame);
  }
  ordered.sort((a, b) => Number(a.sequence ?? 0) - Number(b.sequence ?? 0));

  const ticks = [];
  const signals = [];
  const executions = [];
  const pnlPoints = [];
  const drawdownPoints = [];
  const fills = [];
  const errors = [];
  let lastPriceAt = null;

  for (const frame of ordered) {
    const type = String(frame.type ?? '');
    const payload = frame.payload && typeof frame.payload === 'object' ? frame.payload : {};
    const base = {
      sequence: Number(frame.sequence ?? 0),
      eventId: frame.event_id ?? null,
      type,
      emittedAt: frame.emitted_at ?? null,
    };

    if (type === 'market_tick') {
      ticks.push({
        ...base,
        symbol: payload.symbol ?? null,
        at: payload.timestamp ?? frame.emitted_at ?? null,
        open: payload.open ?? null,
        high: payload.high ?? null,
        low: payload.low ?? null,
        close: payload.close ?? null,
        volume: payload.volume ?? null,
        // `null` when the feed could not measure it, which the payload model carries as distinct
        // from zero and is kept distinct here.
        latencyMs: payload.latency_ms ?? null,
        feedState: payload.feed_state ?? null,
        sourceEventId: payload.source_event_id ?? null,
      });
      continue;
    }

    if (type === 'signal_generated') {
      signals.push({
        ...base,
        signalId: payload.signal_id ?? null,
        decision: payload.decision ?? null,
        symbol: payload.symbol ?? null,
        side: payload.side ?? null,
        quantity: payload.quantity ?? null,
        // `null` when the signal was recorded without a validated price. Never a zero.
        price: payload.price ?? null,
        lifecycleState: payload.order_lifecycle_state ?? null,
        generatedAt: payload.generated_at ?? null,
        environment: payload.environment ?? null,
      });
      continue;
    }

    if (ORDER_EVENT_TYPES.includes(type)) {
      const execution = {
        ...base,
        orderId: payload.order_id ?? null,
        symbol: payload.symbol ?? null,
        side: payload.side ?? null,
        orderType: payload.order_type ?? null,
        quantity: payload.quantity ?? null,
        limitPrice: payload.limit_price ?? null,
        orderState: payload.order_state ?? null,
        filledQuantity: payload.filled_quantity ?? null,
        avgFillPrice: payload.avg_fill_price ?? null,
        feeMinor: payload.fee_minor ?? null,
        slippageMinor: payload.slippage_minor ?? null,
        rejectionReason: payload.rejection_reason ?? null,
        at: payload.at ?? frame.emitted_at ?? null,
      };
      executions.push(execution);
      if (
        (type === 'paper_order_filled' || type === 'paper_order_partially_filled') &&
        execution.avgFillPrice !== null &&
        execution.avgFillPrice !== undefined
      ) {
        fills.push(execution);
      }
      continue;
    }

    if (SESSION_EVENT_TYPES.includes(type)) {
      // `paper_session_started` carries `started_at` and no `session_state`; the other three
      // carry `session_state` and `at`. Both spellings are read, neither is invented.
      executions.push({
        ...base,
        at: payload.at ?? payload.started_at ?? frame.emitted_at ?? null,
        sessionState: payload.session_state ?? null,
        symbol: payload.symbol ?? null,
        feedTransport: payload.feed_transport ?? null,
        marketDataSource: payload.market_data_source ?? null,
        initialCapitalMinor: payload.initial_capital_minor ?? null,
        currency: payload.currency ?? null,
      });
      continue;
    }

    if (type === 'paper_pnl_updated') {
      pnlPoints.push({
        ...base,
        realized: payload.realized_pnl ?? null,
        unrealized: payload.unrealized_pnl ?? null,
        total: payload.total_pnl ?? null,
        totalReturnPct: payload.total_return_pct ?? null,
        priceAt: payload.price_at ?? null,
        stale: payload.stale === true,
      });
      if (payload.price_at) lastPriceAt = payload.price_at;
      continue;
    }

    if (type === 'paper_drawdown_updated') {
      drawdownPoints.push({
        ...base,
        amount: payload.max_drawdown_amount ?? null,
        fraction: payload.max_drawdown_fraction ?? null,
        peakEquity: payload.peak_equity ?? null,
        snapshotCount: payload.snapshot_count ?? null,
      });
      continue;
    }

    if (type === 'paper_position_updated') {
      if (payload.price_at) lastPriceAt = payload.price_at;
      continue;
    }

    if (type === 'paper_error') {
      errors.push({
        ...base,
        code: payload.code ?? null,
        message: payload.message ?? null,
        recoverable: payload.recoverable === true,
        at: payload.at ?? frame.emitted_at ?? null,
      });
      executions.push({ ...base, at: payload.at ?? frame.emitted_at ?? null, code: payload.code ?? null, message: payload.message ?? null });
      continue;
    }
  }

  const lastSequence = ordered.length ? Number(ordered[ordered.length - 1].sequence ?? 0) : 0;

  return {
    ordered,
    ticks,
    latestTick: ticks.length ? ticks[ticks.length - 1] : null,
    signals,
    executions,
    pnlPoints,
    drawdownPoints,
    fills,
    errors,
    lastSequence,
    lastPriceAt,
  };
}

/**
 * The equity-curve points, **from the persisted equity snapshots and from nothing else**.
 *
 * Requirement 18.11: the curve is drawn from `paper_equity_snapshots`, not from accumulated live
 * event state. This function's only parameter is the `equity` array of
 * `GET /api/paper/sessions/{id}/equity`, which is what makes that structural rather than
 * remembered — it has no access to a frame, so a reconnect or a remount redraws the same curve
 * from the same read.
 *
 * The rows are consumed in the order the endpoint returned them (`series_index, taken_at ASC`)
 * and are **not** re-sorted: the drawdown of a resorted series is not the drawdown of the series
 * that was read.
 *
 * @param {Array<Object>} rows - `paper_equity_snapshots` rows as the endpoint served them.
 * @returns {{points: Object[], seriesBoundaries: number[], latest: (Object|null), seriesIndices: number[]}}
 */
export function equityCurvePoints(rows) {
  const source = Array.isArray(rows) ? rows : [];
  const points = [];
  const seriesBoundaries = [];
  const seriesIndices = [];
  let previousSeries = null;

  source.forEach((row, index) => {
    const seriesIndex = Number(row?.series_index ?? 0);
    if (previousSeries !== null && seriesIndex !== previousSeries) {
      seriesBoundaries.push(index);
    }
    if (!seriesIndices.includes(seriesIndex)) seriesIndices.push(seriesIndex);
    previousSeries = seriesIndex;
    points.push({
      index,
      seriesIndex,
      takenAt: row?.taken_at ?? null,
      label: formatClock(row?.taken_at),
      cause: row?.cause ?? null,
      stale: row?.stale === true,
      // Exact text for the tooltip, a number for the pixel. The two never swap roles.
      totalEquityText: row?.total_equity ?? null,
      availableText: row?.available_balance ?? null,
      lockedText: row?.locked_balance ?? null,
      positionValueText: row?.position_market_value ?? null,
      equity: toChartNumber(row?.total_equity),
    });
  });

  return {
    points,
    seriesBoundaries,
    seriesIndices,
    latest: source.length ? source[source.length - 1] : null,
  };
}

// ═══════════════════════════════════════════════════════════════════════════
// THE PANEL STATE VOCABULARY — task 32.3, Requirement 20.5
// ═══════════════════════════════════════════════════════════════════════════

/**
 * The eight states Requirement 20.5 names, spelled once.
 *
 * `PanelBody` in `PaperTrading.jsx` is the single renderer for all eight; every panel on the
 * page routes through it, so there is no second status renderer that could disagree about what
 * an error looks like.
 *
 * `IDLE` and `READY` are **not** among the eight. `IDLE` is "no session is selected yet", which
 * is a property of the page rather than of a panel's data, and `READY` is the panel rendering
 * its children. They are named here so a caller never spells either as a bare string.
 */
export const PANEL_STATES = Object.freeze({
  LOADING: 'loading',
  EMPTY: 'empty',
  ERROR: 'error-with-retry',
  DISABLED: 'disabled',
  UNAUTHORISED: 'unauthorised',
  EXPIRED_SUBSCRIPTION: 'expired-subscription',
  UNAVAILABLE_STRATEGY: 'unavailable-strategy',
  FEED_DISCONNECTED: 'feed-disconnected',
});

/** No session is selected. Not one of the eight — nothing has been asked for yet. */
export const PANEL_IDLE = 'idle';

/** The panel renders its children. Not one of the eight. */
export const PANEL_READY = 'ready';

/**
 * The eight, as a list, so a caller can iterate them rather than re-listing them.
 *
 * `PanelBody` renders one branch per member and falls through to its children for anything else;
 * this is the list to drive that component with when checking that each of the eight has a
 * rendering of its own.
 */
export const REQUIRED_PANEL_STATES = Object.freeze(Object.values(PANEL_STATES));

// ═══════════════════════════════════════════════════════════════════════════
// DERIVING A PANEL STATE FROM A REAL SERVER SIGNAL
// ═══════════════════════════════════════════════════════════════════════════

/**
 * The `MARKETPLACE_*` wire codes `entitlement_resolver.WIRE_CODE_FOR_REASON` produces.
 *
 * Spelled as the server spells them, so this page's expired-subscription and
 * unavailable-strategy states and the refusal that caused them name the same condition. This is
 * the same vocabulary `Strategies.jsx` reads `entitling` and `unavailable_reason` through
 * (`REASON_TEXT` there) — one condition, one set of names, two pages.
 *
 * @type {Readonly<Record<string, string>>}
 */
export const ENTITLEMENT_REASON_TEXT = Object.freeze({
  MARKETPLACE_SUBSCRIPTION_EXPIRED:
    'This subscription period has ended, so it no longer entitles you to run this strategy.',
  MARKETPLACE_NOT_SUBSCRIBED: 'No entitling subscription is held for this listing.',
  MARKETPLACE_STRATEGY_UNAVAILABLE:
    'This listing is unavailable — the strategy behind it cannot be resolved right now.',
  MARKETPLACE_OPERATION_NOT_PERMITTED:
    'This subscription is suspended, so execution is not permitted right now.',
});

/**
 * `entitlement_resolver.EntitlementReason`'s own spellings, as they arrive in
 * `PAPER_START_REFUSED`'s `details.reason`.
 *
 * `paper_session_service.start_paper_session` refuses a non-entitling start with
 * `details = {validation: 'ENTITLEMENT', reason: <one of these>}`. Requirement 20.5 gives this
 * page one state for the four refusing subscription reasons, so they map onto
 * `expired-subscription` — and the panel renders the server's own code beside it, so the
 * distinction Requirement 7.10 makes on the wire is not lost on screen.
 */
export const ENTITLEMENT_REASON_TO_WIRE_CODE = Object.freeze({
  NOT_SUBSCRIBED: 'MARKETPLACE_NOT_SUBSCRIBED',
  EXPIRED: 'MARKETPLACE_SUBSCRIPTION_EXPIRED',
  LISTING_UNAVAILABLE: 'MARKETPLACE_STRATEGY_UNAVAILABLE',
  SUBSCRIPTION_SUSPENDED: 'MARKETPLACE_OPERATION_NOT_PERMITTED',
});

/** `paper_session_service.VALIDATION_STRATEGY_NOT_EXECUTABLE`. */
const VALIDATION_STRATEGY_NOT_EXECUTABLE = 'STRATEGY_NOT_EXECUTABLE';

/** `paper_session_service.VALIDATION_ENTITLEMENT`. */
const VALIDATION_ENTITLEMENT = 'ENTITLEMENT';

/** The two codes that mean "this listing's executable artifact does not resolve". */
const UNAVAILABLE_STRATEGY_CODES = Object.freeze(['MARKETPLACE_STRATEGY_UNAVAILABLE']);

/** The codes that mean "a subscription is required and the one held does not entitle". */
const NON_ENTITLING_CODES = Object.freeze([
  'MARKETPLACE_SUBSCRIPTION_EXPIRED',
  'MARKETPLACE_NOT_SUBSCRIBED',
  'MARKETPLACE_OPERATION_NOT_PERMITTED',
]);

/**
 * The error envelope `marketplace/errors.py`'s single exception handler serialises:
 * `{"error": {"code", "message", "details"}, "request_id"}`.
 *
 * `apiClient`'s `ApiError` carries the parsed body on `.data`. FastAPI's own
 * `HTTPException` path puts the same object under `detail`, so both spellings are read and
 * neither is invented.
 *
 * @param {Object} error - An `ApiError`.
 * @returns {{code: (string|null), message: (string|null), details: Object}}
 */
export function readErrorEnvelope(error) {
  const body = error?.data ?? null;
  const envelope =
    (body && typeof body === 'object' && body.error && typeof body.error === 'object'
      ? body.error
      : null)
    || (body && typeof body === 'object' && body.detail && typeof body.detail === 'object'
      ? body.detail
      : null);
  const details =
    envelope && typeof envelope.details === 'object' && envelope.details !== null
      ? envelope.details
      : {};
  return {
    code: typeof envelope?.code === 'string' && envelope.code ? envelope.code : null,
    message: typeof envelope?.message === 'string' && envelope.message ? envelope.message : null,
    details,
  };
}

/**
 * Which of the eight states a failed read is in, and the server signal that says so.
 *
 * The order of the checks is the order the signals are specific in. A `PAPER_START_REFUSED`
 * carrying `validation: ENTITLEMENT` is a 403, and so is an `unauthorised` refusal — reading the
 * code first is what keeps an expired subscription from being reported as "sign in again".
 *
 * Nothing is inferred from an absent signal: an error with no recognised code and a status that
 * is neither 401 nor 403 is `error-with-retry`, which is the state that admits it does not know
 * why. No branch here returns a value, a zero or a previous reading.
 *
 * @param {Object} error - An `ApiError` from `apiClient`.
 * @returns {{state: string, code: (string|null), reason: (string|null), message: (string|null), status: (number|null), signal: string}}
 */
export function classifyReadFailure(error) {
  const { code, message, details } = readErrorEnvelope(error);
  const status = Number.isFinite(error?.status) ? error.status : null;
  const validation = typeof details.validation === 'string' ? details.validation : null;
  const reason = typeof details.reason === 'string' ? details.reason : null;
  const base = { code, reason, message, status };

  if (code === 'PAPER_START_REFUSED' && validation === VALIDATION_STRATEGY_NOT_EXECUTABLE) {
    return {
      ...base,
      state: PANEL_STATES.UNAVAILABLE_STRATEGY,
      signal: 'PAPER_START_REFUSED details.validation=STRATEGY_NOT_EXECUTABLE',
    };
  }
  if (code === 'PAPER_START_REFUSED' && validation === VALIDATION_ENTITLEMENT) {
    const wire = reason ? ENTITLEMENT_REASON_TO_WIRE_CODE[reason] : null;
    return {
      ...base,
      state:
        wire === 'MARKETPLACE_STRATEGY_UNAVAILABLE'
          ? PANEL_STATES.UNAVAILABLE_STRATEGY
          : PANEL_STATES.EXPIRED_SUBSCRIPTION,
      signal: `PAPER_START_REFUSED details.reason=${reason ?? 'not reported'}`,
    };
  }
  if (code && UNAVAILABLE_STRATEGY_CODES.includes(code)) {
    return { ...base, state: PANEL_STATES.UNAVAILABLE_STRATEGY, signal: `error.code=${code}` };
  }
  if (code && NON_ENTITLING_CODES.includes(code)) {
    return { ...base, state: PANEL_STATES.EXPIRED_SUBSCRIPTION, signal: `error.code=${code}` };
  }
  if (status === 401 || status === 403) {
    return { ...base, state: PANEL_STATES.UNAUTHORISED, signal: `HTTP ${status}` };
  }
  return {
    ...base,
    state: PANEL_STATES.ERROR,
    signal: status === null ? 'no HTTP status (the request did not reach a response)' : `HTTP ${status}`,
  };
}

/**
 * The panel state for a `GET /api/library/my-strategies` entry that does not entitle.
 *
 * `entitling` and `unavailable_reason` are decided by
 * `backend_app/backend/marketplace/library_entries.py` and read here exactly as returned —
 * the same two fields, read the same way, as `Strategies.jsx`. `null` when the entry entitles
 * (or when there is no entry), because "this entry is fine" is not one of the eight states.
 *
 * @param {Object|null} entry
 * @returns {string|null}
 */
export function entitlementPanelState(entry) {
  if (!entry || entry.entitling !== false) return null;
  return entry.unavailable_reason === 'MARKETPLACE_STRATEGY_UNAVAILABLE'
    ? PANEL_STATES.UNAVAILABLE_STRATEGY
    : PANEL_STATES.EXPIRED_SUBSCRIPTION;
}

/**
 * A `MARKETPLACE_*` reason code in words, or `null`.
 *
 * An unrecognised code is reported verbatim rather than softened into a generic sentence, so a
 * code this build has not seen is still visible to the user and to support.
 *
 * @param {string|null|undefined} code
 * @returns {string|null}
 */
export const entitlementReasonText = (code) =>
  (code && ENTITLEMENT_REASON_TEXT[code]) || (code ? `Refused by the server: ${code}` : null);

// ═══════════════════════════════════════════════════════════════════════════
// THE SOCKET'S OWN VOCABULARY
// ═══════════════════════════════════════════════════════════════════════════

/**
 * The statuses that mean a reconnection is in progress (Requirement 20.5's last clause).
 *
 * `websocketClient._setStatus` currently emits `connecting`, `connected`, `disconnected`,
 * `error` and `failed` — it spells the reconnect attempt `connecting`, because
 * `scheduleReconnect` reaches the socket through the same `connect()` a first connection does.
 * Both spellings are accepted so the indicator does not depend on which word that client
 * chooses, and neither is invented: a status outside this set is not reported as reconnecting.
 */
export const RECONNECTING_SOCKET_STATUSES = Object.freeze(['connecting', 'reconnecting']);

/** The one status on which frames are actually arriving. */
export const CONNECTED_SOCKET_STATUS = 'connected';

/** Whether a reconnection is in progress. */
export const isReconnectingStatus = (status) =>
  RECONNECTING_SOCKET_STATUSES.includes(String(status ?? ''));

/**
 * `core/websocket_auth`'s four refusal codes, and what each one is for this page.
 *
 * `CHANNEL_FORBIDDEN` and `CHANNEL_UNAUTHENTICATED` are answers about the caller, so they are
 * `unauthorised`. `CHANNEL_OWNER_UNRESOLVED` is an operator's problem and `CHANNEL_UNKNOWN` is
 * a client/server disagreement about the channel name; neither is the user's authorisation, so
 * both are `error-with-retry` rather than being reported as "not yours".
 */
export const CHANNEL_REFUSAL_PANEL_STATE = Object.freeze({
  CHANNEL_FORBIDDEN: PANEL_STATES.UNAUTHORISED,
  CHANNEL_UNAUTHENTICATED: PANEL_STATES.UNAUTHORISED,
  CHANNEL_OWNER_UNRESOLVED: PANEL_STATES.ERROR,
  CHANNEL_UNKNOWN: PANEL_STATES.ERROR,
});

/** The frame type `websocketClient` hands to a channel handler when the server refuses it. */
export const SUBSCRIPTION_REFUSED_TYPE = 'subscription_refused';

/**
 * The panel state for a `subscription_refused` frame, from the server's own `code`.
 *
 * An unrecognised code is `error-with-retry`: reporting an unknown refusal as "not yours" would
 * be a claim about authorisation this page was not told.
 *
 * @param {Object|null} refusal
 * @returns {string|null}
 */
export function channelRefusalPanelState(refusal) {
  if (!refusal) return null;
  const code = typeof refusal.code === 'string' ? refusal.code : '';
  return CHANNEL_REFUSAL_PANEL_STATE[code] ?? PANEL_STATES.ERROR;
}

// ═══════════════════════════════════════════════════════════════════════════
// THE RETENTION BOUNDS — task 32.4, Requirements 27.5, 20.8
// ═══════════════════════════════════════════════════════════════════════════

/**
 * Retained non-tick event frames per session. Requirement 27.5's bound on events.
 *
 * The order and lifecycle frames — the ones the execution log, the signal stream, the PnL chart
 * and the drawdown chart are built from. Ticks have their own, larger bound below, because a
 * one-minute session produces one tick per candle and one order frame per decision: a single
 * shared bound would let a quiet hour of ticks evict the fills a trader is looking at.
 */
export const MAX_RETAINED_EVENTS = 500;

/** Retained `market_tick` frames per session. Requirement 27.5's bound on ticks. */
export const MAX_RETAINED_TICKS = 1000;

/** Plotted points per chart series. Requirement 27.5's bound on chart points. */
export const MAX_CHART_POINTS_PER_SERIES = 2000;

/** The frame type held under {@link MAX_RETAINED_TICKS} rather than {@link MAX_RETAINED_EVENTS}. */
const TICK_EVENT_TYPE = 'market_tick';

/**
 * The last `cap` members of `list`, discarding the oldest first.
 *
 * Returns the **same array** when it is already within the bound, so a `useMemo` downstream is
 * not invalidated by a copy that carries no new information.
 *
 * @template T
 * @param {T[]} list
 * @param {number} cap
 * @returns {T[]}
 */
export function boundedTail(list, cap) {
  const source = Array.isArray(list) ? list : [];
  if (!Number.isInteger(cap) || cap <= 0) return [];
  if (source.length <= cap) return source;
  return source.slice(source.length - cap);
}

/** `event_id` as the dedup key, or `null` when the frame carries none. */
const frameKey = (frame) =>
  frame && frame.event_id !== undefined && frame.event_id !== null ? String(frame.event_id) : null;

const bySequence = (a, b) => Number(a?.sequence ?? 0) - Number(b?.sequence ?? 0);

/**
 * The retained frame state, from every source that produced frames for this session.
 *
 * This is the single point at which Requirement 27.5's bounds are enforced, and it is a pure
 * function of its inputs so a test can assert the bounds without rendering anything:
 *
 * * a frame whose `event_id` is already retained is **discarded** (Requirement 19.8's rule for
 *   the socket, applied to the REST replay too, because both transports serve the same frames
 *   from the same `paper_channel.replay`);
 * * frames are ordered by `sequence` **before** the bound is applied, so "oldest discarded
 *   first" is discarded by position in the stream rather than by arrival order;
 * * `eventIds` is the bounded `Set` `onFrame` tests against — bounded by construction, because
 *   it is rebuilt from the frames that survived the bound and can therefore never hold more
 *   than `MAX_RETAINED_EVENTS + MAX_RETAINED_TICKS` members;
 * * `lastSequence` is the highest sequence **seen**, including from a frame the bound discarded.
 *   That is what the reconnect gap-close asks above, and taking it from the retained tail
 *   instead would re-request history the page has already applied and thrown away.
 *
 * @param {...Array<Object>} sources - Frame lists, in the order they should be considered.
 * @returns {{
 *   events: Object[], ticks: Object[], frames: Object[], eventIds: Set<string>,
 *   lastSequence: number, duplicatesDiscarded: number, eventsDiscarded: number,
 *   ticksDiscarded: number
 * }}
 */
export function retainFrames(...sources) {
  const seen = new Set();
  const events = [];
  const ticks = [];
  let duplicatesDiscarded = 0;
  let lastSequence = 0;

  for (const source of sources) {
    for (const frame of Array.isArray(source) ? source : []) {
      if (!frame || typeof frame !== 'object') continue;
      const key = frameKey(frame);
      if (key !== null) {
        if (seen.has(key)) {
          duplicatesDiscarded += 1;
          continue;
        }
        seen.add(key);
      }
      const sequence = Number(frame.sequence ?? 0);
      if (Number.isFinite(sequence) && sequence > lastSequence) lastSequence = sequence;
      if (String(frame.type ?? '') === TICK_EVENT_TYPE) ticks.push(frame);
      else events.push(frame);
    }
  }

  events.sort(bySequence);
  ticks.sort(bySequence);

  const boundedEvents = boundedTail(events, MAX_RETAINED_EVENTS);
  const boundedTicks = boundedTail(ticks, MAX_RETAINED_TICKS);

  const eventIds = new Set();
  for (const frame of boundedEvents) {
    const key = frameKey(frame);
    if (key !== null) eventIds.add(key);
  }
  for (const frame of boundedTicks) {
    const key = frameKey(frame);
    if (key !== null) eventIds.add(key);
  }

  return {
    events: boundedEvents,
    ticks: boundedTicks,
    frames: boundedEvents.concat(boundedTicks),
    eventIds,
    lastSequence,
    duplicatesDiscarded,
    eventsDiscarded: events.length - boundedEvents.length,
    ticksDiscarded: ticks.length - boundedTicks.length,
  };
}

/** The empty retained state, for a page with no session selected. */
export const EMPTY_RETAINED = Object.freeze({
  events: Object.freeze([]),
  ticks: Object.freeze([]),
  frames: Object.freeze([]),
  eventIds: new Set(),
  lastSequence: 0,
  duplicatesDiscarded: 0,
  eventsDiscarded: 0,
  ticksDiscarded: 0,
});

// ═══════════════════════════════════════════════════════════════════════════
// THE RESPONSIVE LAYOUT (task 32.5, Requirement 20.7)
// ═══════════════════════════════════════════════════════════════════════════

/**
 * The narrowest and widest widths Requirement 20.7 names, kept here so the page and its tests
 * quote one pair of numbers rather than two.
 */
export const NARROWEST_SUPPORTED_WIDTH = 360;
export const WIDEST_SUPPORTED_WIDTH = 1920;

/** Below this available width every multi-column grid on the page collapses to one column. */
export const SINGLE_COLUMN_MAX_WIDTH = 768;

/** Below this available width every table on the page is rendered as stacked label/value cards. */
export const STACKED_TABLE_MAX_WIDTH = 640;

/**
 * The three layouts the page has, widest last.
 *
 * `stacked` implies `single-column`: a viewport too narrow for two columns is also too narrow for
 * a seven-column table, so the two thresholds are ordered rather than independent.
 */
export const LAYOUT_MODES = Object.freeze({
  STACKED: 'stacked',
  SINGLE_COLUMN: 'single-column',
  WIDE: 'wide',
});

/**
 * Which layout an available content width gets.
 *
 * The argument is the width available to the page's children — the content box of the page root,
 * with its padding already excluded — and NOT the viewport width. That is deliberate: horizontal
 * overflow is decided by the space a grid track actually has, and this page is rendered inside a
 * shell with a 210 px sidebar, so the viewport is always the more generous of the two numbers.
 * Since the available width is monotonic in the viewport width, a 767 px viewport is always at
 * least as collapsed as this function's 768 px threshold requires.
 *
 * An unmeasured width (`null` before the first measurement, or a non-finite value) yields `wide`.
 * That is safe rather than optimistic because the wide templates are themselves overflow-proof —
 * see {@link responsiveGridColumns}.
 *
 * @param {number|null} width - Available content width in CSS pixels.
 * @returns {string} One of {@link LAYOUT_MODES}.
 */
export function layoutModeForWidth(width) {
  if (!Number.isFinite(width) || width <= 0) return LAYOUT_MODES.WIDE;
  if (width < STACKED_TABLE_MAX_WIDTH) return LAYOUT_MODES.STACKED;
  if (width < SINGLE_COLUMN_MAX_WIDTH) return LAYOUT_MODES.SINGLE_COLUMN;
  return LAYOUT_MODES.WIDE;
}

/** Whether this layout renders tables as stacked label/value cards. */
export const isStackedLayout = (mode) => mode === LAYOUT_MODES.STACKED;

/** Whether this layout puts every grid on one column. */
export const isSingleColumnLayout = (mode) =>
  mode === LAYOUT_MODES.STACKED || mode === LAYOUT_MODES.SINGLE_COLUMN;

/**
 * A `grid-template-columns` value that cannot overflow its container.
 *
 * Two independent guarantees, because one of them has to hold before the first measurement lands:
 *
 * 1. Below {@link SINGLE_COLUMN_MAX_WIDTH} the template is `minmax(0, 1fr)` — one column whose
 *    track may shrink below its contents' min-content width. A bare `1fr` is `minmax(auto, 1fr)`,
 *    whose floor is the min-content width of whatever is in it, so a `<select>` or a long
 *    identifier would push the track — and the page — wider than the viewport.
 * 2. At or above it the template keeps the `auto-fit` behaviour the page was built with, but the
 *    track minimum is `min({min}px, 100%)` rather than `{min}px`. `minmax(320px, 1fr)` inside a
 *    300 px container lays out a 320 px track and overflows by 20; `minmax(min(320px, 100%), 1fr)`
 *    lays out a 300 px one. This is what makes the layout correct at widths that were never
 *    measured, including the first paint and an environment with no `ResizeObserver`.
 *
 * @param {number} minTrackPx - The track minimum the wide layout aims for.
 * @param {string} mode - One of {@link LAYOUT_MODES}.
 * @returns {string}
 */
export function responsiveGridColumns(minTrackPx, mode) {
  if (isSingleColumnLayout(mode)) return 'minmax(0, 1fr)';
  return `repeat(auto-fit, minmax(min(${minTrackPx}px, 100%), 1fr))`;
}

/**
 * The chart geometry that a percentage width cannot fix.
 *
 * `ResponsiveContainer` sizes the plot to its parent, but `YAxis width` is a fixed pixel
 * reservation taken out of that plot and `XAxis minTickGap` is a fixed pixel spacing between
 * labels. At 72 px the axis is a quarter of a 264 px plot and the tick labels collide, so both are
 * driven by the measured width instead of being constants.
 *
 * @param {string} mode - One of {@link LAYOUT_MODES}.
 * @returns {{axisWidth: number, tickGap: number}}
 */
export const CHART_GEOMETRY = Object.freeze({
  [LAYOUT_MODES.STACKED]: Object.freeze({ axisWidth: 44, tickGap: 44 }),
  [LAYOUT_MODES.SINGLE_COLUMN]: Object.freeze({ axisWidth: 56, tickGap: 32 }),
  [LAYOUT_MODES.WIDE]: Object.freeze({ axisWidth: 72, tickGap: 24 }),
});

export const chartGeometry = (mode) => CHART_GEOMETRY[mode] || CHART_GEOMETRY[LAYOUT_MODES.WIDE];
