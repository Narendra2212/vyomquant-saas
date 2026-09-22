/**
 * pages/RiskSettings.jsx — the account's risk limits and its four kill switches.
 *
 * retail-ui-simplification task 7.2 (commit 8). Requirements 4.1, 4.2, 4.3, 4.4, 5.1, 5.2,
 * 5.3, 6.2, 6.5, 19.1, 19.2, 19.3. design.md §2.4 (the nine-question procedure), §6.
 *
 * Requirement 4.5's FIRST page, because it is the smallest one carrying a large colour block:
 * 53 literals and 16 absolute pixel sizes in 438 lines. Both entries go to zero here.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT MUST NOT CHANGE, AND WHAT PROVES IT DID NOT
 * ═══════════════════════════════════════════════════════════════════════════
 * This page holds trading-safety controls. Every toggle writes
 * `PUT /api/risk/settings` immediately; every slider writes it 800ms after the trader stops
 * moving it. `tests/unit/pages/riskSettingsKillSwitch.test.jsx` (task 7.1) was written and
 * observed green against the PRE-migration file for exactly that reason, and its strongest
 * assertion is the one this migration had to survive: the request body a kill switch issues
 * from the keyboard is deep-equal to the body it issues from a pointer, on both trees.
 *
 * So the three handlers below — `saveRiskConfig`, `handleToggleSwitch`,
 * `handleStrategyAllocationChange` — are carried across UNCHANGED, debounce timings
 * included, and `handleToggleSwitch` keeps computing the payload inside the `setKillSwitches`
 * updater. That last one is an antipattern and it is deliberately not "fixed" here: the fix
 * changes when the write happens relative to the state change, which is a behavioural change
 * to a kill switch inside a commit whose subject is typography and colour.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * STEP 1 — ONE `loading`/`error` PAIR OUT, THREE PANEL STATES IN (Requirement 4.1)
 * ═══════════════════════════════════════════════════════════════════════════
 * What was here: `isLoadingRisk` — set `true`, set `false`, **and read by nothing** — plus a
 * `Promise.allSettled` over the three reads whose only failure handling was a toast reading
 * *Failed to load risk settings.* Because `allSettled` never rejects, that `catch` was
 * reachable only by a throw in the *processing*, so a 503 on any of the three reads produced
 * no toast, no message and no state: the panel simply rendered the page's constructor
 * defaults — 500 / 10 / 3 and four switch positions — as though the server had reported them.
 * That is the failure Requirement 4.1 exists for, and it was invisible.
 *
 * Each read now has its own `usePanelState`, so each panel says what happened to ITS read:
 *
 *   config  → the limits panel and the kill-switch panel
 *   margin  → the margin-health panel
 *   limits  → the per-strategy allocations panel, whose `empty` arm is a real state
 *
 * Three hooks rather than one because the three failures are three different facts and only
 * one of them stops a trader arming a guard. Same three GETs, once each on mount
 * (`deps: []`), same paths, same parameters — `api-paths.budget.js` is untouched.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * STEP 2 — FIVE SUBSTITUTED FIGURES, DECLARED AND THEN RENDERED HONESTLY (Req 4.2, 19.1-3)
 * ═══════════════════════════════════════════════════════════════════════════
 * `design/pageFields.js` gains a `risk-settings` page with eight entries. The five the
 * declaration exists for were these:
 *
 *     Number(m.margin_ratio ?? 0)   Number(m.free_margin ?? 0)   Number(m.risk_score ?? 0)
 *     Number(s.max_position_size ?? 20)          s.strategy_name ?? `Strategy #${i + 1}`
 *
 * Every one now reads `fromNullable(read(body, entry.path), entry.reason)` and renders
 * through `ds/Metric` or its `NotAvailableMarker`, so a figure the server did not send
 * arrives as the marker with its reason instead of as a plausible number. `risk_score ?? 0`
 * is the one that matters most and it is §6.3's hazard in another field: **a risk score of
 * zero is the safest-looking figure a broken margin read can publish.** A genuine `0.0`
 * margin ratio still renders `0.00%`, because it is a reading.
 *
 * THE MARGIN FIGURES ARE NOW ON SCREEN, AND THAT IS A RESTORATION
 * --------------------------------------------------------------
 * `api/modules/risk.js`'s own JSDoc on `getMarginHealth` says it: *"`pages/RiskSettings.jsx`
 * is the caller and renders `margin_ratio`, `free_margin` and `risk_score`."* It did not.
 * The read fired on every mount, its answer went into `marginData`, and `marginData` was
 * referenced by no JSX — while `ProgressBar` and `RiskMeter` sat imported and unused beside
 * it. So this is the same move task 5.2 made for Marketplace's trending listings: a read the
 * page has always issued is rendered where it belongs, with no request added and none
 * changed.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * STEP 3 — THE PRIMITIVES (Requirement 4.3), AND THE ONE THING THAT STAYED HAND-BUILT
 * ═══════════════════════════════════════════════════════════════════════════
 *   the page frame, title, subtitle, commands   → `ds/PageHeader` + `ds/CommandButton`
 *   the four regions                            → `ds/Panel`, driven by `usePanelState`
 *   loading / empty / error / unavailable        → `ds/Panel`'s own arms (no page copy)
 *   the three margin figures                    → `ds/Metric`
 *   an absent figure anywhere                   → `ds/Metric`'s `NotAvailableMarker`
 *   the save / load / kill-switch outcomes      → `ds/Alert`
 *
 * **`Toggle` stays a page-local component**, and that is the finding Requirement 4.4 asks to
 * be recorded rather than resolved by inventing a primitive: `ds/` has no switch. `ds/Field`
 * covers text, number, select and textarea; `ds/CommandButton` is a command, not a state; and
 * a kill switch is neither — it is a persistent two-state setting. Adding `ds/Switch` for one
 * page is how a second design system starts (Requirement 17.1), so the component stays here,
 * keeps the role, name, tab stop and Enter/Space handler it already had, and every colour it
 * paints now comes from `token`.
 *
 * `ds/Alert` HAS NO `success` SEVERITY, AND THE SEVERITY IS NOT INVENTED
 * --------------------------------------------------------------------
 * Its five are `critical | error | warning | guidance | info`. A successful save therefore
 * reports at `info` with its sentence unchanged — *Risk parameters updated successfully.* —
 * rather than gaining a sixth severity on a shared primitive from inside a page commit. The
 * kill-switch WebSocket activation is the one notice that moves UP: it was a red toast and it
 * is now `critical`, which is the severity that earns an assertive live region, because
 * "every bot you are running has just been halted" is not a polite announcement.
 *
 * NO `errorCopy.js` CONTEXT WAS ADDED EITHER
 * -----------------------------------------
 * `CONTEXT_COPY` has no `risk` key, so the three panels fall to `default` — *Something went
 * wrong / This has been reported. Try again, and use the reference below if you contact
 * support.* Authored copy, never `err.message`. A `risk` entry would read better and it is an
 * edit to a table eleven other surfaces resolve through, which §4.5 keeps out of a page
 * commit. Recorded as a follow-up rather than taken here.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * STEP 4 — THE 16 PIXEL SIZES, BY §2.4'S NINE QUESTIONS (Requirement 2.4)
 * ═══════════════════════════════════════════════════════════════════════════
 * Each resolution is recorded as a comment on its own call site in the form
 * `// fontSize: 12 → --text-micro (label)`. The ratchet blanks comments, so the audit trail
 * costs nothing and the file measures 0. The cohorts:
 *
 *   12 ×3, 13 ×3  the three limit rows — Q4 sends the NAME to `micro` (it has an adjacent
 *                 value it describes) and Q7 sends the VALUE to `title`, which is
 *                 `ds/Metric`'s own label/figure pair and is why those two questions are
 *                 ordered the way they are
 *   12 ×1, 10 ×1  a kill-switch row — Q8 sends the switch NAME to `title` (it names a region
 *                 holding an icon, an explanation and a control, and Q4 misses it because a
 *                 control is not a value) and Q1 sends the EXPLANATION to `body`, +30%. The
 *                 name stays above the explanation in the hierarchy: 14px over 13px
 *   13 ×1         the page subtitle — Q1, a sentence → `body`, via `ds/PageHeader`
 *   24 ×1         the `<h1>` — Q8, page heading → `page`, via `ds/PageHeader`
 *   12 ×2         the two command labels → `ds/CommandButton`; the declaration is removed
 *                 rather than mapped, because the step belongs to the primitive
 *   12 ×1         the toast → `ds/Alert`; same
 *   12 ×1         *No strategy-specific limits configured* → Q1 a sentence, and the render
 *                 moves to `ds/Panel`'s `empty` arm, so the declaration goes with it
 *   12 ×1, 12 ×1  a strategy card — Q8 name → `title`, Q7 allocation → `body`
 *
 * Nine of the sixteen resolve by moving the call site onto a primitive that already reads a
 * declared step; seven resolve in place. Two are shrinks (the limit names, 12 → 10) and both
 * are Q4 answered literally rather than a layout yield: §2.5's tell for an illegal shrink is
 * one that appears beside an overflow it is avoiding, and there is none — `micro` is the
 * floor and the label sits on its own line with the figure.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * STEP 5 — THE 53 COLOUR LITERALS (Requirements 5.1, 5.2, 5.3)
 * ═══════════════════════════════════════════════════════════════════════════
 * The map applied, so a reader can check it against `tokens.css` rather than re-derive it.
 * Eight of the fourteen values were a second spelling of a token that already existed:
 *
 *   #080a0e            -> surface.canvas       #0c1017 -> surface.panel
 *   #1e293b            -> line.default         #334155 -> line.strong
 *   #f8fafc #e2e8f0    -> content.primary      #94a3b8 -> content.secondary
 *   #64748b            -> content.muted        #00d4ff -> brand.base
 *
 * The other six needed a decision, and Requirement 5.3 says the decision is
 * `design/semantic.js`'s rather than the nearest-hue one:
 *
 *   * **#10b981 and #059669** (the switch's on state, its border, the armed-guard icon, the
 *     dirty Save button) -> `token.status.live.fg`, which is the group `statusToken('active')`
 *     returns. That is the same retirement the Marketplace retoken recorded for the same hue,
 *     and `live`/`connected`/`profit` are one green in `tokens.css` — `live` is the name that
 *     describes an ARMED guard rather than a profitable position.
 *   * **#374151 and #4b5563** (the switch's off track and border) -> `line.default` and
 *     `line.strong`. An unarmed guard is not a warning state and must not borrow one: it is
 *     an inactive control, and the line tokens are what inactive controls are drawn in.
 *   * **#ffffff** (the switch knob) -> `content.primary`. Pure white is not in the palette.
 *   * **#ef4444 / #38bdf8** (the toast's error and info arms) and **rgba(0,0,0,0.5)** and
 *     **rgba(0,0,0,0.2)** (two hand-mixed shadows) are gone rather than mapped: `ds/Alert`
 *     derives its hue from `severity`, and `token.shadow.panel` is the declared elevation.
 *
 * One thing is NOT retokened and is recorded instead of guessed: nothing here paints a
 * profit or a loss, so `pnlToken` is not reached on this page at all. The only semantic
 * question was armed-versus-unarmed, and it is answered above.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, AlertTriangle, RefreshCw, Save, Shield, TrendingDown } from 'lucide-react';

import { Alert } from '../components/ds/Alert';
import { CommandButton } from '../components/ds/CommandButton';
import { Metric, NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { api } from '../api';
import { PAGES, PAGE_FIELDS_BY_PAGE } from '../design/pageFields';
import { fromNullable } from '../design/reported';
import { token } from '../design/tokens';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';
import wsClient from '../websocketClient';

// ─────────────────────────────────────────────────────────────────────────────
// The declaration, indexed once
// ─────────────────────────────────────────────────────────────────────────────

/**
 * This page's `pageFields.js` entries by `field`, so a call site names the figure and gets
 * its path and its reason together.
 *
 * `pageFields.js`'s header asks for exactly this shape and no more: the module holds no
 * renderer, so the walk lives at the page that knows what it is rendering.
 */
