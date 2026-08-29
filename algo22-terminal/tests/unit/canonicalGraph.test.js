/**
 * Unit tests for the single canonical graph serializer (src/lib/canonicalGraph.js).
 *
 * Covers SB-05: every node of every category survives, port handles survive, block_id and
 * category are carried rather than inferred, presentation stays in `ui`, no `exchange` key is
 * ever emitted (SB-06), and an unexpressible node raises instead of vanishing.
 *
 * The old serializer used to be imported here on purpose, to demonstrate the defect against the
 * fix. Task 3.4 deleted `src/utils/dagSerializer.js`, so what is asserted now is that the module
 * is gone and that nothing can import it again.
 */

import { describe, it, expect } from 'vitest';
import {
  toCanonical,
  fromCanonical,
  normalizeGraph,
  CanonicalGraphError,
  SCHEMA_VERSION,
  BLOCK_CATEGORIES,
} from '../../src/lib/canonicalGraph';

/**
 * A canvas node in the shape this app actually holds: React Flow `id` / `type` / `position`,
 * with the registry descriptor recorded on `data`.
 *
 * `rfType` is the React Flow renderer selector. It is varied in these tests to show that the
 * canonical serializer never reads semantics from it, while the old serializer filtered on it.
 */
const canvasNode = ({ id, blockId, category, label, params = {}, inputs = [], outputs = [], rfType, position = { x: 0, y: 0 } }) => ({
  id,
  type: rfType === undefined ? blockId : rfType,
  position,
  data: { block_id: blockId, category, label, params, inputs, outputs },
});

const port = (name, type, extra = {}) => ({ port: name, type, ...extra });

/** One graph touching all seven categories, including the three the old serializer dropped. */
const buildCanvas = (rfTypes = {}) => {
  const nodes = [
    canvasNode({
      id: 'n_data_1',
      blockId: 'ohlcv_feed',
      category: 'DATA',
      label: 'ETH/USDT 1h',
      params: { symbol: 'ETH/USDT', timeframe: '1h' },
      outputs: [port('candles', 'OHLCV_FRAME')],
      rfType: rfTypes.data,
      position: { x: 40, y: 200 },
    }),
    canvasNode({
      id: 'n_ind_1',
      blockId: 'ema',
      // Deliberately misleading display label: the old serializer read semantics out of it.
      category: 'INDICATOR',
      label: 'Buy Market',
      params: { window: 21, source: 'close' },
      inputs: [port('series', 'PRICE_SERIES', { required: true })],
      outputs: [port('value', 'SCALAR_SERIES')],
      rfType: rfTypes.indicator,
      position: { x: 280, y: 120 },
    }),
    canvasNode({
      id: 'n_math_1',
      blockId: 'subtract',
      category: 'MATH',
      label: 'Spread',
      params: {},
      inputs: [port('left', 'SCALAR_SERIES', { required: true }), port('right', 'SCALAR_SERIES', { required: true })],
      outputs: [port('value', 'SCALAR_SERIES')],
      rfType: rfTypes.math,
      position: { x: 520, y: 120 },
    }),
    canvasNode({
      id: 'n_feat_1',
      blockId: 'rolling_zscore',
      category: 'FEATURE_ENGINEERING',
      label: 'Rolling z-score',
      params: { window: 96 },
      inputs: [port('series', 'SCALAR_SERIES', { required: true })],
      outputs: [port('matrix', 'FEATURE_MATRIX')],
      rfType: rfTypes.feature,
      position: { x: 760, y: 120 },
    }),
    canvasNode({
      id: 'n_ml_1',
      blockId: 'xgboost',
      category: 'ML_DL',
      label: 'XGBoost',
      params: { confidence_threshold: 0.7 },
      inputs: [port('features', 'FEATURE_MATRIX', { required: true })],
      outputs: [port('prediction', 'PREDICTION')],
      rfType: rfTypes.ml,
      position: { x: 1000, y: 120 },
    }),
    canvasNode({
      id: 'n_logic_1',
      blockId: 'and_gate',
      category: 'LOGIC',
      label: 'AND',
      params: {},
      inputs: [port('operands', 'BOOLEAN_SERIES', { required: true, variadic: true })],
      outputs: [port('value', 'SIGNAL')],
      rfType: rfTypes.logic,
      position: { x: 1240, y: 200 },
    }),
    canvasNode({
      id: 'n_action_1',
      blockId: 'market_order',
      category: 'ACTION',
      label: 'Sell it all',
      params: { side: 'buy', quantity: 0.25, quantity_type: 'base' },
      inputs: [port('signal', 'SIGNAL', { required: true })],
      rfType: rfTypes.action,
      position: { x: 1480, y: 200 },
    }),
  ];

  const edges = [
    { id: 'e_1', source: 'n_data_1', sourceHandle: 'candles', target: 'n_ind_1', targetHandle: 'series', animated: true },
    { id: 'e_2', source: 'n_ind_1', sourceHandle: 'value', target: 'n_math_1', targetHandle: 'left' },
    { id: 'e_3', source: 'n_ind_1', sourceHandle: 'value', target: 'n_math_1', targetHandle: 'right' },
    { id: 'e_4', source: 'n_math_1', sourceHandle: 'value', target: 'n_feat_1', targetHandle: 'series' },
    { id: 'e_5', source: 'n_feat_1', sourceHandle: 'matrix', target: 'n_ml_1', targetHandle: 'features' },
    { id: 'e_6', source: 'n_ml_1', sourceHandle: 'prediction', target: 'n_logic_1', targetHandle: 'operands', data: { port_type: 'PREDICTION' } },
    { id: 'e_7', source: 'n_logic_1', sourceHandle: 'value', target: 'n_action_1', targetHandle: 'signal' },
  ];

  return { nodes, edges };
};

