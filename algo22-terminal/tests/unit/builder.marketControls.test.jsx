/**
 * The DATA node's two market controls. Task 7.3, Requirements 11.7, 11.8, 5.2.
 *
 * `AssetSelector` and `TimeframeSelector` are driven through the same boundary the rest of the
 * builder suites use: the shared axios instance is the only stub, and the hooks, the
 * projection, the registry cache, `ParameterForm`'s control seam and the components themselves
 * are the shipped code.
 *
 * The claim under test, in one sentence: **every market and every interval on screen came off
 * the wire on this page load, and a failure shows a stated reason instead of a list.** The
 * suite therefore spends most of its assertions on the four states a selector can be in and on
 * keeping them apart — a `200` matching nothing, a `503` with no universe, a load in progress,
 * and a real page — because the defect this task removes (SB-06, and the two ten-pair fallback
 * lists task 7.2 and this task deleted) is precisely what it looks like when two of those
 * states are shown as one.
 *
 * The last block is structural, in the style of task 7.2's AST tests: no module the builder can
 * reach may contain a symbol list or an interval list at all. That is the assertion that keeps
 * holding after this file is forgotten.
 */

import React, { useState } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { existsSync, readFileSync, statSync } from 'node:fs';
import path from 'node:path';

const { mockClient } = vi.hoisted(() => ({ mockClient: { get: vi.fn() } }));

vi.mock('../../src/apiClient', () => ({ default: mockClient }));

import { AssetSelector } from '../../src/components/builder/AssetSelector';
import { TimeframeSelector } from '../../src/components/builder/TimeframeSelector';
import { ParameterForm } from '../../src/components/builder/ParameterForm';
import { ASSETS_PATH } from '../../src/api/modules/assets';
import { REGISTRY_TIMEFRAMES_PATH, resetRegistryClient } from '../../src/lib/registryClient';
import { DEBOUNCE_MS } from '../../src/hooks/useAssetSearch';

// ---------------------------------------------------------------------------
// Wire fixtures
// ---------------------------------------------------------------------------

const assetRecord = (symbol, overrides = {}) => {
  const [base, quote] = symbol.split('/');
  return {
    symbol,
    base,
    quote,
    market_type: 'spot',
    active: true,
    price_precision: 2,
    amount_precision: 6,
    min_notional: 5,
    min_amount: 0.0001,
    available_on: ['binance'],
    precision_source: 'binance',
    listing_count: 1,
    ...overrides,
  };
};

const sourceMeta = (overrides = {}) => ({
  generated_at: '2024-05-01T00:00:00+00:00',
  age_seconds: 12.5,
  ttl_seconds: 21600,
  refresh_interval_seconds: 3600,
  stale: false,
  universe_total: 61,
  universe_hash: 'u_abc123',
  exchanges: ['binance', 'okx'],
  exchanges_failed: [],
  cache_backend: 'process',
  refresh_in_flight: false,
  ...overrides,
});

const assetPage = (symbols, overrides = {}) => ({
  status: 200,
  headers: { 'x-asset-universe-hash': 'u_abc123' },
  data: {
    assets: symbols.map((symbol) => (typeof symbol === 'string' ? assetRecord(symbol) : symbol)),
    total: symbols.length,
    limit: 50,
    next_cursor: null,
    universe_changed: false,
    source_meta: sourceMeta(),
    ...overrides,
  },
});

const TIMEFRAME_ENTRIES = [
  { id: '1m', label: '1m', seconds: 60 },
  { id: '5m', label: '5m', seconds: 300 },
  { id: '15m', label: '15m', seconds: 900 },
  { id: '30m', label: '30m', seconds: 1800 },
  { id: '1h', label: '1h', seconds: 3600 },
  { id: '2h', label: '2h', seconds: 7200 },
  { id: '4h', label: '4h', seconds: 14400 },
  { id: '6h', label: '6h', seconds: 21600 },
  { id: '12h', label: '12h', seconds: 43200 },
  { id: '1d', label: '1d', seconds: 86400 },
];

const timeframeResponse = (overrides = {}) => ({
  status: 200,
  headers: { etag: '"r_1-timeframes"' },
  data: {
    registry_version: 'r_1',
    registry_schema_version: 1,
    timeframes: TIMEFRAME_ENTRIES.map((entry) => ({ ...entry })),
    sources: [
      'backend_app.backend.backtesting_engine.VALID_FREQ_MAP',
      'backend_app.backend.master_executor.TF_SEC',
      'backend_app.backend.market_data_validation.TIMEFRAME_MINUTES',
    ],
    total: TIMEFRAME_ENTRIES.length,
    ...overrides,
  },
});

const httpFailure = (status, detail) => {
  const body =
    detail === undefined
      ? { error: `HTTP_${status}_ERROR`, message: 'An error occurred.' }
      : {
          error: detail.error,
          message: detail.message,
          detail,
          status_code: status,
          details: Object.fromEntries(
            Object.entries(detail).filter(([key]) => !['error', 'message'].includes(key)),
          ),
        };
  const error = new Error(body.message);
  error.status = status;
  error.data = body;
  return error;
};

const UNAVAILABLE_MESSAGE =
  'The tradeable market universe is not available yet. It is refreshed on a schedule outside ' +
  'the request path; retry shortly.';

const universeUnavailable = () =>
  httpFailure(503, {
    error: 'ASSET_UNIVERSE_UNAVAILABLE',
    message: UNAVAILABLE_MESSAGE,
    last_refresh_error: 'binance: getaddrinfo failed',
    refresh_in_flight: true,
    retry_after_seconds: 30,
  });