const FIELD = Object.freeze(
  Object.fromEntries(PAGE_FIELDS_BY_PAGE[PAGES.RISK_SETTINGS].map((f) => [f.field, f])),
);

/** A dotted path off a response body. `read(body, 'margin_ratio')`. */
const read = (body, path) =>
  path.split('.').reduce((node, key) => (node == null ? undefined : node[key]), body);

/**
 * One declared field, as a `Reported<T>`, from the response that carries it.
 *
 * The entry's own `reason` travels with the absence, which is the half Requirement 19.3 calls
 * load-bearing. For an `absence: NEVER` entry the reason is `null` by declaration and
 * `reported.js` supplies its own — see the note in `pageFields.js` on why that is not a
 * shortened reason but a fallback the declaration says should be unreachable.
 */
const reported = (body, field) => fromNullable(read(body, field.path), field.reason);

/** A per-strategy field read off one element of `limits[]`, whose path carries a `[]`. */
const reportedItem = (item, field) =>
  fromNullable(read(item, field.path.split('[].')[1]), field.reason);

/**
 * The page's own starting positions for the three range controls.
 *
 * These are `routers/risk.py:48`'s seed values, not numbers chosen here: a range input must
 * have a position before the read lands, and taking the server store's own seed is the only
 * choice that cannot disagree with it. The READOUT beside each control renders the declared
 * path instead, so the control's position and the server's reading stay distinguishable.
 */
