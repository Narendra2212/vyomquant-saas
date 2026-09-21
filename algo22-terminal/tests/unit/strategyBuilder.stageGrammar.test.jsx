/**
 * The five-stage visual grammar on the canvas (task 24.1, design.md §9.1).
 *
 * Requirements 5.1 and 1.5. What is under test is the one design problem §9.1 poses: the
 * backend serves **seven** `BlockCategory` values and Requirement 5.1 names **five** data-flow
 * stages, so the mapping is many-to-one and has to be checkable rather than plausible.
 *
 * * every served category resolves to its §9.1 stage, on a real node, through the real page —
 *   `design/semantic.test.js` checks the table, this checks that the canvas reads it;
 * * a category the frontend has never heard of resolves to the neutral sixth "Unresolved"
 *   band, draws, and shows its **real** category name. A block the backend says exists must be
 *   drawable, which is `blockRegistry.js`'s `FALLBACK_PRESENTATION` philosophy applied to the
 *   canvas (Requirement 4.11's canvas half);
 * * every node body is `surface.raised` whatever its stage, so colour is spent on the stage
 *   band edge and the validation markers only (Requirement 1.5). A per-stage body tint would
 *   rebuild the Material palette tasks 21.6 and 23.2 removed, and it is the one thing about
 *   this design that a later "let's make the stages easier to tell apart" commit would undo;
 * * the lane header strip is persistent and in a fixed left-to-right order;
 * * `edgeStrokeFor` — `line.strong` at rest, `brand` when the edge touches the selection, the
 *   severity's colour when the backend has reported on it.
 *
 * The axios instance is the only stub. `registryClient`, the palette, the canvas and
 * `canonicalGraph` are the shipped code.
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
  StrategyBuilderCanvas,
  edgeStrokeFor,
} from '../../src/pages/StrategyBuilder';
import { resetRegistryClient } from '../../src/lib/registryClient';
import { UndoRedoProvider } from '../../src/contexts/UndoRedoContext';
import { ValidationProvider } from '../../src/contexts/ValidationContext';
import { toCanonical } from '../../src/lib/canonicalGraph';
import { SEVERITY_ERROR, SEVERITY_WARNING } from '../../src/lib/graphValidation';
import { token } from '../../src/design/tokens';
import {
  CATEGORIES,
  COMPATIBILITY_MATRIX,
  DragEventStub,
  PORT_TYPES,
  ResizeObserverStub,
  descriptor,
} from './helpers/registryFixture';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

/** One block per served category, so the canvas holds all seven at once. */
const ONE_PER_CATEGORY = Object.freeze({
  DATA: 'ohlcv_feed',
  INDICATOR: 'sma',
  MATH: 'add',
  LOGIC: 'and',
  FEATURE_ENGINEERING: 'feat_lag',
  ML_DL: 'xgboost',
  ACTION: 'action_buy_market',
});

/**
 * A category no build of this frontend has ever heard of, served the way an eighth
 * `BlockCategory` would arrive: in `categories[]`, with blocks in it. The registry client
 * validates the *shape* of a category entry and not its id, which is what makes this the
 * realistic path rather than a contrived one.
 */
const UNKNOWN_CATEGORY = Object.freeze({ id: 'QUANTUM', display_name: 'Quantum', order: 8 });
const UNKNOWN_BLOCK_ID = 'quantum_annealer';

const unknownDescriptor = () =>
  descriptor(UNKNOWN_BLOCK_ID, 'DATA', {
    category: UNKNOWN_CATEGORY.id,
    display_name: 'Quantum Annealer',
    description: 'A category this frontend does not know.',
  });

const payload = ({ withUnknown = false } = {}) => {
  const blocks = CATEGORIES.map((category) => descriptor(ONE_PER_CATEGORY[category.id], category.id));
  return {
    registry_version: 'r_stage_0001',
    registry_schema_version: 1,
    port_types: [...PORT_TYPES],
    categories: [
      ...CATEGORIES.map((category) => ({ ...category })),
      ...(withUnknown ? [{ ...UNKNOWN_CATEGORY }] : []),
    ],
    blocks: withUnknown ? [...blocks, unknownDescriptor()] : blocks,
    compatibility_matrix: { ...COMPATIBILITY_MATRIX },
  };
};

const served = (options) => ({
  status: 200,
  data: payload(options),
  headers: { etag: '"r_stage_0001"' },
});

const canvasNode = (id, block) => ({
  id,
  type: block.block_id,
  position: { x: 0, y: 0 },
  data: {
    block_id: block.block_id,
    category: block.category,
    label: block.display_name,
    params: {},
    inputs: block.inputs,
    outputs: block.outputs,
    descriptor: block,
  },
});

/** A saved graph holding one node per served category, so all seven stages are on screen. */
const SEVEN_CATEGORY_GRAPH = toCanonical(
  CATEGORIES.map((category) =>
    canvasNode(`n-${category.id.toLowerCase()}`, descriptor(ONE_PER_CATEGORY[category.id], category.id)),
  ),
  [],
  { name: 'Every category' },
);

