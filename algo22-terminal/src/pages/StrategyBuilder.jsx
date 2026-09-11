/**
 * StrategyBuilder.jsx — the authoring surface: palette, canvas, inspector, status strip.
 *
 * This file is where tasks 3.4, 3.6 and 3.9 land, and they land together because they are
 * one change wearing three names:
 *
 * * **3.4 — one serializer.** `src/utils/dagSerializer.js` and the inline
 *   `serializeReactFlowToDAG` that used to shadow it are gone. Every graph that leaves this
 *   page goes through `lib/canonicalGraph.js` (`toCanonical`), and every graph that arrives
 *   comes back through `fromCanonical`. The old serializers filtered on `node.type` and
 *   dropped every DATA, MATH and FEATURE_ENGINEERING node (SB-05); `toCanonical` emits every
 *   node of every category and *raises* rather than dropping one.
 * * **3.6 — one block catalogue.** The palette is rendered from the registry response
 *   (`lib/registryClient.js` → `GET /api/strategy-operations/registry/blocks`), grouped by
 *   `categories[].order`, so all seven categories populate (SB-03, SB-04). The deprecated
 *   `GET /api/strategies/blocks` call is gone, and so are the fail-closed
 *   `blockRegistry.BlockRegistry` shims that stood in for it. On any registry failure the
 *   palette shows an error panel with a retry button and **zero** block entries. There is no
 *   local fallback list: that fallback is the drift mechanism behind SB-03 and SB-04.
 * * **3.9 — no market or exchange identity invented on the save path.** `exchange: "binance"`,
 *   `|| "BTC/USDT"`, `|| "15m"` and the `ccxt_asset_feed` node lookup are gone. `symbol` and
 *   `timeframe` are read from the DATA node's own validated params, and a save with either
 *   unset fails with a structured issue naming the node and the field (SB-06).
 *
 * * **3.10 — the backend's verdict, on the canvas.** Every semantic edit marks the graph
 *   unvalidated at once and schedules one `POST /api/strategies/validate` 400 ms after the
 *   last edit (Requirement 8.10). The report that comes back marks each node and each edge
 *   it names with that node's or edge's severity and issue count, its `fix_hint` text is
 *   rendered verbatim, and an issue naming neither a node nor an edge is rendered as a
 *   graph-level issue rather than dropped (Requirements 8.8, 8.9). The state machine and its
 *   projections live in `lib/graphValidation.js`; this file owns the timer, the request and
 *   the write-back onto the canvas.
 *
 *   Two rules that matter more than they look:
 *   - A response is only accepted when both its request id **and** the graph key it was
 *     issued for still match, so an edit made while a request is in flight invalidates that
 *     request's answer instead of letting a stale report mark a newer graph.
 *   - The backend is authoritative. While a report describes the graph on the canvas, the
 *     local advisory checks in `ValidationContext` are not rendered at all, so the author is
 *     never shown two verdicts that disagree.
 *
 * Why they cannot ship separately: `toCanonical` reads `block_id` and `category` from
 * `data.block_id` / `data.category` and raises `BLOCK_ID_MISSING` / `CATEGORY_MISSING` rather
 * than guessing. Nodes only carry those fields because the palette now stamps the registry
 * descriptor onto every node it creates. Re-pointing the save path without the palette change
 * would raise on every save.
 *
 * Presentation note: this page uses inline styles off the `C` token object from
 * `components/ui-legacy/primitives`, unlike the Tailwind-classed `components/ui/`. The
 * inspector's `ParameterForm` is Tailwind-classed; that boundary is deliberate and left as is.
 */

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import ReactFlow, {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlowProvider,
  applyNodeChanges,
  applyEdgeChanges,
  MiniMap,
  useReactFlow,
} from 'reactflow';
import 'reactflow/dist/style.css';
import {
  Activity, AlertTriangle, ArrowLeft, BarChart2, ChevronDown, ChevronRight, Maximize,
  PanelLeft, PanelRight, Play, RefreshCw, Redo, Save, Search, Trash2, Undo, ZoomIn, ZoomOut,
} from 'lucide-react';
import { C, Inp, Tag2, PanelTitle } from '../components/ui-legacy/primitives';
import { Button } from '../components/ui/Button';
import { DataPipelineProvider } from '../contexts/DataPipelineContext';
import { IndicatorEngineProvider } from '../contexts/IndicatorEngineContext';
import { LogicEngineProvider } from '../contexts/LogicEngineContext';
import { StrategyEngineProvider } from '../contexts/StrategyEngineContext';
import { useUndoRedo, UndoRedoProvider } from '../contexts/UndoRedoContext';
import { useValidation, ValidationProvider } from '../contexts/ValidationContext';
import { getCategoryColor, getCategoryIcon } from '../lib/blockRegistry';
import { CanonicalGraphError, fromCanonical, toCanonical } from '../lib/canonicalGraph';
import {
  getDescriptor,
  getPaletteSections,
  getRegistrySnapshot,
  loadRegistry,
  refreshRegistry,
  subscribe,
} from '../lib/registryClient';
import {
  legalTargetsForDrag,
  reactFlowConnectionValidator,
} from '../lib/connectionLegality';
import { BEHAVIOUR_CHANGING_PARAMS, ParameterForm, blockingParamKeys } from '../components/builder/ParameterForm';
import { AssetSelector } from '../components/builder/AssetSelector';
import { TimeframeSelector } from '../components/builder/TimeframeSelector';
import { NodePreview } from '../components/builder/NodePreview';
import { NodeTrace } from '../components/builder/NodeTrace';
import {
  initialPreviewState,
  previewAvailability,
  previewError,
  previewKey,
  previewReducer,
  projectPreview,
} from '../lib/nodePreview';
import { traceFromPreviewBody, traceFromPreviewFailure } from '../lib/nodeTrace';
import {
  FEED_READ_STATES,
  SAVE_STATES,
  SEVERITY_ERROR,
  SEVERITY_WARNING,
  VALIDATION_DEBOUNCE_MS,
  VALIDATION_STATES,
  collectMarkers,
  deriveFeedState,
  deriveTrainingBlocks,
  deriveTrainingState,
  feedStateFromReport,
  initialValidationState,
  markerLabel,
  markerSignature,
  reportAppliesToCanvas,
  semanticGraphKey,
  validationReducer,
  validationSummary,
} from '../lib/graphValidation';
import { strategiesApi } from '../api/modules/strategies';
import { dataQualityApi } from '../api/modules/dataQuality';
import { useBuilderRealtime } from '../hooks/useBuilderRealtime';
import {
  REALTIME_LABELS,
  REALTIME_STATES,
  deployedLockView,
  nodeRuntimeView,
  refusalList,
} from '../lib/builderRealtime';

/** The drag-and-drop payload key. It carries a `block_id`, never a display label. */
export const DRAG_BLOCK_ID_MIME = 'application/reactflow-block-id';

/** localStorage key for the recovered draft. Bumped: a v1 draft holds no `block_id`. */
export const AUTOSAVE_KEY = 'builder_autosave_v2';

/** The two DATA-node params that decide which market a saved strategy trades (SB-06). */
export const MARKET_IDENTITY_FIELDS = Object.freeze(['symbol', 'timeframe']);

/**
 * The two `ParamType`s whose option sets are not in the descriptor, and the controls that
 * fetch them (task 7.3, Requirements 11.7 and 11.8).
 *
 * `ParameterForm` renders every other type from the `ParamSpec` alone. These two cannot be:
 * SYMBOL's vocabulary is the live asset universe (`GET /api/strategy-operations/assets`) and
 * TIMEFRAME's is the registry's published interval set — and the DATA descriptor deliberately
 * publishes no `options` tuple for either, so there is nothing local to render them from and
 * nothing local that may be invented. Injected as a prop rather than imported by the form,
 * so the form itself still reaches no network and stays assertable without one.
 */
export const MARKET_PARAM_CONTROLS = Object.freeze({
  SYMBOL: AssetSelector,
  TIMEFRAME: TimeframeSelector,
});

/**
 * How often the feed reading is refreshed, in milliseconds (task 7.11).
 *
 * The route is `@limiter.limit("60/minute")` and answers `Cache-Control: no-store`, so the
 * budget is one request per second per caller and none of them may be served from a cache.
 * 15 s spends **4 of those 60** — a 15x margin, so a builder left open in several tabs, or a
 * refresh landing beside a save, still cannot approach the limit. The reads are single-flight
 * and the module deliberately bypasses `apiClient`'s retrying `get()` helper, so one tick can
 * never become three requests. It is not slower than the thing it measures either: the
 * shortest interval the platform publishes is 1m, and `DELAYED` begins at 1.5 intervals
 * (90 s), so a feed that stops is seen well inside the window in which it starts to matter.
 */
export const FEED_READ_INTERVAL_MS = 15000;

// ---------------------------------------------------------------------------
// Save-path validation (SB-06)
// ---------------------------------------------------------------------------

/**
 * One structured issue, field for field with the backend's `schema.make_issue`, so a
 * client-side refusal and a server-side refusal read the same way and can be diffed.
 */
export const saveIssue = (code, message, { nodeId = null, field = null, expected = null, actual = null, fixHint = '' } = {}) => ({
  code,
  severity: 'error',
  node_id: nodeId,
  edge_id: null,
  field,
  message,
  expected,
  actual,
  fix_hint: fixHint,
});

/** A save refused locally. Carries the complete issue list, never just the first one. */
export class SaveValidationError extends Error {
  constructor(issues) {
    super(issues.map((issue) => issue.message).join(' '));
    this.name = 'SaveValidationError';
    this.issues = issues;
  }
}

/**
 * The traded market, read from the DATA node's own validated params (Requirements 12.3, 12.4).
 *
 * There is no fallback. The pre-fix path did
 * `serNodes.find((n) => n.type === 'ccxt_asset_feed')?.params?.symbol || 'BTC/USDT'`, against a
 * node type the palette never produced, so the fallback fired in normal operation and every
 * strategy that omitted a symbol was silently saved against BTC/USDT at 15m (SB-06). A missing
 * value is now a refusal that names the node and the field.
 *
 * @param {object} graph A canonical graph from `toCanonical`.
 * @returns {{symbol: string, timeframe: string}}
 * @throws {SaveValidationError}
 */
export function resolveMarketIdentity(graph) {
  const dataNodes = (graph.nodes || []).filter((node) => node.category === 'DATA');
  if (dataNodes.length === 0) {
    throw new SaveValidationError([
      saveIssue(
        'MISSING_REQUIRED_CATEGORY',
        'This strategy has no DATA block, so it names no market. The traded symbol and ' +
          'timeframe are read from a DATA block, never assumed.',
        {
          field: 'category',
          expected: 'at least one DATA block',
          actual: 'none',
          fixHint: 'Add a DATA block and set its symbol and timeframe.',
        },
      ),
    ]);
  }

  const issues = [];
  for (const node of dataNodes) {
    for (const field of MARKET_IDENTITY_FIELDS) {
      const value = node.params ? node.params[field] : undefined;
      if (typeof value !== 'string' || value.trim() === '') {
        issues.push(
          saveIssue(
            'PARAM_REQUIRED_MISSING',
            `DATA block '${node.id}' (${node.block_id}) has no ${field}. A saved strategy ` +
              `carries the market the author chose; no ${field} is substituted for it.`,
            {
              nodeId: node.id,
              field,
              expected: `a ${field} on '${node.id}'`,
              actual: value === undefined ? null : value,
              fixHint: `Select a ${field} on the '${node.block_id}' block in the inspector.`,
            },
          ),
        );
      }
    }
  }
  if (issues.length > 0) throw new SaveValidationError(issues);

  return {
    symbol: dataNodes[0].params.symbol,
    timeframe: dataNodes[0].params.timeframe,
  };
}

/**
 * The save payload.
 *
 * Deliberately holds **no** `exchange` key: exchange identity is a deployment binding and is
 * never persisted with a strategy (Requirement 12.1). `symbol` and `timeframe` are the DATA
 * node's values, passed through so the legacy `strategies.symbol` / `.timeframe` columns agree
 * with the graph; the graph itself remains the authority.
 */
export function buildSavePayload(graph, market, { name }) {
  return {
    name,
    graph_json: graph,
    nodes: graph.nodes,
    edges: graph.edges,
    symbol: market.symbol,
    timeframe: market.timeframe,
  };
}

// ---------------------------------------------------------------------------
// Palette
// ---------------------------------------------------------------------------

/**
 * Does this descriptor match a palette query (Requirement 4.13)?
 *
 * Matches display name, block id, description and category — for every category, not only the
 * three the old search knew about.
 */
export function matchesPaletteQuery(descriptor, query, categoryName = '') {
  const needle = typeof query === 'string' ? query.trim().toLowerCase() : '';
  if (needle === '') return true;
  const haystack = [
    descriptor.display_name,
    descriptor.block_id,
    descriptor.description,
    descriptor.category,
    categoryName,
  ]
    .filter((part) => typeof part === 'string' && part !== '')
    .join(' ')
    .toLowerCase();
  return haystack.includes(needle);
}

/**
 * The parameter values a newly dropped node starts with.
 *
 * A declared default is copied so the author sees the block's real default (Requirement 5.5).
 * A behaviour-changing parameter is **never** prefilled: `symbol`, `timeframe`, `quantity` and
 * the rest are published `required` with no default precisely so the author must choose
 * (Requirement 5.4), and a prefilled `quantity` is a position size nobody picked.
 */
