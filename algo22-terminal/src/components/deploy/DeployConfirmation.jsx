/**
 * ═══════════════════════════════════════════════════════════════════════════
 * components/deploy/DeployConfirmation.jsx — the `Deploy_Confirmation_Flow`, rendered
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * vyomquant-ui-redesign task 10.5, second half. `design.md` §8.1, §8.2, §8.3, §8.5.
 * Requirements 8.1, 8.2, 8.3, 8.4, 8.5, 19.1. Properties P13, P14.
 *
 * WHAT THIS FILE IS, AND WHAT IT DELIBERATELY IS NOT
 * -------------------------------------------------
 * This is the *rendering* half of task 10.5. Every decision about the flow — which steps
 * exist, whether the real-funds acknowledgement is constructed, which edge a confirm takes,
 * what the dialog is titled, which of the eight Requirement 8.1 fields are available, and
 * what request the submission is — was already made by `lib/deployFlow.js` and is read from
 * it here rather than re-derived. Nothing in this file computes a next state, a step list,
 * an environment, an endpoint or a wire field.
 *
 * The one thing this file *does* that the pure layer cannot is **perform** the request the
 * flow describes. That happens in exactly one place — {@link DeployConfirmation}'s
 * submission effect — and the four independent reasons it cannot happen anywhere else, or
 * twice, are set out on that effect.
 *
 * THREE AUTHORITIES, NONE OF THEM THIS COMPONENT (Requirement 19.1)
 * ---------------------------------------------------------------
 *   1. **`lib/deployFlow.js`** owns the state machine. `transition` is the only source of a
 *      next flow, `submissionOf` the only source of a request.
 *   2. **`ds/ConfirmDialog`** owns the confirmation surface: the focus trap with initial
 *      focus on cancel, Escape, the overlay claim, the viewport clamp, and — the part that
 *      matters most here — the real-funds gate, which it enforces in two independent places
 *      (the button's `disabled` attribute *and* a guard inside its confirm handler). Its
 *      `acknowledgement` prop is passed through untouched; its API is not changed and it is
 *      not wrapped in a second gate of our own.
 *   3. **`lib/deployPreflight.js` + `hooks/useDeployPreflight.js` +
 *      `components/DeployPreflightPanel.jsx`** own whether a deploy is *permitted*. That
 *      trio is wrapped, not replaced, and no verdict is derived here: `deployable` is read,
 *      never computed. `deployFlow.js` holds no deployability opinion on purpose, and this
 *      component does not supply one — it consults the existing gate's answer.
 *
 *      The gate is consulted here for one reason, and it is Requirement 13.6: the preflight
 *      is re-read *while the workflow is open*, and a condition that turns red mid-flow has
 *      to stop the deploy. The workflow is now this dialog, so the dialog has to honour it.
 *      That is the same one authority the page already consults twice (button state and
 *      handler refusal) — a third fail-closed reading of the same summary, not a second
 *      opinion, and it shadows neither of the page's checks.
 *
 * WHICH STEP CARRIES THE PREFLIGHT PANEL, AND WHY
 * ----------------------------------------------
 * **Review.** §8.3's `Review` state lists `+ DeployPreflightPanel result` as part of its own
 * contents, and three things make that the right place rather than a convention to follow:
 *
 *   * The panel answers "may this deployment proceed", which belongs beside the eight facts
 *     the trader is checking, *before* the irreversible step — not after it.
 *   * `Review` is the step a trader can sit on and step back to, so it is where a condition
 *     turning red under them (Requirement 13.6) is both visible and actionable.
 *   * Putting it on `AckLive` would place a scrolling list of verdicts between the real-funds
 *     sentence and the checkbox that acknowledges it, burying the one sentence Requirement
 *     8.2 exists for.
 *
 * `AckLive` still honours the gate — the refusal note below renders on any step whose confirm
 * would submit — so moving the *panel* off that step does not move the *authority* off it.
 *
 * WHY THE ACKNOWLEDGEMENT IS THE DIALOG'S STATE AND NOT THE FLOW'S
 * ---------------------------------------------------------------
 * `deployFlow.js` models the tick as a separate `ACKNOWLEDGE` event, and `ConfirmDialog`
 * models it as internal state gating its own `onConfirm`. Both are right, and they are not
 * in competition: the *trader* still performs two distinct actions in order (tick the box,
 * then activate confirm), which is what Requirement 8.3 asks for, and the dialog is the one
 * enforcing that ordering — twice.
 *
 * So `onConfirm` firing while an `acknowledgement` is mounted is *proof* the box is ticked;
 * there is no path through `ConfirmDialog.handleConfirm` that reaches `onConfirm` otherwise.
 * The handler below therefore dispatches `ACKNOWLEDGE` and then `ADVANCE`, and the machine's
 * `AckLive → Submitting` edge stays gated on `acknowledged === true` exactly as written. The
 * alternative — rendering our own checkbox to dispatch `ACKNOWLEDGE` — would mean
 * reimplementing the gate the design system already owns, and giving up its second guard.
 *
 * A note on lifetime: `ConfirmDialog` resets its tick whenever the acknowledgement's
 * statement changes, and the statement is `null` on every step except `AckLive`. Stepping
 * back to `Review` and forward again therefore always arrives with the box unticked, which
 * is the same conclusion `deployFlow.js` reaches by clearing `acknowledged` on `BACK`.
 *
 * TOKENS ONLY
 * -----------
 * Every colour and length below comes from `design/tokens.js` via `cssVar`, or from
 * `design/semantic.js` via `statusToken`. No hex, no `rgba()`, no `C`. The one exception is
 * visual, not authored: `DeployPreflightPanel` is a pre-existing out-of-scope component with
 * its own literals, and Requirement 19.1 says wrap it rather than rewrite it.
 *
 * @module components/deploy/DeployConfirmation
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { endpoints } from '../../api';
import { cssVar } from '../../design/tokens';
import { statusToken } from '../../design/semantic';
import { readReported } from '../../design/reported';
import { errorLine } from '../../design/errorLine';
import { deploymentRequest } from '../../lib/deployPreflight';
import {
  DEPLOY_EVENT,
  DEPLOY_STATE,
  DEPLOY_STEP,
  advanceRefusal,
  beginDeployFlow,
  canSubmit,
  currentStep,
  submissionOf,
  transition,
} from '../../lib/deployFlow';
import { useDeployPreflight } from '../../hooks/useDeployPreflight';
import ConfirmDialog from '../ds/ConfirmDialog';
import { NotAvailableMarker, formatFigure } from '../ds/Metric';
import DeployPreflightPanel from '../DeployPreflightPanel';

/* ══════════════════════════════════════════════════════════════════════════
 * Authored copy
 *
 * Every sentence a trader reads here is either authored in this table, authored in
 * `lib/deployFlow.js` (titles, confirm labels, blocker messages, the real-funds statement),
 * or produced by `design/errorCopy.js` / `design/errorLine.js`. Nothing is assembled from a
 * server message, a status or an exception (Requirement 14.4).
 * ══════════════════════════════════════════════════════════════════════════ */

