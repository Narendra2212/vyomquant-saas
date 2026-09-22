/**
 * pages/Wizard.jsx — the four-step first-run onboarding surface at `/wizard`.
 *
 * retail-ui-simplification task 7.6 (commit 12). Requirements 2.3, 2.4, 4.1, 4.2, 4.3,
 * 4.4, 5.1, 5.2, 6.2, 19.1, 19.2, 19.3, 19.5. design.md §2.3, §2.4, §2.5, §6.
 *
 * 24 absolute pixel sizes to 0 and 5 colour literals to 0, both budgets lowered in this
 * commit.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE FOUR STEPS AND THEIR ORDER ARE UNCHANGED
 * ═══════════════════════════════════════════════════════════════════════════
 * `Secure Account → Demo Backtest → Connect Exchange → Choose Plan`, the same four
 * strings in the same order, each reached by the same `setStep` call from the same
 * button. No step is removed, merged, skipped or reordered; every back/next path and
 * every explanation of what a step does survives. Requirement 8's one-next-action work
 * is task 12's, not this one.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THREE KINDS OF INVENTED FIGURE WERE STILL HERE, ON THE FIRST SCREEN A NEW
 * RETAIL ACCOUNT SEES
 * ═══════════════════════════════════════════════════════════════════════════
 * An earlier pass fixed this page's fabricated backtest halfway: the unimported
 * `endpoints` reference and the swallowed `ReferenceError` are long gone and
 * `api.strategies.backtest` now really runs. What it did not fix is what the page then
 * DID with the answer, and two more inventions sat beside it. All three are declared in
 * `design/pageFields.js` under a `wizard` page and rendered through `design/reported.js`.
 *
 * **1. TWO FIGURES THAT WERE LITERALS.** The request left the browser, the response came
 * back, and the page threw it away and rendered:
 *
 *       Total Return  +12.4%           Win Rate  68.2%
 *
 * hardcoded to one decimal place under a green tick reading *Backtest Complete!*. The
 * `catch` also set the status to `"complete"`, so a backtest that failed outright
 * published the same two numbers with the same tick. `total_return_pct` and
 * `win_rate_pct` are real keys on the real response — `api/modules/strategies.js`'s
 * `BacktestResult` typedef declares both — so the two figures are now the run's own, and
 * a run that reported neither renders the marker with its declared reason. A genuine `0`
 * still renders `0.0%`, because a strategy that returned nothing returned nothing.
 *
 * **2. A PRICE READ OFF TWO FIELDS THAT DO NOT EXIST.** The plan cards computed
 * `const priceINR = p.inr || 0` and `const priceUSD = p.usd || 0`.
 * `GET /api/billing/plans` is `PricingService.get_localized_plans`, whose per-plan object
 * carries `localized_price`, `currency`, `currency_symbol`, `base_price`, `base_currency`,
 * `checkout_price` and `checkout_currency` — **and no `inr` key and no `usd` key at all.**
 * Both reads were `undefined || 0`, so `priceINR === 0` was structurally always true and
 * **every plan on the page advertised itself as "Free" with a "Start Free" button, Pro and
 * Enterprise included**, while the handler behind that button went on to open a real paid
 * checkout. `pages/Billing.jsx:404` reads `plan.localized_price` and `:406` reads
 * `plan.currency_symbol`, so the correct paths were already in the tree one page away.
 *
 * The `/mo ($${priceUSD})` suffix beside it was dead in two ways at once: it hung off
 * `priceINR > 0`, which was never true, and it sat in JSX children rather than a template
 * literal, so had it ever rendered it would have read `$$0`. It is removed AS DEAD CODE
 * rather than as copy — it has never been on screen. The period it was trying to state
 * survives in the figure's label, *Monthly price*, and `plans[].base_price` with
 * `plans[].base_currency` is where a real second-currency figure would come from if one
 * is wanted later.
 *
 * **3. A GREEN TICK WITH NO READ BEHIND IT.** The *Security Alerts* row was declared
 * `ok: true` — a literal — so every new account was shown a tick against *Notify on new
 * device logins* whatever the truth. Nothing in the backend reports a notification
 * preference, so the row is declared ❌ and says so. Requirement 16.2 keeps the row and
 * its *Manage* control; Requirement 19.5 forbids filling it with a plausible value, and
 * those two are only compatible if the row explains itself.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THREE READS, THREE PANEL STATES (Requirement 4.1)
 * ═══════════════════════════════════════════════════════════════════════════
 * The `securityStatus.loading` flag, the `backtestStatus` idle/running/complete triple and
 * the two `console.error` calls that were the whole of the failure handling are gone. Each
 * read now says what happened to ITS read, and none of the three paths, parameters or
 * bodies changed — `api-paths.budget.js` is untouched.
 *
 *   security  → `supabase.auth.getUser` + `mfa.getAuthenticatorAssuranceLevel`, once on
 *               mount, driving step 1's three rows
 *   backtest  → `api.strategies.backtest`, with the SAME frozen payload, `enabled` until
 *               the trader presses Run so no request is issued before then
 *   plans     → `api.billing.getPlans()`, once on mount, driving step 4's cards
 *
 * The plans read previously had no failure state of any kind: a 500 logged to the console
 * and left `plans` as `[]`, so the last step of onboarding rendered an empty grid and no
 * explanation. It is `ds/Panel`'s `error` arm now, and a 200 carrying zero plans is its
 * `empty` arm — two different facts, which is Requirement 19.4's point.
 *
 * `usePanelState` IS ADOPTED HERE AND WAS DECLINED ON `TwoFA.jsx`, WHICH IS NOT A
 * CONTRADICTION
 * ------------------------------------------------------------------------------------
 * Task 7.3 kept TwoFA's own booleans because what its `isLoading` covered was an
 * ENROLMENT — a multi-call lifecycle with form validation in the same variable, where a
 * hook that may re-issue its reader is the wrong shape. The security read here is none of
 * that: it is a plain read of two facts, it carries no validation, and re-issuing it is
 * harmless. The one thing it shares with TwoFA is that supabase answers `{data, error}`
 * and throws no `ApiError`, so `classifyReadFailure` cannot tell its failures apart and
 * they all land on `error` with `errorCopy`'s authored default. That is a real limit and
 * it is still a strict improvement on `console.error` plus a row reading *not enabled*.
 *
 * WHY THE TWO SECURITY STATES ARE DERIVED FROM A TIMESTAMP'S PRESENCE
 * -----------------------------------------------------------------
 * `user.email_confirmed_at` is `null` for an account whose email is genuinely
 * unconfirmed, so that `null` is a READING and not an absence. Handing it to
 * `fromNullable` directly would turn "not verified" into "not available", which is this
 * module's own failure mode pointed the other way. The figure is therefore
 * `Boolean(user.email_confirmed_at)`, and the marker is reachable only when there is no
 * session to read at all — this page is routed outside the shell, so a signed-out visitor
 * can open it. `getAuthenticatorAssuranceLevel` is the same shape one level along, and the
 * page's own `aal2` expression is carried across unchanged.
 *
 * THE THREE ROW TITLES NOW COME FROM THE DECLARATION
 * -------------------------------------------------
 * Requirement 4.2's labels are read from `pageFields`, never retyped, so *2FA Enabled*
 * reads *Two-factor authentication*. That is the row's NAME; the badge beside it is what
 * reports the state, and a row headed *2FA Enabled* standing next to a badge reading *Not
 * enabled* is a contradiction rather than two spellings. **No explanation was removed:**
 * all three descriptions, including both arms of *Confirmation sent to …* / *Confirm your
 * email address*, are carried verbatim.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE PRIMITIVES (Requirement 4.3), AND THE ONE FINDING THAT KEPT A PAIR HAND-BUILT
 * ═══════════════════════════════════════════════════════════════════════════
 *   the four step regions                → `ds/Panel`, driven by `usePanelState`
 *   loading / empty / error / unavailable → `ds/Panel`'s own arms (no page copy)
 *   the four figures                      → `ds/Metric`
 *   an absent figure anywhere             → `ds/Metric`'s `NotAvailableMarker`
 *   the two security states               → `ds/StatusBadge`
 *   every command                         → `ds/CommandButton`
 *   the two API-key inputs                → `ds/Field`
 *
 * **`ds/SectionHeader` is NOT used for the step heading and subtitle, and that is a
 * Requirement 4.4 finding rather than a shortcut.** Its shape is exactly this pair — a
 * title with a subtitle beneath — but it renders the subtitle at `--text-small` (11px),
 * and all four of this page's subtitles are SENTENCES, which §2.4's Q1 sends to
 * `--text-body` minimum and Requirement 2.3 forbids putting below it. Adopting the
 * primitive would hold four sentences at 11px; changing the primitive's subtitle step
 * would move every section header in the app from inside a page commit. So the pair is
 * rendered directly at `--text-section` and `--text-body`, and the mismatch is recorded
 * here for whoever owns `ds/SectionHeader` next.
 *
 * `handleSelectPlan` IS CARRIED ACROSS UNCHANGED, INCLUDING ITS FAILURE PATH
 * ------------------------------------------------------------------------
 * Same `{ tier, currency: "INR" }` body, same `window.location.href` handover, same
 * `navigate("/app/dashboard")` on both the no-URL and the thrown branch. That last part is
 * a write-path finding — a trader whose checkout could not be opened is sent to the
 * dashboard as though the plan had been chosen — and it is recorded rather than taken
 * here, the way task 7.2 recorded `handleToggleSwitch`'s antipattern: changing when and
 * where a billing action lands is a behavioural change to a payment flow inside a commit
 * whose subject is typography and colour.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 24 PIXEL SIZES, BY §2.4'S NINE QUESTIONS (Requirement 2.4)
 * ═══════════════════════════════════════════════════════════════════════════
 * Q1 carries more here than anywhere else in the tree, because a wizard is mostly
 * sentences and this one had every one of them below the 11px floor or at it:
 *
 *   11 ×4  the four step subtitles      Q1 sentence      → `--text-body`   (+18%)
 *   11 ×1  *Backtest Complete!*         Q1 sentence      → `--text-body`
 *   10 ×1  the security row explanation Q1 sentence      → `--text-body`   (+30%)
 *   10 ×1  the plan feature lines       Q1 sentence      → `--text-body`   (+30%)
 *   18 ×4  the four step headings       Q8 heading       → `--text-section` (18px, exact)
 *   14 ×1  the plan name                Q8 heading       → `--text-title`   (14px, exact)
 *   12 ×1  the security row name        Q8 heading       → `--text-title`
 *   11 ×1  the progress step ordinal    Q5 chip          → `--text-micro`
 *    9 ×1  the progress step label      Q4 label         → `--text-micro`
 *    8 ×1  the RECOMMENDED ribbon       Q5 chip          → `--text-micro`   (+25%)
 *   24 ×1  the plan price               Q7 value, hero   → `ds/Metric` tier 1
 *   16 ×2  the two backtest figures     Q7 value, panel  → `ds/Metric` tier 2
 *    9 ×2  their two labels             Q4 label         → `ds/Metric`'s `--text-micro`
 *   11 ×1  *Processing historical ticks…*                → `ds/LoadingState`; removed
 *   10 ×1  the six exchange buttons                      → `ds/CommandButton`; removed
 *   10 ×1  the `/mo ($$…)` suffix                        → dead code; see above
 *
 * Six of the twenty-four leave with their call site rather than being mapped, because the
 * step belongs to the primitive. Three resolutions are worth arguing rather than listing:
 *
 *   **The step ordinal is a chip, not a label** (Q5 before Q4's miss). The circle is a
 *   filled pill carrying one of three states — done, current, upcoming — and the string it
 *   holds is `1` or `✓`. It does not NAME the step; the label under it does, which is why
 *   Q4 misses it and Q5 catches it. 11 → 10 is a 1px shrink inside a 32px circle and is
 *   Q5 answered literally, not a layout yield.
 *
 *   **The step label loses `whiteSpace: nowrap`** — §2.5's first and cheapest yield.
 *   9 → 10 on four labels across a 560px strip is exactly the growth §2.5 calls the normal
 *   case, and letting *Connect Exchange* wrap costs nothing.
 *
 *   **The plan price goes UP, and the grid yields.** 24 → `ds/Metric` tier 1
 *   (`--text-figure`, 28px) is the +4px §2.5 predicts, and the four-up
 *   `repeat(4,1fr)` grid becomes `1 / 2 / 4` by breakpoint — §2.5's third mechanism, using
 *   the responsive machinery rather than a new one. Reading it as tier 2 instead would have
 *   shrunk a price 42% to avoid that, which is §2.5's named tell for an illegal shrink.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 5 COLOUR LITERALS (Requirements 5.1, 5.2, 5.3)
 * ═══════════════════════════════════════════════════════════════════════════
 *   rgba(0,212,255,0.12)  the current step's circle     → `bg-brand-wash`   (0.10, declared)
 *   rgba(0,212,255,0.05)  the recommended plan card     → `bg-brand-wash`   (same wash, once)
 *   #000 ×2               the glyph on a brand fill     → `text-content-inverse` (#080A0E)
 *   rgba(0,255,136,0.15)  the satisfied row's border    → `border-status-live`
 *
 * Only the last needed `design/semantic.js` rather than a hue match. `#00FF88` is the
 * retired green this palette replaced with `#26A69A`, and `statusToken('active')` returns
 * the `live` group — which is the name that describes a security control that is ON,
 * exactly as task 7.2 argued for an armed guard. Nothing here paints a profit or a loss,
 * so `pnlToken` is not reached on this page at all.
 *
 * TWO OFF-PALETTE CONSTRUCTS NEITHER GUARD CAN SEE WENT WITH THEM, recorded because a
 * scan will not find them for the next reader: `borderRadius: 18` and `borderRadius: 20`,
 * both off the declared radius scale entirely (`--radius-xl` stops at 12), which is the
 * same construct `pages/UpdatePasswordPage.jsx` recorded at task 7.5.
 */