const SAVED = { id: 'stg_stage_1', name: 'Every category', graph_json: SEVEN_CATEGORY_GRAPH };

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

const renderBuilder = (props = {}) =>
  render(
    <MemoryRouter>
      <ReactFlowProvider>
        <UndoRedoProvider>
          <ValidationProvider>
            <StrategyBuilderCanvas {...props} />
          </ValidationProvider>
        </UndoRedoProvider>
      </ReactFlowProvider>
    </MemoryRouter>,
  );

const registryReady = () =>
  waitFor(() => expect(screen.getByTestId('registry-state').textContent).toContain('ready'));

/** How jsdom normalises a colour once it is on an element, so both sides compare equal. */
const asRendered = (value) => {
  const probe = document.createElement('div');
  probe.style.background = value;
  return probe.style.background;
};

/** The `PremiumNodeWrapper` body of a canvas node — React Flow's node div wraps it. */
const nodeBody = (nodeId) => document.querySelector(`[data-id="${nodeId}"]`).firstElementChild;

const stageStrip = (nodeId) =>
  document.querySelector(`[data-testid="node-stage"][data-node-id="${nodeId}"]`);

const lanes = () =>
  screen.getAllByTestId('stage-lane').map((lane) => ({
    id: lane.dataset.stageId,
    order: Number(lane.dataset.stageOrder),
    text: lane.textContent,
  }));

beforeEach(() => {
  global.ResizeObserver = ResizeObserverStub;
  window.DragEvent = DragEventStub;
  window.localStorage.clear();
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  resetRegistryClient();
  mockClient.get.mockResolvedValue(served());
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. Seven categories, five stages
// ---------------------------------------------------------------------------

describe('the seven served categories resolve to §9.1 five stages', () => {
  it('gives every category its stage number, band and real category name', async () => {
    renderBuilder({ initialStrategy: SAVED });
    await registryReady();

    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid="node-stage"]').length).toBe(7),
    );

    // The mapping, spelled out: three categories collapse into stage 2 and the other four
    // take a stage each. This is the assertion that fails if a category is ever quietly
    // re-banded.
    const expected = {
      DATA: { stage: 'MARKET_DATA', order: 1, label: 'Market data' },
      INDICATOR: { stage: 'TRANSFORM', order: 2, label: 'Transform' },
      MATH: { stage: 'TRANSFORM', order: 2, label: 'Transform' },
      FEATURE_ENGINEERING: { stage: 'TRANSFORM', order: 2, label: 'Transform' },
      LOGIC: { stage: 'LOGIC', order: 3, label: 'Logic' },
      ML_DL: { stage: 'MODEL', order: 4, label: 'Model' },
      ACTION: { stage: 'ACTION', order: 5, label: 'Action' },
    };

    for (const [category, band] of Object.entries(expected)) {
      const strip = stageStrip(`n-${category.toLowerCase()}`);
      expect(strip, category).toBeTruthy();
      expect(strip.dataset.stageId, category).toBe(band.stage);
      expect(Number(strip.dataset.stageOrder), category).toBe(band.order);
      expect(strip.dataset.category, category).toBe(category);
      // The stage number and its name are text, and the category keeps its own name beside
      // them: identity stays with the category, layout with the band.
      expect(strip.textContent, category).toContain(`${band.order} · ${band.label}`);
      expect(strip.textContent, category).toContain(category.replace(/_/g, ' '));
      // The stage icon, one per category — `Sigma` for MATH is not `Activity` for INDICATOR.
      expect(strip.querySelectorAll('svg').length, category).toBe(1);
    }

    // Five distinct stages over seven categories, which is the whole claim.
    const stages = Object.values(expected).map((band) => band.stage);
    expect(new Set(stages).size).toBe(5);
  });

  it('draws every node body on surface.raised, whatever its stage (Requirement 1.5)', async () => {
    renderBuilder({ initialStrategy: SAVED });
    await registryReady();

    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid="node-stage"]').length).toBe(7),
    );

    const backgrounds = CATEGORIES.map(
      (category) => nodeBody(`n-${category.id.toLowerCase()}`).style.background,
    );

    // One surface for all seven. If a stage ever earns a body tint, this is where it shows up.
    expect(new Set(backgrounds).size).toBe(1);
    expect(backgrounds[0]).toBe(asRendered(token.surface.raised));
  });
});

// ---------------------------------------------------------------------------
// 2. The neutral sixth band
// ---------------------------------------------------------------------------