/** One line per step, stating what confirming from it does — and does not — do. */
const STEP_DESCRIPTION = Object.freeze({
  [DEPLOY_STEP.CONFIGURE]:
    'Check the deployment target. Nothing is submitted from this step.',
  [DEPLOY_STEP.REVIEW]:
    'Check every field below against what you intended. Nothing has been submitted yet.',
  [DEPLOY_STEP.ACK_LIVE]:
    'This is the last step before real orders are placed.',
});

const SUBMITTING_DESCRIPTION =
  'The deployment has been submitted and is waiting for the server to answer. Nothing can '
  + 'be changed until it does.';

const DEPLOYED_DESCRIPTION = 'The deployment was accepted.';

/**
 * The gate's refusal, when the preflight has an answer and the answer is no.
 *
 * Word-for-word the sentence `pages/Strategies.jsx`'s deploy handler already refuses with,
 * so the same refusal cannot be described two different ways by two surfaces.
 */
const GATE_REFUSAL =
  'Not every mandatory deployment check has passed yet, so nothing was deployed.';

/** Cancel's label, per state. `Close` reads better than `Cancel` after a refusal. */
const CANCEL_LABEL = 'Cancel';
const CLOSE_LABEL = 'Close';

/** The confirm label for a step whose confirm does not reach the backend. */
const CONTINUE_LABEL = 'Continue';

/** §8.3's `Failed --> Review` edge, as the label of the control that takes it. */
const RETURN_LABEL = 'Back to review';

