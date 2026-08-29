/**
 * canonicalGraph.js — the single client-side graph serializer (closes SB-05).
 *
 * One shape, taken from the backend, which is authoritative:
 * `backend_app/backend/strategy_dag/schema.py` (`schema_version` 2).
 *
 *   envelope: { schema_version, strategy_id, version, name, nodes, edges,
 *               metadata, validation_state }
 *   node:     { id, block_id, category, params, inputs, outputs, ui }
 *   port:     { port, type, required?, variadic?, description? }   // key is "port"
 *   edge:     { id, source, source_port, target, target_port, type? }
 *
 * There is deliberately **no `exchange` field anywhere** in the graph (SB-06). Exchange
 * identity is bound at deployment time, never saved with a strategy.
 *
 * Rules this module exists to enforce
 * -----------------------------------
 * 1. Every canvas node is emitted, for every one of the seven categories — DATA, MATH and
 *    FEATURE_ENGINEERING included. There is no type filter and no allow-list. A node this
 *    serializer cannot express raises; it is never dropped. Dropping is the SB-05 defect.
 * 2. Port identity survives: React Flow's `sourceHandle` / `targetHandle` become
 *    `source_port` / `target_port`.
 * 3. `block_id` and `category` are *copied* from the descriptor the palette recorded on the
 *    node (`data.block_id` / `data.category`, or `data.descriptor`). Nothing is ever derived
 *    from `data.label`, from `node.type`, or from any other display string. Label-derived
 *    semantics are how the three previous serializers drifted apart.
 * 4. `ui` is presentation only and is excluded from `dag_hash`, so canvas position, display
 *    label and collapsed state live there and nowhere else. Anything semantic placed in `ui`
 *    would be invisible to the identity hash, so semantic keys in `ui` are rejected.
 *
 * Round-trip contract (Requirement 1.4)
 * -------------------------------------
 * For a canonical graph `g` produced by `toCanonical` (i.e. a normalized graph):
 *
 *     const { nodes, edges, envelope } = fromCanonical(g);
 *     toCanonical(nodes, edges, envelope)   // deep-equals g
 *
 * No node, no parameter and no port is lost in either direction. `normalizeGraph(g)` applies
 * the normalization for graphs that were not produced by `toCanonical`.
 *
 * Deliberate asymmetries — everything that does *not* round-trip, and why:
 *
 * * `ui.position` is always emitted. React Flow requires a position on every node, so a
 *   canonical node that carried no `ui.position` gains `{ x: 0, y: 0 }`. This is the only
 *   field this module adds that was not present.
 * * A port's `required` / `variadic` flags are omitted when false and `description` when
 *   empty, exactly as the backend's `Port.to_dict` does. An explicit `false` therefore
 *   normalizes to absent.
 * * `params` keys whose value is `undefined` are omitted: JSON has no `undefined`, so
 *   keeping them would make the in-memory graph differ from its wire form (Requirement 1.5).
 * * React Flow's `node.type` is a renderer selector with no canonical home. It is
 *   regenerated from `block_id` by `fromCanonical` and is never read back as semantics.
 * * React Flow's edge presentation (`animated`, `style`, `label`, the edge renderer `type`)
 *   has no canonical home and is not carried. Note that the canonical edge `type` is an
 *   advisory *port* type, not React Flow's edge renderer type; the two are never conflated —
 *   it travels in `edge.data.port_type` on the canvas side.
 */

/** The schema version this serializer speaks. */
export const SCHEMA_VERSION = 2;

/** The seven canonical block categories (`BlockCategory` in the backend schema). */
export const BLOCK_CATEGORIES = Object.freeze([
  'DATA',
  'INDICATOR',
  'MATH',
  'LOGIC',
  'FEATURE_ENGINEERING',
  'ML_DL',
  'ACTION',
]);

/** The canonical port type vocabulary (`PortType` in the backend schema). */
export const PORT_TYPES = Object.freeze([
  'OHLCV_FRAME',
  'PRICE_SERIES',
  'SCALAR_SERIES',
  'BOOLEAN_SERIES',
  'FEATURE_MATRIX',
  'PREDICTION',
  'SIGNAL',
  'TRADE_INTENT',
  'SCALAR',
]);