describe('canonicalGraph.toCanonical', () => {
  it('emits every node of every category, DATA, MATH and FEATURE_ENGINEERING included', () => {
    const { nodes, edges } = buildCanvas();

    const graph = toCanonical(nodes, edges, { name: 'All seven' });

    expect(graph.schema_version).toBe(SCHEMA_VERSION);
    expect(graph.nodes).toHaveLength(7);
    expect(graph.nodes.map((node) => node.id)).toEqual([
      'n_data_1',
      'n_ind_1',
      'n_math_1',
      'n_feat_1',
      'n_ml_1',
      'n_logic_1',
      'n_action_1',
    ]);
    expect(new Set(graph.nodes.map((node) => node.category))).toEqual(new Set(BLOCK_CATEGORIES));
    expect(graph.edges).toHaveLength(7);
    expect(Object.keys(graph)).toEqual([
      'schema_version',
      'strategy_id',
      'version',
      'name',
      'nodes',
      'edges',
      'metadata',
      'validation_state',
    ]);
  });

  it('preserves sourceHandle and targetHandle as source_port and target_port', () => {
    const { nodes, edges } = buildCanvas();

    const graph = toCanonical(nodes, edges);

    expect(graph.edges[0]).toEqual({
      id: 'e_1',
      source: 'n_data_1',
      source_port: 'candles',
      target: 'n_ind_1',
      target_port: 'series',
    });
    // The two edges into the MATH node differ only by target port. Losing the handle would
    // collapse them into the same connection.
    expect(graph.edges[1].target_port).toBe('left');
    expect(graph.edges[2].target_port).toBe('right');
    for (const edge of graph.edges) {
      expect(edge.source_port).toBeTruthy();
      expect(edge.target_port).toBeTruthy();
    }
    // The advisory port type travels in edge.data.port_type, never in React Flow's edge.type.
    expect(graph.edges[5].type).toBe('PREDICTION');
  });

  it('carries block_id and category from the descriptor and infers nothing from the label', () => {
    const { nodes, edges } = buildCanvas();

    const graph = toCanonical(nodes, edges);
    const indicator = graph.nodes.find((node) => node.id === 'n_ind_1');
    const action = graph.nodes.find((node) => node.id === 'n_action_1');

    // The INDICATOR node is labelled "Buy Market"; it stays an INDICATOR named `ema`.
    expect(indicator.block_id).toBe('ema');
    expect(indicator.category).toBe('INDICATOR');
    expect(indicator).not.toHaveProperty('indicator');
    expect(indicator).not.toHaveProperty('action');
    // The ACTION node is labelled "Sell it all" but its params say buy; the label is ignored.
    expect(action.block_id).toBe('market_order');
    expect(action.category).toBe('ACTION');
    expect(action.params.side).toBe('buy');
    expect(action).not.toHaveProperty('action');
  });

  it('keeps presentation in ui and nothing semantic', () => {
    const { nodes, edges } = buildCanvas();

    const graph = toCanonical(nodes, edges);
    const data = graph.nodes.find((node) => node.id === 'n_data_1');

    expect(data.ui).toEqual({ position: { x: 40, y: 200 }, label: 'ETH/USDT 1h' });
    expect(data.params).toEqual({ symbol: 'ETH/USDT', timeframe: '1h' });
    for (const node of graph.nodes) {
      expect(Object.keys(node.ui).sort()).toEqual(['label', 'position']);
      expect(node.ui).not.toHaveProperty('params');
      expect(node.ui).not.toHaveProperty('block_id');
      expect(node.ui).not.toHaveProperty('category');
    }

    // A semantic value hidden in `ui` would be invisible to dag_hash, so it is refused.
    const smuggled = [{ ...nodes[0], data: { ...nodes[0].data, ui: { block_id: 'ohlcv_feed' } } }];
    expect(() => toCanonical(smuggled, [])).toThrowError(/ui/);
    try {
      toCanonical(smuggled, []);
    } catch (error) {
      expect(error).toBeInstanceOf(CanonicalGraphError);
      expect(error.code).toBe('UI_SEMANTIC_FIELD');
    }
  });

  it('emits no exchange key anywhere and refuses one that is offered (SB-06)', () => {
    const { nodes, edges } = buildCanvas();

    const graph = toCanonical(nodes, edges, {
      // The name deliberately avoids the word itself, so the substring assertion below is
      // about the emitted shape and not about the strategy's title.
      name: 'Venue bound at deploy time',
      metadata: { created_by: 'user-uuid' },
    });

    expect(JSON.stringify(graph)).not.toContain('exchange');
    expect(graph).not.toHaveProperty('exchange');

    expect(() => toCanonical(nodes, edges, { exchange: 'binance' })).toThrowError(
      /exchange/,
    );
    expect(() =>
      toCanonical(nodes, edges, { metadata: { exchange: 'binance' } }),
    ).toThrowError(/exchange/);

    const withExchangeParam = [
      { ...nodes[0], data: { ...nodes[0].data, params: { ...nodes[0].data.params, exchange: 'binance' } } },
      ...nodes.slice(1),
    ];
    try {
      toCanonical(withExchangeParam, []);
      throw new Error('expected an exchange param to be refused');
    } catch (error) {
      expect(error).toBeInstanceOf(CanonicalGraphError);
      expect(error.code).toBe('EXCHANGE_FIELD_FORBIDDEN');
      expect(error.details.node).toBe('n_data_1');
    }
  });

  it('raises on a node with a missing or unknown block_id or category instead of dropping it', () => {
    const { nodes } = buildCanvas();
    const stripped = { ...nodes[2], data: { ...nodes[2].data, block_id: undefined } };

    try {
      toCanonical([stripped], []);
      throw new Error('expected a node without block_id to raise');
    } catch (error) {
      expect(error).toBeInstanceOf(CanonicalGraphError);
      expect(error.code).toBe('BLOCK_ID_MISSING');
      expect(error.details.node).toBe('n_math_1');
      expect(error.message).toContain('n_math_1');
    }

    const noCategory = { ...nodes[3], data: { ...nodes[3].data, category: undefined } };
    expect(() => toCanonical([noCategory], [])).toThrowError(/carries no category/);

    // A non-canonical category is refused rather than translated by guesswork.
    const legacyCategory = { ...nodes[1], data: { ...nodes[1].data, category: 'indicators' } };
    try {
      toCanonical([legacyCategory], []);
      throw new Error('expected a non-canonical category to raise');
    } catch (error) {
      expect(error.code).toBe('ENUM_INVALID');
      expect(error.details.permitted).toEqual([...BLOCK_CATEGORIES]);
    }
  });

  it('resolves an unhandled edge endpoint only when the port list is unambiguous', () => {
    const { nodes } = buildCanvas();
    const dataNode = nodes[0];
    const indicatorNode = nodes[1];

    // React Flow records sourceHandle = null for a node with a single unnamed handle.
    const resolvable = toCanonical([dataNode, indicatorNode], [
      { id: 'e_1', source: 'n_data_1', sourceHandle: null, target: 'n_ind_1', targetHandle: null },
    ]);
    expect(resolvable.edges[0].source_port).toBe('candles');
    expect(resolvable.edges[0].target_port).toBe('series');

    // The MATH node has two input ports, so a missing handle is genuinely ambiguous.
    const mathNode = nodes[2];
    try {
      toCanonical([indicatorNode, mathNode], [
        { id: 'e_2', source: 'n_ind_1', sourceHandle: 'value', target: 'n_math_1', targetHandle: null },
      ]);
      throw new Error('expected an ambiguous target port to raise');
    } catch (error) {
      expect(error.code).toBe('EDGE_PORT_UNRESOLVED');
      expect(error.details.available).toEqual(['left', 'right']);
    }
  });
});