const cursorInvalid = () =>
  httpFailure(422, {
    error: 'ASSET_CURSOR_INVALID',
    message: 'This cursor was issued for a different filter set.',
  });

/** The real `symbol` and `market_type` `ParamSpec`s from the `ohlcv_feed` descriptor. */
const SYMBOL_SPEC = Object.freeze({
  key: 'symbol',
  label: 'Symbol',
  type: 'SYMBOL',
  required: true,
  default: null,
  options: null,
  example: 'BTC/USDT',
  help: 'The market this strategy trades.',
});

const TIMEFRAME_SPEC = Object.freeze({
  key: 'timeframe',
  label: 'Timeframe',
  type: 'TIMEFRAME',
  required: true,
  default: null,
  // Deliberately no `options` tuple (SB-06). This absence is why the endpoint is the only
  // source, and it is asserted below rather than assumed.
  options: null,
  example: '15m',
  help: 'Bar interval.',
});

const MARKET_TYPE_SPEC = Object.freeze({
  key: 'market_type',
  label: 'Market type',
  type: 'SELECT',
  required: true,
  default: 'spot',
  options: ['spot', 'swap', 'future'],
});

const DATA_PARAMS = Object.freeze([SYMBOL_SPEC, TIMEFRAME_SPEC, MARKET_TYPE_SPEC]);

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

/** Route the one stubbed instance by path, the way the two endpoints really differ. */
const route = ({ assets = [], timeframes = null } = {}) => {
  const assetQueue = [...assets];
  mockClient.get.mockImplementation((url) => {
    if (url === REGISTRY_TIMEFRAMES_PATH) {
      const answer = timeframes === null ? timeframeResponse() : timeframes;
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    }
    if (url === ASSETS_PATH) {
      const answer = assetQueue.length > 1 ? assetQueue.shift() : assetQueue[0];
      if (answer === undefined) return Promise.resolve(assetPage([]));
      return answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer);
    }
    return Promise.reject(new Error(`unexpected request: ${url}`));
  });
};

const assetCalls = () => mockClient.get.mock.calls.filter(([url]) => url === ASSETS_PATH);
const assetParams = (index) => assetCalls()[index][1].params;

/** Advance past the debounce and let the resulting promises settle. */
const settle = async (ms = DEBOUNCE_MS + 50) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

const controlProps = (overrides = {}) => ({
  id: 'pf-n1-symbol',
  name: 'symbol',
  disabled: false,
  required: true,
  'aria-required': true,
  'aria-invalid': false,
  'aria-describedby': 'pf-n1-symbol-help pf-n1-symbol-constraints',
  'data-param-key': 'symbol',
  'data-param-type': 'SYMBOL',
  ...overrides,
});

const renderAssetSelector = (props = {}) => {
  const onChange = props.onChange || vi.fn();
  const utils = render(
    <AssetSelector
      controlProps={controlProps()}
      spec={SYMBOL_SPEC}
      params={DATA_PARAMS}
      values={{ market_type: 'spot' }}
      value={null}
      {...props}
      onChange={onChange}
    />,
  );
  return { ...utils, onChange };
};

const input = () => screen.getByTestId('asset-search-input');
const openList = () => fireEvent.focus(input());

beforeEach(() => {
  vi.useFakeTimers();
  mockClient.get.mockReset();
  resetRegistryClient();
});

afterEach(() => {
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// The asset selector: where its options come from
// ---------------------------------------------------------------------------

describe('AssetSelector: every market comes from the endpoint (Requirement 11.7)', () => {
  it('offers exactly what the response carried, in served order', async () => {
    route({ assets: [assetPage(['BTC/USDT', 'ETH/USDT', 'SOL/USDT'])] });
    renderAssetSelector();
    await settle();
    openList();

    const options = screen.getAllByRole('option');
    expect(options.map((option) => option.getAttribute('data-testid'))).toEqual([
      'asset-option-BTC/USDT',
      'asset-option-ETH/USDT',
      'asset-option-SOL/USDT',
    ]);
    expect(assetCalls()).toHaveLength(1);
  });

  it('offers nothing before the endpoint has answered', () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    openList();

    // No optimistic list, not even a "popular pairs" placeholder.
    expect(screen.queryAllByRole('option')).toHaveLength(0);
    expect(screen.getByTestId('asset-selector-status').textContent).toMatch(/Waiting|Loading/);
  });

  it('shows the endpoint figures per market, with an unpublished minimum named as such', async () => {
    route({
      assets: [
        assetPage([
          assetRecord('BTC/USDT', { min_notional: null, min_amount: null, available_on: ['binance', 'okx'], listing_count: 2 }),
        ]),
      ],
    });
    renderAssetSelector();
    await settle();
    openList();

    const option = screen.getByTestId('asset-option-BTC/USDT');
    expect(option.textContent).toContain('min notional not published');
    expect(option.textContent).not.toContain('min notional 0');
    expect(option.textContent).toContain('listed on 2 venues');
    expect(option.textContent).toContain('from binance');
  });

  it('states its provenance: the venues it was assembled from, and any that failed', async () => {
    route({
      assets: [assetPage(['BTC/USDT'], { source_meta: sourceMeta({ exchanges_failed: ['bybit'] }) })],
    });
    renderAssetSelector();
    await settle();

    const meta = screen.getByTestId('asset-source-meta').textContent;
    expect(meta).toContain('binance, okx');
    expect(meta).toContain('unavailable: bybit');
    expect(meta).toContain('61 markets in the universe');
  });

  it('serves a stale-but-real universe with its age stated', async () => {
    route({
      assets: [assetPage(['BTC/USDT'], { source_meta: sourceMeta({ stale: true, age_seconds: 30000 }) })],
    });
    renderAssetSelector();
    await settle();

    expect(screen.getByTestId('asset-universe-stale').textContent).toContain('30000s old');
  });
});

// ---------------------------------------------------------------------------
// Search, debounced
// ---------------------------------------------------------------------------

describe('AssetSelector: search is debounced', () => {
  it('issues one request for a burst of keystrokes, not one per keystroke', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();
    expect(assetCalls()).toHaveLength(1);

    fireEvent.change(input(), { target: { value: 'B' } });
    fireEvent.change(input(), { target: { value: 'BT' } });
    fireEvent.change(input(), { target: { value: 'BTC' } });

    // Nothing yet: the timer has been re-armed on every keystroke.
    expect(assetCalls()).toHaveLength(1);

    await settle();

    expect(assetCalls()).toHaveLength(2);
    expect(assetParams(1).search).toBe('BTC');
  });

  it('says it is waiting for typing to stop rather than showing a fetch that is not running', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();

    fireEvent.change(input(), { target: { value: 'BT' } });

    expect(screen.getByTestId('asset-selector-status').textContent).toContain(
      'Waiting for you to finish typing',
    );
  });

  it('sends the search as free text over symbol, base and quote', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();

    fireEvent.change(input(), { target: { value: 'sol' } });
    await settle();

    expect(assetParams(1)).toMatchObject({ search: 'sol', active_only: true });
  });
});

