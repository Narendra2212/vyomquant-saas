/**
 * The anti-fallback palette test (task 3.12, Requirement 4.12).
 *
 * One claim, stated as narrowly as it can be: **when the block registry does not answer, the
 * palette shows an error with a retry and zero blocks.** Not a bundled catalogue, not the
 * copy it held a second ago, not a "safe subset" — nothing.
 *
 * Why that claim needs its own suite. SB-03 and SB-04 were not caused by a missing feature;
 * they were caused by a hand-maintained block list in `blockRegistry.js` that drifted from
 * what the engine can run (an always-empty FEATURE_ENGINEERING section; `wma`, `hma`,
 * `catboost` and `autoencoder` unselectable). A local list consulted *only when the fetch
 * fails* is that same defect with better manners: it puts the drifted answer on screen at
 * precisely the moment nobody can check it against the backend, and every block dragged from
 * it becomes a node the engine may not be able to execute. So the fallback is not a
 * resilience feature to be added later. Its absence is the fix.
 *
 * Why this fails against the pre-fix frontend. Before task 3.5, `blockRegistry.js` exported
 * `BlockRegistry` with 33 block definitions and the palette rendered from
 * `getDynamicBlocksByCategory(...)`, which read that object and never the network. A 500 from
 * the registry endpoint therefore changed nothing on screen: the palette still listed blocks,
 * there was no error panel and no retry. Every assertion below — `palette-error` present,
 * `palette-block` count zero, a drop of a known block id creating nothing — is false on that
 * code. The test is not restaged against it: the local list is deleted, and the point of this
 * file is that it stays deleted.
 *
 * The axios instance is the only stub. `registryClient`, the palette, the canvas and the
 * status strip are the shipped code.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { act, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), del: vi.fn(), patch: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: (...args) => mockClient.del(...args),
  patch: (...args) => mockClient.patch(...args),
}));

import {
  ALL_BLOCK_IDS,
  CATEGORIES,
  failed,
  installBuilderStubs,
  renderBuilder,
  served,
  dropBlockId,
  renderedBlockIds,
  waitForRegistry,
} from './helpers/registryFixture';
import {
  getBlocksByCategory,
  getDescriptor,
  getPaletteSections,
  getRegistrySnapshot,
  refreshRegistry,
  resetRegistryClient,
} from '../../src/lib/registryClient';

/**
 * Block ids distinctive enough that finding one in the rendered document means a catalogue
 * leaked, rather than that English contains the word. (`and`, `or`, `min` and `log` are also
 * real block ids, which is exactly why they are not searched for here.)
 */
const DISTINCTIVE_IDS = [
  'ohlcv_feed',
  'bollinger_bands',
  'wma',
  'hma',
  'feat_zscore',
  'feat_rolling_std',
  'catboost',
  'autoencoder',
  'action_buy_market',
];

/** The request headers the registry client sent on call `index`. */
const headersOfCall = (index) => mockClient.get.mock.calls[index][1].headers;

