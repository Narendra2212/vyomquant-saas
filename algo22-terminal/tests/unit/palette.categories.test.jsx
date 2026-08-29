/**
 * The SB-03 and SB-04 palette regression tests (task 3.13,
 * Requirements 4.2, 4.3, 4.4, 4.5, 4.11).
 *
 * Two defects, one root cause
 * ---------------------------
 * SB-03: `FEATURE_ENGINEERING` was a declared category with a rendered palette section and
 * not one block in it — permanently empty, while `feature_engineering.py` computed dozens of
 * features. SB-04: the palette offered seven indicators and six models, so `wma`, `hma`,
 * `catboost` and `autoencoder` were implemented in the engine and unselectable in the UI.
 * Both came from the same place: `blockRegistry.js` was a hand-maintained second catalogue,
 * and `getDynamicBlocksByCategory` mapped only `INDICATORS`, `ML` and `DL` onto it. A second
 * copy of a fact drifts. These two defects are what that drift looked like.
 *
 * So this suite asserts the *shape* of the fix, not a patched-up symptom:
 *
 * * all seven served categories render, in `categories[].order`, each populated — with the
 *   counts the backend actually serves, 100 blocks in total (Requirements 4.2, 4.11);
 * * FEATURE_ENGINEERING renders at least 15 entries (Requirement 4.3);
 * * `wma` and `hma` (Requirement 4.4) and `catboost` and `autoencoder` (Requirement 4.5) are
 *   *selectable*: findable, draggable, and droppable into a node the inspector can configure;
 * * the rendered set is exactly the served set — nothing added by a local list, nothing
 *   withheld by a local allow-list, and a block id no frontend module has ever heard of still
 *   renders (Requirement 4.11);
 * * `blockRegistry.js` exports zero block definitions, checked across its whole export
 *   surface rather than by reading the file and hoping.
 *
 * Why this fails against the pre-fix frontend
 * -------------------------------------------
 * Every assertion here is false on the code before tasks 3.5 and 3.6, and false for the
 * reason the defect existed:
 *
 * * the FEATURE_ENGINEERING section held zero entries, so `at least 15` fails at 0;
 * * `wma`, `hma`, `catboost` and `autoencoder` were absent from `BlockRegistry`, so no
 *   palette entry existed to drag and `handleDrop` had no descriptor to build a node from;
 * * the rendered set came from a module-local constant, so it did **not** equal the mocked
 *   response — that inequality *is* SB-04, and the assertion that the two are equal is the
 *   regression guard;
 * * `blockRegistry.js` exported `BlockRegistry` with 33 descriptors, so the export-surface
 *   scan finds block-shaped objects immediately.
 *
 * The deleted catalogue is not restored to watch that happen. The argument above is the
 * demonstration; the assertions are what keep it true.
 *
 * The axios instance is the only stub. `registryClient`, the palette, the canvas, the
 * inspector and `canonicalGraph` are the shipped code.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { screen, fireEvent, cleanup } from '@testing-library/react';

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
  DRAG_BLOCK_ID_MIME,
  REGISTRY_BLOCK_IDS,
  allDescriptors,
  descriptor,
  dropBlockId,
  installBuilderStubs,
  paletteEntry,
  registryPayload,
  renderBuilder,
  renderedBlockIds,
  served,
  startDrag,
  waitForRegistry,
} from './helpers/registryFixture';
import { resetRegistryClient } from '../../src/lib/registryClient';
import * as blockRegistry from '../../src/lib/blockRegistry';

/** The counts the backend serves. 100 blocks, every category populated. */
const SERVED_COUNTS = {
  DATA: 3,
  INDICATOR: 33,
  MATH: 18,
  LOGIC: 14,
  FEATURE_ENGINEERING: 15,
  ML_DL: 8,
  ACTION: 9,
};