const BUSY_LABEL = 'Submitting…';

/**
 * `errorCopy.js`'s `CONTEXT_COPY` key for last-resort copy. `strategies` rather than a new
 * key: a deploy failure is a strategies-surface failure, and inventing a context key would
 * mean authoring a twelfth entry in a table this task does not own.
 */
const ERROR_CONTEXT = 'strategies';

/* ══════════════════════════════════════════════════════════════════════════
 * Presentation-only helpers
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * How each Requirement 8.1 field is rendered once `readReported` says it is available.
 *
 * A formatting choice, not a flow decision — `lib/deployFlow.js` hands over raw values on
 * purpose. Seven of the eight fields are text the server or the trader authored, so they are
 * `raw`: grouping or rounding an account label or a version string would corrupt it.
 *
 * `estimatedExposure` is the one figure, and it is `number` rather than `currency`
 * deliberately: it comes from `executionConfigFrom`'s `max_order_notional`, and no currency
 * was stated anywhere in the configuration. Attaching a symbol would claim one
 * (Requirement 14.5). `number` with no `precision` groups thousands and rounds nothing.
 */
const FIELD_FORMAT = Object.freeze({ estimatedExposure: 'number' });

/**
 * A stable content key for one configuration.
 *
 * The flow is rebuilt when the *content* of `config` changes, not when its identity does, so
 * a page passing an object literal does not rebuild the flow on every render. This is the
 * same technique `lib/deployPreflight.js`'s `preflightQueryKey` uses for the poll's query,
 * and for the same reason.
 *
 * `null` for a value that cannot be serialised (a cycle), which makes the key constant and
 * so falls back to "rebuild only when the dialog opens" — the safe direction.
 *
 * @param {unknown} config
 * @returns {string|null}
 */
function configSignature(config) {
  try {
    return JSON.stringify(config ?? null);
  } catch {
    return null;
  }
}

/* ══════════════════════════════════════════════════════════════════════════
 * The step list — rendered from the flow, never filtered from a fixed one
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * `flow.steps`, as an ordered list.
 *
 * The list is **iterated**, not filtered: `lib/deployFlow.js` derives it from the resolved
 * environment, so on the Paper and Backtest paths there is no third entry to leave out and
 * on an unresolved target there is only one entry. That is what makes P14 a statement about
 * construction rather than about CSS — this component could not hide an `ackLive` step
 * because it is never handed one.
 *
 * @param {Object} props
 * @param {ReadonlyArray<Object>} props.steps `flow.steps`.
 * @param {number|null} props.activeOrdinal `currentStep(flow)?.ordinal`, or `null` for the
 *   states that are not steps.
 */
