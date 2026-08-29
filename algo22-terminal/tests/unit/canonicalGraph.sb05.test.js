/**
 * The SB-05 serializer regression test (task 3.14, Requirements 1.1, 1.2, 1.3).
 *
 * The defect
 * ----------
 * `src/utils/dagSerializer.js` walked the canvas and kept a node only if its React Flow
 * `node.type` matched a short allow-list of renderer names. Three categories were absent from
 * that list, so **every DATA, MATH and FEATURE_ENGINEERING node was silently discarded** — no
 * error, no warning, no marker on the canvas. The graph that reached the backend was a
 * different strategy from the one on screen: a data source with no data node, arithmetic with
 * no arithmetic, features with no features. Worse, what survived was re-derived from
 * `data.label`, the *display* string, so renaming a node changed what the backend was told the
 * node meant. A second inline serializer in `StrategyBuilder.jsx` shadowed the import and
 * emitted a third shape again. Task 3.4 deleted both; `src/lib/canonicalGraph.js` (task 3.3)
 * is the only serializer left.
 *
 * Why this suite fails against the deleted behaviour
 * --------------------------------------------------
 * The deleted file is deliberately **not** restored to watch a test go red — restoring a
 * removed lossy serializer to demonstrate that it is lossy would be re-shipping the defect.
 * The argument is made here and pinned by assertions on the exact properties the old filter
 * violated:
 *
 * * The canvas below holds 9 nodes, 5 of which are DATA, MATH or FEATURE_ENGINEERING. The old
 *   filter emitted 4. `toCanonical` emits 9 and the node-set assertion is an equality, so a
 *   filter reappearing anywhere fails it (Requirement 1.1).
 * * Every edge is asserted to carry the *exact* `sourceHandle` / `targetHandle` it was drawn
 *   with. The old shape had no port fields at all, and the two edges from `n_ind_1.value` into
 *   `n_math_1.a` and `n_math_1.b` collapsed into one indistinguishable connection
 *   (Requirement 1.2).
 * * `node.type` is varied across the legacy renderer names the old filter switched on, and the
 *   emitted graph is asserted byte-identical. The old serializer's output changed completely;
 *   this one does not read `node.type` at all (Requirement 1.3).
 * * Labels are made deliberately misleading and then removed entirely, and the emitted graph
 *   is asserted identical apart from `ui.label`. The old serializer read `data.label` as
 *   semantics, so its output changed. And a node carrying *only* a label raises
 *   `BLOCK_ID_MISSING` instead of being interpreted — the serializer has no label fallback to
 *   fall back to.
 *
 * How this differs from `canonicalGraph.test.js`
 * ---------------------------------------------
 * That file is the serializer's own unit suite and builds canvas nodes by hand. This one is
 * the *defect* gate and drives the real production path end to end: the block ids and
 * descriptors come from the shared registry harness (`helpers/registryFixture.jsx`, the real
 * 100-block backend catalogue), and the canvas nodes are built by the shipped palette function
 * `createNodeFromDescriptor` — the same call the drop handler makes. Nothing here hand-writes
 * a node shape, because SB-05 lived in the seam between what the palette recorded on a node
 * and what the serializer read back off it, and a hand-written node hides that seam.
 *
 * Nothing is mocked. `descriptor()` is wire data; `createNodeFromDescriptor`, `toCanonical`
 * and `fromCanonical` are the shipped code.
 */

import { describe, it, expect } from 'vitest';

import { REGISTRY_BLOCK_IDS, descriptor } from './helpers/registryFixture';
import { createNodeFromDescriptor } from '../../src/pages/StrategyBuilder';
import {
  BLOCK_CATEGORIES,
  CanonicalGraphError,
  SCHEMA_VERSION,
  fromCanonical,
  toCanonical,
} from '../../src/lib/canonicalGraph';

// ---------------------------------------------------------------------------
// The canvas
// ---------------------------------------------------------------------------

/**
 * One strategy touching all seven categories, described the way the palette describes it: a
 * block id the backend registry actually publishes, plus the params an author would have set.
 *
 * The three categories the old filter discarded are `DATA`, `MATH` and `FEATURE_ENGINEERING`:
 * `n_data_1`, `n_math_1`, `n_math_2`, `n_feat_1` and `n_feat_2`. Five of nine nodes.
 */
