# Requirements Document

## Introduction

VyomQuant is a retail crypto algorithmic-trading SaaS. Its frontend (`algo22-terminal/`, React 18 + Vite + Tailwind CSS v4, deployed to the `vyomquant-frontend` S3/CloudFront distribution) currently mixes several generations of UI work: inline-styled legacy screens driven by a `C` token object in `src/components/ui-legacy/primitives.jsx`, a newer Tailwind-token design system (`DESIGN_SYSTEM_V2.md`), a small partial component library (`src/components/ui/`), and a series of "gamification/addictiveness" audit documents (`ALGO22_UX_MASTER_AUDIT.md`, `IMPLEMENTATION_ROADMAP.md`, `FINAL_UX_CERTIFICATION.md`) whose stated goals (dopamine loops, leaderboards, achievement tiers, market-ticker "aliveness") conflict with this initiative's philosophy.

This initiative redefines the frontend UX direction: VyomQuant is an intelligent, professional trading cockpit that happens to be simple enough for a retail trader — not a gamified consumer app, not a generic SaaS dashboard, and not a marketing site wrapped around trading tools. This requirements document covers Phase 1 (v1) of a multi-phase redesign. It scopes the flagship trading workflow (Dashboard, Strategies, Strategy Builder, Backtester, Live Trading, Signal Trace, Portfolio, Trade History) plus the two pages with hard live-trading-safety and subscription-clarity requirements (Paper Trading, Marketplace subscription states). Billing and Profile/Security/Account pages, the public Landing page, and the full component-duplication/dead-code audit are explicitly deferred to later specs and are out of scope for this document.

## Glossary

- **VyomQuant_Frontend**: The `algo22-terminal` React/Vite single-page application that renders all authenticated trader-facing screens.
- **Design_System**: The centralized set of design tokens (color, spacing, typography scale, radius, shadow, transition, breakpoint, z-index, focus-ring) and shared primitive components (`PageHeader`, `StatusBadge`, `Metric`, `DataTable`, `EmptyState`, `ErrorState`, `LoadingState`, `ConfirmDialog`, `TradingEnvironmentBadge`, `ExchangeStatus`, `PnLDisplay`, `StrategyStatus`, `RiskIndicator`, `SectionHeader`, `FilterBar`, `CommandButton`, and standard form/table/chart/tab/tooltip/dialog/drawer/alert/skeleton/breadcrumb controls) that all in-scope pages consume.
- **Application_Shell**: The persistent authenticated-app layout consisting of the sidebar navigation, top bar (account menu, notifications, connection status), and content outlet, rendered by `AppShell` in `App.jsx`.
- **Trading_Environment**: One of exactly three mutually exclusive modes a piece of UI can represent: Live, Paper, or Backtest.
- **Environment_Indicator**: A persistent, unmistakable visual element that communicates the current `Trading_Environment` for any screen or component displaying position, order, or P&L data.
- **Dashboard_Page**: The route at `/app/dashboard`, the trader's command-center summary view.
- **Strategies_Page**: The route at `/app/strategies`, the strategy management list/table view.
- **Strategy_Builder**: The route at `/app/builder`, the node-graph strategy construction environment.
- **Backtester_Page**: The route at `/app/backtest` (also `/app/backtester`).
- **Live_Trading_Page**: The route(s) displaying live strategy deployment status and execution state (`/app/dashboard` live-trading view and any dedicated live-deployment monitoring screens in scope).
- **Signal_Trace_Page**: The route at `/app/signal-trace`.
- **Portfolio_Page**: The route at `/app/portfolio`.
- **Trade_History_Page**: The route at `/app/trades`.
- **Paper_Trading_Page**: The route at `/app/paper-trading`.
- **Marketplace_Page**: The route at `/app/marketplace`, scoped in this document only to subscription-state display and clarity (not full marketplace browsing/discovery redesign).
- **Deploy_Confirmation_Flow**: The multi-field review step a trader must pass through before a strategy transitions into a Live `Trading_Environment`.
- **Destructive_Action**: Any user-triggered action that stops, deletes, or irreversibly alters a live strategy, live position, or live order (e.g., stop-live-deployment, delete-strategy, cancel-live-order).
- **Empty_State**: A shared component shown when a data collection has zero items.
- **Loading_State**: A shared component/pattern shown while a data collection or panel is being fetched.
- **Error_State**: A shared component shown when a data fetch or action fails.
- **Backend_API**: The existing REST/WebSocket backend (`backend_app`, exposed to the frontend via `src/api/modules/*` and `websocketClient.js`) that this initiative SHALL NOT be modified in trading logic, order execution, auth, billing, or risk-control semantics.