import React, { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bell, CheckCircle, Database, Mail, ShieldCheck, Wifi } from "lucide-react";

import { CommandButton } from "../components/ds/CommandButton";
import { Field } from "../components/ds/Field";
import { Metric, NotAvailableMarker } from "../components/ds/Metric";
import { Panel } from "../components/ds/Panel";
import { StatusBadge } from "../components/ds/StatusBadge";
import { api } from "../api";
import { PAGES, PAGE_FIELDS_BY_PAGE } from "../design/pageFields";
import { fromNullable, unavailable } from "../design/reported";
import { PANEL_STATES, usePanelState } from "../hooks/usePanelState";
import { supabase } from "../supabase";

// ─────────────────────────────────────────────────────────────────────────────
// The declaration, indexed once
// ─────────────────────────────────────────────────────────────────────────────

/** This page's `pageFields.js` entries by `field`. The one index; see `RiskSettings.jsx`. */
const FIELD = Object.freeze(
  Object.fromEntries(PAGE_FIELDS_BY_PAGE[PAGES.WIZARD].map((f) => [f.field, f])),
);

/** A dotted path off a response body. `read(body, 'user.email_confirmed_at')`. */
const read = (body, path) =>
  path.split(".").reduce((node, key) => (node == null ? undefined : node[key]), body);