const CONTROL_START = Object.freeze({ maxLoss: 500, maxPos: 10, leverage: 3 });

/** The four guards, with the wording and the default positions the page has always had. */
const DEFAULT_KILL_SWITCHES = Object.freeze([
  {
    key: 'loss',
    label: 'Daily Loss Limit',
    description: 'Stop all bots if daily loss exceeds limit',
    active: true,
    icon: TrendingDown,
  },
  {
    key: 'blackswan',
    label: 'Black Swan Protection',
    description: 'Halt trading on extreme market volatility',
    active: true,
    icon: AlertTriangle,
  },
  {
    key: 'streak',
    label: 'Consecutive Loss Protection',
    description: 'Pause on 3 consecutive losing trades',
    active: false,
    icon: Activity,
  },
  {
    key: 'capital',
    label: 'Capital Utilization Limit',
    description: 'Stop at 80% capital utilization',
    active: true,
    icon: Shield,
  },
]);

/**
 * What the per-strategy list IS, stated rather than implied.
 *
 * `_user_strategy_limits` is an in-memory per-process dict, so this list reports what one
 * backend process holds and a limit written by an earlier worker is absent from it. That is
 * `LiveTrading.jsx`'s `REGISTRY_CAVEAT` in another store, and a trader who reads the list as
 * complete would conclude a limit is not set when it is. It belongs on the panel, once,
 * rather than on every row.
 */