export function defaultParamsFor(descriptor) {
  const params = {};
  for (const spec of descriptor.params || []) {
    if (!spec || typeof spec.key !== 'string') continue;
    if (BEHAVIOUR_CHANGING_PARAMS.includes(spec.key)) continue;
    if (spec.default === null || spec.default === undefined) continue;
    params[spec.key] = spec.default;
  }
  return params;
}

/**
 * A canvas node built from a registry descriptor.
 *
 * `data.block_id` and `data.category` are copied from the descriptor, which is the whole point:
 * `toCanonical` reads them from there and refuses to guess. `data.inputs` / `data.outputs` carry
 * the descriptor's ports so edges are port-addressed, and `type` is the React Flow renderer
 * selector only — no semantics are ever read back out of it.
 */
export function createNodeFromDescriptor(descriptor, { id, position }) {
  return {
    id,
    type: descriptor.block_id,
    position,
    data: {
      block_id: descriptor.block_id,
      category: descriptor.category,
      label: descriptor.display_name || descriptor.block_id,
      params: defaultParamsFor(descriptor),
      inputs: descriptor.inputs || [],
      outputs: descriptor.outputs || [],
      descriptor,
    },
  };
}

// ---------------------------------------------------------------------------
// Registry state
// ---------------------------------------------------------------------------

/** The registry snapshot, live. Zero blocks unless the state is `ready` — fail closed. */
function useRegistry() {
  const [snapshot, setSnapshot] = useState(getRegistrySnapshot);
  useEffect(() => {
    const unsubscribe = subscribe(setSnapshot);
    loadRegistry();
    return unsubscribe;
  }, []);
  return snapshot;
}

/** The registry payload shape `connectionLegality.js` reads: served blocks plus the matrix. */
const legalityPayload = (snapshot) => ({
  registry_version: snapshot.registryVersion,
  blocks: snapshot.blocks,
  compatibility_matrix: snapshot.compatibilityMatrix,
});

/**
 * The legality verdicts for the edge currently being dragged, or null when nothing is dragging.
 * Read by every handle so what is dimmed during the drag is what the drop would refuse.
 */
const DragLegalityContext = createContext(null);

// ---------------------------------------------------------------------------
// Canvas presentation
// ---------------------------------------------------------------------------

const ApiSyncIndicator = ({ color, text, active = true }) => (
  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginTop: '10px', paddingTop: 6, borderTop: `1px dashed ${C.border}` }}>
    <div style={{ width: 6, height: 6, borderRadius: '50%', background: active ? color : C.t3, boxShadow: active ? `0 0 8px ${color}` : 'none', transition: 'all 0.3s' }} />
    <span className="text-micro" style={{ color: active ? C.t2 : C.t4, letterSpacing: 1, fontFamily: 'monospace', textTransform: 'uppercase', fontWeight: 700 }}>{text}</span>
  </div>
);

/**
 * The node chrome. `severity` is the backend report's verdict for this node; `unvalidated`
 * says the graph has changed since the last verdict, drawn as the design's dotted border.
 * Neither state is carried by the border alone — `DynamicNode` also renders the count and
 * the severity as text with an accessible name.
 */
const PremiumNodeWrapper = ({
  children,
  color,
  selected = false,
  hasError = false,
  severity = null,
  unvalidated = false,
}) => {
  const [isHovered, setIsHovered] = useState(false);
  const borderColor =
    severity === SEVERITY_ERROR || hasError
      ? C.red
      : severity === SEVERITY_WARNING
        ? C.gold
        : selected
          ? C.cyan
          : isHovered
            ? color
            : color + '90';
  const borderStyle = unvalidated && severity === null && !hasError ? 'dotted' : 'solid';
  const shadowStyle = selected
    ? `0 0 0 2px ${C.cyan}, 0 0 24px rgba(0,212,255,0.28)`
    : isHovered
      ? `0 4px 14px rgba(0,0,0,0.45), 0 0 12px ${color}35`
      : `0 2px 8px rgba(0,0,0,0.3), 0 0 8px ${color}15`;

  return (
    <div
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      style={{
        minWidth: 170,
        background: C.bg3,
        border: `2px ${borderStyle} ${borderColor}`,
        borderRadius: 8,
        color: C.t1,
        padding: '8px 10px',
        boxShadow: shadowStyle,
        fontFamily: 'monospace',
        transition: 'all 0.2s cubic-bezier(0.4, 0, 0.2, 1)',
        cursor: 'pointer',
        position: 'relative',
      }}
    >
      {children}
    </div>
  );
};

/** Evenly spaced handle offsets, so a block with several ports has several reachable handles. */
const handleOffset = (index, total) => `${((index + 1) / (total + 1)) * 100}%`;

/**
 * A colour for one runtime state (task 8.5).
 *
 * An addition to the reading, never the reading itself: the label word and the bar count are
 * text in the same element, and the accessible name carries both. A state this build does not
 * know gets the neutral tone rather than a passing one.
 */
const runtimeTone = (state) => {
  if (state === 'READY') return C.cyan;
  if (state === 'WARMING' || state === 'TRAINING') return C.gold;
  if (state === 'AWAITING_MODEL' || state === 'NOT_READY') return C.t2;
  return C.t3;
};

/**
 * One canvas node, drawn from the descriptor recorded on the node.
 *
 * Every declared port gets its own handle carrying the port name as its `id`, which is what
 * makes `sourceHandle` / `targetHandle` — and therefore `source_port` / `target_port` — real.
 * During a drag, an input handle that cannot accept the dragged port is dimmed *and* marked
 * `aria-disabled` with the rejecting reason in its accessible name, so the state is not carried
 * by colour alone (Requirement 6.1).
 */
const DynamicNode = React.memo(function DynamicNode({ id, data, selected }) {
  const drag = useContext(DragLegalityContext);
  const category = data.category || null;
  const CategoryIcon = category ? getCategoryIcon(category) : Activity;
  const color = category ? getCategoryColor(category) : C.t2;
  const inputs = Array.isArray(data.inputs) ? data.inputs : [];
  const outputs = Array.isArray(data.outputs) ? data.outputs : [];
  const marker = data.validation || null;
  const hasError = Boolean(data.hasError) || (marker !== null && marker.severity === SEVERITY_ERROR);

  return (
    <PremiumNodeWrapper
      color={color}
      selected={selected}
      hasError={hasError}
      severity={marker ? marker.severity : null}
      unvalidated={Boolean(data.unvalidated)}
    >
      {inputs.map((port, index) => {
        const verdict = drag ? drag.verdictFor(id, port.port) : null;
        const dimmed = drag !== null && (verdict === null || !verdict.legal);
        const reason = verdict && verdict.message ? verdict.message : 'not compatible with the connection being dragged';
        return (
          <Handle
            key={`in-${port.port}`}
            id={port.port}
            type="target"
            position={Position.Left}
            isConnectable={!dimmed}
            aria-label={`Input port ${port.port} of type ${port.type}${dimmed ? ` — unavailable: ${reason}` : ''}`}
            aria-disabled={dimmed ? 'true' : undefined}
            title={dimmed ? reason : `${port.port}: ${port.type}`}
            data-port-name={port.port}
            data-port-type={port.type}
            data-port-legal={drag === null ? undefined : String(!dimmed)}
            style={{
              top: handleOffset(index, inputs.length),
              background: dimmed ? C.t4 : color,
              opacity: dimmed ? 0.3 : 1,
              border: `1px solid ${C.bg1}`,
              width: '0.625rem',
              height: '0.625rem',
            }}
          />
        );
      })}

      <div style={{ color, letterSpacing: 1, textTransform: 'uppercase', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '4px' }}>
        <CategoryIcon size={10} />
        <span className="text-micro">{(category || 'UNRESOLVED').replace(/_/g, ' ')}</span>
      </div>
      <div className="text-body" style={{ fontWeight: 900 }}>{data.label || data.block_id}</div>
      <div className="text-micro" style={{ color: C.t3 }}>{data.block_id}</div>

      {/*
        The node marker (Requirement 8.10). Severity *and* count are text, and the badge
        carries its own accessible name, so nothing here is signalled by colour alone. The
        first `fix_hint` is shown inline; the full list lives in the issues panel.
      */}
      {marker && (
        <div
          data-testid="node-marker"
          data-node-id={id}
          data-severity={marker.severity}
          data-issue-count={marker.count}
          data-source={marker.source}
          data-codes={marker.codes.join(' ')}
          aria-label={`Block ${id}: ${markerLabel(marker)}`}
          title={marker.issues.map((issue) => issue.fix_hint || issue.message).join('\n')}
          className="text-micro"
          style={{
            marginTop: '3px',
            color: marker.severity === SEVERITY_ERROR ? C.red : C.gold,
            fontWeight: 700,
          }}
        >
          <span aria-hidden="true">{marker.severity === SEVERITY_ERROR ? '✕' : '⚠'} </span>
          {markerLabel(marker)}
        </div>
      )}
      {marker && marker.issues[0] && (
        <div className="text-micro" style={{ color: C.t2, marginTop: '2px', whiteSpace: 'normal' }}>
          {/* Backend text, verbatim (Requirement 8.9). */}
          {marker.issues[0].fix_hint || marker.issues[0].message}
        </div>
      )}
      {!marker && hasError && (
        <div className="text-micro" style={{ color: C.red, marginTop: '2px' }}>
          ⚠ {data.errorMessage || 'Error'}
        </div>
      )}

      {/*
        The runtime state (task 8.5, Requirement 20.12). Four facts, in the backend's own
        words: warming with a bar count, ready, awaiting a model, training with an epoch
        counter — plus the deployed lock, which is a property of the version and is shown
        on the node as the affordance `design.md`'s Visual states table asks for.

        Everything here is text, and every figure comes from the frame. Nothing on this
        node decides whether a block is warming, how many bars it still needs, or whether
        the canvas is locked: `dag_engine` decides the first two and `strategy_lifecycle`
        the third, and re-deciding either here is how the canvas would come to disagree
        with the runtime it is describing.
      */}
      {data.runtime && data.runtime.known && (
        <div
          data-testid="node-runtime-state"
          data-node-id={id}
          data-runtime-state={data.runtime.state}
          data-bars-seen={data.runtime.barsSeen === null ? undefined : data.runtime.barsSeen}
          data-bars-needed={data.runtime.barsNeeded === null ? undefined : data.runtime.barsNeeded}
          data-bars-remaining={
            data.runtime.barsRemaining === null ? undefined : data.runtime.barsRemaining
          }
          data-epoch={data.runtime.training?.epoch ?? undefined}
          data-epochs-total={data.runtime.training?.epochsTotal ?? undefined}
          aria-label={
            `Block ${id} runtime state: ${data.runtime.label}` +
            (data.runtime.detail ? ` — ${data.runtime.detail}` : '')
          }
          title={data.runtime.detail || undefined}
          className="text-micro"
          style={{
            marginTop: '3px',
            color: runtimeTone(data.runtime.state),
            fontWeight: 700,
            whiteSpace: 'normal',
          }}
        >
          {data.runtime.label}
          {data.runtime.detail ? <span style={{ color: C.t2, fontWeight: 400 }}> — {data.runtime.detail}</span> : null}
        </div>
      )}
      {data.deployedLock && (
        <div
          data-testid="node-deployed-lock"
          data-node-id={id}
          aria-label={`Block ${id} is locked: this version is deployed and cannot be edited`}
          title={data.deployedLockReason || undefined}
          className="text-micro"
          style={{ marginTop: '2px', color: C.gold, fontWeight: 700 }}
        >
          <span aria-hidden="true">🔒 </span>
          Locked (deployed)
        </div>
      )}

      {outputs.map((port, index) => (
        <Handle
          key={`out-${port.port}`}
          id={port.port}
          type="source"
          position={Position.Right}
          aria-label={`Output port ${port.port} of type ${port.type}`}
          title={`${port.port}: ${port.type}`}
          data-port-name={port.port}
          data-port-type={port.type}
          style={{
            top: handleOffset(index, outputs.length),
            background: color,
            border: `1px solid ${C.bg1}`,
            width: '0.625rem',
            height: '0.625rem',
          }}
        />
      ))}
    </PremiumNodeWrapper>
  );
});

/**
 * A block's input and output port types, as chips (Requirement 4.14).
 *
 * The direction and the type are both spelled out in text, and the port's own name is in the
 * `title`, so nothing here depends on colour.
 */
const PortChips = ({ ports, direction }) => {
  if (!Array.isArray(ports) || ports.length === 0) return null;
  const label = direction === 'in' ? 'in' : 'out';
  return (
    <ul
      aria-label={direction === 'in' ? 'Input port types' : 'Output port types'}
      style={{ display: 'flex', flexWrap: 'wrap', gap: 4, listStyle: 'none', margin: '4px 0 0', padding: 0 }}
    >
      {ports.map((port) => (
        <li
          key={`${label}-${port.port}`}
          className="text-micro"
          data-testid="port-chip"
          data-port-direction={label}
          data-port-name={port.port}
          data-port-type={port.type}
          title={`${direction === 'in' ? 'Input' : 'Output'} port "${port.port}": ${port.type}${port.required ? ', required' : ''}${port.variadic ? ', accepts several connections' : ''}`}
          style={{
            border: `1px solid ${C.border}`,
            borderRadius: '0.25rem',
            padding: '1px 4px',
            color: C.t2,
            fontFamily: 'monospace',
            background: C.bg2,
          }}
        >
          {label} {port.type}
        </li>
      ))}
    </ul>
  );
};