## Requirements

### Requirement 1: Design System Foundation

**User Story:** As a frontend developer, I want a single centralized design-token and primitive-component source, so that every in-scope page renders with visual and behavioral consistency instead of reinventing styling per page.

#### Acceptance Criteria

1. THE Design_System SHALL define color, spacing, typography-scale, border-radius, shadow, transition, breakpoint, z-index, and focus-ring tokens in exactly one source location consumed by all in-scope pages.
2. THE Design_System SHALL provide the following reusable primitive components: PageHeader, StatusBadge, Metric, DataTable, EmptyState, ErrorState, LoadingState, ConfirmDialog, TradingEnvironmentBadge, ExchangeStatus, PnLDisplay, StrategyStatus, RiskIndicator, SectionHeader, FilterBar, and CommandButton.
3. WHEN an in-scope page (Dashboard_Page, Strategies_Page, Strategy_Builder, Backtester_Page, Live_Trading_Page, Signal_Trace_Page, Portfolio_Page, Trade_History_Page, Paper_Trading_Page, or the subscription-state elements of Marketplace_Page) renders a status indicator, metric value, data table, empty condition, loading condition, or error condition, THE VyomQuant_Frontend SHALL render it using the corresponding Design_System primitive rather than a page-local, one-off implementation.
4. THE Design_System SHALL define exactly one semantic color mapping each for live/running, connected/paired, profitable/buy, loss/sell, error/disconnected, and warning states, and every in-scope page SHALL apply status color exclusively through that mapping.
5. WHERE a page previously rendered status colors, borders, or emphasis styling on more than the elements representing current state, risk, or required action, THE VyomQuant_Frontend SHALL reduce color and visual emphasis on that page to only those elements.

### Requirement 2: Application Shell and Navigation

**User Story:** As a trader, I want a stable, clearly organized navigation shell, so that I always know where I am in the product and what I can do next.

#### Acceptance Criteria

1. THE Application_Shell SHALL render a sidebar containing exactly these navigation entries, grouped by trading workflow: Dashboard, Strategies, Strategy Builder, Backtester, Live Trading, Signal Trace, Portfolio, Trade History, Marketplace, Paper Trading.
2. WHEN a trader navigates between any two in-scope pages, THE Application_Shell SHALL keep the sidebar and top bar dimensions and position unchanged, producing no layout shift of shell elements.
3. WHEN a trader is on an in-scope page, THE Application_Shell SHALL display a visually distinct active state on the corresponding navigation entry.
4. THE Application_Shell SHALL display each navigation entry with both an icon and a text label.
5. THE Application_Shell SHALL display a persistent exchange/WebSocket connection status indicator in the top bar reflecting the current connection state reported by the Backend_API.
6. IF the Backend_API reports the exchange or WebSocket connection as disconnected, THEN THE Application_Shell SHALL reflect the disconnected state in the connection status indicator within 5 seconds of the underlying state change.

### Requirement 3: Dashboard Command Center

**User Story:** As a trader, I want the dashboard to answer "is everything OK?" at a glance, so that I can immediately identify anything needing my attention.

#### Acceptance Criteria