const CANVAS_BLOCKS = Object.freeze([
  {
    id: 'n_data_1',
    blockId: 'ohlcv_feed',
    category: 'DATA',
    params: { symbol: 'ETH/USDT', timeframe: '1h' },
    position: { x: 40, y: 240 },
  },
  { id: 'n_ind_1', blockId: 'sma', category: 'INDICATOR', position: { x: 280, y: 120 } },
  { id: 'n_math_1', blockId: 'add', category: 'MATH', position: { x: 520, y: 120 } },
  { id: 'n_math_2', blockId: 'subtract', category: 'MATH', position: { x: 760, y: 120 } },
  {
    id: 'n_feat_1',
    blockId: 'feat_lag',
    category: 'FEATURE_ENGINEERING',
    params: { window: 5 },
    position: { x: 280, y: 400 },
  },
  {
    id: 'n_feat_2',
    blockId: 'feat_concat',
    category: 'FEATURE_ENGINEERING',
    position: { x: 520, y: 400 },
  },
  {
    id: 'n_ml_1',
    blockId: 'xgboost',
    category: 'ML_DL',
    params: { confidence_threshold: 0.66 },
    position: { x: 760, y: 400 },
  },
  { id: 'n_logic_1', blockId: 'gt', category: 'LOGIC', position: { x: 1000, y: 260 } },
  {
    id: 'n_action_1',
    blockId: 'action_buy_market',
    category: 'ACTION',
    params: { quantity: 0.25, quantity_type: 'BASE' },
    position: { x: 1240, y: 260 },
  },
]);

/**
 * The connections, addressed by port on both ends and written out here as the four-tuple the
 * canonical edge must reproduce exactly.
 *
 * `e_ind_math_a` and `e_ind_math_b` leave the *same* output port and arrive at two *different*
 * input ports of the same node. Drop the handles and they are one connection, which is
 * precisely what the old shape did to them.
 */
const CANVAS_EDGES = Object.freeze([
  ['e_data_ind', 'n_data_1', 'candles', 'n_ind_1', 'series'],
  ['e_data_feat', 'n_data_1', 'candles', 'n_feat_1', 'frame'],
  ['e_ind_math_a', 'n_ind_1', 'value', 'n_math_1', 'a'],
  ['e_ind_math_b', 'n_ind_1', 'value', 'n_math_1', 'b'],
  ['e_math_math', 'n_math_1', 'value', 'n_math_2', 'a'],
  ['e_feat_feat', 'n_feat_1', 'features', 'n_feat_2', 'frame'],
  ['e_feat_ml', 'n_feat_2', 'features', 'n_ml_1', 'features'],
  ['e_ml_logic', 'n_ml_1', 'prediction', 'n_logic_1', 'left'],
  ['e_math_logic', 'n_math_2', 'value', 'n_logic_1', 'right'],
  ['e_logic_action', 'n_logic_1', 'value', 'n_action_1', 'signal'],
]);

/** The three categories the deleted type filter discarded. */
const DROPPED_CATEGORIES = Object.freeze(['DATA', 'MATH', 'FEATURE_ENGINEERING']);

/** The registry descriptor for one canvas entry — the object the palette stamps on the node. */
const descriptorFor = (block) => descriptor(block.blockId, block.category);

/**
 * Build the canvas through the shipped palette path.
 *
 * @param {object} [overrides]
 * @param {(block: object) => string|undefined} [overrides.rfType] React Flow renderer selector
 *   to force onto each node, to prove no semantics are read from it.
 * @param {(block: object) => unknown} [overrides.label] Display label to force onto each node.
 *   Returning `undefined` removes the label.
 */
const buildCanvas = ({ rfType, label } = {}) => {
  const nodes = CANVAS_BLOCKS.map((block) => {
    const node = createNodeFromDescriptor(descriptorFor(block), {
      id: block.id,
      position: { ...block.position },
    });
    // The author's own parameter values, set through the inspector.
    node.data.params = { ...node.data.params, ...(block.params || {}) };
    if (rfType) node.type = rfType(block);
    if (label) {
      const forced = label(block);
      if (forced === undefined) delete node.data.label;
      else node.data.label = forced;
    }
    return node;
  });

  const edges = CANVAS_EDGES.map(([id, source, sourceHandle, target, targetHandle]) => ({
    id,
    source,
    sourceHandle,
    target,
    targetHandle,
    // React Flow edge presentation. It has no canonical home and must not leak into the graph.
    type: 'smoothstep',
    animated: true,
    style: { stroke: '#26A69A' },
  }));

  return { nodes, edges };
};