beforeEach(() => {
  installBuilderStubs();
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  resetRegistryClient();
  // The validation debounce fires on canvas edits; answer it so nothing rejects unhandled.
  mockClient.post.mockResolvedValue({
    valid: true,
    dag_hash: 'dag_fixture',
    validation_state: 'VALID',
    errors: [],
    warnings: [],
    summary: { node_count: 0, edge_count: 0, warmup_bars: 0 },
  });
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. A 500 renders an error state with retry, and zero blocks
// ---------------------------------------------------------------------------

describe('a registry 500 renders a retryable error state and zero block entries', () => {
  beforeEach(() => {
    mockClient.get.mockRejectedValue(failed(500));
  });

  it('renders the error panel, names the failure, and offers a usable retry', async () => {
    renderBuilder();

    const panel = await screen.findByTestId('palette-error');
    // An error state a screen reader is told about, not a silently blank list.
    expect(panel.getAttribute('role')).toBe('alert');
    expect(panel.dataset.errorCode).toBe('REGISTRY_UNAVAILABLE');
    expect(panel.textContent).toContain('HTTP 500');

    const retry = screen.getByTestId('palette-retry');
    expect(retry.tagName).toBe('BUTTON');
    expect(retry.getAttribute('type')).toBe('button');
    expect(retry.disabled).toBe(false);
    retry.focus();
    expect(document.activeElement).toBe(retry);
  });

  it('renders zero block entries and zero category sections', async () => {
    renderBuilder();
    await screen.findByTestId('palette-error');

    expect(screen.queryAllByTestId('palette-block')).toHaveLength(0);
    expect(screen.queryAllByTestId('palette-category')).toHaveLength(0);
    expect(screen.queryAllByTestId('port-chip')).toHaveLength(0);
    // The count beside the search box agrees: nothing is offered.
    expect(screen.getByText('0 blocks')).toBeTruthy();
    await waitForRegistry('error');
  });

  it('puts no block the engine publishes on screen under any name', async () => {
    renderBuilder();
    await screen.findByTestId('palette-error');

    const rendered = document.body.textContent;
    for (const blockId of DISTINCTIVE_IDS) {
      expect(rendered).not.toContain(blockId);
    }
  });

  it('reports zero blocks through every registry lookup the UI can reach', async () => {
    renderBuilder();
    await screen.findByTestId('palette-error');

    const snapshot = getRegistrySnapshot();
    expect(snapshot.isError).toBe(true);
    expect(snapshot.isReady).toBe(false);
    expect(snapshot.blocks).toHaveLength(0);
    expect(getPaletteSections()).toHaveLength(0);
    for (const category of CATEGORIES) {
      expect(getBlocksByCategory(category.id)).toHaveLength(0);
    }
    // Not one of the 100 published ids resolves — including the four SB-04 named.
    for (const blockId of ALL_BLOCK_IDS) {
      expect(getDescriptor(blockId)).toBeNull();
    }
  });

  it('creates no node for a block id dropped while the registry is down', async () => {
    renderBuilder();
    await screen.findByTestId('palette-error');

    // Nothing is draggable, so this drop is hypothetical — which is the point: even handed a
    // block id directly, the canvas has no catalogue to build a node from.
    dropBlockId('catboost');

    const notice = await screen.findByTestId('canvas-notice');
    expect(notice.textContent).toContain('catboost');
    expect(notice.textContent).toMatch(/no descriptor/i);
    expect(screen.getByTestId('status-strip').textContent).toContain('0 nodes');
    expect(screen.queryByTestId('inspector-node-status')).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// 2. Retry is a real refetch, and a failing retry stays empty
// ---------------------------------------------------------------------------

describe('retry re-asks the backend and shows only what the backend then serves', () => {
  it('stays empty while the failure persists, however often it is retried', async () => {
    mockClient.get.mockRejectedValue(failed(500));
    renderBuilder();
    await screen.findByTestId('palette-error');

    fireEvent.click(screen.getByTestId('palette-retry'));
    await waitFor(() => expect(mockClient.get).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByTestId('palette-retry'));
    await waitFor(() => expect(mockClient.get).toHaveBeenCalledTimes(3));

    expect(screen.getByTestId('palette-error')).toBeTruthy();
    expect(renderedBlockIds()).toHaveLength(0);
  });

  it('populates from the response, and only once there is one', async () => {
    mockClient.get.mockRejectedValueOnce(failed(500));
    renderBuilder();
    await screen.findByTestId('palette-error');
    expect(renderedBlockIds()).toHaveLength(0);

    mockClient.get.mockResolvedValueOnce(served());
    fireEvent.click(screen.getByTestId('palette-retry'));

    await waitForRegistry('ready');
    expect(screen.queryByTestId('palette-error')).toBeNull();
    // Exactly what was served: 100 blocks across seven categories.
    expect(renderedBlockIds()).toEqual([...ALL_BLOCK_IDS]);
  });

  it('drops the entity tag on failure, so a retry cannot be answered from a dead cache', async () => {
    // A held copy is what makes a conditional request possible, and a `304` is the backend
    // *stating* the held copy is current. After an error there is no held copy, so the retry
    // must be an unconditional fetch — otherwise a `304` would resurrect a catalogue the
    // backend has since disowned, which is the fallback again by another route.
    mockClient.get.mockResolvedValueOnce(served());
    renderBuilder();
    await waitForRegistry('ready');
    expect(headersOfCall(0)['If-None-Match']).toBeUndefined();

    mockClient.get.mockRejectedValueOnce(failed(500));
    await act(async () => {
      await refreshRegistry();
    });
    // The revalidation was conditional: a copy was held at that point.
    expect(headersOfCall(1)['If-None-Match']).toBe('"r_fixture_0001"');

    await screen.findByTestId('palette-error');
    mockClient.get.mockRejectedValueOnce(failed(500));
    fireEvent.click(screen.getByTestId('palette-retry'));
    await waitFor(() => expect(mockClient.get).toHaveBeenCalledTimes(3));
    // The retry after the failure is not.
    expect(headersOfCall(2)['If-None-Match']).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// 3. No stale copy survives an error
// ---------------------------------------------------------------------------

describe('a failure after a success drops the catalogue rather than freezing it', () => {
  it('shows the error and zero blocks even though a good copy was on screen a moment ago', async () => {
    mockClient.get.mockResolvedValueOnce(served());
    renderBuilder();
    await waitForRegistry('ready');
    expect(renderedBlockIds()).toHaveLength(100);

    // The backend has since lost the registry: a bad deploy, an absent model library, a failed
    // assembly. The palette must not keep offering the catalogue it happened to fetch before
    // that, because a block it offers may no longer be runnable.
    mockClient.get.mockRejectedValue(failed(500));
    await act(async () => {
      await refreshRegistry();
    });

    await screen.findByTestId('palette-error');
    expect(renderedBlockIds()).toHaveLength(0);
    expect(screen.queryAllByTestId('palette-category')).toHaveLength(0);
    expect(getRegistrySnapshot().blocks).toHaveLength(0);
    expect(getDescriptor('wma')).toBeNull();
    for (const blockId of DISTINCTIVE_IDS) {
      expect(document.body.textContent).not.toContain(blockId);
    }
  });
});