describe('canonicalGraph round trip (Requirement 1.4)', () => {
  it('loses no node, parameter or port through fromCanonical then toCanonical', () => {
    const { nodes, edges } = buildCanvas();
    const graph = toCanonical(nodes, edges, {
      strategy_id: 'strategy-uuid',
      version: 'v3',
      name: 'EMA + XGB confirmation',
      metadata: { created_by: 'user-uuid', editor_layout_version: 1 },
      validation_state: 'VALID',
    });

    const canvas = fromCanonical(graph);
    const again = toCanonical(canvas.nodes, canvas.edges, canvas.envelope);

    expect(again).toEqual(graph);
    expect(again.nodes).toHaveLength(graph.nodes.length);
    for (const node of graph.nodes) {
      const returned = again.nodes.find((candidate) => candidate.id === node.id);
      expect(returned.params).toEqual(node.params);
      expect(returned.inputs).toEqual(node.inputs);
      expect(returned.outputs).toEqual(node.outputs);
    }
  });

  it('round-trips a graph handed over by the backend, and normalizeGraph is idempotent', () => {
    const backendGraph = {
      schema_version: 2,
      strategy_id: '8f2b',
      version: 'v1',
      name: 'From the API',
      nodes: [
        {
          id: 'n_data_1',
          block_id: 'ohlcv_feed',
          category: 'DATA',
          params: { symbol: 'BTC/USDT', timeframe: '15m' },
          inputs: [],
          outputs: [{ port: 'candles', type: 'OHLCV_FRAME' }],
          ui: { position: { x: 12, y: 34 }, label: 'BTC/USDT 15m', collapsed: false },
        },
        {
          id: 'n_feat_1',
          block_id: 'log_return',
          category: 'FEATURE_ENGINEERING',
          params: { periods: [1, 5, 15], nested: { alpha: 0.5 } },
          inputs: [{ port: 'frame', type: 'OHLCV_FRAME', required: true }],
          outputs: [{ port: 'matrix', type: 'FEATURE_MATRIX' }],
          ui: { position: { x: 200, y: 34 }, label: 'Log return' },
        },
      ],
      edges: [
        {
          id: 'e_1',
          source: 'n_data_1',
          source_port: 'candles',
          target: 'n_feat_1',
          target_port: 'frame',
          type: 'OHLCV_FRAME',
        },
      ],
      metadata: { created_at: '2026-08-19T13:01:00Z' },
      validation_state: 'VALID',
    };

    const normalized = normalizeGraph(backendGraph);
    expect(normalized).toEqual(backendGraph);
    expect(normalizeGraph(normalized)).toEqual(normalized);

    const canvas = fromCanonical(backendGraph);
    // The canvas form keeps the descriptor on `data`, so it can be serialized again.
    expect(canvas.nodes[1].data.block_id).toBe('log_return');
    expect(canvas.nodes[1].data.category).toBe('FEATURE_ENGINEERING');
    expect(canvas.nodes[1].data.params.periods).toEqual([1, 5, 15]);
    expect(canvas.nodes[0].position).toEqual({ x: 12, y: 34 });
    expect(canvas.nodes[0].data.collapsed).toBe(false);
    expect(canvas.edges[0].sourceHandle).toBe('candles');
    expect(canvas.edges[0].targetHandle).toBe('frame');
  });

  it('refuses a schema version it does not speak', () => {
    expect(() => fromCanonical({ schema_version: 1, nodes: [], edges: [] })).toThrowError(
      /schema_version/,
    );
    expect(() => toCanonical([], [], { schema_version: 3 })).toThrowError(/schema_version/);
  });
});