1. THE Dashboard_Page SHALL render portfolio value, today's P&L, total P&L, and current drawdown as the highest-visual-priority elements on the page.
2. THE Dashboard_Page SHALL render active strategy count, live position summary, recent signals/orders, and system/exchange health as secondary-priority elements positioned below the elements described in 3.1.
3. WHEN one or more exchange connections, live strategies, or order submissions are in an error or disconnected state, THE Dashboard_Page SHALL surface an alert summarizing the condition above the secondary-priority elements.
4. THE Dashboard_Page SHALL NOT render more than one row of equally-weighted metric cards for the primary metrics described in 3.1.
5. WHEN the Backend_API has not yet returned dashboard data, THE Dashboard_Page SHALL render a Loading_State for each panel awaiting data.
6. IF a Backend_API request for dashboard data fails, THEN THE Dashboard_Page SHALL render an Error_State describing the failure and offering a retry action, without displaying fabricated or cached-as-live values.

### Requirement 4: Strategies Management List

**User Story:** As a trader, I want a professional list of my strategies with clear status and actions, so that I can manage my strategy portfolio efficiently.

#### Acceptance Criteria

1. THE Strategies_Page SHALL render strategies in a list or table view showing, per strategy: name, status, version, market/exchange, deployment state, performance summary, risk state, last signal time, last execution time, and updated time.
2. THE Strategies_Page SHALL provide, per strategy, the actions Backtest, Deploy, Edit, Duplicate, View, and Signal Trace.
3. WHERE a per-strategy action is a Destructive_Action or initiates a Live Trading_Environment transition, THE Strategies_Page SHALL render that action visually separated from non-destructive actions.
4. WHEN a trader has zero strategies, THE Strategies_Page SHALL render an Empty_State explaining that no strategies exist and offering a direct action to create one.
5. THE Strategies_Page SHALL NOT render strategies as oversized cards exceeding the information density needed to display the fields listed in 4.1.

### Requirement 5: Strategy Builder Construction Environment

**User Story:** As a trader building an algorithmic strategy, I want a clear, professional node-graph editor with legible data flow and validation, so that I can construct correct strategies with confidence.

#### Acceptance Criteria

1. THE Strategy_Builder SHALL render the strategy graph as a sequence of data-flow stages that are visually distinguishable: market data, transformation/indicator, logic, model, and action nodes.
2. WHEN a trader selects a node, THE Strategy_Builder SHALL display that node's configuration in a dedicated panel without altering the layout or visibility of unrelated canvas nodes.
3. WHILE no node is selected, THE Strategy_Builder SHALL keep the configuration panel closed or collapsed so the canvas is unobstructed.
4. IF a trader attempts to create a connection between two nodes that is invalid for the strategy graph, THEN THE Strategy_Builder SHALL prevent the connection from being created and SHALL display the specific reason the connection is invalid and the action needed to make it valid.
5. THE Strategy_Builder SHALL visually indicate an invalid-connection attempt using a state distinguishable from a destructive or error condition on saved strategy data.
6. WHEN a trader saves a strategy, THE Strategy_Builder SHALL persist the current graph as a new version and display confirmation that the save succeeded.
7. THE Strategy_Builder SHALL provide direct actions to backtest and deploy the currently saved strategy version from within the builder.

### Requirement 6: Backtester Research Flow

**User Story:** As a trader, I want a guided backtest workflow with prioritized results, so that I can evaluate a strategy's historical performance without being overwhelmed by data.

#### Acceptance Criteria

1. THE Backtester_Page SHALL present the backtest configuration flow in this order: strategy selection, market/data selection, period selection, capital and risk configuration, then run action.
2. WHEN a backtest run completes, THE Backtester_Page SHALL render total return, net P&L, maximum drawdown, Sharpe ratio, win rate, and trade count as the highest-visual-priority result elements.
3. THE Backtester_Page SHALL render the equity curve and drawdown curve as secondary-priority result elements positioned below the elements described in 6.2.
4. THE Backtester_Page SHALL render the detailed trade list and extended statistics behind progressive disclosure (e.g., a tab or expandable section) rather than at equal visual weight to the elements described in 6.2.
5. WHILE a backtest run is in progress, THE Backtester_Page SHALL display a Loading_State indicating the run is executing and SHALL disable the run action until the run completes or fails.
6. IF a backtest run fails, THEN THE Backtester_Page SHALL display an Error_State describing the failure and SHALL NOT display fabricated or placeholder result metrics.