/** Envelope validation states (`ValidationState` in the backend schema). */
export const VALIDATION_STATES = Object.freeze(['UNVALIDATED', 'VALID', 'INVALID']);

/** Fields that must never appear anywhere in a saved graph (SB-06). */
export const FORBIDDEN_GRAPH_FIELDS = Object.freeze(['exchange']);

/**
 * Keys that may not appear in `ui`. `ui` is excluded from `dag_hash`, so a semantic value
 * hidden there would be invisible to strategy identity.
 */
export const UI_FORBIDDEN_KEYS = Object.freeze([
  'block_id',
  'category',
  'params',
  'inputs',
  'outputs',
]);

/** Every failure this module raises. Machine-readable `code`, plus targeted `details`. */
export class CanonicalGraphError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'CanonicalGraphError';
    this.code = code;
    this.details = details;
  }
}

const fail = (code, message, details) => {
  throw new CanonicalGraphError(code, message, details);
};

const isPlainObject = (value) =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const nonEmptyString = (value) =>
  typeof value === 'string' && value.trim() !== '' ? value : null;

/** First non-empty string among `candidates`, or null. Order is the precedence order. */
const firstString = (candidates) => {
  for (const candidate of candidates) {
    const value = nonEmptyString(candidate);
    if (value !== null) return value;
  }
  return null;
};

/** Deep copy of JSON-shaped data. Arrays and plain objects are cloned; `undefined` is dropped. */
const clone = (value) => {
  if (Array.isArray(value)) return value.map(clone);
  if (isPlainObject(value)) {
    const out = {};
    for (const key of Object.keys(value)) {
      if (value[key] === undefined) continue;
      out[key] = clone(value[key]);
    }
    return out;
  }
  return value;
};

const cloneObject = (value, what) => {
  if (value === undefined || value === null) return {};
  if (!isPlainObject(value)) {
    fail('SHAPE_INVALID', `${what} must be an object`, { received: typeof value });
  }
  return clone(value);
};

const assertNoForbiddenFields = (container, what, details = {}) => {
  if (!isPlainObject(container)) return;
  for (const forbidden of FORBIDDEN_GRAPH_FIELDS) {
    if (Object.prototype.hasOwnProperty.call(container, forbidden)) {
      fail(
        'EXCHANGE_FIELD_FORBIDDEN',
        `${what} carries a '${forbidden}' field. A saved strategy holds no exchange ` +
          'identity (SB-06); exchange is bound at deployment time.',
        { ...details, field: forbidden },
      );
    }
  }
};

const coerceEnum = (permitted, value, what, details) => {
  if (typeof value === 'string') {
    const candidate = value.trim().toUpperCase();
    if (permitted.includes(candidate)) return candidate;
  }
  return fail(
    'ENUM_INVALID',
    `${what} is ${JSON.stringify(value)}; permitted values are ${permitted.join(', ')}`,
    { ...details, received: value, permitted: [...permitted] },
  );
};

const coerceCoordinate = (value, what, details) => {
  if (value === undefined || value === null) return 0;
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    fail('POSITION_INVALID', `${what} must be a finite number`, {
      ...details,
      received: value,
    });
  }
  return value;
};

const normalizePosition = (position, what, details) => {
  if (position === undefined || position === null) return { x: 0, y: 0 };
  if (!isPlainObject(position)) {
    fail('POSITION_INVALID', `${what} must be an object with x and y`, {
      ...details,
      received: position,
    });
  }
  return {
    x: coerceCoordinate(position.x, `${what}.x`, details),
    y: coerceCoordinate(position.y, `${what}.y`, details),
  };
};

/**
 * Normalize one direction's port list onto the canonical wire form.
 *
 * A port entry must name its port explicitly. A bare type string (the legacy
 * `outputs: ['ohlcv']` shape) cannot name a port, so it raises rather than being given an
 * invented name — port identity is the basis of the whole type system.
 */