/** One declared field, as a `Reported<T>`, with the entry's own reason on the absence. */
const reported = (body, field) => fromNullable(read(body, field.path), field.reason);

/** A per-plan field read off one element of `plans[]`, whose declared path carries a `[]`. */
const reportedItem = (item, field) =>
  fromNullable(read(item, field.path.split("[].")[1]), field.reason);

/** The marker, carrying the entry's reason. One marker in the app. */
const Marker = ({ field, reason }) => (
  <NotAvailableMarker label={field.label} reason={reason ?? field.reason} />
);

// ─────────────────────────────────────────────────────────────────────────────
// The four steps, and the one request the demo runs
// ─────────────────────────────────────────────────────────────────────────────

/** The four steps, in order. Unchanged — the same four strings the page has always had. */
const STEPS = Object.freeze(["Secure Account", "Demo Backtest", "Connect Exchange", "Choose Plan"]);

/** The six exchanges the connect step offers. Unchanged. */
const EXCHANGES = Object.freeze(["Binance", "Bybit", "OKX", "Kraken", "Coinbase", "KuCoin"]);

/**
 * The demo backtest's payload, byte-identical to the one this page has always posted.
 *
 * Frozen at module scope so the reader below closes over a constant: `usePanelState` may
 * re-issue its reader, and a payload rebuilt per render would make two runs of "the same"
 * demo two different requests. Requirement 16.7 — no path, parameter or body changes.
 */