### Requirement 7: Live Trading State Visibility

**User Story:** As a trader running a live strategy, I want to immediately know whether it is actually running and what it is doing, so that I never have to hunt for that information.

#### Acceptance Criteria

1. THE Live_Trading_Page SHALL render, as the highest-visual-priority elements, the current connection state, exchange, account, strategy, market, and Trading_Environment for each active deployment.
2. THE Live_Trading_Page SHALL render position, entry price, mark price, unrealized P&L, realized P&L, exposure, and risk state as secondary-priority elements positioned below the elements described in 7.1.
3. THE Live_Trading_Page SHALL render the latest signal, order, and execution status as tertiary-priority elements positioned below the elements described in 7.2, followed by signal/order history.
4. THE Live_Trading_Page SHALL display an Environment_Indicator on every panel showing live position, order, or P&L data, labeled unambiguously as Live.
5. WHEN the underlying strategy deployment stops, errors, or disconnects, THE Live_Trading_Page SHALL update the connection state element described in 7.1 to reflect that condition within 5 seconds.
6. IF a trader initiates a Destructive_Action against a live deployment (e.g., stop deployment), THEN THE Live_Trading_Page SHALL require an explicit confirmation step before executing the action.

### Requirement 8: Live Deployment Safety and Confirmation

**User Story:** As a trader deploying a strategy to live trading, I want an explicit, unambiguous review before real orders can be placed, so that I cannot accidentally start live trading.

#### Acceptance Criteria

1. WHEN a trader initiates deployment of a strategy to a Live Trading_Environment, THE Deploy_Confirmation_Flow SHALL display strategy name, version, exchange, account, market, quantity/sizing configuration, risk configuration, and estimated exposure before the deployment can be confirmed.
2. THE Deploy_Confirmation_Flow SHALL include a final step containing an explicit statement that confirming will place real orders using real funds.
3. THE Deploy_Confirmation_Flow SHALL require an explicit trader action on the final step described in 8.2 before the Backend_API deployment request is submitted.
4. WHERE the deployment target Trading_Environment is Paper or Backtest rather than Live, THE Deploy_Confirmation_Flow SHALL NOT display the statement described in 8.2.
5. THE Deploy_Confirmation_Flow SHALL visually distinguish a Live deployment confirmation from a Paper or Backtest deployment confirmation using the Environment_Indicator.

### Requirement 9: Signal Trace Explainability

**User Story:** As a trader, I want to see why a strategy generated a buy or sell action, so that I can trust and audit its decisions.

#### Acceptance Criteria

1. THE Signal_Trace_Page SHALL render a timeline showing, in order: market data input, indicator/feature evaluation, model output, logic evaluation, signal, order decision, submission, execution, and position update.
2. WHILE a timeline stage has not yet occurred for a given trace, THE Signal_Trace_Page SHALL render that stage in a visually distinct pending/not-yet-reached state rather than omitting it.
3. THE Signal_Trace_Page SHALL render the timeline in a collapsed, summary form by default, with each stage expandable to reveal its technical detail.
4. WHEN a trader has no signal trace data for the selected strategy, THE Signal_Trace_Page SHALL render an Empty_State explaining why no trace data exists and the action needed to generate one.

### Requirement 10: Portfolio Overview

**User Story:** As a trader, I want a clear view of my total exposure and risk, so that I understand my financial position at a glance.

#### Acceptance Criteria

1. THE Portfolio_Page SHALL render total value, available balance, invested capital, unrealized P&L, realized P&L, and total exposure as the highest-visual-priority elements.
2. THE Portfolio_Page SHALL render current drawdown alongside the elements described in 10.1 rather than in a separate, lower-priority section.
3. THE Portfolio_Page SHALL render a summary list of open positions positioned above the detailed per-position breakdown.
4. WHEN a trader has zero open positions, THE Portfolio_Page SHALL render an Empty_State explaining that no positions are open.