describe('the lossy serializers are gone (SB-05, task 3.4)', () => {
  it('no longer ships src/utils/dagSerializer.js, and nothing references it', async () => {
    const { existsSync, readdirSync, readFileSync, statSync } = await import('node:fs');
    const { join, resolve } = await import('node:path');

    const root = resolve(__dirname, '..', '..');
    expect(existsSync(join(root, 'src', 'utils', 'dagSerializer.js'))).toBe(false);

    const offenders = [];
    const walk = (dir) => {
      for (const entry of readdirSync(dir)) {
        if (entry === 'node_modules' || entry === 'dist' || entry === '.git') continue;
        const path = join(dir, entry);
        if (statSync(path).isDirectory()) {
          walk(path);
          continue;
        }
        if (!/\.(js|jsx|ts|tsx)$/.test(entry)) continue;
        const source = readFileSync(path, 'utf8');
        // Prose may name the deleted module — several files explain why it went. An
        // import of it is what must not exist.
        if (/(?:from\s+|import\s*\(|require\s*\()\s*['"][^'"]*dagSerializer/.test(source)) {
          offenders.push(path);
        }
      }
    };
    walk(join(root, 'src'));
    walk(join(root, 'tests'));

    expect(offenders).toEqual([]);
  });

  it('survives the canvas the old type filter dropped, whatever node.type says', () => {
    // The legacy React Flow types the old filter recognised, plus the three categories it did
    // not: DATA, MATH and FEATURE_ENGINEERING were discarded outright. `toCanonical` reads none
    // of these — it reads data.block_id / data.category.
    const legacy = buildCanvas({
      data: 'data',
      indicator: 'indicator',
      math: 'math',
      feature: 'feature',
      ml: 'mlmodel',
      logic: 'logic',
      action: 'action',
    });
    const canonicalFromLegacyTypes = toCanonical(legacy.nodes, legacy.edges);
    expect(canonicalFromLegacyTypes.nodes).toHaveLength(7);
    for (const id of ['n_data_1', 'n_math_1', 'n_feat_1']) {
      expect(canonicalFromLegacyTypes.nodes.map((node) => node.id)).toContain(id);
    }

    // And the shape the palette actually produces — node.type is the block id — which the old
    // filter dropped in its entirety.
    const { nodes, edges } = buildCanvas();
    expect(toCanonical(nodes, edges).nodes).toHaveLength(7);
  });

  it('keeps port identity and takes no semantics from the display label', () => {
    const { nodes, edges } = buildCanvas({ indicator: 'indicator', action: 'action' });
    const canonical = toCanonical(nodes, edges);

    // Every edge is port-addressed on both ends: the two distinct connections into the MATH
    // node stay distinguishable, which the old serializer made impossible.
    for (const edge of canonical.edges) {
      expect(typeof edge.source_port).toBe('string');
      expect(typeof edge.target_port).toBe('string');
    }

    // The EMA node is labelled "Buy Market" and the ACTION node is labelled "Sell it all". The
    // old serializer read those labels as semantics and wrote `indicator: "buy market"` and
    // `action: "sell"`. The canonical serializer reads the descriptor.
    expect(canonical.nodes.find((node) => node.id === 'n_ind_1').block_id).toBe('ema');
    expect(canonical.nodes.find((node) => node.id === 'n_action_1').params.side).toBe('buy');
  });
});