const ENVELOPE = Object.freeze({
  strategy_id: 'strategy-uuid',
  version: 'v4',
  name: 'SMA spread with an XGB gate',
  metadata: { created_by: 'user-uuid' },
});

const serialize = (options) => {
  const { nodes, edges } = buildCanvas(options);
  return toCanonical(nodes, edges, { ...ENVELOPE });
};

const nodeById = (graph, id) => graph.nodes.find((node) => node.id === id);

const edgeTuples = (graph) =>
  graph.edges.map((edge) => [edge.id, edge.source, edge.source_port, edge.target, edge.target_port]);

/** The graph with every display label removed, so two labellings can be compared. */
const withoutLabels = (graph) => ({
  ...graph,
  nodes: graph.nodes.map((node) => {
    const ui = { ...node.ui };
    delete ui.label;
    return { ...node, ui };
  }),
});

// ---------------------------------------------------------------------------
// Requirement 1.1 — every node survives
// ---------------------------------------------------------------------------

describe('SB-05: every node reaches the canonical graph (Requirement 1.1)', () => {
  it('is built from block ids the backend registry actually publishes', () => {
    // A fixture of invented ids could not show that the serializer carries what the engine
    // can run, so the catalogue is checked before it is relied on.
    for (const block of CANVAS_BLOCKS) {
      expect(REGISTRY_BLOCK_IDS[block.category]).toContain(block.blockId);
    }
    expect(new Set(CANVAS_BLOCKS.map((block) => block.category))).toEqual(
      new Set(BLOCK_CATEGORIES),
    );
  });

  it('emits the whole canvas — the node set is an equality, not a filtered subset', () => {
    const { nodes, edges } = buildCanvas();

    const graph = toCanonical(nodes, edges, { ...ENVELOPE });

    expect(graph.schema_version).toBe(SCHEMA_VERSION);
    expect(graph.nodes).toHaveLength(nodes.length);
    expect(graph.nodes.map((node) => node.id)).toEqual(CANVAS_BLOCKS.map((block) => block.id));
    expect(graph.edges).toHaveLength(edges.length);
  });

  it('keeps every DATA, MATH and FEATURE_ENGINEERING node the old filter discarded', () => {
    const graph = serialize();

    const emitted = graph.nodes.filter((node) => DROPPED_CATEGORIES.includes(node.category));
    expect(emitted.map((node) => node.id)).toEqual([
      'n_data_1',
      'n_math_1',
      'n_math_2',
      'n_feat_1',
      'n_feat_2',
    ]);
    // Five of nine. The old serializer emitted four nodes for this canvas.
    expect(emitted).toHaveLength(5);
    expect(graph.nodes).toHaveLength(9);

    for (const category of DROPPED_CATEGORIES) {
      expect(graph.nodes.filter((node) => node.category === category).length).toBeGreaterThan(0);
    }
    // All seven categories, so nothing is category-gated in either direction.
    expect(new Set(graph.nodes.map((node) => node.category))).toEqual(new Set(BLOCK_CATEGORIES));
  });

  it('carries each node\u2019s params and both port lists through untouched', () => {
    const graph = serialize();

    for (const block of CANVAS_BLOCKS) {
      const node = nodeById(graph, block.id);
      const spec = descriptorFor(block);

      for (const [key, value] of Object.entries(block.params || {})) {
        expect(node.params[key]).toEqual(value);
      }
      // Port names and types survive in declaration order, both directions. (`required: false`
      // and empty `description` normalize to absent, as the backend's `Port.to_dict` does.)
      expect(node.inputs.map((port) => port.port)).toEqual(spec.inputs.map((port) => port.port));
      expect(node.inputs.map((port) => port.type)).toEqual(spec.inputs.map((port) => port.type));
      expect(node.outputs.map((port) => port.port)).toEqual(spec.outputs.map((port) => port.port));
      expect(node.outputs.map((port) => port.type)).toEqual(spec.outputs.map((port) => port.type));
      for (const port of spec.inputs.filter((entry) => entry.required)) {
        expect(node.inputs.find((entry) => entry.port === port.port).required).toBe(true);
      }
    }

    // The FEATURE_ENGINEERING nodes are the ones with the most to lose: dropped outright before,
    // and they are the only source of FEATURE_MATRIX for the model node.
    expect(nodeById(graph, 'n_feat_1').params).toEqual({ window: 5 });
    expect(nodeById(graph, 'n_feat_2').outputs).toEqual([
      { port: 'features', type: 'FEATURE_MATRIX' },
    ]);
  });

  it('survives the wire and a canvas round trip with every node still present', () => {
    const graph = serialize();

    // The wire form (Requirement 1.5's client half): JSON must not change the graph.
    expect(JSON.parse(JSON.stringify(graph))).toEqual(graph);

    const canvas = fromCanonical(graph);
    expect(canvas.nodes).toHaveLength(9);
    expect(toCanonical(canvas.nodes, canvas.edges, canvas.envelope)).toEqual(graph);
  });
});

