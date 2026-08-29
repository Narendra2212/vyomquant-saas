/**
 * connectionLegality.js — the client-side read of edge legality rules R1–R8.
 *
 * WHAT THIS MODULE IS FOR, AND WHAT IT IS NOT
 * ===========================================
 * This module exists for one reason: so drawing an edge does not cost a network round trip.
 * It is **not** a second rule set.
 * `backend_app/backend/strategy_dag/validator.py::is_edge_legal` is the authority, and this
 * file is a transcription of it that reads the *same shipped data* the backend reads.
 *
 * Two consequences, both load-bearing:
 *
 * 1. **Nothing here is a local table.** Every rule is answered from the registry payload the
 *    backend serves (`GET /strategies/registry` → `{blocks, compatibility_matrix, ...}`):
 *      • R3 port existence            → the descriptor's own `inputs` / `outputs`
 *      • R4 type compatibility        → the served `compatibility_matrix`
 *      • R5 category adjacency        → the descriptor's `allowed_successor_categories`
 *      • R6 terminality               → the descriptor's `execution_semantics` / `is_terminal`
 *    There is no hardcoded compatibility matrix and no hardcoded category-adjacency table in
 *    this file. A local copy of any of those is the drift mechanism (defect SB-01), which is
 *    the whole reason this module takes the payload as an **argument** instead of importing a
 *    registry client.
 *
 * 2. **A local "legal" verdict is never final.** Every verdict this module returns is marked
 *    `provisional: true` / `authority: 'client-provisional'`. The backend re-validates on
 *    every request that validates, saves, clones, compiles or deploys a graph
 *    (Requirement 6.12) and its answer wins. `reconcileWithBackend()` encodes that: once a
 *    backend report is in hand, the backend's issues decide, and a local pass can never
 *    suppress a backend rejection.
 *
 * RULE ORDER
 * ==========
 * Evaluated in the order the backend actually evaluates them:
 *
 *     R1 → R2 → R6 → R5 → R3 → R4 → R7 → R8
 *
 * R6 (terminal source) and R5 (category flow) come *before* the port-level R3/R4. That is the
 * backend's documented deviation from the design's listing, and it is deliberate: an ACTION
 * block declares no output ports at all, so R3 would report "unknown port" when the real
 * reason is "an action block is terminal"; likewise a DATA block declares no inputs, so
 * `ML_DL → DATA` reads better as an illegal category flow. Legality is unaffected either way
 * (an edge is illegal if *any* rule rejects it) but the **reason** differs, and the client's
 * reason must agree with the backend's for the same edge — otherwise the author is told one
 * thing while drawing and another thing on save. Matching this order is why `ACTION → DATA`,
 * `ACTION → INDICATOR`, `ACTION → ML_DL` and `ML_DL → DATA` come out as consequences of
 * R5/R6 here, exactly as on the server, rather than as a special-cased forbidden-pairs list.
 *
 * SHAPES
 * ======
 * Graph — the canonical graph produced by `canonicalGraph.js` (`schema_version` 2):
 *   `{ nodes: [{ id, block_id, category, params, inputs, outputs, ui }], edges: [...] }`
 * Edge / candidate edge:
 *   `{ id, source, source_port, target, target_port }`
 * Registry payload — the served registry response, or anything with the same two fields:
 *   `{ blocks: [descriptor], compatibility_matrix: { SOURCE_TYPE: [TARGET_TYPE, ...] } }`
 * Descriptor — `registry.BlockDescriptor.to_dict()`:
 *   `{ block_id, category, inputs, outputs, allowed_successor_categories,
 *      allowed_predecessor_categories, execution_semantics, ... }`
 * Port — `schema.Port.to_dict()`: `{ port, type, required?, variadic?, description? }`
 * Issue — `schema.make_issue()`, field for field:
 *   `{ code, severity, node_id, edge_id, field, message, expected, actual, fix_hint }`
 *
 * WIRING
 * ======
 * This task ships the library only. **The canvas wiring is not done here**: nothing in
 * `StrategyBuilder.jsx` calls this yet. The API the canvas will need is exposed and
 * documented — `legalTargetsForDrag()` for dimming during `onConnectStart`→`onConnectEnd`,
 * `reactFlowIsValidConnection()` for React Flow 11's `isValidConnection` prop, and
 * `checkConnection()` for the rejection reason to show on the refused drop.
 */

/** Canonical severity vocabulary (`schema.SEVERITY_ERROR` / `SEVERITY_WARNING`). */
export const SEVERITY_ERROR = 'error';
export const SEVERITY_WARNING = 'warning';

/**
 * The rule codes, mirrored from `validator.py` so a client message and a server message for
 * the same edge are directly comparable. These are the backend's codes, not new ones.
 */
export const CODE_EDGE_ENDPOINT_UNKNOWN = 'EDGE_ENDPOINT_UNKNOWN'; // R1
export const CODE_SELF_LOOP = 'SELF_LOOP'; // R2
export const CODE_UNKNOWN_SOURCE_PORT = 'UNKNOWN_SOURCE_PORT'; // R3
export const CODE_UNKNOWN_TARGET_PORT = 'UNKNOWN_TARGET_PORT'; // R3
export const CODE_TYPE_MISMATCH = 'TYPE_MISMATCH'; // R4
export const CODE_ILLEGAL_CATEGORY_FLOW = 'ILLEGAL_CATEGORY_FLOW'; // R5
export const CODE_TERMINAL_HAS_NO_OUTPUT = 'TERMINAL_HAS_NO_OUTPUT'; // R6
export const CODE_PORT_ALREADY_CONNECTED = 'PORT_ALREADY_CONNECTED'; // R7
export const CODE_CYCLE = 'CYCLE'; // R8

