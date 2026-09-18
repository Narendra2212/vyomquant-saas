/**
 * ═══════════════════════════════════════════════════════════════════════════
 * strategyBuilder.reviewMode.test.jsx — the tablet review mode (task 24.7)
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * design.md §11.6. Requirements 17.4, 15.3.
 *
 * WHAT IS UNDER TEST
 * ------------------
 * Requirement 17.4 asks for a *defined* Strategy Builder behaviour below `LAPTOP`, and §11.6
 * answers "read-only review mode". Two halves, and the second is the one worth a test:
 *
 *   * reading stays on — the canvas is there, selection works, the inspector opens;
 *   * writing is genuinely OFF, not hidden. A control that disappears is not a control that
 *     refuses, and none of `Ctrl+S`, `Delete`, `Ctrl+Z` or a drop onto the canvas passes
 *     through a control at all. So the assertions below go after the OUTCOMES — is there a new
 *     node, did a request leave, did a parameter change — rather than after the styling.
 *
 * HOW REVIEW MODE IS DRIVEN HERE
 * ------------------------------
 * By a real `shell/ResponsiveGate` above the page, the same way the shell drives it, because the
 * page reads the gate's published `access` rather than measuring a width of its own. `window`
 * has no `matchMedia` under jsdom 23.2.0 and `vitest.config.js` has `setupFiles: []`, so the
 * gate falls back to `window.innerWidth` — which is the lever these tests pull. Nothing about
 * review mode is stubbed.
 *
 * The axios instance is the only stub, as in every other builder suite. `registryClient`, the
 * palette, the canvas, the inspector, `ds/Drawer` and `shell/ResponsiveGate` are shipped code.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ReactFlowProvider } from 'reactflow';

const { mockClient } = vi.hoisted(() => ({
  mockClient: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}));

vi.mock('../../src/apiClient', () => ({
  default: mockClient,
  get: (...args) => mockClient.get(...args),
  post: (...args) => mockClient.post(...args),
  put: (...args) => mockClient.put(...args),
  del: vi.fn(),
  patch: vi.fn(),
}));

import {
  DRAG_BLOCK_ID_MIME,
  REVIEW_MODE_MIN_WIDTH_PX,
  StrategyBuilderCanvas,
  headerActionRefusal,
  reviewModeRefusal,
  saveRefusal,
} from '../../src/pages/StrategyBuilder';
import ResponsiveGate, {
  ROUTE_MIN_VIEWPORT,
  VIEWPORT,
} from '../../src/components/shell/ResponsiveGate';
import { UndoRedoProvider } from '../../src/contexts/UndoRedoContext';
import { ValidationProvider } from '../../src/contexts/ValidationContext';
import { resetRegistryClient } from '../../src/lib/registryClient';
import { resetOverlayRegistry } from '../../src/components/ds/overlayRegistry';
import {
  descriptor,
  installBuilderStubs,
  registryPayload,
  served,
} from './helpers/registryFixture';

// ---------------------------------------------------------------------------
// Fixture: two blocks. This suite is about the mode, not about the catalogue.
// ---------------------------------------------------------------------------

const BLOCKS = [descriptor('ohlcv_feed', 'DATA'), descriptor('sma', 'INDICATOR')];

const PAYLOAD = () => registryPayload({ blocks: BLOCKS });

/** A width inside §11.6's tablet band: clears the app-wide floor, misses this route's minimum. */
const TABLET_PX = VIEWPORT.TABLET.min + 32;
/** A width that clears this route's minimum. */
const LAPTOP_PX = VIEWPORT.LAPTOP.min + 100;

const setViewportWidth = (px) => {
  window.innerWidth = px;
  fireEvent(window, new Event('resize'));
};

/**
 * The page under a REAL gate, at `widthPx`.
 *
 * `pathname` is `/app/builder` because the restriction is per-route: the same width on any other
 * route is `full` access, and a test that passed a different path would be asserting nothing.
 */
const renderAtWidth = (widthPx, props = {}) => {
  window.innerWidth = widthPx;
  return render(
    <MemoryRouter>
      <ResponsiveGate pathname="/app/builder">
        <ReactFlowProvider>
          <UndoRedoProvider>
            <ValidationProvider>
              <StrategyBuilderCanvas {...props} />
            </ValidationProvider>
          </UndoRedoProvider>
        </ReactFlowProvider>
      </ResponsiveGate>
    </MemoryRouter>,
  );
};

const waitForRegistryReady = () =>
  waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'), {
    timeout: 5000,
  });

const shell = () => screen.getByTestId('builder-shell');
const nodeCountText = () => screen.getByTestId('status-strip').textContent;