// ---------------------------------------------------------------------------
// Requirement 1.2 — every port handle survives
// ---------------------------------------------------------------------------

describe('SB-05: every port handle survives (Requirement 1.2)', () => {
  it('emits each edge with the exact source and target handle it was drawn with', () => {
    const graph = serialize();

    expect(edgeTuples(graph)).toEqual(CANVAS_EDGES.map((edge) => [...edge]));
    for (const edge of graph.edges) {
      expect(typeof edge.source_port).toBe('string');
      expect(typeof edge.target_port).toBe('string');
      expect(edge.source_port).not.toBe('');
      expect(edge.target_port).not.toBe('');
    }
  });

  it('keeps two connections from one output port to two input ports distinguishable', () => {
    const graph = serialize();

    const intoMath = graph.edges.filter((edge) => edge.target === 'n_math_1');
    expect(intoMath).toHaveLength(2);
    expect(intoMath.map((edge) => edge.source_port)).toEqual(['value', 'value']);
    expect(intoMath.map((edge) => edge.target_port)).toEqual(['a', 'b']);

    // Port-blind, these two are the same connection — which is how the old shape lost one of
    // the two operands of every binary MATH block.
    const portBlind = new Set(graph.edges.map((edge) => `${edge.source}->${edge.target}`));
    const portAddressed = new Set(
      graph.edges.map(
        (edge) => `${edge.source}:${edge.source_port}->${edge.target}:${edge.target_port}`,
      ),
    );
    expect(portAddressed.size).toBe(graph.edges.length);
    expect(portBlind.size).toBeLessThan(portAddressed.size);
  });

  it('keeps a fan-out from one output port to two different nodes', () => {
    const graph = serialize();

    const fromData = graph.edges.filter((edge) => edge.source === 'n_data_1');
    expect(fromData.map((edge) => edge.source_port)).toEqual(['candles', 'candles']);
    expect(fromData.map((edge) => edge.target)).toEqual(['n_ind_1', 'n_feat_1']);
  });

  it('addresses every edge to a port the endpoint node actually declares', () => {
    const graph = serialize();
    const ports = new Map(
      graph.nodes.map((node) => [
        node.id,
        {
          inputs: node.inputs.map((port) => port.port),
          outputs: node.outputs.map((port) => port.port),
        },
      ]),
    );

    for (const edge of graph.edges) {
      expect(ports.get(edge.source).outputs).toContain(edge.source_port);
      expect(ports.get(edge.target).inputs).toContain(edge.target_port);
    }
  });

  it('carries no React Flow edge presentation into the graph', () => {
    const graph = serialize();

    for (const edge of graph.edges) {
      expect(Object.keys(edge).sort()).toEqual([
        'id',
        'source',
        'source_port',
        'target',
        'target_port',
      ]);
      // `smoothstep` is a renderer name, not a port type. It must never be mistaken for one.
      expect(edge.type).toBeUndefined();
    }
    expect(JSON.stringify(graph)).not.toContain('smoothstep');
  });

  it('restores every handle on the way back to the canvas', () => {
    const canvas = fromCanonical(serialize());

    expect(
      canvas.edges.map((edge) => [edge.id, edge.source, edge.sourceHandle, edge.target, edge.targetHandle]),
    ).toEqual(CANVAS_EDGES.map((edge) => [...edge]));
  });
});