### Requirement 11: Trade History Table

**User Story:** As a trader, I want a dense, scannable record of my trades, so that I can review and filter past activity efficiently.

#### Acceptance Criteria

1. THE Trade_History_Page SHALL render trades in a table with columns for time, market, side, quantity, price, P&L, strategy, and status.
2. THE Trade_History_Page SHALL provide filter and search controls for the table described in 11.1.
3. THE Trade_History_Page SHALL align all numeric columns (quantity, price, P&L) consistently within the table.
4. THE Trade_History_Page SHALL provide pagination or virtualization for the table described in 11.1.
5. WHEN a trader has zero trades matching the current filters, THE Trade_History_Page SHALL render an Empty_State distinguishing "no trades at all" from "no trades match the current filter."
6. THE Trade_History_Page SHALL NOT render individual trades as cards in place of the table described in 11.1.

### Requirement 12: Paper Trading Environment Clarity

**User Story:** As a trader using paper trading, I want it to look and feel like live trading's execution UI while being impossible to confuse with real trading, so that I can rehearse safely.

#### Acceptance Criteria

1. THE Paper_Trading_Page SHALL use the same position, order, and execution-status visual language as the Live_Trading_Page.
2. THE Paper_Trading_Page SHALL display a persistent Environment_Indicator labeled "PAPER TRADING" on every panel showing position, order, or P&L data.
3. THE Paper_Trading_Page SHALL render the Environment_Indicator described in 12.2 using a visually distinct treatment from the Live Environment_Indicator described in Requirement 7.4.
4. THE Paper_Trading_Page SHALL NOT display any action or label that could be interpreted as placing a real order.

### Requirement 13: Marketplace Subscription State Clarity

**User Story:** As a trader browsing the strategy marketplace, I want subscription states to be immediately unambiguous, so that I know whether I can currently use a given strategy.

#### Acceptance Criteria

1. THE Marketplace_Page SHALL render, for each listed strategy, exactly one of the following subscription states using a StatusBadge: Available, Subscribed, Expired, or Pending Verification.
2. THE Marketplace_Page SHALL NOT display proprietary strategy logic, node graphs, or parameter values for a strategy the trader is not subscribed to.
3. WHEN a trader's subscription to a marketplace strategy transitions to Expired, THE Marketplace_Page SHALL update the corresponding StatusBadge within 5 seconds of the next page data refresh.

### Requirement 14: Consistent Empty, Loading, and Error States

**User Story:** As a trader, I want every screen to clearly tell me what's happening when data is missing, loading, or failing, so that I'm never confused by a blank or broken-looking page.

#### Acceptance Criteria

1. WHEN any in-scope page or panel has zero items to display for a data collection, THE VyomQuant_Frontend SHALL render an Empty_State that explains what is missing, why it matters, and provides a specific next action.
2. WHILE any in-scope page or panel is awaiting data from the Backend_API, THE VyomQuant_Frontend SHALL render a Loading_State appropriate to that element (skeleton, inline spinner, button-level, table-level, or chart-level).
3. IF a Backend_API request underlying any in-scope page or panel fails, THEN THE VyomQuant_Frontend SHALL render an Error_State containing a human-readable description of what happened, whether a retry is possible, and a retry action where the failure is retryable.
4. THE VyomQuant_Frontend SHALL NOT render a raw HTTP status code, exception class name, or stack trace as user-facing Error_State content.
5. THE VyomQuant_Frontend SHALL NOT substitute fabricated, hardcoded, or previously-cached-as-live values for P&L, balances, positions, strategy performance, orders, signals, or exchange status when the corresponding Backend_API request has failed.

### Requirement 15: Form, Table, and Chart Consistency

**User Story:** As a trader, I want forms, tables, and charts to behave consistently across the product, so that I can predict how to interact with any screen.

#### Acceptance Criteria