const LIMITS_BASIS =
  'Per-strategy limits the trading service is currently holding. The account-wide limits '
  + 'above apply to every strategy either way.';

/** The one figure format the three limit readouts need, spelled once. */
const formatLimit = {
  maxDailyLoss: (value) => `$${value}`,
  maxPositions: (value) => `${value} Slots`,
  maxLeverage: (value) => `${value}x`,
};

/**
 * A safe, client-facing sentence for a failed WRITE.
 *
 * `ApiError.getUserMessage()` answers from the response envelope — `data.message`,
 * `data.detail`, `data.error` — so the server's own refusal survives, which matters here
 * because `PUT /api/risk/settings` 400s with *Max daily loss must be greater than 0* and
 * that sentence is the most useful thing there is to say about it.
 *
 * What this no longer does is fall through to `err.message`. That was the previous line's
 * last resort and it is the one leg that could put an exception class or a stack frame in
 * front of a retail trader (Requirement 11.3, Property 5). Nothing authored is lost by
 * dropping it: every server-supplied sentence is reached by the call above, and anything
 * else was never copy in the first place.
 */
const writeFailureMessage = (err, fallback) => {
  if (typeof err?.getUserMessage === 'function') {
    const authored = err.getUserMessage();
    if (typeof authored === 'string' && authored.trim()) return authored;
  }
  return fallback;
};

/**
 * The switch a kill switch is.
 *
 * `ds/` HAS NO SWITCH PRIMITIVE and one is not added here — see the header. What this keeps,
 * unchanged from before the migration, is the whole of its accessibility contract: the
 * `switch` role, `aria-checked`, the accessible name from the row's visible text, a tab stop,
 * and Enter/Space activation that `preventDefault`s Space so the page does not scroll the
 * setting out from under the trader. `riskSettingsKillSwitch.test.jsx` asserts every one of
 * those, and asserts that the request this path issues is the request the pointer issues.
 */
const Toggle = ({ active, onClick, disabled = false, label }) => (
  <div
    role="switch"
    aria-checked={active}
    aria-label={label}
    aria-disabled={disabled || undefined}
    tabIndex={0}
    onClick={disabled ? undefined : onClick}
    onKeyDown={(event) => {
      if (disabled) return;
      if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
        // Space scrolls the page by default, which would move the control out from
        // under the setting just toggled.
        event.preventDefault();
        onClick?.(event);
      }
    }}
    style={{
      width: 44,
      height: 24,
      borderRadius: token.radius.xl,
      cursor: disabled ? 'not-allowed' : 'pointer',
      position: 'relative',
      transition: `all ${token.transition.base}`,
      // An ARMED guard is `statusToken('active')`'s group; an unarmed one is an inactive
      // control and takes a line token rather than borrowing a warning hue.
      background: active ? token.status.live.fg : token.line.strong,
      border: `1px solid ${active ? token.status.live.fg : token.line.default}`,
      flexShrink: 0,
      opacity: disabled ? 0.5 : 1,
    }}
  >
    <div
      style={{
        position: 'absolute',
        top: 2,
        left: active ? 22 : 2,
        width: 20,
        height: 20,
        borderRadius: token.radius.full,
        transition: `all ${token.transition.base}`,
        background: token.content.primary,
        boxShadow: token.shadow.panel,
      }}
    />
  </div>
);