// ---------------------------------------------------------------------------
// Requirement 1.3 — semantics come from the descriptor, never from display text
// ---------------------------------------------------------------------------

describe('SB-05: no node semantics are derived from data.label (Requirement 1.3)', () => {
  it('copies block_id and category from the descriptor the palette recorded', () => {
    const graph = serialize();

    for (const block of CANVAS_BLOCKS) {
      const node = nodeById(graph, block.id);
      expect(node.block_id).toBe(block.blockId);
      expect(node.category).toBe(block.category);
      // The canonical node shape, and nothing invented alongside it: the old serializer wrote
      // label-derived `indicator` / `action` keys next to these.
      expect(Object.keys(node).sort()).toEqual([
        'block_id',
        'category',
        'id',
        'inputs',
        'outputs',
        'params',
        'ui',
      ]);
    }
  });

  it('emits an identical graph when every label is replaced with a misleading one', () => {
    // Each label now names a different block, in a different category, than the node is.
    const misleading = {
      n_data_1: 'Sell Market',
      n_ind_1: 'Buy Limit',
      n_math_1: 'ohlcv_feed',
      n_math_2: 'CatBoost',
      n_feat_1: 'Take Profit',
      n_feat_2: 'RSI',
      n_ml_1: 'Add',
      n_logic_1: 'Feature Concat',
      n_action_1: 'Simple Moving Average',
    };

    const honest = serialize();
    const lying = serialize({ label: (block) => misleading[block.id] });

    // Every semantic field is identical; the label is presentation and lives only in `ui`.
    expect(withoutLabels(lying)).toEqual(withoutLabels(honest));
    expect(nodeById(lying, 'n_math_1').block_id).toBe('add');
    expect(nodeById(lying, 'n_math_1').category).toBe('MATH');
    expect(nodeById(lying, 'n_action_1').category).toBe('ACTION');
    expect(nodeById(lying, 'n_ml_1').ui.label).toBe('Add');
    expect(nodeById(lying, 'n_ml_1').block_id).toBe('xgboost');
  });

  it('emits an identical graph when every label is blank, or absent altogether', () => {
    const honest = serialize();

    expect(withoutLabels(serialize({ label: () => '' }))).toEqual(withoutLabels(honest));

    const unlabelled = serialize({ label: () => undefined });
    expect(withoutLabels(unlabelled)).toEqual(withoutLabels(honest));
    for (const node of unlabelled.nodes) {
      expect(node.ui).not.toHaveProperty('label');
      expect(node.block_id).toBeTruthy();
      expect(node.category).toBeTruthy();
    }
  });

  it('keeps the label in ui, which is excluded from the identity hash', () => {
    const graph = serialize();

    for (const block of CANVAS_BLOCKS) {
      const node = nodeById(graph, block.id);
      expect(Object.keys(node.ui).sort()).toEqual(['label', 'position']);
      expect(node.ui.position).toEqual(block.position);
      expect(node.params).not.toHaveProperty('label');
    }
  });

  it('reads no semantics from the React Flow renderer type either', () => {
    const graph = serialize();

    // The renderer names the old filter switched on, plus a nonsense one. `node.type` is a
    // renderer selector with no canonical home, so none of this can change the output.
    const legacyTypes = {
      DATA: 'data',
      INDICATOR: 'indicator',
      MATH: 'math',
      LOGIC: 'logic',
      FEATURE_ENGINEERING: 'feature',
      ML_DL: 'mlmodel',
      ACTION: 'action',
    };

    expect(serialize({ rfType: (block) => legacyTypes[block.category] })).toEqual(graph);
    expect(serialize({ rfType: () => 'custom' })).toEqual(graph);
    expect(serialize({ rfType: () => undefined })).toEqual(graph);
  });

  it('changing block_id or category — and only that — changes the graph', () => {
    // The converse of the label assertions: what the label cannot do, the descriptor fields
    // must. Otherwise "the label is ignored" would be satisfiable by ignoring everything.
    const graph = serialize();

    const { nodes, edges } = buildCanvas();
    const rebranded = nodes.map((node) =>
      node.id === 'n_ind_1'
        ? { ...node, data: { ...node.data, block_id: 'ema', label: node.data.label } }
        : node,
    );
    const recategorised = nodes.map((node) =>
      node.id === 'n_feat_1'
        ? { ...node, data: { ...node.data, category: 'INDICATOR' } }
        : node,
    );

    const withOtherBlock = toCanonical(rebranded, edges, { ...ENVELOPE });
    expect(withOtherBlock).not.toEqual(graph);
    expect(nodeById(withOtherBlock, 'n_ind_1').block_id).toBe('ema');
    expect(nodeById(withOtherBlock, 'n_ind_1').ui.label).toBe(nodeById(graph, 'n_ind_1').ui.label);

    const withOtherCategory = toCanonical(recategorised, edges, { ...ENVELOPE });
    expect(withOtherCategory).not.toEqual(graph);
    expect(nodeById(withOtherCategory, 'n_feat_1').category).toBe('INDICATOR');
  });
});