/*
  `vitest.config.js` has `setupFiles: []`, so `@testing-library/jest-dom`'s matchers are not
  installed and `toBeDisabled()` is not available. These read the DOM property the matcher would
  have read, which is the same assertion with one less dependency.
*/
const isDisabled = (element) => element.disabled === true;
const button = (name) => screen.getByRole('button', { name });

/** Drop `blockId` onto the canvas, the way a completed drag does. */
const dropBlockId = (blockId) => {
  fireEvent.drop(screen.getByTestId('canvas'), {
    clientX: 140,
    clientY: 110,
    dataTransfer: { getData: (mime) => (mime === DRAG_BLOCK_ID_MIME ? blockId : '') },
  });
};

const originalWidth = window.innerWidth;

beforeEach(() => {
  installBuilderStubs();
  resetOverlayRegistry();
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  mockClient.get.mockResolvedValue(served(PAYLOAD()));
  resetRegistryClient();
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
  resetOverlayRegistry();
  window.innerWidth = originalWidth;
});

// ═══════════════════════════════════════════════════════════════════════════
// 1. The width in every sentence comes from `ROUTE_MIN_VIEWPORT`
// ═══════════════════════════════════════════════════════════════════════════

describe('the review-mode width is the gate’s width, not a literal', () => {
  it('reads the pixel floor of the tier ROUTE_MIN_VIEWPORT declares for this route', () => {
    expect(ROUTE_MIN_VIEWPORT['/app/builder']).toBe('LAPTOP');
    expect(REVIEW_MODE_MIN_WIDTH_PX).toBe(VIEWPORT.LAPTOP.min);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 2. The width refusal COMPOSES into task 24.8's two refusal functions
// ═══════════════════════════════════════════════════════════════════════════

describe('the width refusal is a cause in the existing composition', () => {
  it('names the width requirement, and answers null when the width is not the problem', () => {
    expect(reviewModeRefusal('saving', REVIEW_MODE_MIN_WIDTH_PX)).toBe(
      `Open this strategy on a screen at least ${REVIEW_MODE_MIN_WIDTH_PX}px wide before saving`,
    );
    expect(reviewModeRefusal('saving', null)).toBeNull();
  });

  it('is stated ahead of every other header-action cause', () => {
    // Unsaved AND three errors AND review mode: the sentence names the one the trader cannot
    // clear by editing, because in review mode the fields are read-only.
    expect(
      headerActionRefusal('backtesting', {
        unsaved: true,
        errorCount: 3,
        reviewMinimumWidthPx: REVIEW_MODE_MIN_WIDTH_PX,
      }),
    ).toContain(`${REVIEW_MODE_MIN_WIDTH_PX}px wide before backtesting`);
  });

  it('leaves task 24.8’s sentences exactly as they were outside review mode', () => {
    expect(headerActionRefusal('backtesting', { unsaved: true, errorCount: 0 })).toBe(
      'Save this strategy before backtesting',
    );
    expect(headerActionRefusal('deploying', { unsaved: false, errorCount: 2 })).toBe(
      'Fix 2 validation errors before deploying',
    );
    expect(headerActionRefusal('deploying', { unsaved: false, errorCount: 0 })).toBeNull();
  });

  it('is stated ahead of the deployed lock on the save control', () => {
    const state = {
      locked: true,
      lockReason: 'This version is deployed.',
      registryReady: true,
      nodeCount: 2,
      serializerRefused: false,
      errorCount: 0,
    };
    expect(saveRefusal(state)).toBe('This version is deployed.');
    expect(saveRefusal({ ...state, reviewMinimumWidthPx: REVIEW_MODE_MIN_WIDTH_PX })).toContain(
      `${REVIEW_MODE_MIN_WIDTH_PX}px wide before saving`,
    );
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 3. The surface at tablet width
// ═══════════════════════════════════════════════════════════════════════════

describe('below the route minimum the page is in review mode', () => {
  it('collapses the palette to a disabled trigger and refuses all three header actions', async () => {
    renderAtWidth(TABLET_PX);
    await waitForRegistryReady();

    expect(shell().dataset.reviewMode).toBe('true');

    // The palette track is closed and its trigger is on screen, disabled, and still named.
    expect(screen.getByTestId('palette').dataset.open).toBe('false');
    const trigger = screen.getByTestId('palette-trigger');
    expect(isDisabled(trigger)).toBe(true);
    expect(trigger.getAttribute('aria-label')).toContain(`${REVIEW_MODE_MIN_WIDTH_PX}px`);

    // Requirement 15.3: disabled, each with the width as its stated reason.
    for (const name of [/^Save$/, /Backtest this version/, /^Deploy$/]) {
      expect(isDisabled(button(name))).toBe(true);
    }
    const actions = screen.getByTestId('builder-header-actions').textContent;
    expect(actions).toContain(`${REVIEW_MODE_MIN_WIDTH_PX}px wide before saving`);
    expect(actions).toContain(`${REVIEW_MODE_MIN_WIDTH_PX}px wide before backtesting`);
    expect(actions).toContain(`${REVIEW_MODE_MIN_WIDTH_PX}px wide before deploying`);
  });

  it('keeps the zoom control cluster and pan, because those ARE the mode', async () => {
    renderAtWidth(TABLET_PX);
    await waitForRegistryReady();

    for (const name of ['Zoom in', 'Zoom out', 'Fit view']) {
      expect(isDisabled(button(name))).toBe(false);
    }
    // React Flow's own `<Controls />` cluster as well as the toolbar's three (§11.6), and its
    // pan surface. jsdom runs no layout and d3-zoom marks the pane with no class of its own, so
    // the pan and pinch-zoom props themselves are asserted in the source rather than here.
    expect(document.querySelector('.react-flow__controls')).not.toBeNull();
    expect(document.querySelector('.react-flow__pane')).not.toBeNull();

    // Undo and redo replay graph edits, so they go with the editing surface.
    expect(isDisabled(button('Undo'))).toBe(true);
    expect(isDisabled(button('Redo'))).toBe(true);
  });

  it('renders the guidance strip ONCE, from the shell rather than the page', async () => {
    renderAtWidth(TABLET_PX);
    await waitForRegistryReady();

    // §11.6's sentence, verbatim, split across `Alert`'s announcement and its detail.
    const matches = screen.getAllByText(
      `Review mode. Editing a strategy graph needs a screen at least ${REVIEW_MODE_MIN_WIDTH_PX}px wide.`,
    );
    expect(matches).toHaveLength(1);
    expect(screen.getAllByText('You can pan, zoom and inspect nodes here.')).toHaveLength(1);
  });

  it('is off again above the route minimum, with the author’s palette back', async () => {
    renderAtWidth(LAPTOP_PX);
    await waitForRegistryReady();

    expect(shell().dataset.reviewMode).toBe('false');
    expect(screen.getByTestId('palette').dataset.open).toBe('true');
    expect(isDisabled(screen.getByTestId('palette-trigger'))).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 4. Editing is OFF, at the write rather than at the control
// ═══════════════════════════════════════════════════════════════════════════

describe('review mode refuses every write, including the ones with no control', () => {
  it('creates no node from a drop onto the canvas', async () => {
    renderAtWidth(TABLET_PX);
    await waitForRegistryReady();

    expect(nodeCountText()).toContain('0 nodes');
    dropBlockId('ohlcv_feed');
    // Same count, and no "registry publishes no descriptor" notice either: the drop was
    // declined, not mishandled.
    expect(nodeCountText()).toContain('0 nodes');
  });

  it('sends nothing on Ctrl+S', async () => {
    renderAtWidth(TABLET_PX);
    await waitForRegistryReady();

    mockClient.post.mockClear();
    fireEvent.keyDown(window, { key: 's', ctrlKey: true });
    expect(mockClient.post).not.toHaveBeenCalled();
  });

  it('leaves the strategy name uneditable, because a save cannot carry it', async () => {
    renderAtWidth(TABLET_PX);
    await waitForRegistryReady();

    expect(isDisabled(screen.getByLabelText('Strategy name'))).toBe(true);
  });

  it('adds no undo history, because nothing it allows is an edit', async () => {
    renderAtWidth(TABLET_PX);
    await waitForRegistryReady();

    dropBlockId('ohlcv_feed');
    fireEvent.keyDown(window, { key: 'z', ctrlKey: true });
    // Still empty: the drop wrote nothing, so there was nothing for undo to restore either.
    expect(nodeCountText()).toContain('0 nodes');
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 5. The mode follows the width, both ways
// ═══════════════════════════════════════════════════════════════════════════

describe('the mode tracks the width it is derived from', () => {
  it('enters review mode when the window narrows past the route minimum, and leaves again', async () => {
    renderAtWidth(LAPTOP_PX);
    await waitForRegistryReady();
    expect(shell().dataset.reviewMode).toBe('false');

    setViewportWidth(TABLET_PX);
    await waitFor(() => expect(shell().dataset.reviewMode).toBe('true'));
    expect(isDisabled(button(/^Save$/))).toBe(true);

    setViewportWidth(LAPTOP_PX);
    await waitFor(() => expect(shell().dataset.reviewMode).toBe('false'));
    // The author's own palette toggle state survived the round trip.
    expect(screen.getByTestId('palette').dataset.open).toBe('true');
  });
});