const normalizePorts = (raw, nodeId, direction) => {
  if (raw === undefined || raw === null) return [];
  if (!Array.isArray(raw)) {
    fail('PORTS_INVALID', `Node '${nodeId}' field '${direction}' must be a list`, {
      node: nodeId,
      direction,
      received: typeof raw,
    });
  }

  const seen = new Set();
  return raw.map((entry) => {
    if (!isPlainObject(entry)) {
      fail(
        'PORT_INVALID',
        `Node '${nodeId}' has a ${direction} entry that is not a port object. A port must ` +
          "carry an explicit 'port' name and 'type'; a bare type string cannot name a port.",
        { node: nodeId, direction, received: entry },
      );
    }

    const name = firstString([entry.port, entry.name]);
    if (name === null) {
      fail('PORT_INVALID', `Node '${nodeId}' has a ${direction} port with no 'port' name`, {
        node: nodeId,
        direction,
        received: entry,
      });
    }
    if (seen.has(name)) {
      fail('PORT_DUPLICATE', `Node '${nodeId}' declares ${direction} port '${name}' twice`, {
        node: nodeId,
        direction,
        port: name,
      });
    }
    seen.add(name);

    const port = {
      port: name,
      type: coerceEnum(PORT_TYPES, entry.type, `type of ${direction} port '${name}'`, {
        node: nodeId,
        port: name,
      }),
    };
    if (entry.required) port.required = true;
    if (entry.variadic) port.variadic = true;
    const description = nonEmptyString(entry.description);
    if (description !== null) port.description = description;
    return port;
  });
};

/**
 * Build the presentation-only `ui` object.
 *
 * Live canvas state wins over any stale copy inside `data.ui`: `node.position` is where
 * React Flow records a drag, and `data.label` is where it records a rename.
 */
const buildUi = (node, data, nodeId) => {
  const ui = cloneObject(data.ui, `ui of node '${nodeId}'`);
  for (const forbidden of UI_FORBIDDEN_KEYS) {
    if (Object.prototype.hasOwnProperty.call(ui, forbidden)) {
      fail(
        'UI_SEMANTIC_FIELD',
        `Node '${nodeId}' puts '${forbidden}' in 'ui'. 'ui' is excluded from the identity ` +
          'hash, so semantic values there would be invisible to dag_hash.',
        { node: nodeId, field: forbidden },
      );
    }
  }
  assertNoForbiddenFields(ui, `ui of node '${nodeId}'`, { node: nodeId });

  ui.position = normalizePosition(
    node.position !== undefined && node.position !== null ? node.position : ui.position,
    `position of node '${nodeId}'`,
    { node: nodeId },
  );
  if (data.label !== undefined) ui.label = clone(data.label);
  if (data.collapsed !== undefined) ui.collapsed = Boolean(data.collapsed);
  return ui;
};

/** One React Flow node to one canonical `NodeSpec`. */
const toCanonicalNode = (node) => {
  if (!isPlainObject(node)) {
    fail('NODE_INVALID', 'Every canvas node must be an object', { received: typeof node });
  }

  const id = nonEmptyString(node.id);
  if (id === null) {
    fail('NODE_ID_MISSING', 'A canvas node has no id. Node ids are minted once at creation.', {
      received: node.id,
    });
  }

  const data = isPlainObject(node.data) ? node.data : {};
  const descriptor = isPlainObject(data.descriptor) ? data.descriptor : {};

  // block_id and category are copied from the descriptor the palette recorded. Never from
  // node.type, never from data.label (SB-05).
  const blockId = firstString([
    data.block_id,
    data.blockId,
    descriptor.block_id,
    descriptor.blockId,
  ]);
  if (blockId === null) {
    fail(
      'BLOCK_ID_MISSING',
      `Node '${id}' carries no block_id. The palette must record the registry descriptor's ` +
        "block_id on the node ('data.block_id'); it is never derived from the display label " +
        'or the React Flow node type.',
      { node: id },
    );
  }

  const rawCategory = data.category !== undefined ? data.category : descriptor.category;
  if (rawCategory === undefined || rawCategory === null) {
    fail(
      'CATEGORY_MISSING',
      `Node '${id}' (block '${blockId}') carries no category. The palette must record the ` +
        "registry descriptor's category on the node ('data.category').",
      { node: id, block_id: blockId },
    );
  }
  const category = coerceEnum(BLOCK_CATEGORIES, rawCategory, `category of node '${id}'`, {
    node: id,
    block_id: blockId,
  });

  const params = cloneObject(data.params, `params of node '${id}'`);
  assertNoForbiddenFields(params, `params of node '${id}'`, { node: id, block_id: blockId });

  return {
    id,
    block_id: blockId,
    category,
    params,
    inputs: normalizePorts(
      data.inputs !== undefined ? data.inputs : descriptor.inputs,
      id,
      'inputs',
    ),
    outputs: normalizePorts(
      data.outputs !== undefined ? data.outputs : descriptor.outputs,
      id,
      'outputs',
    ),
    ui: buildUi(node, data, id),
  };
};