// ---------------------------------------------------------------------------
// Filtering
// ---------------------------------------------------------------------------

describe('AssetSelector: filtering (Requirement 11.2)', () => {
  it("takes the market-type options from the DATA block's own ParamSpec", async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();
    fireEvent.click(screen.getByTestId('asset-filters-toggle'));

    const select = screen.getByTestId('asset-filter-market-type');
    expect([...select.options].map((option) => option.value)).toEqual([
      '',
      'spot',
      'swap',
      'future',
    ]);
  });

  it("seeds the market-type filter from the block's own market_type value", async () => {
    route({ assets: [assetPage(['BTC/USDT-SWAP'])] });
    renderAssetSelector({ values: { market_type: 'swap' } });
    await settle();

    expect(assetParams(0).market_type).toBe('swap');
  });

  it('sends base, quote and active_only as the endpoint declares them', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();
    fireEvent.click(screen.getByTestId('asset-filters-toggle'));

    fireEvent.change(screen.getByTestId('asset-filter-quote'), { target: { value: 'usdc' } });
    await settle();
    // Upper-cased for an exact match against the exchange's own currency codes.
    expect(assetParams(1).quote).toBe('USDC');

    fireEvent.click(screen.getByTestId('asset-filter-active-only'));
    await settle();
    expect(assetParams(2).active_only).toBe(false);
  });

  it('reports a filter that matched nothing as a real answer, not as a failure', async () => {
    route({ assets: [assetPage([], { total: 0 })] });
    renderAssetSelector();
    await settle();

    // The distinction the whole component exists to keep: "your filters matched nothing" and
    // "the platform cannot say what it trades" have different fixes.
    expect(screen.getByTestId('asset-selector-status').textContent).toContain(
      'No market matches these filters',
    );
    expect(screen.getByTestId('asset-selector-status').textContent).toContain(
      'The market list loaded correctly',
    );
    expect(screen.queryByTestId('asset-selector-error')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Pagination: the keyset cursor
// ---------------------------------------------------------------------------

describe('AssetSelector: pagination uses the cursor (Requirement 11.3)', () => {
  it('reports the page against the filtered total, not against the page size', async () => {
    route({ assets: [assetPage(['BTC/USDT', 'ETH/USDT'], { total: 340, next_cursor: 'c_2' })] });
    renderAssetSelector();
    await settle();

    expect(screen.getByTestId('asset-selector-status').textContent).toContain(
      '2 of 340 matching markets shown',
    );
    expect(screen.getByTestId('asset-load-more').textContent).toContain('2 of 340');
  });

  it('sends the previous page next_cursor and appends the result', async () => {
    route({
      assets: [
        assetPage(['BTC/USDT'], { total: 3, next_cursor: 'c_2' }),
        assetPage(['ETH/USDT'], { total: 3, next_cursor: 'c_3' }),
      ],
    });
    renderAssetSelector();
    await settle();

    fireEvent.click(screen.getByTestId('asset-load-more'));
    await settle(0);

    expect(assetParams(0).cursor).toBeUndefined();
    expect(assetParams(1).cursor).toBe('c_2');
    openList();
    expect(screen.getAllByRole('option').map((option) => option.textContent.slice(0, 8))).toEqual([
      'BTC/USDT',
      'ETH/USDT',
    ]);
  });

  it('offers no continuation when the server sent no cursor', async () => {
    route({ assets: [assetPage(['BTC/USDT'], { next_cursor: null })] });
    renderAssetSelector();
    await settle();

    expect(screen.queryByTestId('asset-load-more')).toBeNull();
  });

  it('drops the cursor when a filter changes, because a cursor is pinned to its filters', async () => {
    route({
      assets: [
        assetPage(['BTC/USDT'], { next_cursor: 'c_2' }),
        assetPage(['ETH/USDT'], { next_cursor: 'c_3' }),
        assetPage(['BTC/USDC'], { next_cursor: null }),
      ],
    });
    renderAssetSelector();
    await settle();
    fireEvent.click(screen.getByTestId('asset-load-more'));
    await settle(0);
    expect(assetParams(1).cursor).toBe('c_2');

    fireEvent.click(screen.getByTestId('asset-filters-toggle'));
    fireEvent.change(screen.getByTestId('asset-filter-quote'), { target: { value: 'USDC' } });
    await settle();

    // Reusing it here is the 422 case. Not sending it is the mechanism; the 422 handler is
    // only the backstop.
    expect(assetParams(2).cursor).toBeUndefined();
    expect(assetParams(2).quote).toBe('USDC');
  });

  it('replaces the list on a filter change instead of showing the old query under new filters', async () => {
    route({
      assets: [assetPage(['BTC/USDT'], { next_cursor: null }), assetPage(['BTC/USDC'])],
    });
    renderAssetSelector();
    await settle();

    fireEvent.change(input(), { target: { value: 'usdc' } });
    await settle();
    openList();

    expect(screen.queryByTestId('asset-option-BTC/USDT')).toBeNull();
    expect(screen.getByTestId('asset-option-BTC/USDC')).toBeTruthy();
  });

  it('states that the universe moved while the list was being paged', async () => {
    route({
      assets: [
        assetPage(['BTC/USDT'], { next_cursor: 'c_2' }),
        assetPage(['BTC/USDT', 'ETH/USDT'], { universe_changed: true, next_cursor: null }),
      ],
    });
    renderAssetSelector();
    await settle();
    fireEvent.click(screen.getByTestId('asset-load-more'));
    await settle(0);

    const notice = screen.getByTestId('asset-universe-changed').textContent;
    expect(notice).toContain('refreshed while these pages were being loaded');
    // The repeated market is shown once, and the repeat is counted rather than swallowed.
    expect(notice).toContain('1 repeated market shown once');
    openList();
    expect(screen.getAllByRole('option')).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// 422: restart once, never loop
// ---------------------------------------------------------------------------

describe('AssetSelector: an invalid cursor restarts the query, once', () => {
  it('restarts from the first page and says so', async () => {
    route({
      assets: [
        assetPage(['BTC/USDT'], { next_cursor: 'c_2' }),
        cursorInvalid(),
        assetPage(['BTC/USDT', 'ETH/USDT'], { next_cursor: null }),
      ],
    });
    renderAssetSelector();
    await settle();

    fireEvent.click(screen.getByTestId('asset-load-more'));
    await settle(0);

    expect(assetCalls()).toHaveLength(3);
    expect(assetParams(1).cursor).toBe('c_2');
    // The restart is a fresh page one, not a re-send of the rejected cursor.
    expect(assetParams(2).cursor).toBeUndefined();
    expect(screen.getByTestId('asset-cursor-restarted')).toBeTruthy();
    expect(screen.queryByTestId('asset-selector-error')).toBeNull();
  });

  it('does not restart twice for the same filter set: a second 422 is an error, not a loop', async () => {
    route({
      assets: [
        assetPage(['BTC/USDT'], { next_cursor: 'c_2' }),
        cursorInvalid(),
        assetPage(['BTC/USDT'], { next_cursor: 'c_2' }),
        cursorInvalid(),
      ],
    });
    renderAssetSelector();
    await settle();

    fireEvent.click(screen.getByTestId('asset-load-more'));
    await settle(0);
    fireEvent.click(screen.getByTestId('asset-load-more'));
    await settle(0);

    // Four requests and then a stop. An unbounded restart loop against an authenticated,
    // rate-limited endpoint is what the guard exists to prevent.
    expect(assetCalls()).toHaveLength(4);
    const error = screen.getByTestId('asset-selector-error');
    expect(error.getAttribute('data-code')).toBe('ASSET_CURSOR_INVALID');
  });
});

// ---------------------------------------------------------------------------
// 503: an error state, never a substitute list
// ---------------------------------------------------------------------------

describe('AssetSelector: an unavailable universe (Requirement 11.6)', () => {
  it('renders the backend sentence verbatim in an alert, with no markets at all', async () => {
    route({ assets: [universeUnavailable()] });
    renderAssetSelector();
    await settle();
    openList();

    const alert = screen.getByRole('alert');
    expect(alert.getAttribute('data-code')).toBe('ASSET_UNIVERSE_UNAVAILABLE');
    expect(alert.getAttribute('data-status')).toBe('503');
    // The backend's own words, not a parallel client sentence.
    expect(alert.textContent).toContain(UNAVAILABLE_MESSAGE);
    expect(screen.queryAllByRole('option')).toHaveLength(0);
  });

  it('substitutes no market list anywhere in the rendered output', async () => {
    route({ assets: [universeUnavailable()] });
    const { container } = renderAssetSelector();
    await settle();

    // The assertion that pins the whole task: no pair-shaped literal survives a 503 — not the
    // ten pairs `DataPipelineContext` used to hold, and not one.
    expect(container.textContent).not.toMatch(/[A-Z0-9]{2,10}\/[A-Z0-9]{2,10}/);
    expect(screen.getByTestId('asset-selector')).toHaveProperty('dataset.assetCount', '0');
  });

  it('says in words that the list is empty because it failed, not because nothing matched', async () => {
    route({ assets: [universeUnavailable()] });
    renderAssetSelector();
    await settle();

    const status = screen.getByTestId('asset-selector-status').textContent;
    expect(status).toContain('could not be loaded');
    expect(status).not.toContain('No market matches these filters');
  });

  it('states the retry interval the server asked for and offers a retry', async () => {
    route({ assets: [universeUnavailable(), assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();

    expect(screen.getByTestId('asset-retry-after').textContent).toContain('30s');

    fireEvent.click(screen.getByTestId('asset-retry'));
    await settle();

    expect(screen.queryByTestId('asset-selector-error')).toBeNull();
    openList();
    expect(screen.getAllByRole('option')).toHaveLength(1);
  });

  it('tells an expired session to sign in rather than to retry', async () => {
    route({ assets: [httpFailure(401)] });
    renderAssetSelector();
    await settle();

    expect(screen.getByRole('alert').textContent).toContain('Sign in again');
    expect(screen.queryByTestId('asset-retry')).toBeNull();
  });

  it('keeps the pages the server really served when a later page fails', async () => {
    route({ assets: [assetPage(['BTC/USDT'], { next_cursor: 'c_2' }), httpFailure(500)] });
    renderAssetSelector();
    await settle();
    fireEvent.click(screen.getByTestId('asset-load-more'));
    await settle(0);
    openList();

    // Those records were real. Discarding them would lose real data to report an error about
    // a page that was never served — the failure is stated beside them instead.
    expect(screen.getAllByRole('option')).toHaveLength(1);
    expect(screen.getByTestId('asset-selector-error')).toBeTruthy();
  });

  it('shows zero markets when the *first* page fails', async () => {
    route({ assets: [httpFailure(500)] });
    renderAssetSelector();
    await settle();
    openList();

    expect(screen.queryAllByRole('option')).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// Choosing a market
// ---------------------------------------------------------------------------

describe('AssetSelector: typing does not choose a market (Requirement 5.4, SB-06)', () => {
  it('commits nothing while the author is typing', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    const { onChange } = renderAssetSelector();
    await settle();

    fireEvent.change(input(), { target: { value: 'BTC/USD' } });
    await settle();

    // "BTC/USD" at a venue that lists "BTC/USDT" must not become the traded market.
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId('asset-selected').textContent).toContain('typing alone does not set it');
  });

  it('commits the symbol the endpoint served when an option is activated', async () => {
    route({ assets: [assetPage(['BTC/USDT', 'ETH/USDT'])] });
    const { onChange } = renderAssetSelector();
    await settle();
    openList();

    fireEvent.click(screen.getByTestId('asset-option-ETH/USDT'));

    expect(onChange).toHaveBeenCalledWith('ETH/USDT');
  });

  it('is never prefilled, and the example is a placeholder rather than a value', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();

    expect(input().value).toBe('');
    expect(input().getAttribute('placeholder')).toContain('BTC/USDT');
    expect(screen.getByTestId('asset-selector').dataset.selected).toBeUndefined();
  });

  it('restates the chosen market in text and can clear it', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    const { onChange } = renderAssetSelector({ value: 'BTC/USDT' });
    await settle();

    expect(screen.getByTestId('asset-selected').textContent).toContain('BTC/USDT');

    fireEvent.click(screen.getByTestId('asset-clear'));
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it('requests nothing at all when the version is read-only (Requirement 9.9)', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector({ disabled: true });
    await settle();

    expect(assetCalls()).toHaveLength(0);
    expect(input().disabled).toBe(true);
    expect(screen.getByTestId('asset-selector-status').textContent).toContain('Read-only');
  });
});

// ---------------------------------------------------------------------------
// Accessibility
// ---------------------------------------------------------------------------

describe('AssetSelector: keyboard and screen-reader operation', () => {
  it('is a labelled combobox over a listbox, wired through ARIA', async () => {
    route({ assets: [assetPage(['BTC/USDT', 'ETH/USDT'])] });
    renderAssetSelector();
    await settle();

    const combobox = screen.getByRole('combobox');
    expect(combobox).toBe(input());
    expect(combobox.getAttribute('aria-autocomplete')).toBe('list');
    expect(combobox.getAttribute('aria-required')).toBe('true');
    expect(combobox.getAttribute('aria-expanded')).toBe('false');
    // The requirement is announced, but the HTML attribute is not set on an input that holds a
    // search string: it would read as invalid while a market was legitimately chosen.
    expect(combobox.required).toBe(false);

    openList();
    expect(screen.getByRole('combobox').getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByRole('listbox').id).toBe(combobox.getAttribute('aria-controls'));
  });

  it("keeps ParameterForm's description wiring and adds its own status to it", async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    renderAssetSelector();
    await settle();

    const described = input().getAttribute('aria-describedby').split(' ');
    // Composed, not replaced: the form's help and constraints are still announced.
    expect(described).toContain('pf-n1-symbol-help');
    expect(described).toContain('pf-n1-symbol-constraints');
    expect(described).toContain('pf-n1-symbol-status');
  });

  it('walks the list with the arrow keys and selects with Enter', async () => {
    route({ assets: [assetPage(['BTC/USDT', 'ETH/USDT', 'SOL/USDT'])] });
    const { onChange } = renderAssetSelector();
    await settle();
    openList();

    fireEvent.keyDown(input(), { key: 'ArrowDown' });
    fireEvent.keyDown(input(), { key: 'ArrowDown' });
    const active = input().getAttribute('aria-activedescendant');
    expect(document.getElementById(active).getAttribute('data-testid')).toBe('asset-option-ETH/USDT');

    fireEvent.keyDown(input(), { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('ETH/USDT');
  });

  it('wraps at both ends and supports Home, End and Escape', async () => {
    route({ assets: [assetPage(['BTC/USDT', 'ETH/USDT', 'SOL/USDT'])] });
    renderAssetSelector();
    await settle();
    openList();

    fireEvent.keyDown(input(), { key: 'ArrowUp' });
    expect(input().getAttribute('aria-activedescendant')).toContain('option-2');

    fireEvent.keyDown(input(), { key: 'Home' });
    expect(input().getAttribute('aria-activedescendant')).toContain('option-0');

    fireEvent.keyDown(input(), { key: 'End' });
    expect(input().getAttribute('aria-activedescendant')).toContain('option-2');

    fireEvent.keyDown(input(), { key: 'Escape' });
    expect(input().getAttribute('aria-expanded')).toBe('false');
    expect(input().getAttribute('aria-activedescendant')).toBeNull();
  });

  it('marks the chosen option as selected rather than only colouring it', async () => {
    route({ assets: [assetPage(['BTC/USDT', 'ETH/USDT'])] });
    renderAssetSelector({ value: 'ETH/USDT' });
    await settle();
    openList();

    expect(screen.getByTestId('asset-option-ETH/USDT').getAttribute('aria-selected')).toBe('true');
    expect(screen.getByTestId('asset-option-BTC/USDT').getAttribute('aria-selected')).toBe('false');
  });

  it('announces the count through a live region and the failure through an alert', async () => {
    route({ assets: [assetPage(['BTC/USDT'], { total: 12 }), universeUnavailable()] });
    renderAssetSelector();
    await settle();

    const status = screen.getByTestId('asset-selector-status');
    expect(status.getAttribute('role')).toBe('status');
    expect(status.getAttribute('aria-live')).toBe('polite');
    expect(status.textContent).toContain('1 of 12');

    fireEvent.change(input(), { target: { value: 'x' } });
    await settle();

    // A failure is assertive, and it is text — the loading, empty and error states are told
    // apart by their words, never by their colour.
    expect(screen.getByRole('alert')).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// The timeframe selector
// ---------------------------------------------------------------------------

const renderTimeframeSelector = (props = {}) => {
  const onChange = props.onChange || vi.fn();
  const utils = render(
    <TimeframeSelector
      controlProps={controlProps({
        id: 'pf-n1-timeframe',
        name: 'timeframe',
        'data-param-key': 'timeframe',
        'data-param-type': 'TIMEFRAME',
        'aria-describedby': 'pf-n1-timeframe-help',
      })}
      spec={TIMEFRAME_SPEC}
      value={null}
      {...props}
      onChange={onChange}
    />,
  );
  return { ...utils, onChange };
};

const timeframeSelect = () => screen.getByTestId('timeframe-select');
const timeframeOptions = () => [...timeframeSelect().options].map((option) => option.value);

describe('TimeframeSelector: the endpoint is the option set (Requirement 11.8)', () => {
  it('publishes no options tuple on the descriptor, which is why the endpoint is the source', () => {
    // Asserted rather than assumed: a hand-written `options` list appearing on this ParamSpec
    // later would be a second, drift-prone vocabulary.
    expect(TIMEFRAME_SPEC.options).toBeNull();
    expect(TIMEFRAME_SPEC.required).toBe(true);
    expect(TIMEFRAME_SPEC.default).toBeNull();
  });

  it('renders exactly the intervals the endpoint served, with their seconds', async () => {
    route({});
    renderTimeframeSelector();
    await act(async () => {});

    expect(timeframeOptions()).toEqual([
      '',
      '1m',
      '5m',
      '15m',
      '30m',
      '1h',
      '2h',
      '4h',
      '6h',
      '12h',
      '1d',
    ]);
    expect(screen.getByTestId('timeframe-option-15m').textContent).toBe('15m · 900s');
  });

  it('offers no interval the endpoint withheld', async () => {
    route({});
    renderTimeframeSelector();
    await act(async () => {});

    // `3m` and `1w` are exactly the labels a client-side list would have carried.
    expect(timeframeOptions()).not.toContain('3m');
    expect(timeframeOptions()).not.toContain('1w');
    expect(timeframeOptions()).not.toContain('1M');
  });

  it('states where the set came from and how many vocabularies were intersected', async () => {
    route({});
    renderTimeframeSelector();
    await act(async () => {});

    const status = screen.getByTestId('timeframe-selector-status').textContent;
    expect(status).toContain('10 intervals');
    expect(status).toContain('intersection of 3 pipeline vocabularies');
  });

  it('starts blank and never auto-resolves to the first published interval', async () => {
    route({});
    const { onChange } = renderTimeframeSelector();
    await act(async () => {});

    expect(timeframeSelect().value).toBe('');
    expect(onChange).not.toHaveBeenCalled();
    expect(timeframeSelect().options[0].textContent).toContain('Select an interval');
  });

  it('commits the chosen interval', async () => {
    route({});
    const { onChange } = renderTimeframeSelector();
    await act(async () => {});

    fireEvent.change(timeframeSelect(), { target: { value: '4h' } });
    expect(onChange).toHaveBeenCalledWith('4h');
  });

  it('offers nothing and says why when the set cannot be loaded', async () => {
    route({ timeframes: httpFailure(503) });
    renderTimeframeSelector();
    await act(async () => {});

    // Only the blank option: an interval list is never invented, and never left silently empty.
    expect(timeframeOptions()).toEqual(['']);
    expect(timeframeSelect().disabled).toBe(true);
    const alert = screen.getByRole('alert');
    expect(alert.getAttribute('data-code')).toBe('REGISTRY_UNAVAILABLE');
    expect(screen.getByTestId('timeframe-selector-status').textContent).toContain(
      'could not be loaded',
    );
    // No selectable interval survives the failure. The blank option's hint still shows the
    // descriptor's own `example` ("e.g. 15m"), which is a placeholder from the registry and not
    // a choosable interval — scoping the check to the option *values* is the honest assertion.
    expect(timeframeOptions().filter((value) => value !== '')).toEqual([]);
    expect(alert.textContent).toContain('never offers a local substitute');
  });

  it('retries after a failure and then serves the real set', async () => {
    let answered = false;
    mockClient.get.mockImplementation((url) => {
      if (url !== REGISTRY_TIMEFRAMES_PATH) return Promise.reject(new Error('unexpected'));
      if (!answered) {
        answered = true;
        return Promise.reject(httpFailure(503));
      }
      return Promise.resolve(timeframeResponse());
    });

    renderTimeframeSelector();
    await act(async () => {});
    expect(timeframeOptions()).toEqual(['']);

    fireEvent.click(screen.getByTestId('timeframe-retry'));
    await act(async () => {});

    expect(timeframeOptions()).toHaveLength(11);
  });

  it('shows a held interval the pipeline no longer publishes, and flags it', async () => {
    route({});
    renderTimeframeSelector({ value: '3m' });
    await act(async () => {});

    // Silently blanking it would make the author think the field was empty while the saved
    // graph still carried `3m`.
    expect(timeframeSelect().value).toBe('3m');
    expect(screen.getByTestId('timeframe-unsupported').textContent).toContain('no longer publishes');
  });

  it('requests nothing when the version is read-only', async () => {
    route({});
    renderTimeframeSelector({ disabled: true });
    await act(async () => {});

    expect(mockClient.get).not.toHaveBeenCalled();
    expect(timeframeSelect().disabled).toBe(true);
  });

  it("keeps ParameterForm's description wiring", async () => {
    route({});
    renderTimeframeSelector();
    await act(async () => {});

    const described = timeframeSelect().getAttribute('aria-describedby').split(' ');
    expect(described).toContain('pf-n1-timeframe-help');
    expect(described).toContain('pf-n1-timeframe-tf-status');
  });
});

// ---------------------------------------------------------------------------
// ParameterForm's control seam
// ---------------------------------------------------------------------------

describe("ParameterForm: the SYMBOL / TIMEFRAME control seam", () => {
  const Harness = ({ controls }) => {
    const [values, setValues] = useState({});
    return (
      <ParameterForm
        params={DATA_PARAMS}
        values={values}
        controls={controls}
        nodeId="n-1"
        blockId="ohlcv_feed"
        onChange={(key, value) => setValues((previous) => ({ ...previous, [key]: value }))}
      />
    );
  };

  it('renders from the descriptor alone when no controls are supplied', () => {
    render(<Harness />);

    // Unchanged behaviour: the form is still usable, and still pure, without the seam.
    const symbol = screen.getByTestId('param-symbol').querySelector('input');
    expect(symbol.getAttribute('data-param-type')).toBe('SYMBOL');
    expect(screen.queryByTestId('asset-selector')).toBeNull();
  });

  it('uses the supplied control for SYMBOL and TIMEFRAME, and nothing else', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    render(<Harness controls={{ SYMBOL: AssetSelector, TIMEFRAME: TimeframeSelector }} />);
    await settle();

    expect(screen.getByTestId('asset-selector')).toBeTruthy();
    expect(screen.getByTestId('timeframe-selector')).toBeTruthy();
    // `market_type` still comes from its own `options` tuple, rendered by the form.
    const marketType = screen.getByTestId('param-market_type').querySelector('select');
    expect([...marketType.options].map((option) => option.value)).toEqual(['', 'spot', 'swap', 'future']);
  });

  it('keeps both market parameters blocking until the author chooses (Requirement 5.4)', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    render(<Harness controls={{ SYMBOL: AssetSelector, TIMEFRAME: TimeframeSelector }} />);
    await settle();

    expect(screen.getByTestId('parameter-form-blocking').getAttribute('data-blocking-keys')).toContain('symbol');
    expect(screen.getByTestId('parameter-form-blocking').getAttribute('data-blocking-keys')).toContain('timeframe');
  });

  it('clears the blocking state when a served market is chosen', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    render(<Harness controls={{ SYMBOL: AssetSelector, TIMEFRAME: TimeframeSelector }} />);
    await settle();

    fireEvent.focus(input());
    fireEvent.click(screen.getByTestId('asset-option-BTC/USDT'));
    fireEvent.change(timeframeSelect(), { target: { value: '15m' } });
    await settle(0);

    expect(screen.queryByTestId('parameter-form-blocking')).toBeNull();
    expect(screen.getByTestId('asset-selector').dataset.selected).toBe('BTC/USDT');
  });

  it('passes the sibling specs through, so the market-type filter needs no local copy', async () => {
    route({ assets: [assetPage(['BTC/USDT'])] });
    render(<Harness controls={{ SYMBOL: AssetSelector }} />);
    await settle();
    fireEvent.click(screen.getByTestId('asset-filters-toggle'));

    expect(
      [...screen.getByTestId('asset-filter-market-type').options].map((option) => option.value),
    ).toEqual(['', 'spot', 'swap', 'future']);
  });
});

// ---------------------------------------------------------------------------
// Structural: no market or interval vocabulary anywhere the builder can reach
// ---------------------------------------------------------------------------

describe('no hardcoded symbol or timeframe list is reachable from the builder', () => {
  const SRC_ROOT = path.resolve(__dirname, '..', '..', 'src');
  const CODE_EXTENSIONS = ['.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs'];

  /** Every module specifier in `source`, comments already stripped by the caller. */
  const specifiersIn = (code) => {
    const patterns = [
      /^[ \t]*import\s+(?:[^'";]*?\sfrom\s*)?['"]([^'"]+)['"]/gm,
      /^[ \t]*export\s+(?:[^'";]*?\s)?from\s*['"]([^'"]+)['"]/gm,
      /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
      /\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    ];
    const found = [];
    for (const pattern of patterns) {
      for (const match of code.matchAll(pattern)) found.push(match[1]);
    }
    return found;
  };

  /**
   * Executable source only: block comments, line comments and JSX comment expressions
   * removed. Deliberate — this very repo's docblocks name the removed lists in prose (this
   * file's own header does), and prose describing a defect is not the defect. What the check
   * has to catch is a literal a bundle would ship.
   */
  const executableSource = (relative) =>
    readFileSync(path.join(SRC_ROOT, relative), 'utf8')
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .replace(/^[ \t]*\/\/.*$/gm, '')
      .replace(/([^:])\/\/[^\n'"`]*$/gm, '$1');

  const resolveRelative = (fromRelative, specifier) => {
    const base = path.resolve(path.dirname(path.join(SRC_ROOT, fromRelative)), specifier);
    const candidates = [
      base,
      ...CODE_EXTENSIONS.map((extension) => `${base}${extension}`),
      ...CODE_EXTENSIONS.map((extension) => path.join(base, `index${extension}`)),
    ];
    for (const candidate of candidates) {
      if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
    }
    return null;
  };

  /** The transitive closure of local modules the builder page can reach. */
  const builderClosure = () => {
    const modules = new Set();
    const queue = ['pages/StrategyBuilder.jsx'];
    while (queue.length > 0) {
      const current = queue.shift();
      if (modules.has(current)) continue;
      modules.add(current);
      if (!CODE_EXTENSIONS.includes(path.extname(current))) continue;
      for (const specifier of specifiersIn(executableSource(current))) {
        if (!specifier.startsWith('./') && !specifier.startsWith('../')) continue;
        const resolved = resolveRelative(current, specifier);
        if (resolved === null) continue;
        const next = path.relative(SRC_ROOT, resolved).split(path.sep).join('/');
        if (!modules.has(next)) queue.push(next);
      }
    }
    return [...modules];
  };

  /** `"BTC/USDT"`, `'ETH/USDT'` — a quoted market pair. */
  const PAIR_LITERAL = /["'][A-Z0-9]{2,10}\/[A-Z0-9]{2,10}["']/g;

  /** Three or more comma-separated quoted bar intervals: an interval vocabulary. */
  const INTERVAL_LIST = /(["'](?:\d+[mhdwM])["']\s*,\s*){2,}["'](?:\d+[mhdwM])["']/g;

  it('reaches a closure large enough for the check to mean something', () => {
    const closure = builderClosure();

    expect(closure).toContain('components/builder/AssetSelector.jsx');
    expect(closure).toContain('components/builder/TimeframeSelector.jsx');
    expect(closure).toContain('contexts/DataPipelineContext.jsx');
    expect(closure).toContain('api/modules/assets.js');
    expect(closure).toContain('lib/registryClient.js');
    expect(closure.length).toBeGreaterThan(20);
  });

  it('names no market pair in executable code (SB-06)', () => {
    const offenders = [];
    for (const module of builderClosure()) {
      if (!CODE_EXTENSIONS.includes(path.extname(module))) continue;
      const found = executableSource(module).match(PAIR_LITERAL);
      if (found) offenders.push([module, [...new Set(found)]]);
    }

    expect(offenders).toEqual([]);
  });

  it('holds no bar-interval vocabulary in executable code (Requirement 11.8)', () => {
    const offenders = [];
    for (const module of builderClosure()) {
      if (!CODE_EXTENSIONS.includes(path.extname(module))) continue;
      const found = executableSource(module).match(INTERVAL_LIST);
      if (found) offenders.push([module, [...new Set(found)]]);
    }

    expect(offenders).toEqual([]);
  });

  it('finds the pair and interval patterns when they are there', () => {
    // Both checks above assert an absence. These two lines are the control that they are not
    // vacuously true.
    expect('const f = ["BTC/USDT", "ETH/USDT"];'.match(PAIR_LITERAL)).toHaveLength(2);
    expect('const t = ["1m", "5m", "15m"];'.match(INTERVAL_LIST)).toHaveLength(1);
  });

  it('invents no market identity in the legacy plan builder either (SB-06)', () => {
    // Found by the check above rather than by reading: `StrategyEngineContext.jsx` is in the
    // builder's closure and held `symbol || 'BTC/USDT'`, `timeframe || '1h'` and
    // `exchange || 'binance'` — the same silent default task 3.9 removed from the save path.
    const code = executableSource('contexts/StrategyEngineContext.jsx');

    expect(code).not.toMatch(PAIR_LITERAL);
    expect(code).not.toContain("|| 'binance'");
    // And an unset market is reported rather than filled in.
    expect(code).toContain("field: 'symbol'");
    expect(code).toContain("field: 'timeframe'");
  });

  it('deletes the fallback from DataPipelineContext rather than moving it', () => {
    const source = readFileSync(path.join(SRC_ROOT, 'contexts/DataPipelineContext.jsx'), 'utf8');
    const code = executableSource('contexts/DataPipelineContext.jsx');

    // The prose that records the removal is expected; the list itself is not.
    expect(source).toContain('ten-symbol fallback');
    expect(code).not.toMatch(PAIR_LITERAL);
    expect(code).not.toMatch(INTERVAL_LIST);
    // And the shape check that used to trigger the fallback no longer chooses a list.
    expect(code).toContain('setAvailableSymbols([])');
    expect(code).toContain('symbolsError');
  });
});