const DEMO_BACKTEST = Object.freeze({
  strategies: ["macd"],
  symbols: ["BTCUSDT"],
  timeframe: "15m",
  initial_capital: 10000,
  trade_size_pct: 0.1,
  stop_loss_pct: 0.02,
  take_profit_pct: 0.04,
});

/**
 * Step 1's three rows: what each one names, what it explains, and where its command goes.
 *
 * `field` is the `pageFields` key, so the row's NAME and — where it has one — its
 * not-available sentence are read from the declaration rather than retyped. `on` and `off`
 * are the badge's words for a state that really was read; a row whose state could not be
 * read renders the marker instead of either of them.
 */
const SECURITY_ROWS = Object.freeze([
  Object.freeze({
    field: "mfaEnabled",
    icon: ShieldCheck,
    description: "TOTP via Google Authenticator",
    on: "Enabled",
    off: "Not enabled",
    action: "Enable",
    to: "/app/2fa",
  }),
  Object.freeze({
    field: "emailVerified",
    icon: Mail,
    description: null, // the trader's own address, resolved below
    on: "Verified",
    off: "Not verified",
    action: "Verify",
    to: "/app/profile",
  }),
  Object.freeze({
    field: "securityAlertsEnabled",
    icon: Bell,
    description: "Notify on new device logins",
    on: null, // declared UNAVAILABLE: neither arm is reachable
    off: null,
    action: "Manage",
    to: "/app/security-logs",
  }),
]);

/**
 * One plan's price, as a `Reported<string>` ready to render.
 *
 * Three facts are kept apart deliberately. A price of `0` is the free tier and IS a
 * reading, so it renders the word the page has always used for it. A figure with no
 * currency at all is not a price, so it renders the marker rather than a bare number a
 * trader could read in the wrong currency. And an unreadable `localized_price` renders the
 * declared reason — never the `0` that used to stand in for it.
 */
const planPrice = (plan) => {
  const figure = reportedItem(plan, FIELD.planPrice);
  if (!figure.available) return figure;

  const amount = Number(figure.value);
  if (!Number.isFinite(amount)) return unavailable(FIELD.planPrice.reason);
  if (amount === 0) return fromNullable("Free", FIELD.planPrice.reason);

  const symbol = read(plan, "currency_symbol");
  if (typeof symbol === "string" && symbol.trim() !== "") {
    return fromNullable(`${symbol.trim()}${amount}`, FIELD.planPrice.reason);
  }
  // No glyph was sent, so the ISO code stands in for it — still the server's own answer.
  const code = read(plan, "currency");
  return typeof code === "string" && code.trim() !== ""
    ? fromNullable(`${amount} ${code.trim()}`, FIELD.planPrice.reason)
    : unavailable(FIELD.planPrice.reason);
};