export default function RiskSettings() {
  const [maxLoss, setMaxLoss] = useState(CONTROL_START.maxLoss);
  const [maxPos, setMaxPos] = useState(CONTROL_START.maxPos);
  const [leverage, setLeverage] = useState(CONTROL_START.leverage);
  const [killSwitches, setKillSwitches] = useState(DEFAULT_KILL_SWITCHES);
  const [strategyLimits, setStrategyLimits] = useState([]);
  const [isSaving, setIsSaving] = useState(false);
  const [notice, setNotice] = useState(null);
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
  const sliderDebounceRef = useRef(null);
  const strategyDebounceMapRef = useRef({});

  // ── The three reads (Requirement 4.1) ─────────────────────────────────────
  //
  // `deps: []` on each, so each fires once on mount exactly as the `Promise.allSettled`
  // did. Same three paths, same absence of parameters.
  const readConfig = useCallback(() => api.risk.getConfig(), []);
  const readMargin = useCallback(() => api.risk.getMarginHealth(), []);
  const readLimits = useCallback(() => api.risk.getStrategyLimits(), []);

  const config = usePanelState(readConfig, { deps: [] });
  const margin = usePanelState(readMargin, { deps: [] });
  const limits = usePanelState(readLimits, { deps: [] });

  // The envelope both shapes the old code accepted: `res.data ?? res`. Kept, because the
  // shape a deployment answers with is not this commit's question.
  const configBody = config.data?.data ?? config.data;
  const marginBody = margin.data?.data ?? margin.data;

  /*
   * The config read seeds the CONTROLS. It is an effect rather than a render-time derivation
   * because these are editable positions: once the trader has moved a slider, the server's
   * answer must not reach in and move it back.
   */
  useEffect(() => {
    if (config.state !== PANEL_STATES.READY) return;
    if (!configBody || typeof configBody !== 'object') return;
    setMaxLoss(configBody.max_daily_loss ?? CONTROL_START.maxLoss);
    setMaxPos(configBody.max_positions ?? CONTROL_START.maxPos);
    setLeverage(configBody.max_leverage ?? CONTROL_START.leverage);
    if (configBody.kill_switches) {
      setKillSwitches((prev) =>
        prev.map((ks) => ({ ...ks, active: configBody.kill_switches[ks.key] ?? ks.active })),
      );
    }
  }, [config.state, configBody]);

  /*
   * The limits read seeds the per-strategy rows. `max_position_size` and `strategy_name` are
   * NO LONGER coerced here — the `?? 20` and the `Strategy #N` are gone and each row carries
   * its own `Reported` figure, resolved at the point of render. `allocationPct` is the range
   * control's position and falls back to 0 rather than to 20: a control positioned at a
   * figure nobody set is what the substitution was.
   */
  useEffect(() => {
    if (limits.state !== PANEL_STATES.READY) return;
    const body = limits.data?.data ?? limits.data;
    const rows = Array.isArray(body?.limits) ? body.limits : (Array.isArray(body) ? body : []);
    setStrategyLimits(
      rows.map((s, i) => {
        const allocation = reportedItem(s, FIELD.strategyAllocationPct);
        return {
          id: s.strategy_id ?? i + 1,
          // The id is a real server field, so it is a different fact rather than an invented
          // one. The declared reason says which of the two is on screen.
          name: reportedItem(s, FIELD.strategyName),
          fallbackName: s.strategy_id ?? null,
          allocation,
          allocationPct: allocation.available
            ? Math.max(0, Math.min(100, Number(allocation.value)))
            : 0,
          maxDailyTrades: s.max_daily_trades ?? null,
          allowedSymbols: s.allowed_symbols ?? [],
          enabled: s.enabled ?? true,
        };
      }),
    );
  }, [limits.state, limits.data]);

  // ── The live risk socket, unchanged ───────────────────────────────────────
  useEffect(() => {
    const unsubActivated = wsClient.subscribe('risk.kill_switch_activated', (data) => {
      // The one notice that moves UP a severity: `critical` is what earns an assertive live
      // region, and every bot on the account has just been halted.
      setNotice({
        severity: 'critical',
        message: data?.message || 'Emergency Kill Switch Activated across platform.',
      });
      setKillSwitches((prev) => prev.map((ks) => ({ ...ks, active: true })));
    });

    const unsubRecovered = wsClient.subscribe('risk.kill_switch_recovered', () => {
      setNotice({ severity: 'info', message: 'Emergency Kill Switch Recovered. Trading active.' });
    });

    return () => {
      if (unsubActivated) unsubActivated();
      if (unsubRecovered) unsubRecovered();
    };
  }, []);

  // ── The write path. Carried across unchanged (task 7.1 asserts it) ────────
  const saveRiskConfig = async (overrides = {}) => {
    setIsSaving(true);
    try {
      const payload = {
        max_daily_loss: Number(
          overrides.max_daily_loss !== undefined ? overrides.max_daily_loss : maxLoss,
        ),
        max_positions: Number(
          overrides.max_positions !== undefined ? overrides.max_positions : maxPos,
        ),
        max_leverage: Number(
          overrides.max_leverage !== undefined ? overrides.max_leverage : leverage,
        ),
        circuit_breaker_armed: true,
        kill_switches:
          overrides.kill_switches
          || killSwitches.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {}),
      };
      const res = await api.risk.updateConfig(payload);
      if (res?.data) {
        setMaxLoss(res.data.max_daily_loss ?? payload.max_daily_loss);
        setMaxPos(res.data.max_positions ?? payload.max_positions);
        setLeverage(res.data.max_leverage ?? payload.max_leverage);
      }
      setNotice({ severity: 'info', message: 'Risk parameters updated successfully.' });
      setHasUnsavedChanges(false);
    } catch (err) {
      setNotice({
        severity: 'error',
        message: writeFailureMessage(err, 'Failed to sync risk parameters.'),
      });
    } finally {
      setIsSaving(false);
    }
  };

  const handleManualSave = () => {
    saveRiskConfig();
  };

  const handleResetToDefaults = () => {
    setMaxLoss(CONTROL_START.maxLoss);
    setMaxPos(CONTROL_START.maxPos);
    setLeverage(CONTROL_START.leverage);
    setKillSwitches((prev) =>
      prev.map((ks) => ({
        ...ks,
        active: ks.key === 'loss' || ks.key === 'blackswan' || ks.key === 'capital',
      })),
    );
    setHasUnsavedChanges(true);
    setNotice({ severity: 'guidance', message: 'Settings reset to defaults. Click Save to apply.' });
  };

  const handleSliderChange = (key, value) => {
    if (key === 'maxLoss') setMaxLoss(value);
    if (key === 'maxPos') setMaxPos(value);
    if (key === 'leverage') setLeverage(value);

    setHasUnsavedChanges(true);
    clearTimeout(sliderDebounceRef.current);
    sliderDebounceRef.current = setTimeout(() => {
      saveRiskConfig({
        max_daily_loss: key === 'maxLoss' ? value : maxLoss,
        max_positions: key === 'maxPos' ? value : maxPos,
        max_leverage: key === 'leverage' ? value : leverage,
      });
    }, 800);
  };

  const handleToggleSwitch = (switchKey) => {
    setKillSwitches((prev) => {
      const next = prev.map((ks) => (ks.key === switchKey ? { ...ks, active: !ks.active } : ks));
      const switchPayload = next.reduce((acc, ks) => ({ ...acc, [ks.key]: ks.active }), {});
      setHasUnsavedChanges(true);
      saveRiskConfig({ kill_switches: switchPayload });
      return next;
    });
  };

  const handleStrategyAllocationChange = (id, value) => {
    const numeric = Math.max(0, Math.min(100, Number(value)));
    setStrategyLimits((prev) =>
      prev.map((s) =>
        s.id === id
          ? { ...s, allocationPct: numeric, allocation: { available: true, value: numeric } }
          : s,
      ),
    );
    setHasUnsavedChanges(true);

    clearTimeout(strategyDebounceMapRef.current[id]);
    strategyDebounceMapRef.current[id] = setTimeout(async () => {
      try {
        await api.risk.updateStrategyLimit(id, { max_position_size: numeric });
        setNotice({ severity: 'info', message: 'Strategy allocation updated.' });
        setHasUnsavedChanges(false);
      } catch (err) {
        setNotice({
          severity: 'error',
          message: writeFailureMessage(err, 'Failed to update strategy limit.'),
        });
      }
    }, 800);
  };

  // ───────────────────────────────────────────────────────────────────────────
  // One limit row: a name, its reported figure, and the control that sets it
  // ───────────────────────────────────────────────────────────────────────────

  const renderLimit = ({ field, controlId, value, min, max, step, onChange }) => {
    const figure = reported(configBody, field);
    return (
      <div key={field.field}>
        <div className="mb-1.5 flex items-baseline justify-between gap-3">
          {/* fontSize: 12 → --text-micro (label — Q4: it names the adjacent figure). The
              same label/figure treatment `ds/Metric` applies to its own pair. */}
          <label
            htmlFor={controlId}
            className="text-micro font-medium uppercase tracking-wide text-content-secondary"
          >
            {field.label}
          </label>
          {figure.available ? (
            // fontSize: 13 → --text-title (value — Q7, panel-level).
            <span className="font-mono text-title font-semibold text-brand">
              {formatLimit[field.field](figure.value)}
            </span>
          ) : (
            // The app's one marker, carrying the reason. The control below still has a
            // position, and the two are deliberately not the same thing.
            <NotAvailableMarker label={field.label} reason={figure.reason} />
          )}
        </div>
        <input
          id={controlId}
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={onChange}
          style={{ width: '100%', accentColor: token.brand.base }}
        />
      </div>
    );
  };

  return (
    <div className="relative flex-1 overflow-y-auto bg-surface-canvas p-6 text-content-primary">
      <PageHeader
        title="Risk Management"
        // fontSize: 13 → --text-body (sentence), set by the primitive.
        subtitle="Configure risk parameters and institutional safety guards"
        actions={
          <>
            <CommandButton
              intent="secondary"
              icon={RefreshCw}
              onClick={handleResetToDefaults}
              disabled={isSaving}
              disabledReason="A save is in flight. Reset is available again once it lands."
            >
              Reset
            </CommandButton>
            <CommandButton
              intent="primary"
              icon={Save}
              onClick={handleManualSave}
              loading={isSaving}
              loadingLabel="Saving changes"
              disabled={!isSaving && !hasUnsavedChanges}
              disabledReason="Nothing to save — the guards and limits write as you change them."
            >
              Save Changes
            </CommandButton>
          </>
        }
      />

      {/* The outcome of a write, and the two events the risk socket reports. `severity`
          chooses the hue, the icon and whether the announcement is assertive; the sentences
          are unchanged from the toast this replaces. */}
      {notice ? (
        <Alert
          severity={notice.severity}
          title={notice.message}
          onDismiss={() => setNotice(null)}
          dismissLabel="Dismiss this message"
          className="mb-5"
        />
      ) : null}

      <div className="mb-6 grid grid-cols-1 gap-5 xl:grid-cols-[1.2fr_1fr]">
        <Panel
          title="Portfolio risk limits"
          state={config.state}
          loading={{ kind: 'skeleton-metric', rows: 3, label: 'Loading your risk limits' }}
          empty={{
            headline: 'No risk limits are recorded yet',
            body: 'Set a daily loss cap, a position count and a leverage ceiling, and every '
              + 'strategy on the account trades inside them.',
            action: { label: 'Save these limits', onClick: handleManualSave },
          }}
          error={{ error: config.error, onRetry: config.refetch }}
          unavailable={{ reason: 'Your risk limits cannot be read right now.' }}
          unauthorised={{ error: config.error }}
        >
          <div className="flex flex-col gap-5">
            {renderLimit({
              field: FIELD.maxDailyLoss,
              controlId: 'risk-max-daily-loss',
              value: maxLoss,
              min: 50,
              max: 5000,
              step: 50,
              onChange: (e) => handleSliderChange('maxLoss', Number(e.target.value)),
            })}
            {renderLimit({
              field: FIELD.maxPositions,
              controlId: 'risk-max-positions',
              value: maxPos,
              min: 1,
              max: 30,
              step: 1,
              onChange: (e) => handleSliderChange('maxPos', Number(e.target.value)),
            })}
            {renderLimit({
              field: FIELD.maxLeverage,
              controlId: 'risk-max-leverage',
              value: leverage,
              min: 1,
              max: 20,
              step: 1,
              onChange: (e) => handleSliderChange('leverage', Number(e.target.value)),
            })}
          </div>
        </Panel>

        <Panel
          title="Automated protection guards"
          state={config.state}
          loading={{ kind: 'skeleton-cards', rows: 4, label: 'Loading your protection guards' }}
          empty={{
            headline: 'No protection guards are recorded yet',
            body: 'A guard halts trading on its own when the condition it watches is met, '
              + 'without waiting for you to be at the screen.',
            action: { label: 'Save these guards', onClick: handleManualSave },
          }}
          error={{ error: config.error, onRetry: config.refetch }}
          unavailable={{ reason: 'Your protection guards cannot be read right now.' }}
          unauthorised={{ error: config.error }}
        >
          <div className="flex flex-col gap-3">
            {killSwitches.map((ks) => (
              <div
                key={ks.key}
                className="flex items-center justify-between gap-3 rounded-lg border border-line-default bg-surface-canvas px-3 py-2.5"
              >
                <div className="flex items-start gap-2.5">
                  <ks.icon
                    size={18}
                    aria-hidden="true"
                    style={{
                      color: ks.active ? token.status.live.fg : token.content.muted,
                      flexShrink: 0,
                    }}
                  />
                  <div>
                    {/* fontSize: 12 → --text-title (heading — Q8: it names the row, which
                        holds the glyph, the explanation and the control. Q4 misses it
                        because a switch is a control and not an adjacent value). */}
                    <div className="text-title font-semibold text-content-primary">{ks.label}</div>
                    {/* fontSize: 10 → --text-body (sentence, +30%). It explains what the
                        guard does, which is the one thing a trader needs before arming it. */}
                    <div className="mt-0.5 text-body text-content-secondary">{ks.description}</div>
                  </div>
                </div>
                <Toggle
                  active={ks.active}
                  label={ks.label}
                  onClick={() => handleToggleSwitch(ks.key)}
                />
              </div>
            ))}
          </div>
        </Panel>
      </div>

      {/* The read `api/modules/risk.js` already documents this page as rendering. */}
      <Panel
        title="Margin health"
        state={margin.state}
        loading={{ kind: 'skeleton-metric', rows: 1, columns: 3, label: 'Loading your margin health' }}
        empty={{
          headline: 'No margin health has been reported',
          body: 'These three readings come from your account balances, so they appear once the '
            + 'account has been opened and read.',
          action: { label: 'Read again', onClick: margin.refetch },
        }}
        error={{ error: margin.error, onRetry: margin.refetch }}
        unavailable={{ reason: 'Your margin health cannot be read right now.' }}
        unauthorised={{ error: margin.error }}
        className="mb-6"
      >
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Metric
            label={FIELD.marginRatio.label}
            value={reported(marginBody, FIELD.marginRatio)}
            format="percent"
            precision={2}
            tier={2}
            className="rounded-lg border border-line-default bg-surface-canvas p-3"
          />
          <Metric
            label={FIELD.freeMargin.label}
            value={reported(marginBody, FIELD.freeMargin)}
            format="percent"
            precision={2}
            tier={2}
            className="rounded-lg border border-line-default bg-surface-canvas p-3"
          />
          <Metric
            label={FIELD.riskScore.label}
            value={reported(marginBody, FIELD.riskScore)}
            format="integer"
            tier={2}
            className="rounded-lg border border-line-default bg-surface-canvas p-3"
          />
        </div>
      </Panel>

      <Panel
        title={`Strategy capital allocations (${strategyLimits.length})`}
        state={limits.state}
        loading={{ kind: 'skeleton-cards', rows: 2, label: 'Loading your per-strategy limits' }}
        empty={{
          headline: 'No per-strategy limit is set',
          body: 'The account-wide limits above apply to every strategy. A per-strategy limit '
            + 'caps how much capital one of them may use on its own.',
          action: { label: 'Open your strategies', to: '/app/strategies' },
        }}
        error={{ error: limits.error, onRetry: limits.refetch }}
        unavailable={{ reason: 'Your per-strategy limits cannot be read right now.' }}
        unauthorised={{ error: limits.error }}
      >
        <p className="mb-4 text-body text-content-secondary">{LIMITS_BASIS}</p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {strategyLimits.map((s) => (
            <div
              key={s.id}
              className="rounded-lg border border-line-default bg-surface-canvas p-3"
            >
              <div className="mb-2 flex items-baseline justify-between gap-3">
                {/* fontSize: 12 → --text-title (heading — Q8: it names the card, which holds
                    the figure and the control). */}
                <label
                  htmlFor={`risk-allocation-${s.id}`}
                  className="text-title font-semibold text-content-primary"
                >
                  {s.name.available ? (
                    s.name.value
                  ) : (
                    <>
                      <NotAvailableMarker label={FIELD.strategyName.label} reason={s.name.reason} />
                      {s.fallbackName ? (
                        <span className="ml-2 font-mono text-micro text-content-muted">
                          {s.fallbackName}
                        </span>
                      ) : null}
                    </>
                  )}
                </label>
                {s.allocation.available ? (
                  // fontSize: 12 → --text-body (value — Q7, in-panel secondary, so it sits
                  // one step below the card's name rather than competing with it).
                  <span className="font-mono text-body font-semibold text-brand">
                    {`${s.allocationPct}% Allocation`}
                  </span>
                ) : (
                  <NotAvailableMarker
                    label={FIELD.strategyAllocationPct.label}
                    reason={s.allocation.reason}
                  />
                )}
              </div>
              <input
                id={`risk-allocation-${s.id}`}
                type="range"
                min="0"
                max="100"
                value={s.allocationPct}
                onChange={(e) => handleStrategyAllocationChange(s.id, e.target.value)}
                style={{ width: '100%', accentColor: token.brand.base }}
              />
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