/**
 * The palette failure state (Requirement 4.12).
 *
 * An explicit panel, a real focusable `<button>` for the retry, and zero block entries. The
 * retry is a genuine refetch: `registryClient` drops its cached payload on any error, so it
 * cannot serve a registry the backend has since disowned.
 */
const PaletteErrorPanel = ({ error, onRetry, retrying }) => (
  <div
    role="alert"
    data-testid="palette-error"
    data-error-code={error ? error.code : undefined}
    style={{ border: `1px solid ${C.red}`, background: `${C.red}12`, borderRadius: '0.375rem', padding: '10px', display: 'flex', flexDirection: 'column', gap: '6px' }}
  >
    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: C.red }}>
      <AlertTriangle size={14} />
      <span className="text-micro" style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1 }}>
        Block palette unavailable
      </span>
    </div>
    <p className="text-micro" style={{ color: C.t2, margin: 0 }}>
      {error ? error.message : 'The block registry could not be loaded.'}
    </p>
    <p className="text-micro" style={{ color: C.t3, margin: 0, fontFamily: 'monospace' }}>
      {error ? error.code : 'REGISTRY_UNAVAILABLE'}
      {error && error.status ? ` · HTTP ${error.status}` : ''}
    </p>
    {error && error.authExpired ? (
      <p className="text-micro" style={{ color: C.t2, margin: 0 }}>
        This session is no longer signed in. Signing in again is the fix; the registry itself may
        be healthy.
      </p>
    ) : null}
    <p className="text-micro" style={{ color: C.t3, margin: 0 }}>
      No blocks are shown while the registry is unreachable. The palette never substitutes a
      local list, because a stale catalogue is how a block the engine cannot run reaches a
      strategy.
    </p>
    <button
      type="button"
      onClick={onRetry}
      disabled={retrying}
      data-testid="palette-retry"
      style={{
        alignSelf: 'flex-start',
        display: 'flex',
        alignItems: 'center',
        gap: '6px',
        background: C.bg3,
        border: `1px solid ${C.borderLight}`,
        borderRadius: '0.375rem',
        padding: '6px 10px',
        color: C.t1,
        fontFamily: 'monospace',
        cursor: retrying ? 'wait' : 'pointer',
      }}
    >
      <RefreshCw size={12} />
      {retrying ? 'Retrying…' : 'Retry'}
    </button>
  </div>
);

/** The empty marker set. Built once, so "no report" has a stable identity. */
const NO_MARKERS = collectMarkers(null);

const SEVERITY_COLOUR = { [SEVERITY_ERROR]: C.red, [SEVERITY_WARNING]: C.gold };

/**
 * One issue row.
 *
 * The text is the backend's own: `fix_hint` first, `message` second, both verbatim
 * (Requirement 8.9). Every row that names a node or a connection is a real `<button>`, so the
 * list is keyboard-navigable and each entry is a way to reach the thing it is complaining
 * about. Severity is spelled out in text and in `data-severity`, never colour alone.
 */
const ValidationIssueRow = ({ issue, onFocus }) => {
  const severity = issue.severity;
  const colour = SEVERITY_COLOUR[severity] || C.t2;
  const target = issue.edge_id ? `connection ${issue.edge_id}` : issue.node_id ? `block ${issue.node_id}` : 'the whole strategy';
  const body = (
    <>
      <span className="text-micro" style={{ color: colour, fontWeight: 700, textTransform: 'uppercase' }}>
        {severity}
      </span>{' '}
      <span className="text-micro" style={{ color: C.t3, fontFamily: 'monospace' }}>{issue.code}</span>{' '}
      <span style={{ color: C.t1 }}>{issue.fix_hint ? issue.fix_hint : issue.message}</span>
      {issue.fix_hint && issue.message && issue.fix_hint !== issue.message ? (
        <span style={{ display: 'block', color: C.t2 }}>{issue.message}</span>
      ) : null}
      {issue.expected !== null && issue.expected !== undefined ? (
        <span style={{ display: 'block', color: C.t3, fontFamily: 'monospace' }}>
          expected {JSON.stringify(issue.expected)} · got {JSON.stringify(issue.actual ?? null)}
        </span>
      ) : null}
      {issue.server_override ? (
        <span style={{ display: 'block', color: C.gold }} data-testid="server-override">
          The server recomputed this value; the strategy will run with the server's version.
        </span>
      ) : null}
    </>
  );

  const attributes = {
    'data-testid': 'validation-issue',
    'data-code': issue.code || undefined,
    'data-severity': severity,
    'data-node-id': issue.node_id || undefined,
    'data-edge-id': issue.edge_id || undefined,
    'data-field': issue.field || undefined,
    'data-scope': issue.edge_id ? 'edge' : issue.node_id ? 'node' : 'graph',
    'data-server-override': issue.server_override ? 'true' : undefined,
  };

  return (
    <li style={{ borderTop: `1px solid ${C.border}`, padding: '4px 0' }} className="text-micro">
      {onFocus ? (
        <button
          type="button"
          onClick={onFocus}
          aria-label={`${severity}: ${issue.fix_hint || issue.message} — go to ${target}`}
          {...attributes}
          style={{ display: 'block', width: '100%', textAlign: 'left', background: 'transparent', border: 'none', padding: 0, cursor: 'pointer', color: C.t1 }}
        >
          {body}
        </button>
      ) : (
        <div {...attributes}>{body}</div>
      )}
    </li>
  );
};

/**
 * The issue list (Requirements 8.9, 8.10).
 *
 * Three groups, because the report has three kinds of subject and the third one is the one a
 * naive per-node projection loses: `MISSING_REQUIRED_CATEGORY` and the other graph-level codes
 * name neither a node nor an edge, so they get their own group rather than being dropped.
 */