function StepTrail({ steps, activeOrdinal }) {
  return (
    <ol
      data-deploy="step-trail"
      style={{
        listStyle: 'none',
        display: 'flex',
        flexWrap: 'wrap',
        gap: cssVar('spacing.3'),
        margin: 0,
        padding: 0,
        fontSize: cssVar('text.micro'),
        textTransform: 'uppercase',
        letterSpacing: '0.06em',
      }}
    >
      {steps.map((step) => {
        const active = step.ordinal === activeOrdinal;
        return (
          <li
            key={step.id}
            data-deploy-step={step.id}
            data-deploy-step-active={active ? 'true' : 'false'}
            aria-current={active ? 'step' : undefined}
            style={{
              color: active ? cssVar('color.content.primary') : cssVar('color.content.secondary'),
              fontWeight: active ? 600 : 400,
            }}
          >
            {step.heading}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * A note that states a refusal, with no server text in it.
 *
 * `role="alert"` rather than `role="status"`: every use below is a reason the trader cannot
 * proceed, which is worth interrupting for, and each one is rendered as soon as it becomes
 * true rather than only after a rejected click.
 */
function RefusalNote({ children, marker }) {
  const treatment = statusToken('warning');
  return (
    <p
      role="alert"
      data-deploy={marker}
      style={{
        margin: 0,
        padding: cssVar('spacing.3'),
        background: treatment.wash,
        border: `1px solid ${treatment.fg}`,
        borderRadius: cssVar('radius.md'),
        color: cssVar('color.content.primary'),
        fontSize: cssVar('text.small'),
      }}
    >
      {children}
    </p>
  );
}

/* ══════════════════════════════════════════════════════════════════════════
 * The component
 * ══════════════════════════════════════════════════════════════════════════ */

/**
 * §8.3's `Deploy_Confirmation_Flow`, hosted in one `ds/ConfirmDialog`.
 *
 * ═══ THE PROPS A PAGE MUST PASS ═══
 *
 * @param {Object} props
 * @param {boolean} props.open Whether the flow is open. Going `false → true` opens a fresh
 *   flow in `Configure`; nothing is remembered from a previous opening.
 * @param {Object} props.config The deployment being confirmed, passed straight to
 *   `beginDeployFlow`. Its fields are that function's, not this component's:
 *   `{strategyId, version, environment, account, strategyName, exchange, market, capital,
 *   tradeSizePct, riskConfigId, gapStrategy}` — plus the optional display overrides
 *   `accountLabel`, `sizingLabel`, `riskConfigLabel`, `symbol`, `exchangeAccountId`. A field
 *   the page does not carry renders the not-available marker and does **not** block the
 *   flow; `strategyId`, `version` and a resolvable `environment` are the three that do
 *   (`deployBlockers`). The object may be an inline literal — the flow is keyed on its
 *   content, not its identity.
 * @param {Function} props.onCancel Called after the flow has been cancelled, for the page to
 *   clear `open`. Also the target of Escape and of the dialog's cancel action. Required.
 * @param {Function} [props.onDeployed] `(response) => void`, called once the POST has been
 *   accepted, with the server's body. This is where §8.3's *"toast + navigate to Live
 *   Trading"* belongs — the page owns that, not this component.
 * @param {Function} [props.onFailed] `(error) => void`, called once the POST has been
 *   refused. Optional: the dialog already shows the translated failure and offers
 *   §8.3's `Failed → Review` edge, so a page only needs this to log or to refresh a row.
 * @param {Object} [props.preflight] The summary from `hooks/useDeployPreflight` — pass the
 *   page's existing poll (`Strategies.jsx` already has one) and this component will not
 *   start a second. Omit it and the component polls for itself while `open`. Either way the
 *   preflight is the authority and no verdict is derived here.
 * @param {React.ReactNode} [props.children] Rendered on the **`Configure`** step only, which
 *   is where a page's existing target form belongs (§8.3 step 1 is `environment · exchange
 *   account · market · sizing · risk`, and that form is the page's — this component adds no
 *   form logic). Editing it re-derives the flow, so the review grid can never describe a
 *   configuration other than the one on screen.
 */
export function DeployConfirmation({
  open,
  config,
  onCancel,
  onDeployed,
  onFailed,
  preflight: externalPreflight,
  children,
}) {
  /*
   * Callbacks and the raw config are read through refs. The page owns them and may recreate
   * them on every render; making them effect dependencies would restart the preflight poll,
   * or worse, re-run the submission effect, on an unrelated parent render.
   */
  const configRef = useRef(config);
  configRef.current = config;
  const cancelRef = useRef(onCancel);
  cancelRef.current = onCancel;
  const deployedRef = useRef(onDeployed);
  deployedRef.current = onDeployed;
  const failedRef = useRef(onFailed);
  failedRef.current = onFailed;

  const [flow, setFlow] = useState(() => beginDeployFlow(config));
  /** The server's refusal, held for `ConfirmDialog`'s `error` prop. Never rendered raw. */
  const [failure, setFailure] = useState(null);

  const stateRef = useRef(flow.state);
  stateRef.current = flow.state;

  /**
   * How many times a submission has been sent. Compared against `flow.submissionCount`,
   * which `lib/deployFlow.js` increments on each entry into `Submitting`. Declared here
   * because both the opening effect and the submission effect below read it.
   */
  const sentCount = useRef(0);
  /** The server's answer, held between the await and the page callback. */
  const responseRef = useRef(null);

  /* ── Opening, and re-deriving the flow when the configuration changes ────────────────── */

  const signature = configSignature(config);
  const appliedSignature = useRef(signature);
  // Initialised from the current `open` so a dialog mounted already-open does not throw away
  // the flow `useState` just built.
  const wasOpen = useRef(open === true);

  useEffect(() => {
    if (open !== true) {
      wasOpen.current = false;
      return;
    }
    const reopened = wasOpen.current === false;
    wasOpen.current = true;
    const changed = appliedSignature.current !== signature;
    appliedSignature.current = signature;

    if (!reopened && !changed) return;
    /*
     * A configuration change past `Configure` is not offered — the form only exists on step
     * 1 — and yanking a flow that is mid-flight or already answered would be worse than
     * ignoring it. While still on `Configure`, re-deriving is the safe direction and the
     * same conclusion `useDeployPreflight` reaches: a new question means the previous answer
     * is not an answer to it.
     */
    if (!reopened && stateRef.current !== DEPLOY_STATE.CONFIGURE) return;

    setFlow(beginDeployFlow(configRef.current));
    setFailure(null);
    sentCount.current = 0;
  }, [open, signature]);

  /* ── The gate (Requirements 13.3, 13.4, 13.6) ───────────────────────────────────────── */

  /*
   * `flow.config` is frozen at construction and carried by reference through every
   * transition, so this memo is stable across steps — the poll is not restarted by stepping
   * forward. `deploymentRequest` is the same function `pages/Strategies.jsx` builds its query
   * with, so the gate is asked about the binding that will be submitted and not another one.
   */
  const deployRequest = useMemo(() => deploymentRequest(flow.config), [flow.config]);

  const ownPreflight = useDeployPreflight({
    strategyId: flow.config.strategyId ? String(flow.config.strategyId) : null,
    version: flow.config.version ?? null,
    query: deployRequest.query,
    // A page that already polls hands its summary in; asking the same question twice would
    // double the load on an endpoint whose rate ceiling is sized for one poll per workflow.
    enabled: open === true && !externalPreflight,
  });

  const gate = externalPreflight ?? ownPreflight;

  /* ── What confirming from here does ─────────────────────────────────────────────────── */

  const step = currentStep(flow);

  /**
   * Whether activating confirm on this step reaches the backend.
   *
   * Asked of the machine rather than derived: `canSubmit` reads the transition table, and the
   * `ACKNOWLEDGE` probe is how the same question is asked on `AckLive`, where the tick lives
   * in the dialog and so the flow's own `acknowledged` is still false. `transition` is pure,
   * total and never throws, so probing it costs one frozen object and cannot have an effect.
   */
  const confirmSubmits =
    canSubmit(flow) || canSubmit(transition(flow, DEPLOY_EVENT.ACKNOWLEDGE));

  const submitting = flow.state === DEPLOY_STATE.SUBMITTING;
  const failed = flow.state === DEPLOY_STATE.FAILED;

  /** The gate's answer, read — never computed (Requirement 19.1). */
  const gateRefuses = confirmSubmits && gate.deployable !== true;
  const gateRefusal = gate.error ? errorLine(gate.error, ERROR_CONTEXT) : GATE_REFUSAL;

  /** `deployBlockers`, as the one sentence `advanceRefusal` composes from them. */
  const blocked = flow.blockers.length > 0;
  const blockerRefusal = blocked ? advanceRefusal(flow) : null;

  /* ── The one place the request is issued ────────────────────────────────────────────── */

  useEffect(() => {
    /*
     * ═══ REQUIREMENT 8.3 / P13 — THE ONLY CALL TO THE DEPLOY ENDPOINT ═══
     *
     * `submissionOf` returns non-null in exactly one state, `Submitting`, and only for a flow
     * `lib/deployFlow.js` itself produced (a `WeakSet` brand, not a forgeable field). So
     * there is no state before the confirmation step is satisfied in which this effect has
     * anything to send, and no hand-written object that can make it think otherwise.
     *
     * FOUR INDEPENDENT REASONS IT CANNOT SEND TWICE:
     *
     *   1. `Submitting` has no `ADVANCE` edge, so a second confirm cannot re-enter it. This
     *      is the machine's guarantee and it is the load-bearing one.
     *   2. `submissionCount` is compared against what has already been sent, so even a
     *      re-run of this effect for the same entry — a StrictMode double-invoke, a parent
     *      re-render producing a new flow identity — sends nothing.
     *   3. `ConfirmDialog` is `busy` throughout, which disables both of its actions and makes
     *      Escape inert, so the trader cannot activate confirm again in the first place.
     *   4. The cleanup drops a late answer, so an unmount mid-flight cannot dispatch into a
     *      flow that is no longer on screen.
     *
     * The request is `submissionOf(flow)` and nothing else: same endpoint, same body, same
     * `environment` query parameter, through the same `endpoints.strategies.deployVersion`
     * call `pages/Strategies.jsx` makes (Requirement 19.1). No field is added here, and the
     * path is not assembled here.
     */
    const submission = submissionOf(flow);
    if (submission === null) return undefined;
    if (sentCount.current >= flow.submissionCount) return undefined;
    sentCount.current = flow.submissionCount;

    let live = true;

    (async () => {
      try {
        const response = await endpoints.strategies.deployVersion(
          submission.strategyId,
          submission.version,
          submission.body,
          { environment: submission.environment },
        );
        if (!live) return;
        responseRef.current = response;
        setFlow((current) => transition(current, DEPLOY_EVENT.SUCCEEDED));
        if (typeof deployedRef.current === 'function') deployedRef.current(response);
      } catch (error) {
        if (!live) return;
        // Held for `ConfirmDialog`, which renders it through `translateError` and never as
        // `error.message` (Requirement 14.4).
        setFailure(error);
        setFlow((current) => transition(current, DEPLOY_EVENT.REJECTED));
        if (typeof failedRef.current === 'function') failedRef.current(error);
      }
    })();

    return () => {
      live = false;
    };
  }, [flow]);

  /* ── Handlers ───────────────────────────────────────────────────────────────────────── */

  const handleCancel = useCallback(() => {
    // `CANCEL` first, so the flow is terminal before the page can react to it. `Cancelled`
    // carries no submission, which is why Escape and cancel send nothing.
    setFlow((current) => transition(current, DEPLOY_EVENT.CANCEL));
    setFailure(null);
    if (typeof cancelRef.current === 'function') cancelRef.current();
  }, []);

  const handleBack = useCallback(() => {
    setFlow((current) => transition(current, DEPLOY_EVENT.BACK));
    setFailure(null);
  }, []);

  const handleConfirm = useCallback(() => {
    // §8.3's `Failed --> Review`. The confirm control is the only one the dialog offers
    // besides cancel, so it carries the edge; a separate retry would be a second button that
    // submits the same live order.
    if (stateRef.current === DEPLOY_STATE.FAILED) {
      handleBack();
      return;
    }

    /*
     * Requirements 13.4 / 13.6 — the preflight's answer, not ours. Fail closed: a summary
     * that has not been answered, or was answered no, or could not be read, all leave
     * `deployable` false and all refuse here. The refusal is already on screen (see
     * `gateRefuses` below), so this is the rule behind a rendering rather than the only
     * notice of it.
     */
    if (gateRefuses) return;

    if (stateRef.current === DEPLOY_STATE.ACK_LIVE) {
      /*
       * Reaching here is proof the box is ticked: `ConfirmDialog.handleConfirm` returns
       * early unless its acknowledgement is satisfied, and it does so in two independent
       * places. The two trader actions Requirement 8.3 asks for have both happened, in
       * order, so the machine is told about them in order.
       */
      setFlow((current) =>
        transition(transition(current, DEPLOY_EVENT.ACKNOWLEDGE), DEPLOY_EVENT.ADVANCE),
      );
      return;
    }

    setFlow((current) => transition(current, DEPLOY_EVENT.ADVANCE));
  }, [gateRefuses, handleBack]);

  /* ── The review grid (Requirement 8.1) ──────────────────────────────────────────────── */

  /**
   * All eight fields, in `lib/deployFlow.js`'s order, every time.
   *
   * `flow.review` always carries one entry per Requirement 8.1 field, so there is no
   * configuration for which a row is absent — the only variation is whether a row shows a
   * value or the marker. `readReported` makes that one decision, once, and the unavailable
   * arm renders `ds/Metric`'s `NotAvailableMarker`, which is where the em-dash, the
   * `"{label}: not available"` accessible name and the reason already live. No second marker
   * is invented here.
   *
   * `ConfirmDialog`'s own `review` prop lays the grid out; the marker is passed as that
   * row's `value`, which is a React node like any other child, so the dialog's API is used
   * as written rather than extended. Its built-in marker is not reached, because the value
   * it would test for absence is present — and that is deliberate: its marker carries no
   * reason, and Requirement 19.3 wants the reason.
   */
  const reviewRows = useMemo(
    () =>
      flow.review.map((field) => {
        const report = readReported(field.reported);
        const text = report.available
          ? formatFigure(report.value, { format: FIELD_FORMAT[field.id] ?? 'raw' })
          : null;

        return {
          label: field.label,
          value:
            text === null || text === undefined ? (
              <NotAvailableMarker label={field.label} reason={report.reason} />
            ) : (
              text
            ),
        };
      }),
    [flow.review],
  );

  /* ── Assembling the step ────────────────────────────────────────────────────────────── */

  const onReview = step?.id === DEPLOY_STEP.REVIEW;
  const showBack = flow.state === DEPLOY_STATE.REVIEW || flow.state === DEPLOY_STATE.ACK_LIVE;

  let description = step === null ? null : STEP_DESCRIPTION[step.id];
  if (submitting) description = SUBMITTING_DESCRIPTION;
  else if (flow.state === DEPLOY_STATE.DEPLOYED) description = DEPLOYED_DESCRIPTION;
  else if (failed) description = null;

  let confirmLabel = CONTINUE_LABEL;
  if (failed) confirmLabel = RETURN_LABEL;
  else if (confirmSubmits) confirmLabel = flow.presentation.confirmLabel;

  const body = (
    <div
      data-deploy="flow"
      data-deploy-state={flow.state}
      data-deploy-environment={flow.environment ?? 'unconfirmed'}
      style={{ display: 'flex', flexDirection: 'column', gap: cssVar('spacing.4') }}
    >
      <StepTrail steps={flow.steps} activeOrdinal={step?.ordinal ?? null} />

      {blocked ? <RefusalNote marker="blocked">{blockerRefusal}</RefusalNote> : null}

      {/* §8.3 step 1's target form is the page's, and it belongs on the page's step. */}
      {step?.id === DEPLOY_STEP.CONFIGURE ? children : null}

      {/* Requirement 13.3: the server's own verdict per condition, on the Review step. The
          panel's `summary` is left off — the eight-field grid immediately above already
          states which deployment these verdicts are about, honestly, including the fields
          that are not available, and the panel's summary takes plain strings so it has no
          way to say "not available" without a second marker. */}
      {onReview ? (
        <DeployPreflightPanel
          conditions={gate.conditions}
          deployable={gate.deployable}
          reported={gate.reported}
          error={gate.error}
          isLoading={gate.isLoading}
          checkedAt={gate.checkedAt}
          refresh={gate.refresh}
        />
      ) : null}

      {gateRefuses ? <RefusalNote marker="gate-refusal">{gateRefusal}</RefusalNote> : null}

      {showBack ? (
        <div>
          <button
            type="button"
            onClick={handleBack}
            data-deploy="back"
            style={{
              padding: `${cssVar('spacing.1')} ${cssVar('spacing.3')}`,
              background: 'transparent',
              border: `1px solid ${cssVar('color.line.strong')}`,
              borderRadius: cssVar('radius.sm'),
              color: cssVar('color.content.primary'),
              font: 'inherit',
              fontSize: cssVar('text.small'),
              cursor: 'pointer',
            }}
          >
            Back
          </button>
        </div>
      ) : null}
    </div>
  );

  return (
    <ConfirmDialog
      open={open === true}
      onCancel={handleCancel}
      onConfirm={handleConfirm}
      // Requirement 8.5: title, intent and the header environment badge all come from the
      // flow's presentation, which is keyed on the resolved environment.
      title={flow.presentation.title}
      intent={flow.presentation.confirmIntent}
      environment={flow.presentation.environment ?? undefined}
      description={description ?? undefined}
      review={onReview ? reviewRows : undefined}
      /*
       * Requirements 8.2 / 8.4 / P14. Read off the step, not off a flag: the step's own
       * `acknowledgement` is the object `stepsFor` constructed inside its `LIVE` branch, and
       * off the Live path there is no `ackLive` step to read it from. Nothing is hidden here
       * because nothing was built.
       */
      acknowledgement={step?.acknowledgement}
      confirmLabel={confirmLabel}
      cancelLabel={failed ? CLOSE_LABEL : CANCEL_LABEL}
      busy={submitting}
      busyLabel={BUSY_LABEL}
      error={failed ? failure : undefined}
      errorContext={ERROR_CONTEXT}
    >
      {body}
    </ConfirmDialog>
  );
}

export default DeployConfirmation;