/** The rule order this module evaluates, as data, so a test can assert it. */
export const RULE_ORDER = Object.freeze([
  { rule: 'R1', code: CODE_EDGE_ENDPOINT_UNKNOWN },
  { rule: 'R2', code: CODE_SELF_LOOP },
  { rule: 'R6', code: CODE_TERMINAL_HAS_NO_OUTPUT },
  { rule: 'R5', code: CODE_ILLEGAL_CATEGORY_FLOW },
  { rule: 'R3', code: CODE_UNKNOWN_SOURCE_PORT },
  { rule: 'R3', code: CODE_UNKNOWN_TARGET_PORT },
  { rule: 'R4', code: CODE_TYPE_MISMATCH },
  { rule: 'R7', code: CODE_PORT_ALREADY_CONNECTED },
  { rule: 'R8', code: CODE_CYCLE },
]);

/**
 * Who decided. A verdict from this module is always `client-provisional`; only a verdict
 * carrying the backend's report is `backend`.
 */
export const AUTHORITY_CLIENT_PROVISIONAL = 'client-provisional';
export const AUTHORITY_BACKEND = 'backend';

/** The id given to the hypothetical edge behind a drag, when the caller supplies none. */
export const DRAG_CANDIDATE_EDGE_ID = '__drag_candidate__';

/** The value `execution_semantics` takes on a terminal block (`ExecutionSemantics.TERMINAL`). */
const EXECUTION_SEMANTICS_TERMINAL = 'TERMINAL';

/**
 * Every failure this module raises: a malformed argument, or a registry payload it cannot
 * read. Machine-readable `code` plus targeted `details`, matching `CanonicalGraphError`.
 *
 * These are deliberately **thrown**, not returned as an issue. "I cannot evaluate the rules"
 * is not "the edge is legal": a caller that cannot build a checker must refuse the drag, the
 * same way `registryClient` fails closed rather than falling back to a local block list.
 */
export class ConnectionLegalityError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.name = 'ConnectionLegalityError';
    this.code = code;
    this.details = details;
  }
}