1. THE VyomQuant_Frontend SHALL render an explicit text label for every form field on in-scope pages, distinct from and in addition to any placeholder text.
2. WHEN a trader submits a form on an in-scope page with an invalid field value, THE VyomQuant_Frontend SHALL display an inline validation message identifying the specific field and the reason it is invalid.
3. WHEN a form control on an in-scope page is disabled, THE VyomQuant_Frontend SHALL display the reason the control is disabled.
4. THE DataTable primitive SHALL support sortable columns, consistent numeric-column alignment, and a sticky header.
5. THE chart components on in-scope pages SHALL render labeled axes, a tooltip on hover/focus, and a legend where more than one data series is displayed.
6. WHERE an in-scope page contains advanced or infrequently-used form settings, THE VyomQuant_Frontend SHALL render those settings collapsed by default.

### Requirement 16: Meaningful Notifications

**User Story:** As a trader, I want to be notified only about events that matter, so that notifications remain useful signals rather than noise.

#### Acceptance Criteria

1. THE VyomQuant_Frontend SHALL raise a notification for each of the following event categories when reported by the Backend_API: deployment succeeded, deployment failed, exchange disconnected, order rejected, strategy stopped, backtest completed, and subscription expired.
2. THE VyomQuant_Frontend SHALL NOT raise a notification for informational or routine events outside the categories listed in 16.1 on in-scope pages.

### Requirement 17: Responsive Layout for In-Scope Pages

**User Story:** As a trader using different screen sizes, I want every in-scope page to remain usable and legible, so that I'm not blocked by broken layouts.

#### Acceptance Criteria

1. THE VyomQuant_Frontend SHALL render each in-scope page without horizontal overflow at desktop, laptop, and tablet viewport widths.
2. WHEN the viewport width is reduced to tablet size, THE DataTable primitive SHALL become horizontally scrollable or condense its visible columns rather than overflow or clip.
3. THE VyomQuant_Frontend SHALL render modal and drawer components on in-scope pages fully within the viewport without overlapping other open modals or drawers.
4. THE Strategy_Builder SHALL provide a defined behavior for tablet-width viewports (e.g., restricted editing with a message, or a scrollable/zoomable canvas) rather than an unusable or clipped canvas.

### Requirement 18: Accessibility of Trading-Critical Actions

**User Story:** As a trader who relies on keyboard navigation or assistive technology, I want to operate every trading-critical action without a mouse, so that I am not excluded from safely using the product.

#### Acceptance Criteria

1. THE VyomQuant_Frontend SHALL make every trading-critical action (deploy, stop deployment, cancel order, confirm live deployment) operable via keyboard on in-scope pages.
2. WHEN a focusable element on an in-scope page receives keyboard focus, THE VyomQuant_Frontend SHALL render a visible focus indicator on that element.
3. WHEN a modal or dialog opens on an in-scope page, THE VyomQuant_Frontend SHALL trap keyboard focus within that modal or dialog until it is closed.
4. THE VyomQuant_Frontend SHALL provide an accessible name for every interactive control (button, input, toggle) on in-scope pages.

### Requirement 19: Non-Interference with Trading Logic and Backend Behavior

**User Story:** As a platform owner, I want this redesign confined to the frontend presentation layer, so that trading correctness, security, and billing behavior are not put at risk.

#### Acceptance Criteria

1. THE redesign of in-scope pages SHALL NOT modify order execution logic, risk-control logic, strategy-versioning logic, authentication/authorization logic, or billing logic implemented in the Backend_API.
2. WHERE an in-scope page requires a Backend_API change to support a required UX behavior (e.g., a missing field or status value), THE implementation SHALL make the minimal necessary Backend_API change and SHALL document that change explicitly rather than approximating it with fabricated frontend data.
3. IF a capability described in this document does not yet exist in the Backend_API, THEN THE corresponding in-scope page SHALL render an explicit "not available" state rather than simulated or placeholder functionality.
4. THE VyomQuant_Frontend SHALL NOT contain TODO, FIXME, "coming soon" placeholder text, non-functional buttons, non-functional links, or non-functional dropdowns on any in-scope page.