export default function Wizard() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [isCheckoutLoading, setIsCheckoutLoading] = useState("");
  // Whether the trader has asked for the demo run. `usePanelState`'s `enabled` gate, so
  // no request is issued before the button is pressed and the panel is `idle` until it is.
  const [backtestRequested, setBacktestRequested] = useState(false);

  // ── The three reads (Requirement 4.1) ─────────────────────────────────────

  /**
   * Step 1's read. Two supabase calls, one panel.
   *
   * `user` and `aal` are returned as they arrived rather than flattened into booleans,
   * because the booleans are the FIGURES and `pageFields` declares how each is derived. A
   * visitor with no session gets `{user: null, aal: null}` — a successful read of nothing,
   * which is what makes the three rows render their reasons instead of three `false`s.
   */
  const readSecurity = useCallback(async () => {
    const { data: userData, error: userError } = await supabase.auth.getUser();
    if (userError) throw userError;
    const user = userData?.user ?? null;
    if (!user) return { user: null, aal: null };

    const { data: aal, error: aalError } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel();
    if (aalError) throw aalError;
    return { user, aal: aal ?? null };
  }, []);

  const readPlans = useCallback(() => api.billing.getPlans(), []);
  const runBacktest = useCallback(() => api.strategies.backtest(DEMO_BACKTEST), []);

  const security = usePanelState(readSecurity, { deps: [] });
  const plans = usePanelState(readPlans, { deps: [] });
  const backtest = usePanelState(runBacktest, { deps: [], enabled: backtestRequested });

  // The envelope both shapes these responses arrive in: `res.data ?? res`. Kept, because
  // the shape a deployment answers with is not this commit's question.
  const securityBody = security.data?.data ?? security.data;
  const backtestBody = backtest.data?.data ?? backtest.data;

  // ── Step 1's three figures, each resolved once against its declaration ────

  const securityState = useMemo(() => {
    const user = read(securityBody, "user") ?? null;
    const aal = read(securityBody, "aal") ?? null;
    return {
      // The PRESENCE of the confirmation timestamp, not the timestamp: a null
      // `email_confirmed_at` is a genuine "not verified" and renders as one.
      emailVerified: fromNullable(
        user ? Boolean(read(user, "email_confirmed_at")) : null,
        FIELD.emailVerified.reason,
      ),
      // The page's own existing expression, carried across unchanged.
      mfaEnabled: fromNullable(
        aal ? read(aal, "currentLevel") === "aal2" || read(aal, "nextLevel") === "aal2" : null,
        FIELD.mfaEnabled.reason,
      ),
      // Declared UNAVAILABLE: nothing reports a notification preference, so there is no
      // arm but this one. The row and its command stay; the tick does not.
      securityAlertsEnabled: unavailable(FIELD.securityAlertsEnabled.reason),
      emailAddress: user ? read(user, "email") : null,
    };
  }, [securityBody]);

  // ── Step 4's cards ───────────────────────────────────────────────────────

  const planRows = useMemo(() => {
    const body = plans.data?.data ?? plans.data;
    const raw = Array.isArray(body?.plans) ? body.plans : (Array.isArray(body) ? body : []);
    return raw.map((plan, index) => ({
      // The id IS the tier the checkout is created for, so it is never substituted: a plan
      // that arrived without one cannot be selected, and the button says why.
      id: typeof plan?.id === "string" && plan.id.trim() !== "" ? plan.id : null,
      key: typeof plan?.id === "string" && plan.id.trim() !== "" ? plan.id : `plan-${index}`,
      recommended: plan?.recommended === true,
      name: reportedItem(plan, FIELD.planName),
      price: planPrice(plan),
      // A LIST, so not a `Reported` — `reported.js` refuses arrays by design. Four missing
      // feature lines are visible as four missing feature lines.
      features: Array.isArray(plan?.features) ? plan.features.slice(0, 4) : [],
    }));
  }, [plans.data]);

  /*
   * A 200 carrying zero plans is `empty`, not `ready`.
   *
   * `isEmptyPayload`'s collection allowlist does not include `plans`, and widening a shared
   * heuristic from a page commit is how it stops being an allowlist — so the page names
   * which of the eight states this panel is in, which is a choice among the declared eight
   * and not a ninth. This is `SecurityLogs.jsx`'s `summaryPanelState` move inverted.
   */
  const plansState =
    plans.state === PANEL_STATES.READY && planRows.length === 0
      ? PANEL_STATES.EMPTY
      : plans.state;

  // ── The write path. Carried across unchanged — see the header ────────────
  const handleSelectPlan = async (planId) => {
    if (planId === "free") {
      navigate("/app/dashboard");
      return;
    }
    setIsCheckoutLoading(planId);
    try {
      const data = await api.billing.createCheckout({ tier: planId, currency: "INR" });
      if (data && data.checkoutUrl) {
        window.location.href = data.checkoutUrl;
      } else {
        navigate("/app/dashboard");
      }
    } catch (err) {
      console.error("Wizard checkout error:", err);
      navigate("/app/dashboard");
    } finally {
      setIsCheckoutLoading("");
    }
  };

  /** The demo has been attempted and settled, either way. What gates the Next button. */
  const backtestSettled =
    backtestRequested
    && backtest.state !== PANEL_STATES.IDLE
    && backtest.state !== PANEL_STATES.LOADING;

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-surface-canvas p-8">
      {/* ── Progress ──────────────────────────────────────────────────────── */}
      <ol className="mb-9 flex w-full items-center" style={{ maxWidth: 560 }}>
        {STEPS.map((label, index) => {
          const done = index < step;
          const current = index === step;
          return (
            <li key={label} className="flex flex-1 items-center">
              <div className="flex flex-col items-center">
                {/* fontSize: 11 → --text-micro (chip — Q5: a filled pill carrying one of
                    three states, holding `1` or `✓`. It does not name the step; the label
                    below does, which is why Q4 misses it). `#000` on the brand fill →
                    `text-content-inverse`; the current step's wash → `bg-brand-wash`. */}
                <span
                  aria-hidden="true"
                  className={`flex h-8 w-8 items-center justify-center rounded-full border-2 font-mono text-micro font-bold ${
                    done
                      ? "border-brand bg-brand text-content-inverse"
                      : current
                        ? "border-brand bg-brand-wash text-brand"
                        : "border-line-default bg-surface-inset text-content-secondary"
                  }`}
                >
                  {done ? "✓" : index + 1}
                </span>
                {/* fontSize: 9 → --text-micro (label — Q4: it names the step the ordinal
                    above only counts). `whiteSpace: nowrap` is dropped: §2.5's first and
                    cheapest layout yield, so the +1px has somewhere to go. */}
                <span
                  aria-current={current ? "step" : undefined}
                  className={`mt-1 text-center font-mono text-micro ${
                    current ? "text-content-primary" : "text-content-secondary"
                  }`}
                >
                  {label}
                </span>
              </div>
              {index < STEPS.length - 1 ? (
                <span
                  aria-hidden="true"
                  className={`mx-1 mb-4 h-0.5 flex-1 ${done ? "bg-brand" : "bg-line-default"}`}
                />
              ) : null}
            </li>
          );
        })}
      </ol>

      <div
        className="w-full rounded-xl border border-line-default bg-surface-raised p-8"
        style={{ maxWidth: step === 3 ? 720 : 480 }}
      >
        {/* ══ Step 1 — Secure Account ═══════════════════════════════════════ */}
        {step === 0 && (
          <div>
            {/* fontSize: 18 → --text-section (heading — Q8, it names the step region).
                `ds/SectionHeader` is not used for this pair; see the header's finding. */}
            <h2 className="mb-1 text-section font-semibold text-content-primary">
              Secure Your Account
            </h2>
            {/* fontSize: 11 → --text-body (sentence — Q1, and Requirement 2.3's floor). */}
            <p className="mb-5 text-body text-content-secondary">
              These settings protect your funds. Please complete all steps.
            </p>

            <Panel
              state={security.state}
              loading={{ kind: "skeleton-cards", rows: 3, label: "Reading your security settings" }}
              empty={{
                headline: "No security settings to show",
                body: "Your account\u2019s protections could not be listed. Open your profile to "
                  + "review them directly.",
                action: { label: "Open profile", to: "/app/profile" },
              }}
              error={{ error: security.error, onRetry: security.refetch }}
              unavailable={{ reason: "Your security settings cannot be read right now." }}
              unauthorised={{ error: security.error }}
              className="mb-4 border-none bg-transparent shadow-none"
            >
              {SECURITY_ROWS.map((row) => {
                const field = FIELD[row.field];
                const state = securityState[row.field];
                const satisfied = state.available && state.value === true;
                const description =
                  row.description
                  ?? (securityState.emailAddress
                    ? `Confirmation sent to ${securityState.emailAddress}`
                    : "Confirm your email address");
                return (
                  <div
                    key={row.field}
                    className={`mb-2 flex items-center gap-2.5 rounded-lg border bg-surface-inset p-3.5 ${
                      // `rgba(0,255,136,0.15)` → `border-status-live`. `#00FF88` is the
                      // retired green; `statusToken('active')` is the `live` group, which
                      // is the name for a protection that is ON (task 7.2's argument for
                      // an armed guard, same hue, same reasoning).
                      satisfied ? "border-status-live" : "border-line-default"
                    }`}
                  >
                    <row.icon
                      size={15}
                      aria-hidden="true"
                      className={satisfied ? "shrink-0 text-status-live" : "shrink-0 text-content-secondary"}
                    />
                    <div className="min-w-0 flex-1">
                      {/* fontSize: 12 → --text-title (heading — Q8: it names a region
                          holding an icon, an explanation and a control, and Q4 misses it
                          because a control is not a value). Read from the declaration. */}
                      <div className="text-title font-semibold text-content-primary">
                        {field.label}
                      </div>
                      {/* fontSize: 10 → --text-body (sentence — Q1). +30%, on the one line
                          that says what the setting does before a trader acts on it. */}
                      <div className="text-body text-content-secondary">{description}</div>
                    </div>
                    {state.available ? (
                      <StatusBadge
                        state={state.value === true ? "active" : "pending"}
                        label={state.value === true ? row.on : row.off}
                        dot
                        className="shrink-0"
                      />
                    ) : (
                      <Marker field={field} reason={state.reason} />
                    )}
                    {/* The command stays on every row a trader still has something to do
                        on — including a row whose state could not be read, which is
                        exactly when they need the page it opens. */}
                    {satisfied ? (
                      <CheckCircle size={15} aria-hidden="true" className="shrink-0 text-status-live" />
                    ) : (
                      <CommandButton intent="secondary" onClick={() => navigate(row.to)}>
                        {row.action}
                      </CommandButton>
                    )}
                  </div>
                );
              })}
            </Panel>

            <CommandButton
              intent="primary"
              onClick={() => setStep(1)}
              className="mt-4 w-full justify-center"
            >
              Continue →
            </CommandButton>
          </div>
        )}

        {/* ══ Step 2 — Demo Backtest ════════════════════════════════════════ */}
        {step === 1 && (
          <div>
            {/* fontSize: 18 → --text-section (heading — Q8). */}
            <h2 className="mb-1 text-section font-semibold text-content-primary">
              Run Your First Backtest
            </h2>
            {/* fontSize: 11 → --text-body (sentence — Q1). */}
            <p className="mb-5 text-body text-content-secondary">
              See how a simple MACD crossover strategy would have performed on BTC/USDT over the
              last 30 days.
            </p>

            {backtest.state === PANEL_STATES.IDLE ? (
              <div className="mb-6 flex flex-col items-center justify-center rounded-lg border border-line-default bg-surface-panel p-6">
                <Database size={40} aria-hidden="true" className="mb-4 text-brand opacity-80" />
                <CommandButton intent="primary" onClick={() => setBacktestRequested(true)}>
                  Run Real Backtest
                </CommandButton>
              </div>
            ) : (
              <Panel
                state={backtest.state}
                /* `Processing historical ticks...` is `ds/LoadingState`'s sentence now —
                   the primitive owns both the step and the live region. */
                loading={{
                  kind: "skeleton-metric",
                  rows: 1,
                  columns: 2,
                  label: "Processing historical ticks",
                }}
                empty={{
                  headline: "This run reported no results",
                  body: "The demo finished without publishing any metrics. Nothing in your "
                    + "account is affected — the demo places no orders.",
                  action: { label: "Run it again", onClick: backtest.refetch },
                }}
                error={{ error: backtest.error, onRetry: backtest.refetch }}
                unavailable={{ reason: "The demo backtest cannot be run right now." }}
                unauthorised={{ error: backtest.error }}
                className="mb-6"
              >
                <div className="flex flex-col items-center">
                  <CheckCircle size={32} aria-hidden="true" className="mb-3 text-status-live" />
                  {/* fontSize: 9 → --text-micro (label) and fontSize: 16 → the tier-2
                      figure step, both set by `ds/Metric` rather than by this page. The
                      two figures are the RUN'S OWN — see the header on the literals that
                      used to stand here. */}
                  <div className="mb-4 grid w-full grid-cols-1 gap-3 sm:grid-cols-2">
                    <Metric
                      label={FIELD.backtestTotalReturnPct.label}
                      value={reported(backtestBody, FIELD.backtestTotalReturnPct)}
                      format="percent"
                      precision={1}
                      tier={2}
                      hint={FIELD.backtestTotalReturnPct.tooltip}
                      className="rounded-lg border border-line-default bg-surface-panel p-3 text-center"
                    />
                    <Metric
                      label={FIELD.backtestWinRatePct.label}
                      value={reported(backtestBody, FIELD.backtestWinRatePct)}
                      format="percent"
                      precision={1}
                      tier={2}
                      hint={FIELD.backtestWinRatePct.tooltip}
                      className="rounded-lg border border-line-default bg-surface-panel p-3 text-center"
                    />
                  </div>
                  {/* fontSize: 11 → --text-body (sentence — Q1). */}
                  <span className="text-body font-semibold text-content-primary">
                    Backtest Complete!
                  </span>
                </div>
              </Panel>
            )}

            <CommandButton
              intent="primary"
              onClick={() => setStep(2)}
              disabled={!backtestSettled}
              disabledReason="Run the demo backtest first. It takes a few seconds and places no orders."
              loading={backtest.state === PANEL_STATES.LOADING}
              loadingLabel="Processing historical ticks"
              className="w-full justify-center"
            >
              Next: Connect Exchange →
            </CommandButton>
          </div>
        )}

        {/* ══ Step 3 — Connect Exchange ═════════════════════════════════════ */}
        {step === 2 && (
          <div>
            {/* fontSize: 18 → --text-section (heading — Q8). */}
            <h2 className="mb-1 text-section font-semibold text-content-primary">
              Connect Your Exchange
            </h2>
            {/* fontSize: 11 → --text-body (sentence — Q1). */}
            <p className="mb-5 text-body text-content-secondary">
              Add API credentials to deploy bots. Keys are encrypted at rest.
            </p>
            {/* fontSize: 10 → `ds/CommandButton`'s own step; the declaration is removed
                rather than mapped, because the step belongs to the primitive. All six
                exchanges keep their button, their name and their tab stop. */}
            <div className="mb-4 grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {EXCHANGES.map((exchange) => (
                <CommandButton key={exchange} intent="secondary" className="justify-center">
                  {exchange}
                </CommandButton>
              ))}
            </div>
            <div className="mb-4 flex flex-col gap-3">
              <Field id="wizard-api-key" label="API Key" placeholder="Paste your API key..." type="password" />
              <Field
                id="wizard-secret-key"
                label="Secret Key"
                placeholder="Paste your secret key..."
                type="password"
              />
            </div>
            <div className="flex gap-2">
              <CommandButton intent="secondary" icon={Wifi}>
                Test Connection
              </CommandButton>
              <CommandButton
                intent="primary"
                onClick={() => setStep(3)}
                className="flex-1 justify-center"
              >
                Next: Choose Plan →
              </CommandButton>
            </div>
          </div>
        )}

        {/* ══ Step 4 — Choose Plan ══════════════════════════════════════════ */}
        {step === 3 && (
          <div>
            {/* fontSize: 18 → --text-section (heading — Q8). */}
            <h2 className="mb-1 text-center text-section font-semibold text-content-primary">
              Choose Your Plan
            </h2>
            {/* fontSize: 11 → --text-body (sentence — Q1). */}
            <p className="mb-6 text-center text-body text-content-secondary">
              Select a plan to unlock powerful features.
            </p>

            <Panel
              state={plansState}
              loading={{ kind: "skeleton-cards", rows: 4, label: "Reading the available plans" }}
              empty={{
                headline: "No plans are available right now",
                body: "You can start using the platform on the free tier and choose a plan later "
                  + "from Billing.",
                action: { label: "Go to the dashboard", to: "/app/dashboard" },
              }}
              error={{ error: plans.error, onRetry: plans.refetch }}
              unavailable={{ reason: "The plan list cannot be read right now." }}
              unauthorised={{ error: plans.error }}
              className="border-none bg-transparent shadow-none"
            >
              {/* §2.5's third yield: the price grew 4px onto `--text-figure`, so the fixed
                  `repeat(4,1fr)` becomes 1 / 2 / 4 by breakpoint using the responsive
                  machinery rather than a new mechanism. */}
              <div className="grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4">
                {planRows.map((plan) => (
                  <div
                    key={plan.key}
                    className={`relative flex flex-col rounded-xl border p-5 ${
                      // `rgba(0,212,255,0.05)` → the declared `bg-brand-wash`, which is the
                      // same wash the current progress step reads, spelled once.
                      plan.recommended
                        ? "border-brand bg-brand-wash"
                        : "border-line-default bg-surface-inset"
                    }`}
                  >
                    {plan.recommended ? (
                      /* fontSize: 8 → --text-micro (chip — Q5: one word, a pill, carrying
                         a state). +25%, off the floor. `#000` → `text-content-inverse`. */
                      <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 rounded-full bg-brand px-2.5 py-0.5 text-micro font-bold uppercase tracking-wide text-content-inverse">
                        Recommended
                      </span>
                    ) : null}
                    {/* fontSize: 14 → --text-title (heading — Q8, it names the card). Read
                        from the declaration, and rendered through `reported.js` so a plan
                        that arrived without a name says so rather than showing nothing. */}
                    {plan.name.available ? (
                      <div className="text-title font-semibold text-content-primary">
                        {plan.name.value}
                      </div>
                    ) : (
                      <Marker field={FIELD.planName} reason={plan.name.reason} />
                    )}
                    {/* fontSize: 24 → `ds/Metric` tier 1 (`--text-figure`). The card's hero
                        figure, and the one the page used to invent — see the header. */}
                    <Metric
                      label={FIELD.planPrice.label}
                      value={plan.price}
                      format="raw"
                      tier={1}
                      className="my-2.5"
                    />
                    {/* fontSize: 10 → --text-body (sentence — Q1). +30% on the lines that
                        say what the plan includes. */}
                    <ul className="mb-3.5 flex flex-1 flex-col gap-1.5">
                      {plan.features.map((feature) => (
                        <li key={feature} className="flex items-center gap-1.5 text-body text-content-secondary">
                          <CheckCircle
                            size={11}
                            aria-hidden="true"
                            className={plan.recommended ? "shrink-0 text-brand" : "shrink-0 text-status-live"}
                          />
                          {feature}
                        </li>
                      ))}
                    </ul>
                    <CommandButton
                      intent={plan.id === "free" ? "secondary" : "primary"}
                      onClick={() => handleSelectPlan(plan.id)}
                      loading={isCheckoutLoading === plan.id}
                      loadingLabel="Opening checkout..."
                      disabled={plan.id === null}
                      disabledReason="This plan could not be identified, so it cannot be selected here. Any plan can be chosen from Billing."
                      className="w-full justify-center"
                    >
                      {plan.id === "free" ? "Start Free" : "Select →"}
                    </CommandButton>
                  </div>
                ))}
              </div>
            </Panel>
          </div>
        )}
      </div>
    </div>
  );
}