/**
 * Resolve an edge endpoint's port name.
 *
 * An explicit handle is always honoured. When React Flow recorded no handle — a node with a
 * single unnamed handle — the port is resolved from the node's own resolved port list, and
 * only when that list holds exactly one port. Anything ambiguous raises: an edge is never
 * addressed to a guessed port.
 */
const resolvePort = (explicit, node, direction, edgeId, endpointId) => {
  if (explicit !== null) return explicit;

  const ports = node ? (direction === 'source' ? node.outputs : node.inputs) : [];
  if (ports.length === 1) return ports[0].port;

  return fail(
    'EDGE_PORT_UNRESOLVED',
    `Edge '${edgeId}' has no ${direction === 'source' ? 'sourceHandle' : 'targetHandle'} and ` +
      `node '${endpointId}' does not have exactly one ${direction === 'source' ? 'output' : 'input'} ` +
      `port (it has ${ports.length}), so the port cannot be resolved without guessing.`,
    {
      edge: edgeId,
      node: endpointId,
      direction,
      available: ports.map((port) => port.port),
    },
  );
};

/** One React Flow edge to one canonical `EdgeSpec`. */
const toCanonicalEdge = (edge, nodeIndex) => {
  if (!isPlainObject(edge)) {
    fail('EDGE_INVALID', 'Every canvas edge must be an object', { received: typeof edge });
  }

  const id = nonEmptyString(edge.id);
  if (id === null) {
    fail('EDGE_ID_MISSING', 'A canvas edge has no id', { received: edge.id });
  }
  const source = nonEmptyString(edge.source);
  const target = nonEmptyString(edge.target);
  for (const [key, value] of [
    ['source', source],
    ['target', target],
  ]) {
    if (value === null) {
      fail('EDGE_ENDPOINT_MISSING', `Edge '${id}' has no '${key}' node`, {
        edge: id,
        field: key,
      });
    }
  }

  const canonical = {
    id,
    source,
    source_port: resolvePort(
      firstString([edge.sourceHandle, edge.source_port, edge.sourcePort]),
      nodeIndex.get(source),
      'source',
      id,
      source,
    ),
    target,
    target_port: resolvePort(
      firstString([edge.targetHandle, edge.target_port, edge.targetPort]),
      nodeIndex.get(target),
      'target',
      id,
      target,
    ),
  };

  // The canonical `type` is an advisory *port* type. React Flow's `edge.type` is a renderer
  // selector, so it is deliberately not consulted; the port type travels in `edge.data`.
  const data = isPlainObject(edge.data) ? edge.data : {};
  const rawType =
    data.port_type !== undefined
      ? data.port_type
      : data.type !== undefined
        ? data.type
        : edge.port_type;
  if (rawType !== undefined && rawType !== null && rawType !== '') {
    canonical.type = coerceEnum(PORT_TYPES, rawType, `type of edge '${id}'`, { edge: id });
  }

  return canonical;
};

/**
 * Serialize canvas state to a canonical graph (`schema_version` 2).
 *
 * @param {Array<object>} nodes React Flow nodes.
 * @param {Array<object>} edges React Flow edges.
 * @param {object} [envelope] Envelope fields: `strategy_id`, `version`, `name`, `metadata`,
 *   `validation_state`. `schema_version` may be supplied but must be 2.
 * @returns {object} The canonical graph.
 * @throws {CanonicalGraphError} When a node or edge cannot be expressed canonically. Nothing
 *   is ever dropped to make serialization succeed.
 */
