/**
 * ═══════════════════════════════════════════════════════════════════════════
 * ds/index — the public surface of the design system
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 6.23. design.md §5.1, §13.3.
 *
 *   import { Panel, DataTable, Metric } from '../components/ds';
 *
 * ═══ WHY `Chart` IS NOT HERE ═══
 *
 * `ds/Chart` is the only module in `src/` that imports recharts, which is ~350KB
 * before gzip. Exactly three in-scope pages chart (Dashboard, Portfolio,
 * Backtester); Strategies, Trade History and Signal Trace do not, and design.md
 * §13.3 says they must not pay for it.
 *
 * A barrel re-export would defeat that outright. `export { Chart } from './Chart'`
 * puts recharts in this module's import graph, so `import { Panel } from
 * '../components/ds'` on Trade History would hoist `vendor-recharts` into that
 * page's chunk — a page with no chart paying for the chart library, which is the
 * exact regression §13.3 forbids. Bundlers cannot tree-shake it away, because a
 * static import anywhere in the entry graph pulls the chunk in regardless of
 * whether any binding from it is used.
 *
 * So `Chart` is imported lazily at its call sites instead, which is also what
 * keeps it off the critical path:
 *
 *   const Chart = lazy(() => import('../components/ds/Chart'));
 *
 * `Chart.jsx` carries a default export for exactly that reason. See its docblock
 * for the intended `Panel` + `Suspense` form and for the importer ratchet its test
 * holds.
 *
 * ═══ WHAT ELSE IS DELIBERATELY ABSENT ═══
 *
 * This barrel lists PRIMITIVES. Four modules in `ds/` are not primitives and each
 * says so in its own docblock:
 *
 *   `devAssert.js`       Internal contract enforcement. Primitives import it
 *                        directly; a page has no reason to.
 *   `overlayRegistry.js` Internal single-overlay rule (Requirement 17.3), shared
 *                        by `ConfirmDialog` and `Drawer`. Holds no React state and
 *                        renders nothing.
 *   `ActionControl.jsx`  The rendering of an `{ label, to | href | onClick }` action
 *                        spec, reached only through `EmptyState` and `ErrorState`.
 *                        Not one of design.md §5's primitives.
 *   `Spinner`            A named export of `LoadingState.jsx`, exported there only
 *                        so `CommandButton` can hand it to `ui/Button` while a
 *                        command is in flight. Callers wanting a loading affordance
 *                        want `LoadingState` or `Skeleton`.
 *
 * `Tabs`, `Tooltip`, `Alert` and `Breadcrumb` were the four §5.2 controls task
 * 6.23 names that had no module yet. They are here now, and each one is listed
 * below in its alphabetical slot.
 *
 * `readTrail` is exported alongside `Breadcrumb` because the shell (task 8.7)
 * resolves its trail from `shell/navigation.js` and has to know whether anything
 * survived cleaning before it reserves space for a breadcrumb row.
 * `nextTabIndex` and `alertRole` are exported for the same class of reason: they
 * are the pure decisions inside `Tabs` and `Alert` — which tab an arrow key moves
 * to, and whether a severity earns an assertive or a polite live region — and a
 * consumer or a test asserting either of them should read the one implementation
 * rather than restate it.
 *
 * ═══ NAMED RE-EXPORTS, NOT `export *` ═══
 *
 * `DataTable` and `Metric` both export `NOT_AVAILABLE` (the same `'—'` marker,
 * spelled once in each because neither should have to import the other to render a
 * blank cell). Under `export * from` a name exported by two modules is ambiguous,
 * and ESM resolves that by silently omitting it — so `NOT_AVAILABLE` would vanish
 * from this barrel with no error anywhere. Listing every binding by hand costs a
 * long file and buys a surface that cannot fail that way, and that a reviewer can
 * read. `NOT_AVAILABLE` is re-exported once, from `Metric`.
 *
 * @module components/ds
 */

export { Accordion, ADVANCED_FIELDS_ATTR, partitionAdvanced, useDeclaredAdvancedFields } from './Accordion';

export { Alert, ALERT_SEVERITIES, ALERT_VARIANTS, alertRole } from './Alert';

export { Breadcrumb, readTrail } from './Breadcrumb';

export { CommandButton, COMMAND_INTENTS, hasAccessibleText } from './CommandButton';

export { ConfirmDialog, CONFIRM_INTENTS } from './ConfirmDialog';

export {
  COLUMN_ALIGNMENTS,
  COLUMN_FORMATS,
  comparatorFor,
  compareNumeric,
  compareText,
  compareTimestamp,
  DataTable,
  DEFAULT_PAGE_SIZE,
  DENSITIES,
  DROPPED_PRIORITY,
  formatCellValue,
  nextSort,
  pageCount,
  pageSlice,
  projectRow,
  readCell,
  SORT_DIRECTIONS,
  sortRows,
  toEpochMs,
  toFiniteNumber,
  // `NOT_AVAILABLE` is NOT taken from here — see the docblock. It comes from `Metric`.
} from './DataTable';

export { Drawer, DRAWER_PLACEMENTS } from './Drawer';

export { EmptyState, EMPTY_VARIANTS, REQUIRED_EMPTY_FIELDS } from './EmptyState';

export { ErrorState } from './ErrorState';

export { ExchangeStatus, isMeasuredLatency } from './ExchangeStatus';

export { Field, FIELD_ATTR, NUMERIC_TYPES, resolveInputType, shouldShowError } from './Field';

export { COUNT_ATTR, emptyVariantFor, FILTER_KINDS, FilterBar } from './FilterBar';

// `Spinner` is exported by this module but not from this barrel — see the docblock.
export { LOADING_KINDS, LoadingState, SKELETON_GEOMETRY } from './LoadingState';

export {
  formatFigure,
  METRIC_FORMATS,
  METRIC_TIERS,
  Metric,
  NOT_AVAILABLE,
  NotAvailableMarker,
  tierClasses,
} from './Metric';

export { PAGE_HEADER_HEIGHT_PX, PageHeader } from './PageHeader';

export { MONEY_CONTENT, Panel } from './Panel';

export { PnLDisplay } from './PnLDisplay';

export {
  DEFAULT_RISK_THRESHOLDS,
  deriveRiskLevel,
  normaliseRiskLevel,
  RISK_LEVELS,
  RiskIndicator,
} from './RiskIndicator';

export { SectionHeader } from './SectionHeader';

export { Skeleton } from './Skeleton';

export { COLOUR_PROPS, humaniseState, NotAvailable, StatusBadge, STATUS_BADGE_SIZES } from './StatusBadge';

export {
  normaliseStrategyStatus,
  resolveStrategyHealth,
  STRATEGY_STATUSES,
  StrategyStatus,
} from './StrategyStatus';

export { nextTabIndex, TAB_NAVIGATION_KEYS, Tabs } from './Tabs';

export { Tooltip, TOOLTIP_PLACEMENTS } from './Tooltip';

export {
  ENVIRONMENT_BADGE_VARIANTS,
  environmentBadge,
  TradingEnvironmentBadge,
} from './TradingEnvironmentBadge';