/** The four blocks SB-04 made unreachable, with the category each is served in. */
const SB04_BLOCKS = [
  { blockId: 'wma', category: 'INDICATOR', requirement: '4.4' },
  { blockId: 'hma', category: 'INDICATOR', requirement: '4.4' },
  { blockId: 'catboost', category: 'ML_DL', requirement: '4.5' },
  { blockId: 'autoencoder', category: 'ML_DL', requirement: '4.5' },
];

const section = (categoryId) =>
  screen.queryAllByTestId('palette-category').find((el) => el.dataset.categoryId === categoryId);

const entriesIn = (categoryId) =>
  [...section(categoryId).querySelectorAll('[data-testid="palette-block"]')].map(
    (entry) => entry.dataset.blockId,
  );

beforeEach(() => {
  installBuilderStubs();
  mockClient.get.mockReset();
  mockClient.post.mockReset();
  mockClient.put.mockReset();
  resetRegistryClient();
  mockClient.get.mockResolvedValue(served());
  // The validation debounce fires on canvas edits; answer it so nothing rejects unhandled.
  mockClient.post.mockResolvedValue({
    valid: true,
    dag_hash: 'dag_fixture',
    validation_state: 'VALID',
    errors: [],
    warnings: [],
    summary: { node_count: 1, edge_count: 0, warmup_bars: 14 },
  });
});

afterEach(() => {
  cleanup();
  resetRegistryClient();
});

// ---------------------------------------------------------------------------
// 1. Seven categories, all populated (Requirements 4.2, 4.11)
// ---------------------------------------------------------------------------

describe('all seven categories render populated from the served registry', () => {
  it('renders one section per served category, in categories[].order', async () => {
    renderBuilder();
    await waitForRegistry('ready');

    const sections = screen.getAllByTestId('palette-category');
    expect(sections.map((el) => el.dataset.categoryId)).toEqual(CATEGORIES.map((c) => c.id));
    expect(sections.map((el) => Number(el.dataset.categoryOrder))).toEqual([1, 2, 3, 4, 5, 6, 7]);
    // The display name the backend serves, not one the frontend invents.
    for (const category of CATEGORIES) {
      expect(section(category.id).textContent).toContain(category.display_name);
    }
  });

  it('populates every one of the seven, with the counts the backend serves', async () => {
    renderBuilder();
    await waitForRegistry('ready');

    for (const category of CATEGORIES) {
      const rendered = entriesIn(category.id);
      // Requirement 4.2: no category is empty. SB-03 was one that always was.
      expect(rendered.length).toBeGreaterThan(0);
      expect(rendered).toEqual([...REGISTRY_BLOCK_IDS[category.id]]);
      expect(rendered).toHaveLength(SERVED_COUNTS[category.id]);
      // The header count agrees with the entries beneath it.
      expect(
        section(category.id).querySelector('[data-testid="palette-category-count"]').textContent,
      ).toBe(String(SERVED_COUNTS[category.id]));
    }

    expect(renderedBlockIds()).toHaveLength(100);
  });

  it('renders at least 15 FEATURE_ENGINEERING entries, each one selectable', async () => {
    renderBuilder();
    await waitForRegistry('ready');

    const features = entriesIn('FEATURE_ENGINEERING');
    // Requirement 4.3, and the exact assertion SB-03 fails at zero.
    expect(features.length).toBeGreaterThanOrEqual(15);
    expect(new Set(features).size).toBe(features.length);

    // Every entry is a drag source, so "populated" means usable and not merely listed.
    for (const blockId of features) {
      const entry = paletteEntry(blockId);
      expect(entry.getAttribute('draggable')).toBe('true');
      expect(entry.dataset.category).toBe('FEATURE_ENGINEERING');
    }
  });
});

// ---------------------------------------------------------------------------
// 2. The four SB-04 blocks are selectable (Requirements 4.4, 4.5)
// ---------------------------------------------------------------------------