export function toCanonical(nodes, edges, envelope = {}) {
  if (nodes !== undefined && nodes !== null && !Array.isArray(nodes)) {
    fail('SHAPE_INVALID', "'nodes' must be a list", { received: typeof nodes });
  }
  if (edges !== undefined && edges !== null && !Array.isArray(edges)) {
    fail('SHAPE_INVALID', "'edges' must be a list", { received: typeof edges });
  }
  if (!isPlainObject(envelope)) {
    fail('SHAPE_INVALID', "'envelope' must be an object", { received: typeof envelope });
  }
  assertNoForbiddenFields(envelope, 'The graph envelope');

  if (
    envelope.schema_version !== undefined &&
    envelope.schema_version !== null &&
    envelope.schema_version !== SCHEMA_VERSION
  ) {
    fail(
      'UNSUPPORTED_SCHEMA_VERSION',
      `This serializer emits schema_version ${SCHEMA_VERSION}, not ` +
        `${JSON.stringify(envelope.schema_version)}`,
      { received: envelope.schema_version },
    );
  }

  const metadata = cloneObject(envelope.metadata, 'Envelope metadata');
  assertNoForbiddenFields(metadata, 'Envelope metadata');

  const canonicalNodes = [];
  const nodeIndex = new Map();
  for (const node of nodes || []) {
    const canonicalNode = toCanonicalNode(node);
    if (nodeIndex.has(canonicalNode.id)) {
      fail('NODE_ID_DUPLICATE', `Two canvas nodes share the id '${canonicalNode.id}'`, {
        node: canonicalNode.id,
      });
    }
    nodeIndex.set(canonicalNode.id, canonicalNode);
    canonicalNodes.push(canonicalNode);
  }

  const canonicalEdges = [];
  const edgeIds = new Set();
  for (const edge of edges || []) {
    const canonicalEdge = toCanonicalEdge(edge, nodeIndex);
    if (edgeIds.has(canonicalEdge.id)) {
      fail('EDGE_ID_DUPLICATE', `Two canvas edges share the id '${canonicalEdge.id}'`, {
        edge: canonicalEdge.id,
      });
    }
    edgeIds.add(canonicalEdge.id);
    canonicalEdges.push(canonicalEdge);
  }

  return {
    schema_version: SCHEMA_VERSION,
    strategy_id: nonEmptyString(envelope.strategy_id) || '',
    version: nonEmptyString(envelope.version) || '',
    name: typeof envelope.name === 'string' ? envelope.name : '',
    nodes: canonicalNodes,
    edges: canonicalEdges,
    metadata,
    validation_state:
      envelope.validation_state === undefined || envelope.validation_state === null
        ? 'UNVALIDATED'
        : coerceEnum(VALIDATION_STATES, envelope.validation_state, 'validation_state'),
  };
}

/**
 * Convert a canonical graph back to canvas state.
 *
 * @param {object} graph A canonical graph (`schema_version` 2). Version 1 graphs are migrated
 *   server-side at read time and never reach this function.
 * @returns {{nodes: Array<object>, edges: Array<object>, envelope: object}} Canvas state plus
 *   the envelope fields, so the caller can feed all three straight back to `toCanonical`.
 * @throws {CanonicalGraphError} When the graph is not a readable canonical graph.
 */