describe('an unknown category draws in the Unresolved band rather than being hidden', () => {
  it('renders the node, with its real category name, and adds the sixth lane', async () => {
    mockClient.get.mockResolvedValue(served({ withUnknown: true }));
    renderBuilder();
    await registryReady();

    // Only the five declared lanes until something needs the sixth: an always-empty lane is
    // SB-03 in miniature.
    expect(lanes().map((lane) => lane.id)).toEqual([
      'MARKET_DATA',
      'TRANSFORM',
      'LOGIC',
      'MODEL',
      'ACTION',
    ]);

    // The palette offers a category it has never heard of (Requirement 4.11) …
    const entry = await waitFor(() => {
      const found = screen
        .queryAllByTestId('palette-block')
        .find((block) => block.dataset.blockId === UNKNOWN_BLOCK_ID);
      if (!found) throw new Error('the unknown-category block is not in the palette');
      return found;
    });
    expect(entry.dataset.category).toBe(UNKNOWN_CATEGORY.id);

    // … and dropping it draws a node rather than swallowing one.
    fireEvent.drop(screen.getByTestId('canvas'), {
      clientX: 160,
      clientY: 120,
      dataTransfer: {
        getData: (mime) => (mime === DRAG_BLOCK_ID_MIME ? UNKNOWN_BLOCK_ID : ''),
      },
    });

    const strip = await waitFor(() => {
      const found = document.querySelector('[data-testid="node-stage"]');
      if (found === null) throw new Error('the node was not drawn');
      return found;
    });

    expect(strip.dataset.stageId).toBe('UNRESOLVED');
    expect(Number(strip.dataset.stageOrder)).toBe(6);
    // The real category name, not a substitute and not a blank.
    expect(strip.dataset.category).toBe(UNKNOWN_CATEGORY.id);
    expect(strip.textContent).toContain('Unresolved');
    expect(strip.textContent).toContain(UNKNOWN_CATEGORY.id);

    // The lane appears now that it has a node to label.
    await waitFor(() => expect(lanes().map((lane) => lane.id)).toEqual([
      'MARKET_DATA',
      'TRANSFORM',
      'LOGIC',
      'MODEL',
      'ACTION',
      'UNRESOLVED',
    ]));
  });
});

// ---------------------------------------------------------------------------
// 3. The lane header strip
// ---------------------------------------------------------------------------

describe('the stage lane header strip', () => {
  it('is persistent, numbered and in a fixed left-to-right order', async () => {
    renderBuilder();
    await registryReady();

    // Present on an empty canvas: the pipeline is readable before the first block is dropped.
    expect(screen.getByTestId('stage-lane-strip')).toBeTruthy();
    expect(lanes()).toEqual([
      { id: 'MARKET_DATA', order: 1, text: '1 · Market data' },
      { id: 'TRANSFORM', order: 2, text: '2 · Transform' },
      { id: 'LOGIC', order: 3, text: '3 · Logic' },
      { id: 'MODEL', order: 4, text: '4 · Model' },
      { id: 'ACTION', order: 5, text: '5 · Action' },
    ]);

    // Which categories each lane covers, published so the strip and the nodes cannot disagree.
    const covered = screen
      .getAllByTestId('stage-lane')
      .flatMap((lane) => lane.dataset.categories.split(' '));
    expect(covered.sort()).toEqual(CATEGORIES.map((category) => category.id).sort());
  });

  it('sits above the canvas rather than over it', async () => {
    renderBuilder();
    await registryReady();

    // A sibling, not an overlay: an absolutely positioned strip would cover the top row of
    // nodes and would move the drop coordinates off the canvas's own box.
    const strip = screen.getByTestId('stage-lane-strip');
    const canvas = screen.getByTestId('canvas');
    expect(strip.contains(canvas)).toBe(false);
    expect(canvas.contains(strip)).toBe(false);
    expect(strip.compareDocumentPosition(canvas) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// 4. Edge strokes
// ---------------------------------------------------------------------------

describe('edgeStrokeFor: line.strong at rest, brand on the selection', () => {
  const edge = (extra = {}) => ({ id: 'e-1', source: 'n-a', target: 'n-b', ...extra });

  it('draws an unselected edge with line.strong', () => {
    expect(edgeStrokeFor(edge(), null)).toBe(token.line.strong);
    expect(edgeStrokeFor(edge(), undefined)).toBe(token.line.strong);
    expect(edgeStrokeFor(edge(), 'n-elsewhere')).toBe(token.line.strong);
  });

  it('draws brand when either end is the selected node', () => {
    expect(edgeStrokeFor(edge(), 'n-a')).toBe(token.brand.base);
    expect(edgeStrokeFor(edge(), 'n-b')).toBe(token.brand.base);
  });

  it('keeps a reported edge on its severity colour, selected or not', () => {
    const reported = (severity) => edge({ data: { validation: { severity, count: 1 } } });

    expect(edgeStrokeFor(reported(SEVERITY_ERROR), null)).toBe(token.status.error.fg);
    expect(edgeStrokeFor(reported(SEVERITY_WARNING), null)).toBe(token.status.warning.fg);
    // A verdict about the connection outranks where the cursor happens to be.
    expect(edgeStrokeFor(reported(SEVERITY_ERROR), 'n-a')).toBe(token.status.error.fg);
    expect(edgeStrokeFor(reported(SEVERITY_WARNING), 'n-b')).toBe(token.status.warning.fg);
  });
});