describe('wma, hma, catboost and autoencoder are selectable', () => {
  it.each(SB04_BLOCKS)(
    'offers $blockId in $category and drags it by block id (Requirement $requirement)',
    async ({ blockId, category }) => {
      renderBuilder();
      await waitForRegistry('ready');

      const entry = paletteEntry(blockId);
      expect(entry).toBeTruthy();
      expect(entry.dataset.category).toBe(category);
      expect(entry.textContent).toContain(blockId);

      // The drag carries the block id, which is what the drop resolves against the registry.
      const dragged = startDrag(entry);
      expect(dragged[DRAG_BLOCK_ID_MIME]).toBe(blockId);
    },
  );

  it.each(SB04_BLOCKS)('drops $blockId onto the canvas as a configurable node', async ({ blockId, category }) => {
    renderBuilder();
    await waitForRegistry('ready');

    dropBlockId(blockId);

    // A node was created and selected — no "the registry publishes no descriptor" refusal.
    expect(screen.queryByTestId('canvas-notice')).toBeNull();
    expect(screen.getByTestId('status-strip').textContent).toContain('1 nodes');

    const status = await screen.findByTestId('inspector-node-status');
    expect(status.dataset.nodeId).toBeTruthy();

    // The inspector is showing this block, with the category and ports the registry serves.
    const inspector = status.closest('div');
    expect(inspector.textContent).toContain(blockId);
    expect(inspector.textContent).toContain(category);
    const chips = [...inspector.querySelectorAll('[data-testid="port-chip"]')];
    expect(chips.length).toBeGreaterThan(0);
  });

  it('finds each of them by search, across the categories the old search never looked at', async () => {
    renderBuilder();
    await waitForRegistry('ready');

    const search = screen.getByLabelText('Search blocks');
    for (const { blockId } of SB04_BLOCKS) {
      fireEvent.change(search, { target: { value: blockId } });
      expect(renderedBlockIds()).toContain(blockId);
    }
  });
});

// ---------------------------------------------------------------------------
// 3. The palette is the response, not a local list (Requirement 4.11)
// ---------------------------------------------------------------------------

describe('the rendered palette is exactly what the registry served', () => {
  it('renders every served block and not one more', async () => {
    renderBuilder();
    await waitForRegistry('ready');

    // Equality both ways: a local list would add entries, a local allow-list would drop them.
    // Under SB-04 this comparison was between the mocked response and a module constant, and
    // it did not hold.
    expect(renderedBlockIds()).toEqual([...ALL_BLOCK_IDS]);
  });

  it('offers no model block the environment does not publish', async () => {
    // Requirement 4.6: a model library absent from the running environment is omitted from the
    // response. The palette must then omit it too — a local list would keep offering it, and
    // dragging it would build a node the engine cannot execute.
    const withheld = new Set(['catboost', 'autoencoder', 'wma', 'hma']);
    const trimmed = allDescriptors().filter((block) => !withheld.has(block.block_id));
    mockClient.get.mockResolvedValue(served(registryPayload({ blocks: trimmed })));

    renderBuilder();
    await waitForRegistry('ready');

    const rendered = renderedBlockIds();
    expect(rendered).toHaveLength(96);
    for (const blockId of withheld) {
      expect(rendered).not.toContain(blockId);
      expect(paletteEntry(blockId)).toBeUndefined();
    }
    // And nothing anywhere on the page names them.
    for (const blockId of withheld) {
      expect(screen.getByTestId('palette').textContent).not.toContain(blockId);
    }
  });

  it('renders a block id no frontend module has ever heard of', async () => {
    // The frontend holds no catalogue to check a served block against, so a block added to the
    // engine reaches the palette with no frontend change at all. That is the property that
    // makes SB-03 and SB-04 structurally impossible rather than merely fixed.
    const novel = descriptor('brand_new_engine_block', 'FEATURE_ENGINEERING');
    mockClient.get.mockResolvedValue(
      served(registryPayload({ blocks: [...allDescriptors(), novel] })),
    );

    renderBuilder();
    await waitForRegistry('ready');

    expect(renderedBlockIds()).toHaveLength(101);
    const entry = paletteEntry('brand_new_engine_block');
    expect(entry).toBeTruthy();
    expect(entry.dataset.category).toBe('FEATURE_ENGINEERING');
    expect(entriesIn('FEATURE_ENGINEERING')).toHaveLength(16);
  });
});