const fail = (code, message, details) => {
  throw new ConnectionLegalityError(code, message, details);
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

/**
 * One structured issue, field for field with `schema.make_issue`.
 *
 * Absent fields are `null` (Python's `None`) and `fix_hint` defaults to the empty string, so
 * a client issue and a server issue are the same object shape and can be diffed key by key.
 */
export function makeIssue(
  code,
  severity,
  message,
  { nodeId = null, edgeId = null, field = null, expected = null, actual = null, fixHint = '' } = {},
) {
  return {
    code,
    severity,
    node_id: nodeId,
    edge_id: edgeId,
    field,
    message,
    expected,
    actual,
    fix_hint: fixHint,
  };
}

const portLabel = (nodeId, portName) => `${nodeId}.${portName}`;

/** `${nodeId}` + `${portName}` as one stable key, for the drag-time dimming lookup. */
export const portKey = (nodeId, portName) => `${nodeId}\u0000${portName}`;

/** A port entry's name. The wire key is `port` (`Port.to_dict`); `name` is accepted too. */
const portName = (port) => (isPlainObject(port) ? firstString([port.port, port.name]) : null);

const portList = (descriptor, direction) => {
  const raw = descriptor ? descriptor[direction] : null;
  return Array.isArray(raw) ? raw : [];
};

const findPort = (descriptor, direction, name) =>
  portList(descriptor, direction).find((port) => portName(port) === name) || null;

const portNames = (descriptor, direction) =>
  portList(descriptor, direction)
    .map(portName)
    .filter((name) => name !== null);

// ---------------------------------------------------------------------------
// The registry index: every rule's input, read from the served payload
// ---------------------------------------------------------------------------

/**
 * Index a served registry payload for lookup.
 *
 * Nothing is derived, defaulted or invented here. `descriptors` is the served `blocks` list
 * keyed by `block_id`; `compatibility` is the served `compatibility_matrix` turned into
 * `Map<string, Set<string>>` and read by nothing but R4.
 *
 * @param {object} payload The registry response, or an index this function already produced
 *   (idempotent, so a caller may pass either).
 * @returns {{registry_version: (string|null), descriptors: Map<string, object>,
 *   compatibility: Map<string, Set<string>>, isCompatible: (a: string, b: string) => boolean,
 *   descriptor: (blockId: string) => (object|null)}}
 * @throws {ConnectionLegalityError} When the payload carries no block list or no compatibility
 *   matrix. Fail closed: an unreadable registry means the rules cannot be evaluated at all.
 */
export function buildRegistryIndex(payload) {
  if (payload && payload.__connectionLegalityIndex === true) return payload;
  if (!isPlainObject(payload)) {
    fail('REGISTRY_UNAVAILABLE', 'A registry payload is required to evaluate edge legality', {
      received: payload === null ? 'null' : typeof payload,
    });
  }

  const blocks = payload.blocks !== undefined ? payload.blocks : payload.descriptors;
  const descriptors = new Map();
  if (blocks instanceof Map) {
    for (const [key, descriptor] of blocks) descriptors.set(key, descriptor);
  } else if (Array.isArray(blocks)) {
    for (const descriptor of blocks) {
      const blockId = isPlainObject(descriptor) ? nonEmptyString(descriptor.block_id) : null;
      if (blockId === null) {
        fail('REGISTRY_MALFORMED', 'A registry descriptor carries no block_id', {
          received: descriptor,
        });
      }
      descriptors.set(blockId, descriptor);
    }
  } else if (isPlainObject(blocks)) {
    for (const key of Object.keys(blocks)) descriptors.set(key, blocks[key]);
  } else {
    fail(
      'REGISTRY_UNAVAILABLE',
      "The registry payload carries no 'blocks'. Legality is decided from the served " +
        'descriptors; there is no local block list to fall back to.',
      { received: blocks === undefined ? 'undefined' : typeof blocks },
    );
  }

  const rawMatrix = payload.compatibility_matrix;
  const compatibility = new Map();
  if (rawMatrix instanceof Map) {
    for (const [source, targets] of rawMatrix) {
      compatibility.set(source, new Set(targets || []));
    }
  } else if (isPlainObject(rawMatrix)) {
    for (const source of Object.keys(rawMatrix)) {
      const targets = rawMatrix[source];
      if (!Array.isArray(targets) && !(targets instanceof Set)) {
        fail(
          'REGISTRY_MALFORMED',
          `compatibility_matrix['${source}'] must list the target port types it may feed`,
          { source, received: typeof targets },
        );
      }
      compatibility.set(source, new Set(targets));
    }
  } else {
    fail(
      'REGISTRY_UNAVAILABLE',
      "The registry payload carries no 'compatibility_matrix'. R4 is answered from the " +
        'served matrix only; this module holds no copy of it.',
      { received: rawMatrix === undefined ? 'undefined' : typeof rawMatrix },
    );
  }

  return {
    __connectionLegalityIndex: true,
    registry_version: nonEmptyString(payload.registry_version),
    descriptors,
    compatibility,
    descriptor: (blockId) =>
      blockId !== null && blockId !== undefined && descriptors.has(blockId)
        ? descriptors.get(blockId)
        : null,
    /** Rule R4, and the only place the matrix is read. Mirrors `registry.compatible`. */
    isCompatible: (source, target) => {
      const targets = compatibility.get(source);
      return targets === undefined ? false : targets.has(target);
    },
    /** The target types `source` may feed, sorted — the `expected` field of a TYPE_MISMATCH. */
    compatibleTargets: (source) => [...(compatibility.get(source) || [])].sort(),
  };
}

/**
 * Is this descriptor terminal (rule R6)?
 *
 * Read from the descriptor, never from its category: `execution_semantics === 'TERMINAL'` is
 * what `BlockDescriptor.is_terminal` computes, and an explicit `is_terminal` boolean is
 * honoured for a payload that ships the derived view as well.
 */
const isTerminal = (descriptor) => {
  if (!isPlainObject(descriptor)) return false;
  if (typeof descriptor.is_terminal === 'boolean') return descriptor.is_terminal;
  return descriptor.execution_semantics === EXECUTION_SEMANTICS_TERMINAL;
};

/**
 * The authoritative category of a node: the descriptor's, falling back to the node's.
 *
 * Mirrors `validator._category_of`. The fallback only ever applies to a node whose block did
 * not resolve, and such a node is rejected by R3 anyway.
 */
const categoryOf = (node, descriptor) => {
  const fromDescriptor = isPlainObject(descriptor) ? nonEmptyString(descriptor.category) : null;
  if (fromDescriptor !== null) return fromDescriptor;
  return isPlainObject(node) ? nonEmptyString(node.category) : null;
};

/**
 * The successor categories a descriptor permits (rule R5), or `null` when it declares none.
 *
 * `null` means "no declaration to check against" and skips R5, exactly as the backend's
 * `if allowed_successors is not None` does. An **empty list** is a declaration — it means the
 * block may feed nothing — and rejects.
 */
const allowedSuccessorCategories = (descriptor) => {
  if (!isPlainObject(descriptor)) return null;
  const declared = descriptor.allowed_successor_categories;
  if (Array.isArray(declared)) return declared;
  if (declared instanceof Set) return [...declared];
  return null;
};

// ---------------------------------------------------------------------------
// Graph indices
// ---------------------------------------------------------------------------

const graphNodes = (graph) => (isPlainObject(graph) && Array.isArray(graph.nodes) ? graph.nodes : []);
const graphEdges = (graph) => (isPlainObject(graph) && Array.isArray(graph.edges) ? graph.edges : []);

/** `node_id -> node`, first occurrence winning, as `StrategyGraph.node_index` does. */
export function buildNodeIndex(graph) {
  const index = new Map();
  for (const node of graphNodes(graph)) {
    if (!isPlainObject(node)) continue;
    const id = nonEmptyString(node.id);
    if (id === null || index.has(id)) continue;
    index.set(id, node);
  }
  return index;
}

/**
 * `portKey(target, target_port) -> edges[]`, mirroring `validator._build_inbound`.
 *
 * Pass it to `isEdgeLegal` to answer R7 in O(1) instead of scanning every edge — which is
 * what makes the drag-time query over every input port on the canvas affordable.
 */
export function buildInboundIndex(graph) {
  const inbound = new Map();
  for (const edge of graphEdges(graph)) {
    if (!isPlainObject(edge)) continue;
    const target = nonEmptyString(edge.target);
    const port = nonEmptyString(edge.target_port);
    if (target === null || port === null) continue;
    const key = portKey(target, port);
    const bucket = inbound.get(key);
    if (bucket === undefined) inbound.set(key, [edge]);
    else bucket.push(edge);
  }
  return inbound;
}

/**
 * `source -> [target, ...]`, de-duplicated, insertion-ordered, endpoints outside `known`
 * dropped. Mirrors `validator._build_adjacency` — including the ordering, because the order
 * successors are visited in decides *which* cycle a graph with several cycles reports, and
 * the client's reported path must be the backend's.
 */
const buildAdjacency = (edges, known) => {
  const adjacency = new Map();
  const seen = new Set();
  for (const edge of edges) {
    if (!isPlainObject(edge)) continue;
    const source = nonEmptyString(edge.source);
    const target = nonEmptyString(edge.target);
    if (source === null || target === null) continue;
    if (known !== undefined && (!known.has(source) || !known.has(target))) continue;
    const pair = portKey(source, target);
    if (seen.has(pair)) continue;
    seen.add(pair);
    const bucket = adjacency.get(source);
    if (bucket === undefined) adjacency.set(source, [target]);
    else bucket.push(target);
  }
  return adjacency;
};

/** Every node reachable from `roots`, iteratively. Roots included. `validator._reachable_from`. */
const reachableFrom = (adjacency, roots) => {
  const seen = new Set();
  const stack = [...roots];
  while (stack.length > 0) {
    const current = stack.pop();
    if (seen.has(current)) continue;
    seen.add(current);
    for (const target of adjacency.get(current) || []) {
      if (!seen.has(target)) stack.push(target);
    }
  }
  return seen;
};

// ---------------------------------------------------------------------------
// R8: cycle detection — iterative, and it returns the path
// ---------------------------------------------------------------------------

const WHITE = 0;
const GREY = 1;
const BLACK = 2;

/**
 * One real cycle as an ordered, **closed** node sequence, or `[]` for a DAG.
 *
 * A non-empty result reads as the loop the author drew: `['a','b','c','a']`, which the UI
 * renders `a → b → c → a` (Requirements 6.8, 6.15). It is the true path, not a visited set —
 * a visited set cannot be shown to the author, who needs to know which connection to remove.
 *
 * @param {Array<object|string>} nodes Canonical nodes, or bare node ids.
 * @param {Array<object>} edges Canonical edges. Endpoints outside `nodes` are ignored rather
 *   than trusted; R1 reports those.
 * @returns {Array<string>} The closed cycle path, or `[]`.
 *
 * Iterative by construction, with an explicit stack of `{node, targets, cursor}` frames; the
 * cursor is what makes a resumed frame continue where it left off instead of rescanning, so
 * this is equivalent to the recursive form without its stack depth. That matters here: a
 * strategy may hold 200 nodes in one chain, deep recursion in JS overflows the call stack,
 * and an overflow during a drag would take the canvas down rather than refuse an edge. The
 * backend's `find_cycle` is iterative for the same reason.
 *
 * Loop invariants: `path` always holds the current GREY chain from the search root to the tip,
 * which is why the reported sequence is the real cycle; `pathAt` mirrors `path` as
 * `node -> position`, so closing the loop is O(1) rather than a scan. A BLACK node
 * participates in no cycle reachable from it and is never re-entered.
 */
export function findCycle(nodes, edges) {
  const nodeIds = [];
  for (const item of nodes || []) {
    const id = typeof item === 'string' ? nonEmptyString(item) : nonEmptyString(item && item.id);
    if (id !== null) nodeIds.push(id);
  }
  const known = new Set(nodeIds);
  const adjacency = buildAdjacency(edges || [], known);

  const state = new Map();
  for (const id of nodeIds) state.set(id, WHITE);

  for (const root of nodeIds) {
    if (state.get(root) !== WHITE) continue;

    state.set(root, GREY);
    const path = [root];
    const pathAt = new Map([[root, 0]]);
    const stack = [{ node: root, targets: adjacency.get(root) || [], cursor: 0 }];

    while (stack.length > 0) {
      const frame = stack[stack.length - 1];
      let descended = false;

      while (frame.cursor < frame.targets.length) {
        const next = frame.targets[frame.cursor];
        frame.cursor += 1;
        const colour = state.get(next);
        if (colour === GREY) {
          // `next` is on the current path: the cycle is that suffix, closed.
          return path.slice(pathAt.get(next)).concat([next]);
        }
        if (colour === WHITE) {
          state.set(next, GREY);
          pathAt.set(next, path.length);
          path.push(next);
          stack.push({ node: next, targets: adjacency.get(next) || [], cursor: 0 });
          descended = true;
          break;
        }
        // BLACK: fully explored and cycle-free, nothing to do.
      }

      if (!descended && frame.cursor >= frame.targets.length) {
        stack.pop();
        path.pop();
        pathAt.delete(frame.node);
        state.set(frame.node, BLACK);
      }
    }
  }

  return [];
}

/**
 * Would adding `edge` to `graph` close a loop (rule R8)? Mirrors `validator.creates_cycle`.
 *
 * Safe to call with an edge already in the graph — the reachability question is the same
 * either way — so one function answers both the connect-time and the validate-time question.
 */
export function createsCycle(graph, edge) {
  if (!isPlainObject(edge)) return false;
  if (edge.source === edge.target) return true;
  const known = new Set([...buildNodeIndex(graph).keys()]);
  const adjacency = buildAdjacency([...graphEdges(graph), edge], known);
  return reachableFrom(adjacency, [edge.target]).has(edge.source);
}

/** The closed cycle path `edge` would create. Mirrors `validator.cycle_path_with_edge`. */
export function cyclePathWithEdge(graph, edge) {
  const edges = [...graphEdges(graph)];
  const edgeId = isPlainObject(edge) ? edge.id : null;
  if (!edges.some((existing) => isPlainObject(existing) && existing.id === edgeId)) {
    edges.push(edge);
  }
  return findCycle(graphNodes(graph), edges);
}

// ---------------------------------------------------------------------------
// R1–R8 for one edge
// ---------------------------------------------------------------------------

/**
 * Rules R1–R8 for one edge. `null` when the edge is legal, otherwise exactly one issue.
 *
 * A transcription of `validator.is_edge_legal`, evaluated in the backend's order
 * (R1, R2, R6, R5, R3, R4, R7, R8) so the code and the reason the author sees while drawing
 * are the code and the reason the server would return for the same edge.
 *
 * **A `null` here is provisional.** It means no rule this client can evaluate rejected the
 * edge; the backend re-validates and decides (Requirement 6.12). See `reconcileWithBackend`.
 *
 * @param {object} graph A canonical graph. Not mutated.
 * @param {object} edge The edge to judge. It need not be in `graph`: the same call answers
 *   "may I add this?" and "is this one legal?".
 * @param {object} registry A served registry payload, or a `buildRegistryIndex` result.
 * @param {object} [options]
 * @param {boolean} [options.checkCycle=true] Evaluate R8. `false` suppresses R8 only, for a
 *   caller evaluating acyclicity once over the whole graph instead of once per edge.
 * @param {Map} [options.inbound] A `buildInboundIndex` result, to answer R7 without a scan.
 * @param {Map} [options.nodeIndex] A `buildNodeIndex` result, to resolve endpoints without a scan.
 * @returns {object|null} One issue, or `null`.
 * @throws {ConnectionLegalityError} When `graph`, `edge` or `registry` cannot be read. Not
 *   being able to evaluate the rules is never reported as legality.
 */
export function isEdgeLegal(graph, edge, registry, options = {}) {
  if (!isPlainObject(graph)) {
    fail('SHAPE_INVALID', 'A canonical graph must be an object', {
      received: graph === null ? 'null' : typeof graph,
    });
  }
  if (!isPlainObject(edge)) {
    fail('SHAPE_INVALID', 'An edge must be an object', {
      received: edge === null ? 'null' : typeof edge,
    });
  }

  const index = buildRegistryIndex(registry);
  const { checkCycle = true, inbound, nodeIndex } = options;
  const nodes = nodeIndex instanceof Map ? nodeIndex : buildNodeIndex(graph);

  const edgeId = edge.id === undefined || edge.id === null ? null : edge.id;
  const sourcePort = edge.source_port === undefined ? null : edge.source_port;
  const targetPort = edge.target_port === undefined ? null : edge.target_port;

  // -- R1 endpoints resolve ------------------------------------------------
  const src = nodes.get(edge.source) || null;
  const dst = nodes.get(edge.target) || null;
  if (src === null || dst === null) {
    const missing = [];
    if (src === null) missing.push(`source='${edge.source}'`);
    if (dst === null) missing.push(`target='${edge.target}'`);
    return makeIssue(
      CODE_EDGE_ENDPOINT_UNKNOWN,
      SEVERITY_ERROR,
      `Connection '${edgeId}' names a block that is not in this strategy (${missing.join(', ')}).`,
      {
        edgeId,
        expected: 'both endpoints present in the graph',
        actual: { source: edge.source, target: edge.target },
        fixHint: 'Delete the connection, or re-add the block it points at.',
      },
    );
  }

  // -- R2 no self loop -----------------------------------------------------
  if (src.id === dst.id) {
    return makeIssue(
      CODE_SELF_LOOP,
      SEVERITY_ERROR,
      `A block cannot feed itself: '${src.id}' is both ends of connection '${edgeId}'.`,
      {
        nodeId: src.id,
        edgeId,
        expected: 'source and target to be different blocks',
        actual: src.id,
        fixHint: 'Point one end of the connection at a different block.',
      },
    );
  }

  const srcDescriptor = index.descriptor(src.block_id);
  const dstDescriptor = index.descriptor(dst.block_id);
  const srcCategory = categoryOf(src, srcDescriptor);
  const dstCategory = categoryOf(dst, dstDescriptor);

  // -- R6 terminal blocks have no outputs ----------------------------------
  // Before R3 on purpose: an ACTION block declares no outputs at all, so R3 would blame an
  // "unknown port" for what is really "an action ends the path". Matches the backend.
  if (isTerminal(srcDescriptor)) {
    return makeIssue(
      CODE_TERMINAL_HAS_NO_OUTPUT,
      SEVERITY_ERROR,
      `'${src.block_id}' is terminal: an action block ends a path and cannot feed another block.`,
      {
        nodeId: src.id,
        edgeId,
        expected: 'a non-terminal source block',
        actual: `${src.block_id} (${srcCategory}, TERMINAL)`,
        fixHint: 'Take the connection from the block that feeds the action instead.',
      },
    );
  }

  // -- R5 category adjacency, from the descriptor's own declaration --------
  const allowedSuccessors = allowedSuccessorCategories(srcDescriptor);
  if (allowedSuccessors !== null && !allowedSuccessors.includes(dstCategory)) {
    const permitted = [...allowedSuccessors].sort();
    return makeIssue(
      CODE_ILLEGAL_CATEGORY_FLOW,
      SEVERITY_ERROR,
      `${srcCategory} cannot feed ${dstCategory}: '${src.block_id}' may only feed ` +
        `${permitted.length > 0 ? permitted.join(', ') : 'nothing'}.`,
      {
        nodeId: dst.id,
        edgeId,
        field: portLabel(dst.id, targetPort),
        expected: permitted,
        actual: dstCategory,
        fixHint:
          'Insert a block of a permitted category between the two, or connect to a different block.',
      },
    );
  }

  // -- R3 ports exist and face the right way -------------------------------
  const outPort = findPort(srcDescriptor, 'outputs', sourcePort);
  if (outPort === null) {
    const known = portNames(srcDescriptor, 'outputs');
    const detail =
      srcDescriptor !== null
        ? `'${src.block_id}' publishes outputs ${JSON.stringify(known)}.`
        : `The block registry publishes no descriptor for '${src.block_id}'.`;
    return makeIssue(
      CODE_UNKNOWN_SOURCE_PORT,
      SEVERITY_ERROR,
      `Unknown output port '${sourcePort}' on '${src.id}'. ${detail}`,
      {
        nodeId: src.id,
        edgeId,
        field: portLabel(src.id, sourcePort),
        expected: known,
        actual: sourcePort,
        fixHint: 'Re-draw the connection from a port the block publishes.',
      },
    );
  }

  const inPort = findPort(dstDescriptor, 'inputs', targetPort);
  if (inPort === null) {
    const known = portNames(dstDescriptor, 'inputs');
    const detail =
      dstDescriptor !== null
        ? `'${dst.block_id}' accepts inputs ${JSON.stringify(known)}.`
        : `The block registry publishes no descriptor for '${dst.block_id}'.`;
    return makeIssue(
      CODE_UNKNOWN_TARGET_PORT,
      SEVERITY_ERROR,
      `Unknown input port '${targetPort}' on '${dst.id}'. ${detail}`,
      {
        nodeId: dst.id,
        edgeId,
        field: portLabel(dst.id, targetPort),
        expected: known,
        actual: targetPort,
        fixHint: 'Re-draw the connection into a port the block accepts.',
      },
    );
  }

  // -- R4 type compatibility, via the served matrix ------------------------
  if (!index.isCompatible(outPort.type, inPort.type)) {
    return makeIssue(
      CODE_TYPE_MISMATCH,
      SEVERITY_ERROR,
      `${outPort.type} cannot feed ${inPort.type}. Insert a converting block or pick a ` +
        'different port.',
      {
        nodeId: dst.id,
        edgeId,
        field: portLabel(dst.id, portName(inPort)),
        expected: index.compatibleTargets(outPort.type),
        actual: inPort.type,
        fixHint:
          `'${src.id}.${portName(outPort)}' produces ${outPort.type}; connect it to a port ` +
          'that accepts that type.',
      },
    );
  }

  // -- R7 single-arity input already occupied ------------------------------
  if (!inPort.variadic) {
    const existing =
      inbound instanceof Map
        ? inbound.get(portKey(dst.id, targetPort)) || []
        : graphEdges(graph).filter(
            (candidate) =>
              isPlainObject(candidate) &&
              candidate.target === dst.id &&
              candidate.target_port === targetPort,
          );
    const occupied = existing.filter((candidate) => candidate.id !== edgeId);
    if (occupied.length > 0) {
      return makeIssue(
        CODE_PORT_ALREADY_CONNECTED,
        SEVERITY_ERROR,
        `${portLabel(dst.id, targetPort)} accepts one connection and already has one.`,
        {
          nodeId: dst.id,
          edgeId,
          field: portLabel(dst.id, targetPort),
          expected: 1,
          actual: occupied.length + 1,
          fixHint:
            'Remove the existing connection first, or use a block whose input accepts ' +
            'several sources.',
        },
      );
    }
  }

  // -- R8 adding the edge must not create a cycle --------------------------
  if (checkCycle && createsCycle(graph, edge)) {
    const path = cyclePathWithEdge(graph, edge);
    return makeIssue(CODE_CYCLE, SEVERITY_ERROR, `Connection would create a loop: ${path.join(' -> ')}`, {
      nodeId: src.id,
      edgeId,
      expected: 'an acyclic graph',
      actual: path,
      fixHint: 'Remove one connection on that loop; a strategy must flow one way.',
    });
  }

  return null;
}

// ---------------------------------------------------------------------------
// Verdicts, and who owns them
// ---------------------------------------------------------------------------

/**
 * The connect-time gate: the full R1–R8 verdict for one candidate edge, as a verdict object.
 *
 * The verdict is always `provisional: true` / `authority: 'client-provisional'`. That is the
 * contract, not a hedge: this check exists to spare a round trip while the author drags, and
 * the backend re-runs every rule on save, clone, compile and deploy. Feed a backend report
 * through `reconcileWithBackend` to get an authoritative verdict.
 *
 * @returns {{accepted: boolean, provisional: true, authority: string, issue: (object|null),
 *   edge_id: (string|null), code: (string|null), message: (string|null)}}
 */
export function checkConnection(graph, edge, registry, options = {}) {
  const issue = isEdgeLegal(graph, edge, registry, options);
  return {
    accepted: issue === null,
    provisional: true,
    authority: AUTHORITY_CLIENT_PROVISIONAL,
    edge_id: isPlainObject(edge) && edge.id !== undefined ? edge.id : null,
    issue,
    code: issue === null ? null : issue.code,
    message: issue === null ? null : issue.message,
  };
}

/**
 * Fold the backend's answer over a local verdict. The backend wins, always.
 *
 * This is the mechanism that stops a local pass from suppressing a backend rejection: once
 * `backendIssues` is supplied, `accepted` is computed from the backend's error-severity
 * issues alone and the local verdict contributes nothing but its own record. Until then the
 * verdict stays `provisional`.
 *
 * @param {object} localVerdict A `checkConnection` result.
 * @param {Array<object>|null|undefined} backendIssues The backend report's issues (its
 *   `errors` / `warnings`, or the whole `issues` list). Pass `null`/`undefined` when no
 *   backend answer is in hand yet.
 * @param {object} [options]
 * @param {string} [options.edgeId] Consider only issues concerning this edge. Defaults to the
 *   local verdict's edge id; pass `null` to consider every supplied issue.
 * @returns {{accepted: boolean, provisional: boolean, authority: string, issue: (object|null),
 *   issues: Array<object>, local: object}}
 */
export function reconcileWithBackend(localVerdict, backendIssues, options = {}) {
  const local = isPlainObject(localVerdict)
    ? localVerdict
    : { accepted: false, provisional: true, authority: AUTHORITY_CLIENT_PROVISIONAL, issue: null };

  if (backendIssues === null || backendIssues === undefined) {
    return {
      accepted: Boolean(local.accepted),
      provisional: true,
      authority: AUTHORITY_CLIENT_PROVISIONAL,
      issue: local.issue === undefined ? null : local.issue,
      issues: local.issue ? [local.issue] : [],
      local,
    };
  }

  const edgeId = Object.prototype.hasOwnProperty.call(options, 'edgeId')
    ? options.edgeId
    : local.edge_id === undefined
      ? null
      : local.edge_id;

  const relevant = (Array.isArray(backendIssues) ? backendIssues : [])
    .filter(isPlainObject)
    .filter((issue) => edgeId === null || edgeId === undefined || issue.edge_id === edgeId);
  const errors = relevant.filter((issue) => issue.severity !== SEVERITY_WARNING);

  return {
    accepted: errors.length === 0,
    provisional: false,
    authority: AUTHORITY_BACKEND,
    issue: errors.length > 0 ? errors[0] : null,
    issues: relevant,
    local,
  };
}

// ---------------------------------------------------------------------------
// Drag-time dimming (Requirement 6.1)
// ---------------------------------------------------------------------------

/**
 * Normalize a drag origin. Accepts this module's own shape, the canonical edge shape, and
 * React Flow 11's `onConnectStart` payload (`{ nodeId, handleId, handleType }`).
 */
const normalizeOrigin = (origin) => {
  if (!isPlainObject(origin)) {
    fail('SHAPE_INVALID', 'A drag origin must be an object naming the node and the port', {
      received: origin === null ? 'null' : typeof origin,
    });
  }
  const nodeId = firstString([origin.node_id, origin.nodeId, origin.source]);
  const port = firstString([origin.port, origin.port_name, origin.handleId, origin.source_port, origin.sourceHandle]);
  if (nodeId === null) {
    fail('DRAG_ORIGIN_INVALID', 'A drag origin must name the node the drag started from', {
      received: origin,
    });
  }
  if (port === null) {
    fail(
      'DRAG_ORIGIN_INVALID',
      `A drag origin must name the output port on '${nodeId}' the drag started from; a port ` +
        'is never guessed.',
      { received: origin, node: nodeId },
    );
  }
  return { node_id: nodeId, port };
};

/**
 * Which target ports accept the port currently being dragged (Requirement 6.1).
 *
 * Every input port **declared by the registry descriptor** of every node on the canvas is
 * judged by the same `isEdgeLegal` call the drop will make, so what is dimmed during the drag
 * and what is refused on the drop cannot disagree. A node's own submitted port list is never
 * consulted, for the same reason the backend does not consult it: a hand-edited payload must
 * not be able to widen its own contract.
 *
 * The canvas wiring is **not** part of this task. The intended use, for a later task:
 *   • `onConnectStart(_, { nodeId, handleId })` → call this, keep the result in a ref
 *   • render an input handle dimmed when `!result.isPortLegal(nodeId, portName)`
 *   • `onConnectEnd` → drop the result
 *   • `isValidConnection` → `reactFlowConnectionValidator(...)`, so the drop re-checks
 *
 * @param {object} graph The canonical graph as it stands, before the drop.
 * @param {object} origin `{ node_id, port }`, or React Flow's `{ nodeId, handleId }`.
 * @param {object} registry A served registry payload, or a `buildRegistryIndex` result.
 * @param {object} [options]
 * @param {string} [options.edgeId] Id for the hypothetical edge. Defaults to
 *   `DRAG_CANDIDATE_EDGE_ID`.
 * @param {boolean} [options.checkCycle=true] Include R8 in the drag-time verdict.
 * @returns {{origin: object, verdicts: Array<object>, legalPortKeys: Set<string>,
 *   illegalPortKeys: Set<string>, legalNodeIds: Set<string>, provisional: true,
 *   authority: string, isPortLegal: Function, isNodeLegal: Function, verdictFor: Function}}
 */
export function legalTargetsForDrag(graph, origin, registry, options = {}) {
  if (!isPlainObject(graph)) {
    fail('SHAPE_INVALID', 'A canonical graph must be an object', {
      received: graph === null ? 'null' : typeof graph,
    });
  }
  const index = buildRegistryIndex(registry);
  const { node_id: originNodeId, port: originPort } = normalizeOrigin(origin);
  const { edgeId = DRAG_CANDIDATE_EDGE_ID, checkCycle = true } = options;

  const nodeIndex = buildNodeIndex(graph);
  const inbound = buildInboundIndex(graph);

  const originNode = nodeIndex.get(originNodeId) || null;
  const originDescriptor = originNode === null ? null : index.descriptor(originNode.block_id);
  const originOutPort = findPort(originDescriptor, 'outputs', originPort);

  const verdicts = [];
  const legalPortKeys = new Set();
  const illegalPortKeys = new Set();
  const legalNodeIds = new Set();
  const byKey = new Map();

  for (const node of graphNodes(graph)) {
    const nodeId = isPlainObject(node) ? nonEmptyString(node.id) : null;
    if (nodeId === null) continue;
    const descriptor = index.descriptor(node.block_id);
    for (const port of portList(descriptor, 'inputs')) {
      const name = portName(port);
      if (name === null) continue;
      const issue = isEdgeLegal(
        graph,
        {
          id: edgeId,
          source: originNodeId,
          source_port: originPort,
          target: nodeId,
          target_port: name,
        },
        index,
        { checkCycle, inbound, nodeIndex },
      );
      const key = portKey(nodeId, name);
      const verdict = {
        node_id: nodeId,
        port: name,
        type: port.type === undefined ? null : port.type,
        legal: issue === null,
        code: issue === null ? null : issue.code,
        message: issue === null ? null : issue.message,
        issue,
      };
      verdicts.push(verdict);
      byKey.set(key, verdict);
      if (issue === null) {
        legalPortKeys.add(key);
        legalNodeIds.add(nodeId);
      } else {
        illegalPortKeys.add(key);
      }
    }
  }

  return {
    origin: {
      node_id: originNodeId,
      port: originPort,
      type: originOutPort === null ? null : originOutPort.type,
    },
    verdicts,
    legalPortKeys,
    illegalPortKeys,
    legalNodeIds,
    provisional: true,
    authority: AUTHORITY_CLIENT_PROVISIONAL,
    /** True when this input port accepts the dragged port. Dim the handle when false. */
    isPortLegal: (nodeId, name) => legalPortKeys.has(portKey(nodeId, name)),
    /** True when any input port on this node accepts the dragged port. Dim the node when false. */
    isNodeLegal: (nodeId) => legalNodeIds.has(nodeId),
    /** The full verdict for one target port, including the rejecting issue. */
    verdictFor: (nodeId, name) => byKey.get(portKey(nodeId, name)) || null,
  };
}

// ---------------------------------------------------------------------------
// React Flow 11 adapters
// ---------------------------------------------------------------------------

/**
 * React Flow's `Connection` (`{ source, sourceHandle, target, targetHandle }`) as a canonical
 * candidate edge. A missing handle is *not* defaulted: a port is never guessed, so an
 * unhandled connection is judged with a null port and rejected by R3 — the same answer the
 * backend gives for a port it cannot find.
 */
export function connectionToEdge(connection, edgeId = DRAG_CANDIDATE_EDGE_ID) {
  if (!isPlainObject(connection)) {
    fail('SHAPE_INVALID', 'A React Flow connection must be an object', {
      received: connection === null ? 'null' : typeof connection,
    });
  }
  return {
    id: edgeId,
    source: connection.source,
    source_port: firstString([connection.sourceHandle, connection.source_port]),
    target: connection.target,
    target_port: firstString([connection.targetHandle, connection.target_port]),
  };
}

/**
 * A `(connection) => boolean` for React Flow 11's `isValidConnection` prop.
 *
 * `isValidConnection` can only answer yes or no, so the rejecting issue is handed to the
 * optional `onReject` callback — that is where the canvas raises the toast naming the reason
 * (Requirements 6.2–6.8). Wiring this into `StrategyBuilder.jsx` is a later task.
 *
 * @param {object} args
 * @param {object|Function} args.graph The canonical graph, or a getter returning it. A getter
 *   keeps the validator correct as the canvas changes without rebuilding it on every edit.
 * @param {object} args.registry A served registry payload, or a `buildRegistryIndex` result.
 * @param {Function} [args.onReject] `(issue, connection) => void`, called on a refusal.
 * @returns {(connection: object) => boolean}
 */
export function reactFlowConnectionValidator({ graph, registry, onReject } = {}) {
  const index = buildRegistryIndex(registry);
  const readGraph = typeof graph === 'function' ? graph : () => graph;
  return (connection) => {
    const issue = isEdgeLegal(readGraph(), connectionToEdge(connection), index);
    if (issue !== null && typeof onReject === 'function') onReject(issue, connection);
    return issue === null;
  };
}