const ValidationIssuePanel = ({ markers, stale, onFocusNode, onFocusEdge }) => {
  if (markers.issues.length === 0) return null;
  const nodeMarkers = Object.values(markers.nodes);
  const edgeMarkers = Object.values(markers.edges);

  return (
    <div
      data-testid="validation-issues"
      data-stale={stale ? 'true' : 'false'}
      data-error-count={markers.errorCount}
      data-warning-count={markers.warningCount}
      data-graph-issue-count={markers.graph.length}
      data-override-count={markers.overrides.length}
      style={{ borderTop: `1px solid ${C.border}`, padding: '8px 12px', overflowY: 'auto', maxHeight: 260 }}
    >
      <h3 className="text-micro" style={{ color: C.t2, margin: '0 0 4px', textTransform: 'uppercase', letterSpacing: 1 }}>
        Validation issues
      </h3>
      {stale ? (
        <p role="status" data-testid="validation-issues-stale" className="text-micro" style={{ color: C.gold, margin: '0 0 4px' }}>
          This report describes an earlier version of this graph. The canvas has changed since,
          so these markers are not shown on it.
        </p>
      ) : null}

      {markers.graph.length > 0 && (
        <section aria-label="Issues with the whole strategy" data-testid="graph-issues">
          <h4 className="text-micro" style={{ color: C.t3, margin: '4px 0 0', textTransform: 'uppercase' }}>
            Whole strategy ({markers.graph.length})
          </h4>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {markers.graph.map((issue, index) => (
              <ValidationIssueRow key={`graph-${issue.code}-${index}`} issue={issue} onFocus={null} />
            ))}
          </ul>
        </section>
      )}

      {nodeMarkers.map((marker) => (
        <section key={`node-${marker.id}`} aria-label={`Issues with block ${marker.id}`}>
          <h4
            className="text-micro"
            data-testid="node-issue-group"
            data-node-id={marker.id}
            data-severity={marker.severity}
            data-issue-count={marker.count}
            style={{ color: C.t3, margin: '4px 0 0', textTransform: 'uppercase' }}
          >
            Block {marker.id} — {markerLabel(marker)}
          </h4>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {marker.issues.map((issue, index) => (
              <ValidationIssueRow
                key={`${marker.id}-${issue.code}-${index}`}
                issue={issue}
                onFocus={() => onFocusNode(marker.id)}
              />
            ))}
          </ul>
        </section>
      ))}

      {edgeMarkers.map((marker) => (
        <section key={`edge-${marker.id}`} aria-label={`Issues with connection ${marker.id}`}>
          <h4
            className="text-micro"
            data-testid="edge-issue-group"
            data-edge-id={marker.id}
            data-severity={marker.severity}
            data-issue-count={marker.count}
            style={{ color: C.t3, margin: '4px 0 0', textTransform: 'uppercase' }}
          >
            Connection {marker.id} — {markerLabel(marker)}
          </h4>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {marker.issues.map((issue, index) => (
              <ValidationIssueRow
                key={`${marker.id}-${issue.code}-${index}`}
                issue={issue}
                onFocus={() => onFocusEdge(marker.id)}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
};

/** One status-strip cell. `known={false}` renders the honest unknown, never a passing state. */
const StatusCell = ({ testId, label, state, text, detail, known = true, tone = null }) => (
  <span
    data-testid={testId}
    data-state={state}
    data-known={known ? 'true' : 'false'}
    title={detail || undefined}
    style={{ color: tone || (known ? C.t2 : C.t3), display: 'inline-flex', gap: 4 }}
  >
    <span style={{ color: C.t3 }}>{label}</span>
    <span style={{ fontWeight: 700 }}>{text}</span>
    {known ? null : <span style={{ color: C.t3 }}>(unknown)</span>}
  </span>
);

// ---------------------------------------------------------------------------
// The builder
// ---------------------------------------------------------------------------

/**
 * @param {object} props
 * @param {object} [props.feedObservation] A **direct** observation of the market feed held by
 *   a caller: `{ connected, lastEventAt, timeframe, expectedIntervalMs, availableBars,
 *   warmupBars }`. Normally absent, and then the strip's feed reading comes from the backend:
 *   task 7.11 polls `GET /strategy-operations/strategies/{id}/data-quality` for a saved
 *   strategy and renders the server's own state and `display` sentence. An unsaved canvas has
 *   no version to report on, a read in flight or a failed read is reported as an explicit
 *   unknown, and none of those is ever shown as `LIVE` (Requirements 19.7-19.10). When this
 *   prop *is* supplied it wins, because a direct observation is closer to the feed than a
 *   report about the saved version.
 * @param {object} [props.trainingJob] One training job as `GET /training/jobs/{job_id}`
 *   reports it (task 6.6): `{ job_id, status, epoch_current, epochs_total, progress,
 *   eta_seconds, eta_state, failure_reason, ... }`. Absent means unknown, not trained.
 * @param {object} [props.trainingBlock] A blocked training half — either the save
 *   response's `training` field with `state: 'BLOCKED'`, or the 422 `detail` from
 *   `POST /training/jobs`. Rendered with its required and available quantities
 *   (Requirement 14.9). A save made from this page sets it from its own response; the prop
 *   exists so a caller that already holds one can hand it over.
 * @param {string} [props.deploymentId] The running deployment this canvas is watching, if
 *   any (task 8.5). Supplied rather than discovered because a deployment is not a property
 *   of the canvas: one saved strategy can have several, and the runtime state on screen has
 *   to be attributable to the one the caller opened. Absent means the `deployment.*` and
 *   `execution.*` channels are not subscribed — not that there is no deployment.
 * @param {boolean} [props.realtimeEnabled] Switch the realtime subscription off for a view
 *   that does not want one. The 30 s status poll still runs, so the strip stays honest.
 */
function StrategyBuilderCanvas({
  initialStrategy,
  strategyProp,
  onBackProp,
  onBacktestProp,
  feedObservation = null,
  trainingJob = null,
  trainingBlock = null,
  deploymentId = '',
  realtimeEnabled = true,
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const strategy = strategyProp ?? location.state?.strategy ?? null;
  const onBack = onBackProp ?? (() => navigate('/app/strategies'));
  const onBacktest = onBacktestProp ?? ((payload) => navigate('/app/backtest', { state: { strategy: payload } }));

  const { zoomIn, zoomOut, fitView } = useReactFlow();
  const { pushState, undo, redo, canUndo, canRedo } = useUndoRedo();
  // Local checks are advisory. They render only while the backend has no verdict for the
  // graph currently on the canvas; see `backendAuthoritative` below.
  const { errors, warnings, isValid, validateGraph } = useValidation();

  const registry = useRegistry();
  const [retryingRegistry, setRetryingRegistry] = useState(false);

  const [searchQuery, setSearchQuery] = useState('');
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [isSavingStrategy, setIsSavingStrategy] = useState(false);
  const [saveState, setSaveState] = useState('');
  const [saveStatus, setSaveStatus] = useState(SAVE_STATES.UNSAVED);
  const [saveIssues, setSaveIssues] = useState([]);
  // A blocked training half observed by this page's own save. Separate from `saveIssues`
  // because the two say different things: a save issue means nothing was persisted, while a
  // training block means the version WAS saved and only training was refused (Requirements
  // 14.3, 14.4, 14.7, 14.8 — and no job row exists either way).
  const [observedTrainingBlock, setObservedTrainingBlock] = useState(null);
  const [collapsedCategories, setCollapsedCategories] = useState({});
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [libraryOpen, setLibraryOpen] = useState(true);
  const [canvasNotice, setCanvasNotice] = useState(null);
  const [connectionIssue, setConnectionIssue] = useState(null);
  const [dragLegality, setDragLegality] = useState(null);
  const [inspectorBlocking, setInspectorBlocking] = useState({});

  const reactFlowWrapper = useRef(null);
  const nodeSeq = useRef(0);
  const loadedStrategy = initialStrategy || strategy || null;
  const [strategyIdState, setStrategyIdState] = useState(loadedStrategy?.id || null);
  const [strategyName, setStrategyName] = useState(loadedStrategy?.name || 'Untitled Strategy');

  // The canvas starts empty. It used to be seeded with a `ccxt_asset_feed` node carrying
  // `symbol: "BTC/USDT"` and `timeframe: "15m"` — a market nobody chose, which is SB-06 — and
  // that node carried no registry descriptor, so it could not be serialized at all.
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);

  // -- registry-derived views ---------------------------------------------

  const paletteSections = useMemo(
    () => (registry.isReady ? getPaletteSections() : []),
    [registry],
  );

  const registryPayload = useMemo(
    () => (registry.isReady ? legalityPayload(registry) : null),
    [registry],
  );

  /** React Flow renderer selectors, one per served block id (`fromCanonical` sets `type`). */
  const nodeTypes = useMemo(() => {
    const map = {};
    for (const block of registry.blocks) map[block.block_id] = DynamicNode;
    return map;
  }, [registry]);

  // -- the one serialization path -----------------------------------------

  /**
   * The canvas as a canonical graph, or the `CanonicalGraphError` that stopped it.
   *
   * Held rather than recomputed per call site so the save path, the connection validator and
   * the drag-time dimming all judge exactly the same graph.
   */
  const canonical = useMemo(() => {
    try {
      return {
        graph: toCanonical(nodes, edges, {
          name: strategyName,
          strategy_id: strategyIdState || '',
        }),
        error: null,
      };
    } catch (error) {
      if (error instanceof CanonicalGraphError) return { graph: null, error };
      throw error;
    }
  }, [nodes, edges, strategyName, strategyIdState]);

  const canonicalRef = useRef(canonical.graph);
  canonicalRef.current = canonical.graph;

  /**
   * The identity of what is on the canvas, semantically.
   *
   * `ui` is excluded, so dragging a node is not an edit: it neither marks the graph
   * unvalidated nor spends a request (Property 4 — the identity hash is invariant under
   * presentation-only change). Critically, marker write-back also lives outside the key
   * (`node.data.validation`), which is what stops the marker effect from re-triggering the
   * validation that produced the marker.
   */
  const graphKey = useMemo(() => semanticGraphKey(canonical.graph), [canonical.graph]);

  // -- validation: local advisory, backend authoritative ------------------

  useEffect(() => {
    validateGraph(nodes, edges);
  }, [nodes, edges, validateGraph]);

  const [validation, dispatchValidation] = useReducer(validationReducer, undefined, initialValidationState);

  /** Monotonic request id. Keyed against, so a late answer can be recognised as late. */
  const requestSeq = useRef(0);

  /**
   * Ask the backend. Never throws: every outcome is a dispatch, so a rejected request cannot
   * leave the machine stuck in `validating`.
   *
   * The call goes through `strategiesApi`, i.e. the shared authenticated axios instance. No
   * token is read or stored here.
   */
  const requestValidation = useCallback(async (key, graph) => {
    const requestId = requestSeq.current + 1;
    requestSeq.current = requestId;
    dispatchValidation({ type: 'request', requestId, graphKey: key });
    try {
      const report = await strategiesApi.validate(graph);
      // The endpoint answers 200 for an invalid graph, so a body without a boolean `valid`
      // is not "invalid" — it is a broken contract, and saying "invalid" would be a verdict
      // the backend never gave.
      if (report === null || typeof report !== 'object' || typeof report.valid !== 'boolean') {
        dispatchValidation({
          type: 'failed',
          requestId,
          error: {
            code: 'VALIDATION_MALFORMED',
            message:
              'The validation endpoint answered with a body that carries no verdict, so this ' +
              'graph has not been validated.',
          },
        });
        return;
      }
      dispatchValidation({ type: 'report', requestId, graphKey: key, report, at: Date.now() });
    } catch (error) {
      dispatchValidation({
        type: 'failed',
        requestId,
        error: {
          code: error?.code || 'VALIDATION_REQUEST_FAILED',
          status: error?.status ?? error?.response?.status ?? null,
          message: error?.message || 'The validation request failed.',
        },
      });
    }
  }, []);

  // Requirement 8.10, first half: the graph is unvalidated the moment it changes. This runs
  // before the debounce below, so there is no window in which an edited graph still reads as
  // valid.
  useEffect(() => {
    if (graphKey === null) {
      dispatchValidation({
        type: 'unserializable',
        issue: canonical.error
          ? saveIssue(canonical.error.code, canonical.error.message)
          : saveIssue('GRAPH_UNSERIALIZABLE', 'This canvas cannot be serialized.'),
      });
      return;
    }
    dispatchValidation({ type: 'edit', graphKey });
  }, [graphKey, canonical.error]);

  // Requirement 8.10, second half: one request, 400 ms after the *last* edit. The cleanup
  // cancels the pending timer on every further edit, so ten rapid edits cost one request.
  useEffect(() => {
    if (graphKey === null || nodes.length === 0) return undefined;
    const timer = setTimeout(() => {
      const graph = canonicalRef.current;
      if (graph === null) return;
      requestValidation(graphKey, graph);
    }, VALIDATION_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // `nodes.length` rather than `nodes`: a marker write-back changes the array identity but
    // not the count, and must not restart the debounce.
  }, [graphKey, nodes.length, requestValidation]);

  /** The report, but only while it describes the graph on the canvas. */
  const liveReport = reportAppliesToCanvas(validation) ? validation.report : null;

  /** True while the backend has an opinion about exactly this graph. It then owns the UI. */
  const backendAuthoritative = liveReport !== null;

  /**
   * Markers from the last report received, whether or not it still applies. The issue list
   * renders these labelled stale after an edit, because throwing away the only report the
   * author has seen is worse than telling them it is one edit old.
   */
  const heldMarkers = useMemo(() => collectMarkers(validation.report), [validation.report]);

  /** Markers that may be drawn on the canvas: only ever from a report for *this* graph. */
  const markers = backendAuthoritative ? heldMarkers : NO_MARKERS;

  /**
   * The advisory local checks, in marker shape — used only when the backend has not spoken
   * about this graph. Two verdicts are never rendered at once (design.md → advisory vs
   * authority): the moment a report lands for this graph it replaces these entirely.
   */
  const localNodeMarkers = useMemo(() => {
    const collected = {};
    const add = (entry, severity) => {
      if (!entry || !entry.nodeId) return;
      const marker =
        collected[entry.nodeId] ||
        (collected[entry.nodeId] = {
          scope: 'node',
          id: entry.nodeId,
          severity: null,
          errorCount: 0,
          warningCount: 0,
          count: 0,
          overrideCount: 0,
          codes: [],
          issues: [],
          source: 'local',
        });
      marker.count += 1;
      if (severity === SEVERITY_ERROR) marker.errorCount += 1;
      else marker.warningCount += 1;
      marker.severity = marker.errorCount > 0 ? SEVERITY_ERROR : SEVERITY_WARNING;
      const code = String(entry.type || 'ADVISORY').toUpperCase();
      if (!marker.codes.includes(code)) marker.codes.push(code);
      marker.issues.push({
        code,
        severity,
        node_id: entry.nodeId,
        edge_id: null,
        field: null,
        message: entry.message,
        expected: null,
        actual: null,
        // The local checks have no backend hint to quote, and this file does not invent one.
        fix_hint: '',
        advisory: true,
      });
    };
    for (const error of errors) add(error, SEVERITY_ERROR);
    for (const warning of warnings) add(warning, SEVERITY_WARNING);
    return collected;
  }, [errors, warnings]);

  const nodeMarkers = backendAuthoritative ? markers.nodes : localNodeMarkers;
  const graphUnvalidated =
    validation.state === VALIDATION_STATES.UNVALIDATED ||
    validation.state === VALIDATION_STATES.VALIDATING;

  /**
   * Stamp markers onto the nodes React Flow draws (Requirement 8.10).
   *
   * A new node object is produced **only** when that node's marker signature or unvalidated
   * flag actually changed; otherwise the same object, and the same array, come back. An
   * effect of this shape that returns a fresh array unconditionally is what caused the render
   * loop the previous task had to fix: new nodes → new canonical graph → new report → new
   * nodes, forever.
   */
  useEffect(() => {
    setNodes((current) => {
      let changed = false;
      const next = current.map((node) => {
        const marker = nodeMarkers[node.id] || null;
        const signature = markerSignature(marker);
        const held = node.data.validationSignature || '';
        if (held === signature && Boolean(node.data.unvalidated) === graphUnvalidated) return node;
        changed = true;
        return {
          ...node,
          data: {
            ...node.data,
            validation: marker,
            validationSignature: signature,
            unvalidated: graphUnvalidated,
            hasError: marker !== null && marker.severity === SEVERITY_ERROR,
            errorMessage: marker && marker.issues[0] ? marker.issues[0].message : undefined,
          },
        };
      });
      return changed ? next : current;
    });
  }, [nodeMarkers, graphUnvalidated]);

  /**
   * The same for edges (Requirement 8.10, the connection half).
   *
   * The marker rides in `edge.data.validation`; `data.port_type` is untouched, so this cannot
   * change what `toCanonical` serializes. `ariaLabel` is React Flow's accessible name for the
   * edge, and `label` puts the count on the path as text — the issues panel below lists the
   * same markers as reachable rows, because an SVG path is not a good place to read from.
   */
  useEffect(() => {
    const edgeMarkers = backendAuthoritative ? markers.edges : {};
    setEdges((current) => {
      let changed = false;
      const next = current.map((edge) => {
        const marker = edgeMarkers[edge.id] || null;
        const signature = markerSignature(marker);
        const data = edge.data && typeof edge.data === 'object' ? edge.data : {};
        if ((data.validationSignature || '') === signature) return edge;
        changed = true;
        const stroke = marker === null
          ? C.cyan
          : marker.severity === SEVERITY_ERROR
            ? C.red
            : C.gold;
        return {
          ...edge,
          animated: marker === null,
          label: marker === null ? undefined : markerLabel(marker),
          ariaLabel:
            marker === null
              ? `Connection ${edge.id}`
              : `Connection ${edge.id}: ${markerLabel(marker)}`,
          style: { ...(edge.style || {}), stroke, strokeWidth: 2, strokeDasharray: marker === null ? undefined : '6 3' },
          data: { ...data, validation: marker, validationSignature: signature },
        };
      });
      return changed ? next : current;
    });
  }, [markers, backendAuthoritative]);

  /**
   * Every required parameter still unset, per node — the save gate (Requirement 5.4).
   *
   * Computed for the whole graph rather than only the selected node, because a blocking
   * parameter on a node nobody has clicked blocks the save just the same. The inspector's
   * `onBlockingChange` is folded in on top, so what the form shows and what the gate enforces
   * cannot disagree.
   */
  const blockingParams = useMemo(() => {
    const collected = [];
    for (const node of nodes) {
      const descriptor = node.data.descriptor || getDescriptor(node.data.block_id);
      const fromDescriptor = descriptor ? blockingParamKeys(descriptor.params, node.data.params) : [];
      const fromInspector = inspectorBlocking[node.id] || [];
      const keys = [...new Set([...fromDescriptor, ...fromInspector])];
      for (const key of keys) {
        collected.push({ nodeId: node.id, blockId: node.data.block_id, field: key });
      }
    }
    return collected;
    // `registry` is a dependency because a descriptor arriving later changes the answer: a
    // node loaded before the registry answered has no parameter specs to be judged against.
  }, [nodes, inspectorBlocking, registry]);

  // -- canvas events ------------------------------------------------------

  // The history push stays outside the state updater: an updater may run twice, and a
  // side effect inside one would record the same edit twice.
  const onNodesChange = useCallback((changes) => {
    const next = applyNodeChanges(changes, nodes);
    setNodes(next);
    pushState({ nodes: next, edges });
  }, [nodes, edges, pushState]);

  const onEdgesChange = useCallback((changes) => {
    const next = applyEdgeChanges(changes, edges);
    setEdges(next);
    pushState({ nodes, edges: next });
  }, [nodes, edges, pushState]);

  /**
   * React Flow's `isValidConnection`: the R1–R8 gate from `connectionLegality.js`.
   *
   * The client check is UX latency only — the backend re-validates every graph it is sent —
   * but it fails closed: with no registry there is no compatibility matrix to judge against,
   * and "I cannot evaluate the rules" is not "the edge is legal".
   */
  const connectionValidator = useMemo(() => {
    if (registryPayload === null) return null;
    try {
      return reactFlowConnectionValidator({
        // A getter, so the validator stays correct as the canvas changes without being rebuilt
        // on every edit.
        graph: () => canonicalRef.current,
        registry: registryPayload,
        onReject: (issue) => setConnectionIssue(issue),
      });
    } catch {
      return null;
    }
  }, [registryPayload]);

  const isValidConnection = useCallback(
    (connection) => {
      if (connectionValidator === null) {
        setConnectionIssue(
          saveIssue(
            'REGISTRY_UNAVAILABLE',
            'Connections cannot be checked while the block registry is unavailable.',
            { fixHint: 'Retry the block palette, then draw the connection again.' },
          ),
        );
        return false;
      }
      if (canonicalRef.current === null) {
        setConnectionIssue(
          saveIssue(
            canonical.error ? canonical.error.code : 'GRAPH_UNSERIALIZABLE',
            canonical.error ? canonical.error.message : 'This canvas cannot be serialized.',
            { fixHint: 'Remove the block reported below and add it again from the palette.' },
          ),
        );
        return false;
      }
      const accepted = connectionValidator(connection);
      if (accepted) setConnectionIssue(null);
      return accepted;
    },
    [connectionValidator, canonical.error],
  );

  /** Dim every input port that cannot accept the port being dragged, before the drop. */
  const onConnectStart = useCallback(
    (_, { nodeId, handleId, handleType }) => {
      setConnectionIssue(null);
      if (handleType !== 'source' || registryPayload === null || canonicalRef.current === null) return;
      try {
        setDragLegality(
          legalTargetsForDrag(canonicalRef.current, { nodeId, handleId }, registryPayload),
        );
      } catch {
        // A drag whose origin port cannot be resolved dims nothing rather than dimming
        // everything: `isValidConnection` still refuses the drop.
        setDragLegality(null);
      }
    },
    [registryPayload],
  );

  const onConnectEnd = useCallback(() => setDragLegality(null), []);

  const onConnect = useCallback((params) => {
    const edge = {
      id: `e-${params.source}:${params.sourceHandle || ''}-${params.target}:${params.targetHandle || ''}`,
      source: params.source,
      sourceHandle: params.sourceHandle ?? null,
      target: params.target,
      targetHandle: params.targetHandle ?? null,
      animated: true,
      style: { stroke: C.cyan, strokeWidth: 2 },
    };
    if (edges.some((existing) => existing.id === edge.id)) return;
    const next = [...edges, edge];
    setEdges(next);
    pushState({ nodes, edges: next });
    setConnectionIssue(null);
  }, [nodes, edges, pushState]);

  const onNodeClick = useCallback((_, node) => {
    setSelectedNodeId(node.id);
  }, []);

  const handleDragOver = useCallback((event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const mintNodeId = useCallback(() => {
    const taken = new Set(nodes.map((node) => node.id));
    let candidate;
    do {
      nodeSeq.current += 1;
      candidate = `n-${nodeSeq.current}`;
    } while (taken.has(candidate));
    return candidate;
  }, [nodes]);

  /**
   * Create a node from the dropped block id.
   *
   * The descriptor comes from the registry — the only place block shape is read from — and a
   * block id the registry does not publish creates nothing at all. Inventing a node for it
   * would produce a strategy the engine cannot run.
   */
  const handleDrop = useCallback((event) => {
    event.preventDefault();
    const bounds = reactFlowWrapper.current?.getBoundingClientRect();
    const blockId = event.dataTransfer.getData(DRAG_BLOCK_ID_MIME);
    if (!blockId || !bounds) return;

    const descriptor = getDescriptor(blockId);
    if (!descriptor) {
      setCanvasNotice(
        `The block registry publishes no descriptor for '${blockId}', so no block was added. ` +
          'Reload the palette and try again.',
      );
      return;
    }

    const newNode = createNodeFromDescriptor(descriptor, {
      id: mintNodeId(),
      position: {
        x: event.clientX - bounds.left - 85,
        y: event.clientY - bounds.top - 25,
      },
    });

    setCanvasNotice(null);
    const next = [...nodes, newNode];
    setNodes(next);
    pushState({ nodes: next, edges });
    setSelectedNodeId(newNode.id);
  }, [nodes, edges, mintNodeId, pushState]);

  const handleDeleteNode = useCallback(() => {
    if (!selectedNodeId) return;
    setNodes((nds) => nds.filter((node) => node.id !== selectedNodeId));
    setEdges((eds) => eds.filter((edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId));
    setSelectedNodeId(null);
    pushState({
      nodes: nodes.filter((node) => node.id !== selectedNodeId),
      edges: edges.filter((edge) => edge.source !== selectedNodeId && edge.target !== selectedNodeId),
    });
  }, [selectedNodeId, nodes, edges, pushState]);

  const handleUndo = useCallback(() => {
    const previousState = undo();
    if (previousState) {
      setNodes(previousState.nodes);
      setEdges(previousState.edges);
    }
  }, [undo]);

  const handleRedo = useCallback(() => {
    const nextState = redo();
    if (nextState) {
      setNodes(nextState.nodes);
      setEdges(nextState.edges);
    }
  }, [redo]);

  const handleFitView = useCallback(() => {
    fitView({ duration: 800 });
  }, [fitView]);

  const handleRetryRegistry = useCallback(async () => {
    setRetryingRegistry(true);
    try {
      await refreshRegistry();
    } finally {
      setRetryingRegistry(false);
    }
  }, []);

  const handleParamChange = useCallback((key, value) => {
    setNodes((nds) => nds.map((node) => (
      node.id === selectedNodeId
        ? { ...node, data: { ...node.data, params: { ...node.data.params, [key]: value } } }
        : node
    )));
  }, [selectedNodeId]);

  const handleInspectorBlocking = useCallback((keys) => {
    if (!selectedNodeId) return;
    setInspectorBlocking((current) => {
      const previous = current[selectedNodeId] || [];
      if (previous.length === keys.length && previous.every((key, index) => key === keys[index])) {
        return current;
      }
      return { ...current, [selectedNodeId]: keys };
    });
  }, [selectedNodeId]);

  // -- save ---------------------------------------------------------------

  const handleSaveStrategy = useCallback(async () => {
    const trimmedName = strategyName.trim() || 'Untitled Strategy';
    setIsSavingStrategy(true);
    setSaveIssues([]);
    setObservedTrainingBlock(null);
    setSaveStatus(SAVE_STATES.SAVING);
    setSaveState('Compiling...');

    try {
      // One serializer. Nothing is filtered, and an unexpressible node raises here rather
      // than being silently dropped from the saved strategy (SB-05).
      const graph = toCanonical(nodes, edges, {
        name: trimmedName,
        strategy_id: strategyIdState || '',
      });

      const blockers = blockingParams;
      if (blockers.length > 0) {
        throw new SaveValidationError(
          blockers.map((blocker) =>
            saveIssue(
              'PARAM_REQUIRED_MISSING',
              `'${blocker.nodeId}' (${blocker.blockId}) has no ${blocker.field}, and that ` +
                'parameter has no default because it changes how the strategy trades.',
              {
                nodeId: blocker.nodeId,
                field: blocker.field,
                fixHint: `Set ${blocker.field} on '${blocker.nodeId}' in the inspector.`,
              },
            ),
          ),
        );
      }

      // Market identity comes from the DATA node's own params, or the save fails naming the
      // node and the field. No literal is ever substituted (SB-06, Requirement 12.4).
      const market = resolveMarketIdentity(graph);

      const compileResponse = await strategiesApi.compile({
        blueprint: graph,
        version: 'v1.0',
        metadata: { name: trimmedName },
      });
      if (!compileResponse || compileResponse.error) {
        throw new Error(compileResponse?.error || 'Compilation failed');
      }

      setSaveState('Saving...');
      const payload = buildSavePayload(graph, market, { name: trimmedName });

      const response = strategyIdState
        ? await strategiesApi.update(strategyIdState, payload)
        : await strategiesApi.create(payload);

      if (response && (response.strategy || response.strategy_id || response.id)) {
        setStrategyIdState(response.strategy?.id || response.strategy_id || response.id);
      }
      // Requirement 15.12: the save response STATES what happened to the training half, and
      // a `BLOCKED` one is rendered rather than dropped. The version was still saved, so this
      // is not a save failure — it is a saved strategy that cannot train yet, and the author
      // is told by exactly how much (Requirement 14.9).
      setObservedTrainingBlock(response?.training ?? null);
      setSaveStatus(SAVE_STATES.SAVED);
      setSaveState(`Saved · ${market.symbol} ${market.timeframe}`);
    } catch (err) {
      // A 422 from `POST /training/jobs` carries the same block payload on `detail`. Read
      // where the API client put it, without assuming which client shape delivered it.
      const blockedDetail = err?.detail ?? err?.response?.data?.detail ?? null;
      if (blockedDetail && blockedDetail.error === 'TRAINING_BLOCKED') {
        setObservedTrainingBlock(blockedDetail);
      }
      if (err instanceof SaveValidationError) {
        setSaveIssues(err.issues);
        setSaveStatus(SAVE_STATES.REFUSED);
        setSaveState(`Save refused: ${err.issues.length} problem${err.issues.length === 1 ? '' : 's'}`);
      } else if (err instanceof CanonicalGraphError) {
        setSaveIssues([saveIssue(err.code, err.message, { nodeId: err.details?.node || null, field: err.details?.field || null })]);
        setSaveStatus(SAVE_STATES.REFUSED);
        setSaveState(`Save refused: ${err.code}`);
      } else {
        setSaveStatus(SAVE_STATES.FAILED);
        setSaveState(`Save Failed: ${err.message || 'Unknown error'}`);
      }
    } finally {
      setIsSavingStrategy(false);
    }
  }, [strategyName, nodes, edges, strategyIdState, blockingParams]);

  // -- keyboard -----------------------------------------------------------

  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.ctrlKey || e.metaKey) {
        switch (e.key) {
          case 's':
            e.preventDefault();
            handleSaveStrategy();
            break;
          case 'z':
            e.preventDefault();
            if (e.shiftKey) handleRedo();
            else handleUndo();
            break;
          default:
            break;
        }
        return;
      }
      switch (e.key) {
        case 'Delete':
        case 'Backspace':
          if (selectedNodeId) {
            e.preventDefault();
            handleDeleteNode();
          }
          break;
        case 'Escape':
          setSelectedNodeId(null);
          break;
        default:
          break;
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [selectedNodeId, handleDeleteNode, handleUndo, handleRedo, handleSaveStrategy]);

  // -- load and autosave --------------------------------------------------

  /** A canonical graph carried by a loaded strategy record, or null. */
  const canonicalFromRecord = (record) => {
    if (!record || typeof record !== 'object') return null;
    if (record.graph_json && typeof record.graph_json === 'object') return record.graph_json;
    if (record.schema_version === 2 && Array.isArray(record.nodes)) return record;
    if (Array.isArray(record.nodes) && record.nodes.some((node) => node && node.block_id)) {
      return { schema_version: 2, nodes: record.nodes, edges: record.edges || [] };
    }
    return null;
  };

  useEffect(() => {
    if (!loadedStrategy) return;
    if (loadedStrategy.name) setStrategyName(loadedStrategy.name);

    const graph = canonicalFromRecord(loadedStrategy);
    if (graph === null) {
      if (Array.isArray(loadedStrategy.nodes) && loadedStrategy.nodes.length > 0) {
        // A pre-canonical record. It is reported rather than loaded: guessing a block id from
        // a display label is the SB-05 defect this rework removed.
        setCanvasNotice(
          'This strategy was saved in an older format that does not record which block each ' +
            'node is. It cannot be opened on the canvas; re-create it from the palette.',
        );
      }
      return;
    }

    try {
      const restored = fromCanonical(graph);
      setNodes(restored.nodes);
      setEdges(restored.edges);
      setCanvasNotice(null);
    } catch (error) {
      setCanvasNotice(
        `This strategy could not be opened: ${error.message}`,
      );
    }
  }, [loadedStrategy]);

  useEffect(() => {
    const autosaveInterval = setInterval(() => {
      if (canonicalRef.current === null) return;
      try {
        window.localStorage.setItem(
          AUTOSAVE_KEY,
          JSON.stringify({ graph: canonicalRef.current, strategyName }),
        );
      } catch {
        // A full or unavailable localStorage is not worth a user-facing error.
      }
    }, 30000);
    return () => clearInterval(autosaveInterval);
  }, [strategyName]);

  useEffect(() => {
    if (loadedStrategy) return;
    let saved;
    try {
      saved = window.localStorage.getItem(AUTOSAVE_KEY);
    } catch {
      return;
    }
    if (!saved) return;
    try {
      const draft = JSON.parse(saved);
      const restored = fromCanonical(draft.graph);
      setNodes(restored.nodes);
      setEdges(restored.edges);
      if (draft.strategyName) setStrategyName(draft.strategyName);
    } catch {
      // An unreadable draft is discarded rather than partially applied.
      try {
        window.localStorage.removeItem(AUTOSAVE_KEY);
      } catch {
        /* nothing further to do */
      }
    }
  }, [loadedStrategy]);

  // -- palette rendering --------------------------------------------------

  const toggleCategory = useCallback((category) => {
    setCollapsedCategories((prev) => ({ ...prev, [category]: !prev[category] }));
  }, []);

  const visibleSections = useMemo(
    () =>
      paletteSections
        .map((section) => ({
          ...section,
          matches: section.blocks.filter((block) =>
            matchesPaletteQuery(block, searchQuery, section.display_name),
          ),
        }))
        .filter((section) => searchQuery.trim() === '' || section.matches.length > 0),
    [paletteSections, searchQuery],
  );

  const totalMatches = useMemo(
    () => visibleSections.reduce((count, section) => count + section.matches.length, 0),
    [visibleSections],
  );

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId],
  );

  const selectedDescriptor = useMemo(() => {
    if (!selectedNode) return null;
    return selectedNode.data.descriptor || getDescriptor(selectedNode.data.block_id);
  }, [selectedNode, registry]);

  /**
   * The selected node's backend issues, handed to `ParameterForm` (Requirement 8.9).
   *
   * The form matches them to fields by `issue.field` and renders `fix_hint` verbatim. Only
   * backend issues travel: the local advisory checks carry no `field` and no hint, and
   * feeding them in would build the second message path this requirement forbids.
   */
  const selectedNodeIssues = useMemo(() => {
    if (!selectedNodeId || !backendAuthoritative) return [];
    const marker = markers.nodes[selectedNodeId];
    return marker ? marker.issues : [];
  }, [selectedNodeId, backendAuthoritative, markers]);

  // -- node preview (task 5.7, Requirements 24.7, 24.8) -------------------
  //
  // The values are computed by the backend, through the same executors the runtime uses.
  // Nothing here evaluates a block; the page owns the timer, the request and the React state,
  // exactly as it does for validation, and `lib/nodePreview.js` owns the rules.

  const [preview, dispatchPreview] = useReducer(previewReducer, undefined, initialPreviewState);

  /** Monotonic request id, so a late answer can be recognised as late. */
  const previewSeq = useRef(0);

  /**
   * Which strategy, which node, which graph. Null when no preview is possible at all — an
   * unsaved strategy, no selection, or a canvas that will not serialize.
   */
  const activePreviewKey = useMemo(
    () => previewKey(strategyIdState, selectedNodeId, graphKey),
    [strategyIdState, selectedNodeId, graphKey],
  );

  /** Why the preview cannot be asked for, when it cannot. Rendered as a sentence. */
  const previewAvailable = useMemo(
    () =>
      previewAvailability({
        strategyId: strategyIdState,
        nodeId: selectedNodeId,
        graphKey,
        validationState: validation.state,
      }),
    [strategyIdState, selectedNodeId, graphKey, validation.state],
  );

  // -- the execution trace (task 9.3, Requirement 24.6) --------------------
  //
  // The trace rides the preview response on both of its outcomes, so there is no second
  // request, no second ownership resolution and no third trace store: `ExecutionTracer` and
  // `signal_trace_engine` recorded it, `_node_trace_payload` published it and
  // `lib/nodeTrace.js` projects it. The 422 branch is the one that matters most — a run that
  // produced nothing is exactly the run whose recorded inputs, duration and failure answer
  // "why did nothing happen?".
  //
  // Held beside the preview rather than inside it: the preview reducer is task 5.7's, and the
  // trace has to survive the *failed* path, where that reducer correctly drops the preview.
  const [nodeTrace, setNodeTrace] = useState(() => ({ key: null, trace: null }));

  // Selecting a different node, or editing the graph, drops the held preview. A series of
  // numbers under the wrong block's name has no residual value, so it is not kept and
  // labelled stale the way a validation report is. The trace goes with it, for the same
  // reason and one more: a trace is a statement about one run of one graph.
  useEffect(() => {
    dispatchPreview({ type: 'select', key: activePreviewKey });
    setNodeTrace({ key: null, trace: null });
  }, [activePreviewKey]);

  /**
   * Ask the backend. Never throws: every outcome is a dispatch, so a rejected request cannot
   * leave the panel stuck on "computing".
   *
   * The canvas graph travels as `blueprint`, because the canvas is usually ahead of the saved
   * version and previewing the saved one would answer a question the author did not ask. No
   * `bars` is sent: the window is the server's to choose and to cap.
   */
  const requestPreview = useCallback(async () => {
    const key = activePreviewKey;
    if (key === null) return;
    const graph = canonicalRef.current;
    if (graph === null) return;

    const requestId = previewSeq.current + 1;
    previewSeq.current = requestId;
    dispatchPreview({ type: 'request', requestId, key });
    try {
      const body = await strategiesApi.previewNode(strategyIdState, selectedNodeId, {
        blueprint: graph,
      });
      // The trace is taken from the body before the projection can throw on it: a preview body
      // this client cannot read is exactly the case where the run's own account of itself is
      // worth keeping.
      const trace = traceFromPreviewBody(body);
      if (previewSeq.current === requestId) setNodeTrace({ key, trace });
      dispatchPreview({
        type: 'result',
        requestId,
        key,
        preview: projectPreview(body),
        at: Date.now(),
      });
    } catch (error) {
      // Same staleness rule the reducer applies: a late answer for a selection nobody is
      // looking at is dropped rather than rendered under another block's name.
      //
      // A failure replaces the held trace only when it carries one. A refused validation and a
      // transport error executed nothing, so they invalidate nothing — and the branch above
      // stores the trace before `projectPreview` can throw, so a body this client could not
      // read still leaves the run's own account of itself on screen.
      if (previewSeq.current === requestId) {
        const failureTrace = traceFromPreviewFailure(error);
        if (failureTrace !== null) setNodeTrace({ key, trace: failureTrace });
      }
      dispatchPreview({ type: 'failed', requestId, key, error: previewError(error) });
    }
  }, [activePreviewKey, strategyIdState, selectedNodeId]);

  /**
   * The held trace, but only while it still describes the selection on screen.
   *
   * The same rule `previewAppliesToSelection` states for the preview: the key it was recorded
   * for has to be the key that is selected now, or the panel would be reporting one block's
   * run under another block's name.
   */
  const selectedNodeTrace = useMemo(
    () =>
      nodeTrace.key !== null && nodeTrace.key === activePreviewKey ? nodeTrace.trace : null,
    [nodeTrace, activePreviewKey],
  );

  // -- feed state, read from the backend (task 7.11, Requirements 19.7-19.10) ----------
  //
  // The page owns the request, the timer and the React state; `feed_state.py` owns the
  // classification and `lib/graphValidation.js` → `feedStateFromReport` owns the projection.
  // Nothing here compares an age to an interval, so the strip cannot disagree with the
  // classifier that decides.

  const [feedRead, setFeedRead] = useState(() => ({
    status: FEED_READ_STATES.NOT_APPLICABLE,
    body: null,
    error: null,
  }));

  /**
   * The in-flight-safe status read, exposed for task 8.5's snapshot and safety poll.
   *
   * Requirement 23.5 wants a state snapshot on reconnect and 23.6 wants a status poll every
   * 30 s while the connection is down. Both are "read the current status over the
   * authenticated REST surface", and this page already has exactly one such read: 7.11's
   * data-quality poll, which is single-flight, abort-aware and deliberately bypasses
   * `apiClient`'s 60 s response cache — so a snapshot taken through it cannot be answered
   * with a stale reading, which is the one thing a snapshot must not be. Adding a second
   * read for the same job would be two things to keep in agreement.
   */
  const readFeedNowRef = useRef(null);

  useEffect(() => {
    const strategyId = typeof strategyIdState === 'string' ? strategyIdState.trim() : '';
    if (strategyId === '') {
      // An unsaved canvas has no version, so there is no configured data source to report
      // on. Not applicable, and no request: asking with `undefined` in the path would be a
      // 404 dressed up as a feed failure.
      setFeedRead({ status: FEED_READ_STATES.NOT_APPLICABLE, body: null, error: null });
      return undefined;
    }

    let cancelled = false;
    let inFlight = false;
    const controller = new AbortController();

    setFeedRead({ status: FEED_READ_STATES.LOADING, body: null, error: null });

    const read = async () => {
      // Single-flight: a slow answer must not let the interval stack requests on top of it,
      // which is what would turn a 15 s poll into a burst against a 60/minute route.
      if (inFlight) return;
      inFlight = true;
      try {
        const { data } = await dataQualityApi.forStrategy(strategyId, {
          signal: controller.signal,
        });
        if (cancelled) return;
        setFeedRead({ status: FEED_READ_STATES.REPORTED, body: data, error: null });
      } catch (error) {
        if (cancelled) return;
        setFeedRead({
          status: FEED_READ_STATES.FAILED,
          body: null,
          error: {
            status: Number.isFinite(error?.status) ? error.status : null,
            message: typeof error?.message === 'string' ? error.message : '',
          },
        });
      } finally {
        inFlight = false;
      }
    };

    readFeedNowRef.current = read;
    read();
    const timer = setInterval(read, FEED_READ_INTERVAL_MS);
    return () => {
      cancelled = true;
      readFeedNowRef.current = null;
      controller.abort();
      clearInterval(timer);
    };
  }, [strategyIdState]);

  // -- realtime (task 8.5, Requirements 20.12, 21.6, 23.1-23.6) ------------
  //
  // One connection for the whole browser session, shared and ref-counted, over the
  // platform's existing `websocketClient`. This page is a consumer: it subscribes the
  // channels its view needs, renders the frames, and unsubscribes on unmount. It classifies
  // nothing — the runtime labels are `dag_engine`'s, the deployed lock is
  // `strategy_lifecycle`'s, and a refused subscription is the server's refusal, reported
  // rather than swallowed.

  const requestStatusSnapshot = useCallback(() => {
    const read = readFeedNowRef.current;
    if (typeof read === 'function') read();
  }, []);

  const { realtime, status: realtimeStatus, channels: realtimeChannels, reason: realtimeReason } =
    useBuilderRealtime({
      strategyId: typeof strategyIdState === 'string' ? strategyIdState : '',
      deploymentId,
      trainingJobIds: trainingJob && trainingJob.job_id ? [String(trainingJob.job_id)] : [],
      onSnapshot: requestStatusSnapshot,
      enabled: realtimeEnabled,
    });

  const deployedLock = useMemo(() => deployedLockView(realtime), [realtime]);
  const subscriptionRefusals = useMemo(() => refusalList(realtime), [realtime]);

  /**
   * Stamp the runtime state and the deployed lock onto the nodes (task 8.5,
   * Requirements 20.12 and 9.9).
   *
   * Same discipline as the validation-marker effect above, and for the same reason: a new
   * node object is produced only when that node's rendered runtime reading actually changed.
   * A `deployment.runtime_state` frame arrives on every evaluated bar, so an effect that
   * returned a fresh array unconditionally would re-render the whole canvas — and, through
   * the canonical-graph memo, re-validate it — once per bar.
   *
   * The signature is the rendered reading, not the frame: two frames that say the same thing
   * about a node are the same thing on screen.
   */
  useEffect(() => {
    setNodes((current) => {
      let changed = false;
      const next = current.map((node) => {
        const runtime = nodeRuntimeView(realtime, node.id);
        const signature = runtime.known ? `${runtime.state}|${runtime.detail}` : '';
        const lockSignature = deployedLock.locked ? deployedLock.reason || 'locked' : '';
        if (
          (node.data.runtimeSignature || '') === signature &&
          (node.data.deployedLockSignature || '') === lockSignature
        ) {
          return node;
        }
        changed = true;
        return {
          ...node,
          data: {
            ...node.data,
            runtime,
            runtimeSignature: signature,
            deployedLock: deployedLock.locked,
            deployedLockReason: deployedLock.reason,
            deployedLockSignature: lockSignature,
          },
        };
      });
      return changed ? next : current;
    });
  }, [realtime, deployedLock]);

  // -- the status strip (Requirement 8.11) --------------------------------

  const summary = useMemo(() => validationSummary(validation, markers), [validation, markers]);

  /**
   * The market the graph names, read from the first DATA node's params. Unlike
   * `resolveMarketIdentity` this one does not refuse — the strip reports what is known, and
   * "nothing yet" is a legitimate answer while the author is still wiring.
   */
  const market = useMemo(() => {
    const dataNode = nodes.find((node) => node.data.category === 'DATA');
    const params = dataNode ? dataNode.data.params || {} : {};
    return {
      symbol: typeof params.symbol === 'string' ? params.symbol : null,
      timeframe: typeof params.timeframe === 'string' ? params.timeframe : null,
    };
  }, [nodes]);

  /**
   * Feed state, as the backend reports it (task 7.11, Requirements 19.7-19.10).
   *
   * The reading is the data-quality response, projected without reinterpretation: the state
   * word, the two thresholds, the age and the sentence on screen are all the server's. Every
   * branch that is not a report — unsaved canvas, read in flight, failed read, unreadable
   * body, an unrecognised state word, or a reading about a different market — is `UNKNOWN`
   * with `known: false`, so a feed nobody has measured is never a green light.
   *
   * A caller-supplied `feedObservation` still wins, because a direct observation of the feed
   * is closer to it than a report about the saved version; the warmup requirement then comes
   * from the validation report, so `INSUFFICIENT_DATA` is judged against a real number.
   */
  const feed = useMemo(() => {
    if (feedObservation) {
      const reportWarmup = liveReport && liveReport.summary ? liveReport.summary.warmup_bars : null;
      return deriveFeedState(
        { warmupBars: Number.isFinite(reportWarmup) ? reportWarmup : undefined, ...feedObservation },
        { market },
      );
    }
    return feedStateFromReport(feedRead, { market });
  }, [feedObservation, liveReport, market, feedRead]);

  const mlNodeCount = useMemo(
    () => nodes.filter((node) => node.data.category === 'ML_DL').length,
    [nodes],
  );

  const training = useMemo(
    () => deriveTrainingState({ mlNodeCount, job: trainingJob }),
    [mlNodeCount, trainingJob],
  );

  // Requirement 14.9. The quantities are the server's; nothing here recomputes a threshold,
  // because the gate that refused the request owns that arithmetic and a second copy of it
  // in the client would eventually disagree with it.
  const trainingBlocks = useMemo(
    () => deriveTrainingBlocks(observedTrainingBlock ?? trainingBlock),
    [observedTrainingBlock, trainingBlock],
  );

  /** Select the node an issue names, so every issue row is a way to reach the problem. */
  const focusNode = useCallback((nodeId) => {
    setSelectedNodeId(nodeId);
    setInspectorOpen(true);
  }, []);

  /** Select a connection: the edge is marked selected and its source node is inspected. */
  const focusEdge = useCallback((edgeId) => {
    const target = edges.find((candidate) => candidate.id === edgeId);
    setEdges((current) =>
      current.map((edge) =>
        Boolean(edge.selected) === (edge.id === edgeId) ? edge : { ...edge, selected: edge.id === edgeId },
      ),
    );
    if (target) focusNode(target.source);
  }, [edges, focusNode]);

  // An unset required parameter deliberately does **not** disable the button. A disabled
  // control says "no" without saying why; clicking through produces the structured refusal that
  // names the node and the field, which is the whole point of the SB-06 fix.
  //
  // The deployed lock is the one case where disabling IS right (task 8.5, Requirement 9.9):
  // the reason is already on screen as a sentence from the backend, so the control is not
  // saying "no" silently — and the write it would attempt is one migration 004c's trigger
  // refuses, so offering it would be offering an edit that cannot land. Until a
  // `canvas_state` verdict arrives, `locked` is false and nothing changes.
  const saveDisabled =
    isSavingStrategy ||
    !isValid ||
    !registry.isReady ||
    canonical.graph === null ||
    nodes.length === 0 ||
    deployedLock.locked;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: C.bg1 }}>
      {/* Toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', background: C.bg2, borderBottom: `1px solid ${C.border}`, gap: '8px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Button variant="ghost" size="sm" Icon={ArrowLeft} onClick={onBack}>Back</Button>
          <Inp
            id="strategy-name"
            aria-label="Strategy name"
            ph="Strategy Name"
            val={strategyName}
            onChange={(e) => setStrategyName(e.target.value)}
          />
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <Button variant="ghost" size="sm" Icon={Undo} onClick={handleUndo} disabled={!canUndo} title="Undo (Ctrl+Z)" />
          <Button variant="ghost" size="sm" Icon={Redo} onClick={handleRedo} disabled={!canRedo} title="Redo (Ctrl+Shift+Z)" />
          <div style={{ width: 1, height: 24, background: C.border }} />
          <Button variant="ghost" size="sm" Icon={ZoomOut} onClick={() => zoomOut()} title="Zoom Out" />
          <Button variant="ghost" size="sm" Icon={ZoomIn} onClick={() => zoomIn()} title="Zoom In" />
          <Button variant="ghost" size="sm" Icon={Maximize} onClick={handleFitView} title="Fit View" />
          <div style={{ width: 1, height: 24, background: C.border }} />
          <Button variant="ghost" size="sm" Icon={PanelLeft} onClick={() => setLibraryOpen(!libraryOpen)} title="Toggle Library" />
          <Button variant="ghost" size="sm" Icon={PanelRight} onClick={() => setInspectorOpen(!inspectorOpen)} title="Toggle Inspector" />
          <div style={{ width: 1, height: 24, background: C.border }} />
          <Button variant="outline" size="sm" Icon={Save} onClick={handleSaveStrategy} disabled={saveDisabled}>
            {isSavingStrategy ? 'Saving...' : 'Save'}
          </Button>
          <Button variant="primary" size="sm" Icon={Play} onClick={handleSaveStrategy} disabled={saveDisabled} title="Compile & Save">
            Compile
          </Button>
          {onBacktest && (
            <Button
              variant="primary"
              size="sm"
              Icon={BarChart2}
              disabled={canonical.graph === null || nodes.length === 0}
              onClick={() => onBacktest({ id: strategyIdState, name: strategyName, nodes, edges })}
            >
              Backtest
            </Button>
          )}
        </div>
      </div>

      {/*
        Blocking states, each stated in words with the node and field named.

        The local advisory banner renders **only** while the backend has no verdict for this
        graph. Once a report applies, the backend's verdict is the only verdict on screen, so
        the author is never shown two answers that disagree (design.md → advisory vs authority).
      */}
      {!backendAuthoritative && !isValid && errors.length > 0 && (
        <div
          role="alert"
          data-testid="local-advisory"
          style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 16px', background: `${C.red}20`, borderBottom: `1px solid ${C.red}` }}
        >
          <AlertTriangle size={16} style={{ color: C.red }} aria-hidden="true" />
          <span className="text-small" style={{ color: C.red, fontFamily: 'monospace' }}>
            {errors.length} error{errors.length !== 1 ? 's' : ''}: {errors[0]?.message}
            {' '}(local check — the backend has not validated this version yet)
          </span>
        </div>
      )}

      {/* The validation request failed. The last known report is kept, and said to be old. */}
      {validation.state === VALIDATION_STATES.UNAVAILABLE && (
        <div
          role="alert"
          data-testid="validation-unavailable"
          data-code={validation.error ? validation.error.code : undefined}
          data-status={validation.error && validation.error.status !== null ? validation.error.status : undefined}
          style={{ padding: '8px 16px', background: `${C.gold}20`, borderBottom: `1px solid ${C.gold}`, color: C.gold, fontFamily: 'monospace' }}
          className="text-small"
        >
          {summary.headline} — {summary.detail}
        </div>
      )}

      {canonical.error && (
        <div role="alert" data-testid="serializer-error" style={{ padding: '8px 16px', background: `${C.red}20`, borderBottom: `1px solid ${C.red}`, color: C.red, fontFamily: 'monospace' }} className="text-small">
          {canonical.error.code}: {canonical.error.message}
        </div>
      )}

      {canvasNotice && (
        <div role="status" data-testid="canvas-notice" style={{ padding: '8px 16px', background: `${C.gold}20`, borderBottom: `1px solid ${C.gold}`, color: C.gold, fontFamily: 'monospace' }} className="text-small">
          {canvasNotice}
        </div>
      )}

      {/*
        The deployed lock (task 8.5, Requirement 9.9). The verdict, the sentence and the list
        of frozen fields are all task 8.3's `canvas_state`, forwarded — this banner formats
        nothing and decides nothing. That is the point of the backend publishing the block: the
        canvas cannot offer an edit that migration 004c's immutability trigger would then
        reject, and it cannot lock a canvas the backend says is editable either.
      */}
      {deployedLock.locked && (
        <div
          role="status"
          data-testid="deployed-lock"
          data-lifecycle-state={deployedLock.lifecycleState || undefined}
          data-frozen-fields={deployedLock.frozenFields.join(' ')}
          style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px 16px', background: `${C.gold}20`, borderBottom: `1px solid ${C.gold}`, color: C.gold, fontFamily: 'monospace' }}
          className="text-small"
        >
          <span aria-hidden="true">🔒</span>
          <span>
            {deployedLock.reason
              || 'This version is deployed, so the canvas is read-only.'}
          </span>
        </div>
      )}

      {/*
        A refused realtime subscription, reported (Requirement 21.6).

        The server refuses the subscription and keeps the connection, so the honest thing on
        screen is "this channel is not carrying updates, and here is the server's reason" —
        not silence, which is indistinguishable from a channel with nothing to say, and not a
        disconnect, which is not what happened. The reason is the server's sentence verbatim.
      */}
      {subscriptionRefusals.length > 0 && (
        <div
          role="alert"
          data-testid="subscription-refusals"
          style={{ padding: '8px 16px', background: `${C.gold}20`, borderBottom: `1px solid ${C.gold}` }}
        >
          {subscriptionRefusals.map((refusal) => (
            <div
              key={refusal.channel}
              className="text-small"
              style={{ color: C.gold, fontFamily: 'monospace' }}
              data-channel={refusal.channel}
              data-code={refusal.code}
            >
              Live updates for {refusal.channel} are unavailable — {refusal.reason}
            </div>
          ))}
        </div>
      )}

      {connectionIssue && (
        <div role="alert" data-testid="connection-issue" style={{ padding: '8px 16px', background: `${C.gold}20`, borderBottom: `1px solid ${C.gold}`, color: C.gold, fontFamily: 'monospace' }} className="text-small">
          Connection refused — {connectionIssue.message}
          {connectionIssue.fix_hint ? ` ${connectionIssue.fix_hint}` : ''}
        </div>
      )}

      {saveIssues.length > 0 && (
        <div role="alert" data-testid="save-issues" style={{ padding: '8px 16px', background: `${C.red}20`, borderBottom: `1px solid ${C.red}` }}>
          {saveIssues.map((issue, index) => (
            <div
              key={`${issue.code}-${issue.node_id || 'graph'}-${issue.field || index}`}
              className="text-small"
              style={{ color: C.red, fontFamily: 'monospace' }}
              data-code={issue.code}
              data-node-id={issue.node_id || undefined}
              data-field={issue.field || undefined}
            >
              {issue.message} {issue.fix_hint}
            </div>
          ))}
        </div>
      )}

      {/*
        Blocking training messages (Requirement 14.9). Each one is rendered with its
        required quantity AND its available quantity, both taken from the payload the
        admission gate produced — this panel formats, it does not measure. The gold tone
        rather than red is deliberate and matches what happened: the strategy IS saved, and
        the version is a real immutable version; it is training that was refused, and no
        training job row exists (Requirements 14.3, 14.4, 14.7, 14.8).
      */}
      {trainingBlocks.length > 0 && (
        <div
          role="alert"
          data-testid="training-blocks"
          data-block-count={trainingBlocks.length}
          style={{ padding: '8px 16px', background: `${C.gold}20`, borderBottom: `1px solid ${C.gold}` }}
        >
          {trainingBlocks.map((block) => (
            <div
              key={`${block.reason}-${block.nodeId || 'graph'}`}
              data-testid="training-block"
              data-reason={block.reason}
              data-node-id={block.nodeId || undefined}
              data-block-id={block.blockId || undefined}
              data-quantity-count={block.quantities.length}
              className="text-small"
              style={{ color: C.gold, fontFamily: 'monospace', display: 'flex', flexDirection: 'column', gap: '2px' }}
            >
              <span data-testid="training-block-message">
                Training blocked — {block.reason}: {block.message}
              </span>
              {block.quantities.map((quantity) => (
                <span
                  key={quantity.label}
                  data-testid="training-block-quantity"
                  data-label={quantity.label}
                  data-required={quantity.required}
                  data-available={quantity.available}
                  style={{ paddingLeft: '12px' }}
                >
                  {quantity.text}
                </span>
              ))}
              {block.issues.map((issue) => (
                <span
                  key={issue.key}
                  data-testid="training-block-issue"
                  data-code={issue.code || undefined}
                  style={{ paddingLeft: '12px', color: C.t3 }}
                >
                  {issue.message}
                  {issue.quantity ? ` (${issue.quantity.text})` : ''}
                  {issue.fixHint ? ` ${issue.fixHint}` : ''}
                </span>
              ))}
              {block.fixHint ? (
                <span data-testid="training-block-fix" style={{ paddingLeft: '12px', color: C.t3 }}>
                  {block.fixHint}
                </span>
              ) : null}
              {block.jobCreated ? null : (
                <span data-testid="training-block-no-job" style={{ paddingLeft: '12px', color: C.t3 }}>
                  No training job was created, so nothing is queued or running for this graph.
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Main content */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Palette */}
        {libraryOpen && (
          <div style={{ width: 280, background: C.bg2, borderRight: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column' }} data-testid="palette">
            <div style={{ padding: '12px', borderBottom: `1px solid ${C.border}` }}>
              <label
                htmlFor="palette-search"
                className="text-micro"
                style={{ color: C.t3, fontFamily: 'monospace', letterSpacing: 1, textTransform: 'uppercase', display: 'block', marginBottom: '6px' }}
              >
                Search blocks
              </label>
              <div style={{ position: 'relative' }}>
                <Search size={14} aria-hidden="true" style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: C.t3 }} />
                <input
                  id="palette-search"
                  type="search"
                  placeholder="name, block id, description or category"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  aria-describedby="palette-search-result-count"
                  className="text-small"
                  style={{
                    width: '100%',
                    background: C.bg3,
                    border: `1px solid ${C.border}`,
                    borderRadius: '0.375rem',
                    padding: '8px 12px 8px 32px',
                    color: C.t1,
                    outline: 'none',
                  }}
                />
              </div>
              <p
                id="palette-search-result-count"
                role="status"
                className="text-micro"
                style={{ color: C.t3, margin: '6px 0 0', fontFamily: 'monospace' }}
              >
                {registry.isReady
                  ? `${totalMatches} block${totalMatches === 1 ? '' : 's'} · registry ${registry.registryVersion}`
                  : registry.isLoading
                    ? 'Loading the block registry…'
                    : '0 blocks'}
              </p>
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
              {registry.isError ? (
                <PaletteErrorPanel error={registry.error} onRetry={handleRetryRegistry} retrying={retryingRegistry} />
              ) : registry.isLoading && !registry.isReady ? (
                <p className="text-micro" style={{ color: C.t3, fontFamily: 'monospace' }}>Loading blocks…</p>
              ) : visibleSections.length === 0 ? (
                <p className="text-micro" style={{ color: C.t3, fontFamily: 'monospace' }} data-testid="palette-empty">
                  {searchQuery.trim() === '' ? 'No blocks available.' : `Nothing matches “${searchQuery}”.`}
                </p>
              ) : (
                visibleSections.map((section) => {
                  const CategoryIcon = getCategoryIcon(section.id);
                  const isCollapsed = Boolean(collapsedCategories[section.id]) && searchQuery.trim() === '';
                  const sectionId = `palette-section-${section.id}`;
                  return (
                    <div key={section.id} style={{ marginBottom: '16px' }} data-testid="palette-category" data-category-id={section.id} data-category-order={section.order}>
                      <button
                        type="button"
                        onClick={() => toggleCategory(section.id)}
                        aria-expanded={!isCollapsed}
                        aria-controls={sectionId}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '6px',
                          padding: '8px',
                          width: '100%',
                          background: 'transparent',
                          border: 'none',
                          cursor: 'pointer',
                          color: C.t2,
                          textAlign: 'left',
                        }}
                      >
                        {isCollapsed ? <ChevronRight size={14} aria-hidden="true" style={{ color: C.t3 }} /> : <ChevronDown size={14} aria-hidden="true" style={{ color: C.t3 }} />}
                        <CategoryIcon size={14} aria-hidden="true" style={{ color: getCategoryColor(section.id) }} />
                        <span className="text-micro" style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1 }}>
                          {section.display_name}
                        </span>
                        <span className="text-micro" style={{ marginLeft: 'auto', color: C.t3 }} data-testid="palette-category-count">
                          {section.matches.length}
                        </span>
                      </button>

                      {!isCollapsed && (
                        <div id={sectionId} style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '4px' }}>
                          {section.matches.map((block) => (
                            <div
                              key={block.block_id}
                              draggable
                              data-testid="palette-block"
                              data-block-id={block.block_id}
                              data-category={block.category}
                              onDragStart={(e) => {
                                e.dataTransfer.setData(DRAG_BLOCK_ID_MIME, block.block_id);
                                e.dataTransfer.effectAllowed = 'move';
                              }}
                              style={{
                                background: C.bg3,
                                border: `1px solid ${C.border}`,
                                borderRadius: '0.375rem',
                                padding: '8px 10px',
                                cursor: 'grab',
                              }}
                            >
                              <div className="text-small" style={{ fontWeight: 600, color: C.t1 }}>
                                {block.display_name || block.block_id}
                              </div>
                              <div className="text-micro" style={{ color: C.t3, fontFamily: 'monospace' }}>
                                {block.block_id}
                              </div>
                              {block.description ? (
                                <div className="text-micro" style={{ color: C.t3 }}>{block.description}</div>
                              ) : null}
                              <PortChips ports={block.inputs} direction="in" />
                              <PortChips ports={block.outputs} direction="out" />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })
              )}
            </div>
          </div>
        )}

        {/* Canvas */}
        <div
          style={{ flex: 1, position: 'relative' }}
          ref={reactFlowWrapper}
          data-testid="canvas"
          onDragOver={handleDragOver}
          onDrop={handleDrop}
        >
          <DragLegalityContext.Provider value={dragLegality}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              onConnectStart={onConnectStart}
              onConnectEnd={onConnectEnd}
              isValidConnection={isValidConnection}
              onNodeClick={onNodeClick}
              nodeTypes={nodeTypes}
              fitView
              deleteKeyCode={null}
              /*
                `design.md` → Visual states, the Deployed row: "lock affordance, read-only
                canvas". Selection and panning stay on — reading a locked version is exactly
                what an author does with one, and the inspector, the previews and the issue
                panel all still work. What stops is editing.
              */
              nodesDraggable={!deployedLock.locked}
              nodesConnectable={!deployedLock.locked}
              edgesFocusable={!deployedLock.locked}
            >
              <Background color={C.bg3} gap={16} />
              <Controls />
              {nodes.length > 15 && <MiniMap nodeColor={C.cyan} nodeStrokeWidth={3} zoomable pannable />}
            </ReactFlow>
          </DragLegalityContext.Provider>

          {nodes.length === 0 && (
            <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', pointerEvents: 'none', color: C.t3, fontFamily: 'monospace', textAlign: 'center', padding: '0 24px' }} className="text-small">
              Drag a block from the palette to start. A strategy needs a DATA block — its symbol
              and timeframe are the market this strategy trades.
            </div>
          )}
        </div>

        {/* Inspector */}
        {inspectorOpen && (
          <div style={{ width: 320, background: C.bg2, borderLeft: `1px solid ${C.border}`, display: 'flex', flexDirection: 'column' }}>
            <div style={{ padding: '12px', borderBottom: `1px solid ${C.border}` }}>
              <PanelTitle title="Inspector" sub={selectedNode ? selectedNode.data.label : 'Select a node'} />
            </div>

            <div style={{ flex: 1, overflowY: 'auto', padding: '12px' }}>
              {selectedNode ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <div>
                    <div className="text-micro" style={{ color: C.t3, fontFamily: 'monospace', letterSpacing: 1, textTransform: 'uppercase', marginBottom: '8px' }}>
                      Block
                    </div>
                    <Tag2>{selectedNode.data.block_id}</Tag2>
                    <div className="text-micro" style={{ color: C.t3, marginTop: '4px' }}>
                      {selectedNode.data.category}
                    </div>
                    <PortChips ports={selectedNode.data.inputs} direction="in" />
                    <PortChips ports={selectedNode.data.outputs} direction="out" />

                    {/* Node status: the same marker the canvas draws, in words. */}
                    <p
                      className="text-micro"
                      data-testid="inspector-node-status"
                      data-node-id={selectedNode.id}
                      data-severity={selectedNode.data.validation ? selectedNode.data.validation.severity : undefined}
                      data-issue-count={selectedNode.data.validation ? selectedNode.data.validation.count : 0}
                      style={{ color: selectedNode.data.validation ? (selectedNode.data.validation.severity === SEVERITY_ERROR ? C.red : C.gold) : C.t3, margin: '6px 0 0' }}
                    >
                      {selectedNode.data.validation
                        ? markerLabel(selectedNode.data.validation)
                        : backendAuthoritative
                          ? 'No issues reported for this block'
                          : 'Not validated yet'}
                    </p>
                  </div>

                  {selectedDescriptor ? (
                    <ParameterForm
                      params={selectedDescriptor.params}
                      values={selectedNode.data.params}
                      onChange={handleParamChange}
                      // Requirement 8.9: the backend's own issues, matched to fields by
                      // `issue.field` and rendered with `fix_hint` verbatim by the form.
                      issues={selectedNodeIssues}
                      nodeId={selectedNode.id}
                      blockId={selectedNode.data.block_id}
                      // Requirements 11.7 / 11.8: the symbol and timeframe controls are
                      // populated from the discovery and registry endpoints. No symbol or
                      // interval list exists in this client to fall back to.
                      controls={MARKET_PARAM_CONTROLS}
                      onBlockingChange={handleInspectorBlocking}
                    />
                  ) : (
                    <p className="text-micro" style={{ color: C.t3, fontFamily: 'monospace' }}>
                      The registry publishes no descriptor for “{selectedNode.data.block_id}”, so its
                      parameters cannot be shown.
                    </p>
                  )}

                  {/*
                    Requirements 24.7 / 24.8: the last values this block produces, computed by
                    the executors that run it, over a window the server bounds. A
                    FEATURE_ENGINEERING node's produced column names come with it.
                  */}
                  <NodePreview
                    state={preview.state}
                    preview={preview.preview}
                    error={preview.error}
                    availability={previewAvailable}
                    onRequest={requestPreview}
                  />

                  {/*
                    Requirement 24.6: the trace the run already recorded for this block — its
                    bound inputs, its output, its duration and every recorded failure — read
                    out of `dag_engine.ExecutionTracer` and `signal_trace_engine`, which is
                    what makes "why did nothing happen?" answerable. `runtime` is task 8.5's
                    reading, already stamped onto the node by the canvas effect: a block that
                    is warming never ran, and that is a different answer from one that failed.
                  */}
                  <NodeTrace
                    trace={selectedNodeTrace}
                    runtime={selectedNode.data.runtime || null}
                  />

                  <Button variant="danger" size="sm" Icon={Trash2} onClick={handleDeleteNode} style={{ width: '100%' }}>
                    Delete Node
                  </Button>
                </div>
              ) : (
                <div className="text-small" style={{ color: C.t3, fontFamily: 'monospace', textAlign: 'center', padding: '20px' }}>
                  Click a node to inspect
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* The report, listed: node issues, connection issues and graph-level issues */}
      <ValidationIssuePanel
        markers={heldMarkers}
        stale={!backendAuthoritative}
        onFocusNode={focusNode}
        onFocusEdge={focusEdge}
      />

      {/*
        Status strip (Requirement 8.11): validation summary, feed state, save state, training
        state. A live region, so a verdict arriving after a 400 ms debounce is announced rather
        than only appearing. Each cell states what it knows; where a state is genuinely unknown
        it says so instead of showing a passing colour.
      */}
      <div
        role="status"
        aria-live="polite"
        aria-label="Builder status"
        data-testid="status-strip"
        className="text-micro"
        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 16px', background: C.bg2, borderTop: `1px solid ${C.border}`, fontFamily: 'monospace', color: C.t3, gap: 12, flexWrap: 'wrap' }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <StatusCell
            testId="validation-state"
            label="validation"
            state={summary.state}
            text={summary.headline}
            detail={summary.detail}
            known={
              summary.state === VALIDATION_STATES.VALID ||
              summary.state === VALIDATION_STATES.INVALID
            }
            tone={
              summary.state === VALIDATION_STATES.INVALID
                ? C.red
                : summary.state === VALIDATION_STATES.VALID
                  ? C.green
                  : summary.state === VALIDATION_STATES.UNAVAILABLE
                    ? C.gold
                    : null
            }
          />
          <StatusCell
            testId="feed-state"
            label="feed"
            state={feed.state}
            text={feed.label}
            detail={feed.detail}
            known={feed.known}
            tone={feed.state === 'LIVE' ? C.green : feed.known ? C.gold : null}
          />
          {/*
            Requirement 19.8's display clause: the age of the last event together with the
            expected interval, on screen as figures. The sentence is the server's `display`
            field **verbatim** — the same convention as `ParameterForm`'s `fix_hint` and
            `NodePreview`'s `detail.message` — because the classifier that decided the state
            is the one that must be quoted. It is text inside the strip's live region, so a
            screen reader receives it and the state is never carried by colour alone. The
            figures behind the sentence ride along as data attributes for the same reason the
            report carries them: a rendered state must be checkable against its numbers.
          */}
          {feed.display ? (
            <span
              data-testid="feed-display"
              data-feed-state={feed.state}
              data-age-seconds={feed.ageSeconds === null ? '' : String(feed.ageSeconds)}
              data-expected-interval-seconds={
                feed.expectedIntervalSeconds === null ? '' : String(feed.expectedIntervalSeconds)
              }
              style={{ color: C.t3 }}
            >
              {feed.display}
            </span>
          ) : null}
          <StatusCell
            testId="save-state"
            label="save"
            state={saveStatus}
            text={saveState || 'Not saved yet'}
            detail={saveIssues.length ? `${saveIssues.length} refusal(s)` : undefined}
            known={saveStatus !== SAVE_STATES.UNSAVED}
            tone={
              saveStatus === SAVE_STATES.REFUSED || saveStatus === SAVE_STATES.FAILED
                ? C.red
                : saveStatus === SAVE_STATES.SAVED
                  ? C.green
                  : null
            }
          />
          <StatusCell
            testId="training-state"
            label="training"
            state={training.state}
            text={training.label}
            detail={training.detail}
            known={training.known}
            tone={training.state === 'FAILED' ? C.red : null}
          />
          {/*
            The realtime connection, reported literally (task 8.5, Requirement 23.4).

            A SEPARATE cell from `feed`, deliberately. The feed cell is a measurement of
            market data — 7.4 classifies it and 7.11 reads it from the data-quality endpoint —
            and this one is the state of the push connection. Folding the two would let a
            dropped socket overwrite a true reading about the market with a statement about
            the transport, and would hide a genuinely stale feed behind a healthy socket. Both
            are in the strip's live region, so both are announced.
          */}
          <StatusCell
            testId="realtime-state"
            label="realtime"
            state={realtimeStatus}
            text={REALTIME_LABELS[realtimeStatus] || realtimeStatus}
            detail={realtimeReason || `${realtimeChannels.length} channel(s)`}
            known={realtimeStatus === REALTIME_STATES.CONNECTED}
            tone={
              realtimeStatus === REALTIME_STATES.CONNECTED
                ? C.green
                : realtimeStatus === REALTIME_STATES.DISCONNECTED
                  ? C.gold
                  : null
            }
          />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span>{nodes.length} nodes</span>
          <span>{edges.length} connections</span>
          <span data-testid="registry-state">registry {registry.state}</span>
          {blockingParams.length > 0 && (
            <span style={{ color: C.red }} data-testid="blocking-count">
              {blockingParams.length} required parameter{blockingParams.length === 1 ? '' : 's'} unset
            </span>
          )}
          <span
            data-testid="validation-requests"
            data-requests={validation.requests}
            data-discarded={validation.discarded}
            style={{ color: C.t4 }}
          >
            {validation.requests} check{validation.requests === 1 ? '' : 's'}
            {validation.discarded > 0 ? `, ${validation.discarded} superseded` : ''}
          </span>
          <span>Ctrl+S Save</span>
        </div>
      </div>
    </div>
  );
}

function StrategyBuilderWrapper(props) {
  return (
    <ReactFlowProvider>
      <DataPipelineProvider mode="backtest">
        <IndicatorEngineProvider>
          <LogicEngineProvider>
            <StrategyEngineProvider>
              <UndoRedoProvider>
                <ValidationProvider>
                  <StrategyBuilderCanvas {...props} />
                </ValidationProvider>
              </UndoRedoProvider>
            </StrategyEngineProvider>
          </LogicEngineProvider>
        </IndicatorEngineProvider>
      </DataPipelineProvider>
    </ReactFlowProvider>
  );
}

export { StrategyBuilderCanvas, ApiSyncIndicator, DynamicNode, PortChips, PaletteErrorPanel };
export default StrategyBuilderWrapper;