// ---------------------------------------------------------------------------
// 4. `blockRegistry.js` exports zero block definitions
// ---------------------------------------------------------------------------

/** Keys that mark a value as a block definition rather than presentation. */
const BLOCK_SHAPED_KEYS = ['block_id', 'blockId', 'runtime_ref', 'params', 'inputs', 'outputs', 'validate'];

const isReactComponent = (value) =>
  typeof value === 'function' ||
  (typeof value === 'object' && value !== null && '$$typeof' in value);

/** Every block-shaped object reachable from `value`, reported by its export path. */
const findBlockShaped = (value, path, found = [], seen = new WeakSet()) => {
  if (value === null || typeof value !== 'object' || isReactComponent(value)) return found;
  if (seen.has(value)) return found;
  seen.add(value);

  if (!Array.isArray(value)) {
    const hit = BLOCK_SHAPED_KEYS.filter((key) => key in value);
    if (hit.length > 0) found.push(`${path} (keys: ${hit.join(', ')})`);
  }
  for (const [key, child] of Object.entries(value)) {
    findBlockShaped(child, `${path}.${key}`, found, seen);
  }
  return found;
};

/** Every string reachable from `value`. */
const reachableStrings = (value, out = new Set(), seen = new WeakSet()) => {
  if (typeof value === 'string') {
    out.add(value);
    return out;
  }
  if (value === null || typeof value !== 'object' || isReactComponent(value)) return out;
  if (seen.has(value)) return out;
  seen.add(value);
  for (const child of Object.values(value)) reachableStrings(child, out, seen);
  return out;
};

describe('blockRegistry.js exports zero block definitions', () => {
  it('exposes no block-shaped value anywhere in its export surface', () => {
    const found = Object.entries(blockRegistry).flatMap(([name, value]) =>
      findBlockShaped(value, name),
    );
    expect(found).toEqual([]);
  });

  it('names no block the registry publishes', () => {
    // Its only strings are the seven category ids, the port-type vocabulary and colours —
    // none of which is a block id. A single served block id appearing here would be a
    // catalogue growing back.
    const strings = reachableStrings({ ...blockRegistry });
    const blockIds = new Set(ALL_BLOCK_IDS);
    expect([...strings].filter((value) => blockIds.has(value))).toEqual([]);
  });

  it('offers no export a caller could mistake for a block catalogue', () => {
    for (const name of [
      'BlockRegistry',
      'BLOCK_DEFINITIONS',
      'getBlockByType',
      'getBlocksByCategory',
      'getAllBlocks',
      'getDynamicBlocksByCategory',
      'LEGACY_CATEGORY_ALIASES',
    ]) {
      expect(blockRegistry[name]).toBeUndefined();
    }
  });

  it('exports the presentation surface it is supposed to, and only that', () => {
    expect(Object.keys(blockRegistry).sort()).toEqual([
      'BlockCategories',
      'CATEGORY_PRESENTATION',
      'StreamTypes',
      'getCategoryColor',
      'getCategoryIcon',
      'getCategoryPresentation',
      'normalizeCategoryId',
    ]);

    // The two vocabularies are identity maps over ids, not descriptor tables.
    for (const map of [blockRegistry.StreamTypes, blockRegistry.BlockCategories]) {
      for (const [key, value] of Object.entries(map)) expect(value).toBe(key);
    }
    expect(Object.keys(blockRegistry.BlockCategories).sort()).toEqual(
      CATEGORIES.map((category) => category.id).sort(),
    );

    // Presentation is an icon and a colour per category. Nothing that changes meaning.
    for (const [categoryId, presentation] of Object.entries(blockRegistry.CATEGORY_PRESENTATION)) {
      expect(Object.keys(presentation).sort()).toEqual(['color', 'icon']);
      expect(blockRegistry.normalizeCategoryId(categoryId)).toBe(categoryId);
    }
  });
});