// ---------------------------------------------------------------------------
// An unstampable node raises. It is never dropped, and never guessed at.
// ---------------------------------------------------------------------------

describe('SB-05: a node the palette did not stamp raises rather than vanishing', () => {
  /** The canvas with one node's `data` replaced wholesale. */
  const canvasWithBrokenNode = (id, data) => {
    const { nodes, edges } = buildCanvas();
    return {
      nodes: nodes.map((node) => (node.id === id ? { ...node, data } : node)),
      edges,
    };
  };

  it('raises BLOCK_ID_MISSING for a node carrying nothing but a display label', () => {
    // This is the load-bearing case. A node like this is exactly what the old serializer read
    // its semantics out of, and there is deliberately no label fallback to fall back to.
    const { nodes, edges } = canvasWithBrokenNode('n_math_1', { label: 'Add' });

    try {
      toCanonical(nodes, edges, { ...ENVELOPE });
      throw new Error('expected an unstamped node to raise');
    } catch (error) {
      expect(error).toBeInstanceOf(CanonicalGraphError);
      expect(error.code).toBe('BLOCK_ID_MISSING');
      expect(error.details.node).toBe('n_math_1');
      expect(error.message).toContain('n_math_1');
      // The message says where the block id has to come from, because the fix is a palette fix.
      expect(error.message).toContain('block_id');
    }
  });

  it('raises for an unstamped node in each of the three dropped categories', () => {
    for (const id of ['n_data_1', 'n_math_2', 'n_feat_2']) {
      const stripped = canvasWithBrokenNode(id, { label: 'Something', params: {} });
      expect(() => toCanonical(stripped.nodes, stripped.edges, { ...ENVELOPE })).toThrowError(
        CanonicalGraphError,
      );

      try {
        toCanonical(stripped.nodes, stripped.edges, { ...ENVELOPE });
      } catch (error) {
        expect(error.code).toBe('BLOCK_ID_MISSING');
        expect(error.details.node).toBe(id);
      }
    }
  });

  it('raises CATEGORY_MISSING when the block id is stamped and the category is not', () => {
    const { nodes, edges } = buildCanvas();
    const halfStamped = nodes.map((node) =>
      node.id === 'n_feat_1'
        ? { ...node, data: { ...node.data, category: undefined, descriptor: undefined } }
        : node,
    );

    try {
      toCanonical(halfStamped, edges, { ...ENVELOPE });
      throw new Error('expected a node without a category to raise');
    } catch (error) {
      expect(error).toBeInstanceOf(CanonicalGraphError);
      expect(error.code).toBe('CATEGORY_MISSING');
      expect(error.details.node).toBe('n_feat_1');
      expect(error.details.block_id).toBe('feat_lag');
    }
  });

  it('raises rather than emitting a graph that is missing a node', () => {
    // The distinction that matters: the failure mode is now loud. There is no partial graph to
    // inspect, so there is no way for a caller to receive 8 nodes when the author drew 9.
    const { nodes, edges } = canvasWithBrokenNode('n_data_1', { label: 'ETH/USDT 1h' });

    let graph = null;
    try {
      graph = toCanonical(nodes, edges, { ...ENVELOPE });
    } catch (error) {
      expect(error.code).toBe('BLOCK_ID_MISSING');
    }
    expect(graph).toBeNull();
  });
});