export function fromCanonical(graph) {
  if (!isPlainObject(graph)) {
    fail('SHAPE_INVALID', 'A canonical graph must be an object', { received: typeof graph });
  }
  assertNoForbiddenFields(graph, 'The graph envelope');

  if (graph.schema_version !== SCHEMA_VERSION) {
    fail(
      'UNSUPPORTED_SCHEMA_VERSION',
      `This serializer reads schema_version ${SCHEMA_VERSION}, not ` +
        `${JSON.stringify(graph.schema_version)}`,
      { received: graph.schema_version },
    );
  }
  if (graph.nodes !== undefined && graph.nodes !== null && !Array.isArray(graph.nodes)) {
    fail('SHAPE_INVALID', "Graph field 'nodes' must be a list", { received: typeof graph.nodes });
  }
  if (graph.edges !== undefined && graph.edges !== null && !Array.isArray(graph.edges)) {
    fail('SHAPE_INVALID', "Graph field 'edges' must be a list", { received: typeof graph.edges });
  }

  const nodes = (graph.nodes || []).map((node) => {
    if (!isPlainObject(node)) {
      fail('NODE_INVALID', 'Every graph node must be an object', { received: typeof node });
    }
    const id = nonEmptyString(node.id);
    if (id === null) {
      fail('NODE_ID_MISSING', 'A graph node has no id', { received: node.id });
    }
    const blockId = nonEmptyString(node.block_id);
    if (blockId === null) {
      fail('BLOCK_ID_MISSING', `Graph node '${id}' has no block_id`, { node: id });
    }
    const category = coerceEnum(
      BLOCK_CATEGORIES,
      node.category,
      `category of node '${id}'`,
      { node: id, block_id: blockId },
    );

    const ui = cloneObject(node.ui, `ui of node '${id}'`);
    const data = {
      block_id: blockId,
      category,
      params: cloneObject(node.params, `params of node '${id}'`),
      inputs: normalizePorts(node.inputs, id, 'inputs'),
      outputs: normalizePorts(node.outputs, id, 'outputs'),
      ui,
    };
    // Presentation is projected onto the fields React Flow reads, and kept in `ui` as well
    // so nothing has to be reconstructed on the way back.
    if (Object.prototype.hasOwnProperty.call(ui, 'label')) data.label = clone(ui.label);
    if (Object.prototype.hasOwnProperty.call(ui, 'collapsed')) {
      data.collapsed = Boolean(ui.collapsed);
    }

    return {
      id,
      // Renderer selector only. Semantics live in data.block_id / data.category.
      type: blockId,
      position: normalizePosition(ui.position, `ui.position of node '${id}'`, { node: id }),
      data,
    };
  });

  const edges = (graph.edges || []).map((edge) => {
    if (!isPlainObject(edge)) {
      fail('EDGE_INVALID', 'Every graph edge must be an object', { received: typeof edge });
    }
    const id = nonEmptyString(edge.id);
    if (id === null) {
      fail('EDGE_ID_MISSING', 'A graph edge has no id', { received: edge.id });
    }
    const fields = {};
    for (const key of ['source', 'source_port', 'target', 'target_port']) {
      const value = nonEmptyString(edge[key]);
      if (value === null) {
        fail(
          'EDGE_ENDPOINT_MISSING',
          `Edge '${id}' has no '${key}'; every edge is port-addressed on both ends`,
          { edge: id, field: key },
        );
      }
      fields[key] = value;
    }

    const canvasEdge = {
      id,
      source: fields.source,
      sourceHandle: fields.source_port,
      target: fields.target,
      targetHandle: fields.target_port,
    };
    if (edge.type !== undefined && edge.type !== null && edge.type !== '') {
      canvasEdge.data = {
        port_type: coerceEnum(PORT_TYPES, edge.type, `type of edge '${id}'`, { edge: id }),
      };
    }
    return canvasEdge;
  });

  const metadata = cloneObject(graph.metadata, 'Graph metadata');
  assertNoForbiddenFields(metadata, 'Graph metadata');

  return {
    nodes,
    edges,
    envelope: {
      schema_version: SCHEMA_VERSION,
      strategy_id: nonEmptyString(graph.strategy_id) || '',
      version: nonEmptyString(graph.version) || '',
      name: typeof graph.name === 'string' ? graph.name : '',
      metadata,
      validation_state:
        graph.validation_state === undefined || graph.validation_state === null
          ? 'UNVALIDATED'
          : coerceEnum(VALIDATION_STATES, graph.validation_state, 'validation_state'),
    },
  };
}

/**
 * Return `graph` in normalized canonical form: one canvas round trip.
 *
 * `toCanonical` is the normalizer, so this is idempotent, and for any normalized graph the
 * round trip is exact equality (Requirement 1.4). It exists so a caller — the round-trip
 * property test in particular — can compare like with like without restating the
 * normalization rules documented at the top of this file.
 */
export function normalizeGraph(graph) {
  const { nodes, edges, envelope } = fromCanonical(graph);
  return toCanonical(nodes, edges, envelope);
}
