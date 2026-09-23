/**
 * pages/Billing.jsx — the subscription: the account's plan, its entitlement allowances, the
 * priced catalogue, the stored payment method and the invoice history.
 *
 * retail-ui-simplification task 7.9 (commit 15). Requirements 3.1, 3.2, 4.1, 4.2, 4.3, 4.4,
 * 5.1, 5.2, 6.2, 16.1, 19.1, 19.2, 19.3, 19.5, 22.1, 22.2. design.md §2.4 (the nine-question
 * procedure), §2.5, §6 (the zero-versus-unavailable hazard).
 *
 * **39 absolute pixel sizes to 0**, **9 colour literals to 0**, and 28 inline `monospace`
 * declarations resolved against Requirement 3.1. Both budgets are lowered in this commit and
 * the font-size entry is DELETED (Requirement 1.5).
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THIS IS A MONEY SURFACE, AND IT SUBSTITUTED ZEROS
 * ═══════════════════════════════════════════════════════════════════════════
 * design.md §6 names this page as the one where collapsing "a figure that is genuinely zero"
 * into "a figure nobody could read" would be *most plausible and most expensive*. It had
 * already happened, ten times over. Every one is now declared in `design/pageFields.js` under
 * a `billing` page — 27 entries — with its real source path or its explicit unavailability,
 * and renders through `design/reported.js`. The full account is there; the short version:
 *
 *   1. **`currentPlan?.name || "Free"`.** A paid account whose entitlements read failed was
 *      told it was on the free plan. This is the same substitution `pages/Profile.jsx` carried
 *      as `'Free Tier'` — and Profile's *Manage subscription* control sends the trader HERE to
 *      check it, so the claim was made twice by two pages reading two different endpoints.
 *   2. **`subscription_status` defaulted to `"active"` three times over** — `useState("active")`,
 *      `data.subscription_status || "active"`, and `…?.toUpperCase() || "ACTIVE"` at the chip.
 *      A failed read rendered a green *ACTIVE* pill.
 *   3. **And then took the controls away.** `isFreePlan` was `currentPlan?.id === "free" ||
 *      !currentPlan?.id`, so an unreadable plan WAS the free plan: no *Cancel*, no *Resume*,
 *      no *Manage billing*. `isPaymentFailed` came off the fabricated `"active"`, so an
 *      account that really was `past_due` got no payment-failure banner either. The panel is
 *      `ds/Panel`'s `error` arm with a retry now, which is the state that says so.
 *   4. **`Cancels at period end`** — a claim about when access ends, rendered when
 *      `renewal_date` was null. `fmtDate` answered the literal `"N/A"`, a marker with no
 *      reason, which is the half Requirement 19.3 calls load-bearing.
 *   5. **Eight `|| 0`s over `usage.*` and `quotas.*`.** The free plan's real quotas for live
 *      bots, ML trainings and marketplace publishing are all literally `0`
 *      (`subscription_engine.py:86`–`:90`), so the fabricated zero and the genuine zero were
 *      the same glyph on the same tile for the same trader. That is §6's hazard on the field
 *      where it is hardest to see.
 *   6. **An unreadable price advertised itself as free.** `formatPlanPrice` returned
 *      `` `${currencySymbol}0` `` for a missing plan and substituted `0` for a missing
 *      `localized_price`. `pages/Wizard.jsx` had the identical defect through `p.inr`/`p.usd`
 *      and task 7.6 removed it — on the same catalogue, three commits ago.
 *   7. **`decimals` was recomputed here** as `currency === "JPY" || currency === "KRW" ? 0 : 2`
 *      when `plans[].decimals` is a real key on the same object. Requirement 16.4 forbids
 *      deriving client-side what the server reports, and this derivation rounded every other
 *      zero-decimal currency to two places.
 *   8. **A hardcoded `$` in front of a non-dollar amount.** `` `Billed as $${p.checkout_price
 *      || p.base_price} ${p.checkout_currency}` `` rendered `$2499 INR`.
 *      `plans[].checkout_currency_symbol` is a real key. The `|| p.base_price` arm swapped in
 *      a figure that is USD by declaration (`pricing_service.py:186`) for a genuine `0`.
 *   9. **Every non-INR invoice carried a dollar sign.** `inv.currency === "INR" ?
 *      ₹${amtINR || 0} : $${amtUSD || 0}` — the else arm was not "USD", it was *everything
 *      else*, and both arms substituted `0`, so an unreadable amount rendered as a settled
 *      invoice for nothing. The amount is selected by the row's own currency now, and a
 *      currency with no matching column is an absence rather than a dollar figure.
 *  10. **A geolocation nobody performed.** The pricing context defaulted to `"USD"` / `"$"` /
 *      `"US"` / `"United States"` / `"ip"`, so a failed catalogue read rendered
 *      *United States (USD)* under an *Auto-detected* chip.
 *
 * A genuine `0` is a reading and still renders as one everywhere: a ₹0 invoice line, a free
 * tier priced at zero, a plan that includes no live bots, a quota nothing has consumed
 * (Requirement 19.1). What is gone is the substitution that made each of those
 * indistinguishable from a read that failed.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * ONE `Promise.allSettled` OUT, FOUR PANEL STATES IN (Requirement 4.1)
 * ═══════════════════════════════════════════════════════════════════════════
 * What was here: `isLoadingBilling`, `isLoadingPlans` and a `billingError` string, over one
 * `Promise.allSettled` of three reads guarded by three `if (res.status === 'fulfilled')`
 * blocks — **and no `else` on any of them.** `billingError` was set from the `try`'s `catch`,
 * which `allSettled` makes unreachable, so a 500 on invoices left `billingHistory` at `[]`
 * and the table rendered *No invoices found.* A failed read and an account with no invoices
 * produced the same screen, which is precisely the `empty`/`error` collapse Requirement 19.4
 * and Property 9 exist to forbid. `loadPlans`'s own failure path was one `console.error`, so
 * a failed catalogue read rendered **"Loading plans…" forever**.
 *
 * Each read now drives its own `usePanelState`, so each panel says what happened to ITS read:
 *
 *   plans        → the priced catalogue, and the pricing-context row above it
 *   entitlements → the current-plan panel: status, period, and the four allowance tiles
 *   invoices     → the billing-history table
 *   methods      → the stored payment method
 *
 * Same four paths, same absence of parameters, once each on mount. `api-paths.budget.js` is
 * untouched and no request, route or query parameter changed — including the one that is easy
 * to change by accident: **the mount still calls `api.billing.getPlans()` with no argument**,
 * so it is still `GET /api/billing/plans` and not `?currency=USD`. That is why the display
 * currency starts as *nothing* rather than as `"USD"`: the literal default was both a
 * fabricated reading (item 10) and, the moment it reached the reader, a new query parameter.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE SOCKET IS UNTOUCHED (production-launch-hardening tasks 8.1, 8.2, 8.3)
 * ═══════════════════════════════════════════════════════════════════════════
 * The subscription effect below is carried across verbatim: `isAuthenticated()` for whether a
 * session exists, `GET /api/auth/me` for whose it is, `wsClient.acquire('/ws/user/<id>')` plus
 * `subscribeChannel('billing', …)` for the hold, and the same five frame kinds
 * (`subscription_update`, `plan_changed`, `subscription_cancelled`, `cancellation_reversed`,
 * `payment_failed`) refreshing the same two reads. `tests/unit/pages/billingSocketLifecycle.test.jsx`
 * and `tests/unit/lib/socketCredential.test.js` pass unchanged.
 *
 * Two things about it that this commit deliberately did NOT tidy:
 *
 *   * **`/ws/user/{user_id}` requires an `exchange_id` query parameter it never receives.**
 *     Known, open, and a backend concern — Requirement 16.7 puts `backend_app/` out of scope
 *     for this spec, so it is recorded here rather than worked around on the client.
 *   * **`currencyRef` is gone, and the frame handler still re-reads the catalogue in the
 *     currency the page is showing.** The ref moved rather than disappearing: `plansCurrency`
 *     below is the same value at the same lifetime, read by the plans reader itself instead of
 *     being passed as an argument, because `usePanelState`'s `refetch` takes none. A billing
 *     frame therefore still issues `getPlans('<the current currency>')`, which is the detail
 *     that suite pins with `toHaveBeenLastCalledWith`.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 28 INLINE `monospace` DECLARATIONS (Requirements 3.1, 3.2)
 * ═══════════════════════════════════════════════════════════════════════════
 * Requirement 3.2 asks for the value-versus-prose classification to be recorded rather than
 * just applied. It is, in full — 28 sites, by line number in the pre-migration file. The
 * headline is that **the rule was applied backwards here**: 23 of the 28 sat on prose,
 * commands and labels, while the largest money figure on the page — the 36px plan price —
 * carried none at all.
 *
 * **5 are a VALUE or an IDENTIFIER and keep the treatment** (Requirement 3.1's first clause —
 * a figure or an identifier is read character by character, and a proportional face makes
 * `l`/`1` and `0`/`O` ambiguous exactly where that matters):
 *
 *   :536  the currency trigger, `{symbol} {code}`   an ISO 4217 code
 *   :584  each currency row, `{code} ({symbol})`    an ISO 4217 code
 *   :641  the quota denominator, `/ {limit}`        a figure
 *   :812  the invoice `<table>`                     dates, money and references
 *   :823  the invoice reference cell                an identifier
 *
 * Two of those five keep it *through a primitive*: `ds/DataTable` owns the table's treatment
 * and `ds/Metric` renders every numeric format as `font-mono tabular-nums`. The other three
 * keep an explicit `font-mono` class.
 *
 * **23 are PROSE, a COMMAND or a LABEL and lose it** (Requirement 3.1's second clause):
 *
 *    7 commands — *Update Payment Method* (:448), *Manage Billing* (:612), *Resume
 *      Subscription* (:658), *Cancel Subscription* (:668), *Refresh* (:677),
 *      *Subscribe* / *Current Plan* (:767), *Manage via Stripe Portal* (:803). All seven are
 *      `ds/CommandButton`s now
 *    6 sentences — *Updating pricing…* (:691), *Loading plans…* (:698), the plan description
 *      (:738), the *Billed as …* line (:746), each capability line (:755) and *No payment
 *      methods on file.* (:787)
 *    5 labels — *Renews* (:502), *Cancels* (:507), the pricing-context pill (:517), *Select
 *      Currency* (:568) and each allowance tile's name (:637)
 *    2 banner shells whose whole content is a sentence — the error strip (:422) and the
 *      success strip (:429), both `ds/Alert` now
 *    1 heading — *PAYMENT FAILED — ACTION REQUIRED* (:441), now `ds/Alert`'s `title`
 *    1 unit — `/month` (:742), which is `ds/Metric`'s `unit` and renders in
 *      `content-secondary` without it
 *    1 date — *Expires m/y* (:795). A timestamp is a value but not an identifier, and
 *      `pages/SecurityLogs.jsx` set the precedent at task 7.4: its `ipAddress` cell carries
 *      `font-mono` and its `recordedAt` cell does not
 *
 * 7 + 6 + 5 + 2 + 1 + 1 + 1 = 23, and 23 + 5 = 28, which is what the scan measures.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 39 PIXEL SIZES, BY §2.4'S NINE QUESTIONS (Requirement 2.4)
 * ═══════════════════════════════════════════════════════════════════════════
 * Distribution: 11 ×15, 12 ×10, 10 ×5, 9 ×2, 13 ×2, 18 ×1, 20 ×1, 22 ×1, 24 ×1, 36 ×1.
 * **32 of the 39 sit at or below 12px**, so most resolutions are upward — §2.5's "growth is
 * the normal case". Each resolution is recorded as a comment on its own call site in the form
 * `// fontSize: 11 → --text-body (sentence)`; the ratchet blanks comments, so the audit trail
 * costs nothing and the file measures 0. The cohorts:
 *
 *   **20 of the 39 leave with their call site**, because the step belongs to the primitive
 *   rather than to this page: the two notice strips and the payment-failure block, including
 *   its button (12, 12, 13, 12, 11) onto `ds/Alert`; the six remaining command labels
 *   (11 ×5, 12) onto `ds/CommandButton`; an allowance label, its figure, the price and the
 *   price's unit (11, 20, 36, 12) onto `ds/Metric`; the *Available Plans* heading, *No payment
 *   methods on file.* and *Loading plans…* (18, 12, 12) onto `ds/Panel`'s heading and its
 *   `empty` / `loading` arms; the `<table>` and its `<th>`s (11, 9) onto `ds/DataTable`
 *   **The other 19 resolve in place:**
 *   **5 → `--text-micro`** as a label (Q4) or a chip (Q5): *Auto-detected* (9), *Select
 *   currency* (10), each currency's human name (10), *RECOMMENDED* (10), *CURRENT* (10)
 *   **8 → `--text-body`** — Q1 sentences (the plan description 12, each capability line 12,
 *   the *Billed as* line 10, *Updating pricing…* 11) and Q7 secondary values (the
 *   pricing-context pill 11, the currency trigger 11, each currency row's code 11, the card
 *   identifier 13)
 *   **4 → a `--text-micro` label BESIDE a `--text-body` value**, which is §2.4's
 *   one-call-site-two-roles case: *Renews* (11), *Cancels* (11), the allowance denominator
 *   (12) and the card expiry (11) each sat on a container holding an eyebrow word and a figure
 *   **1 → `--text-section`** and **1 → `--text-title`** — the current plan's name (24) and
 *   each catalogue card's name (22)
 *
 * Four resolutions carry an argument rather than a lookup.
 *
 * **The three shrinks are all hierarchy, and none of them avoids a layout change.** §2.5 makes
 * a shrink legal but rare and names its illegal form — "a shrink chosen to avoid a layout
 * change, whose tell is a shrink that appears in the same diff as an overflow". These three
 * are the opposite case: each one was *outranking its own container*.
 *
 *   `24 → --text-section` (18)  the current plan's name. At 24 it equalled the page `<h1>`,
 *                               and `tokens.css:74` reserves `--text-page` for the page
 *                               heading. It is this panel's hero value, so it takes the
 *                               highest step below that.
 *   `22 → --text-title` (14)    each catalogue card's name. A card heading inside a panel may
 *                               not outrank the panel's own heading, which `ds/Panel` renders
 *                               at `--text-title`. The card's dominant element is its PRICE,
 *                               which is the relationship the old 22-over-36 pair already had
 *                               and which survives at 14-over-28.
 *   `36 → --text-figure` (28)   the price. 36 is not one of the seven steps and §2.2 forbids
 *                               inventing an eighth; `tokens.css:75` reserves `--text-figure`
 *                               for a tier-1 figure, which this is, and 28 is the largest the
 *                               declaration holds.
 *
 * **The 18px *Available Plans* heading also shrinks, to `--text-title`, and that is a
 * consequence of adopting `ds/Panel` rather than a mapping.** The three sibling regions on
 * this page are panels, `tokens.css:72` gives a panel heading `--text-title`, and one of three
 * headings set at `--text-section` would read as a section containing the other two.
 *
 * **The `Renews` / `Cancels` line is §2.4's multi-role case, so its one declaration resolves
 * to two steps.** `fontSize: 11` sat on a `<span>` holding an eyebrow word and a date. Q4
 * sends the word to `--text-micro` and Q7 sends the date to `--text-body`, so the container
 * loses its declaration and each child takes its own step — the same resolution §2.4 records
 * for `PaperTrading.jsx:3367`.
 *
 * **One declaration is removed rather than resolved.** *Loading plans…* (12) was the page's
 * entire loading AND failure state for the catalogue, and `ds/Panel`'s `loading` arm renders a
 * skeleton shaped like the content it replaces while its `error` arm renders a retry. A
 * sentence that meant two different things does not survive as either.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE 9 COLOUR LITERALS (Requirements 5.1, 5.2, 5.3)
 * ═══════════════════════════════════════════════════════════════════════════
 *   #f59e0b          the 70%-of-quota usage bar. **It is `--color-status-warning` exactly** —
 *                    the same six digits the token layer declares — so this is a token spelled
 *                    as a number, and `bg-status-warning` is the value it already was.
 *                    `getUsageColor`'s whole three-branch body goes with it: the hue now comes
 *                    from the same `statusToken` groups `ds/RiskIndicator` reads.
 *   #00000088        the currency dropdown's shadow → `token.shadow.overlay`, which is the
 *                    declared elevation for a floating layer. The literal was a 53% black at
 *                    a depth the token layer does not declare at all.
 *   #000 ×3          the *RECOMMENDED* and *CURRENT* ribbons and the *Subscribe* button, all
 *                    three text-on-brand → `token.content.inverse` (#080A0E), which is the
 *                    declared inverse and is not pure black.
 *   #fff ×2          the payment-failure button's label and the card glyph →
 *                    `token.content.primary` (#F0F2F5). Pure white is not in the palette;
 *                    task 7.3 made the same substitution on `TwoFA.jsx`'s knob.
 *   #1a1f71/#003087  **a Visa-blue gradient, on every card brand.** Two literals belonging to
 *                    another company's identity, painted behind a generic `CreditCard` glyph
 *                    whatever the stored method actually was — so a Mastercard rendered in
 *                    Visa's colours. Removed rather than retokened: the tile is
 *                    `surface-inset` with the glyph in `content-secondary`, and the BRAND is
 *                    stated in words beside it, which is the one channel that cannot be wrong.
 *
 * **Both of this page's two gradients go** (requirements §1.10 counts them: "Billing 2"). The
 * second was `linear-gradient(135deg, surface.raised, surface.panel)` on the current-plan
 * card — a hand-built elevation between two declared surfaces, which is what `ds/Panel` is.
 * Requirement 10.1's rule is that a gradient on a trading surface needs a declared token or it
 * does not belong; neither of these has one.
 *
 * **Fourteen off-palette constructs neither guard can see go with them**, recorded because a
 * scan will not find them for the next reader: `${token.status.loss.fg}12`, `…44`, `…10`,
 * `…55`, `…35`, `${token.status.profit.fg}12`, `…44`, `…20`, `…50`, `${token.brand.base}15`,
 * `…40`, `…20`, `…50`, `${token.line.default}20`. Each is a token with two hex digits
 * concatenated onto it — a hand-mixed alpha wearing a token's name, carrying no `#`, so
 * `no-colour-literals` never counted one of them. `token.brand.wash` and the
 * `status.*.wash` values are the declared washes and are what they become, or they leave with
 * the element that was painting them.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * REQUIREMENT 4.4 FINDINGS — RECORDED, NOT RESOLVED
 * ═══════════════════════════════════════════════════════════════════════════
 * Nothing is added to `ds/`, to `usePanelState` or to `src/design/` beyond this page's own
 * `pageFields.js` entries. Five gaps were reached and each is a finding rather than a change
 * to a shared module.
 *
 *   1. **`design/semantic.js`'s vocabulary has no billing statuses.** `active` resolves to the
 *      live group and `cancelled` to neutral, which `semantic.js` declares explicitly;
 *      `past_due`, `payment_failed`, `trial`, `expired` and the invoice's own `paid` are not in
 *      it at all and fall through to neutral. `design/subscriptionState.js` is NOT the answer —
 *      it is the *marketplace* `SubscriptionState` enum (seven values, `ACTIVE`/`CANCELLED`/…),
 *      a different vocabulary about a different thing, and reading a billing status through it
 *      would be a coincidence of spelling. Requirement 5.3 puts "which token means this state"
 *      on `semantic.js` and nowhere else, so every status on this page goes to
 *      `ds/StatusBadge` verbatim and a calm chip for `past_due` or `paid` is accepted rather
 *      than a hue being decided here. **Nothing is lost but a colour:** `ds/StatusBadge` always
 *      renders the word — "colour is never the only channel" is its own header — and the
 *      distinction that matters most, a failed payment, is carried by the `ds/Alert` above the
 *      panel at `severity="error"`, which resolves through the declared layer. Extending the
 *      vocabulary, or adding a billing-side table beside `subscriptionState.js`, is a
 *      shared-module change a page commit may not make.
 *   2. **`ds/` has no disclosure or select control.** The currency picker stays a page-local
 *      `<button>` plus an absolutely-positioned list. `ds/Field`'s `select` type was
 *      considered and refused: it would replace a popover of 21 rows with a native
 *      `<select>`, which is a different control with a different keyboard model, and swapping
 *      one for the other is a behaviour change on a write path in a typography commit. It
 *      keeps its whole accessibility contract and gains three things it did not have —
 *      `aria-haspopup`, `aria-expanded` and an `aria-label` that does not depend on a read.
 *   3. **`ds/Metric` can only put a unit AFTER the figure.** So a price is denominated by its
 *      ISO code (`999.00 INR / month`) rather than by a prefixed glyph. That is
 *      `pages/Portfolio.jsx`'s established form — `unit={currency}` off `overview.currency` —
 *      and it is the reason no `$` is reintroduced anywhere on this page: there is no symbol
 *      in front of any figure to be wrong. The server's `currency_symbol` still renders, on
 *      the currency control, where it is labelled as the symbol rather than applied to a
 *      number.
 *   4. **`ds/Panel`'s `money` prop is not passed, and that is a judgement.** `MONEY_CONTENT`
 *      is positions, orders, fills, trades, balances, equity, P&L, exposure and transactions —
 *      and `money` REQUIRES an `environment`, because Requirement 12.2 exists to stop a
 *      trader mistaking paper P&L for live. A subscription invoice has no *trading*
 *      environment, and passing `null` would render *ENVIRONMENT UNCONFIRMED* over a Stripe
 *      receipt, which states something false about a real charge.
 *   5. **Cancelling a subscription still takes one click.** There is no confirmation to
 *      preserve — there never was one — and `ds/CommandButton`'s `confirm` prop plus
 *      `ds/ConfirmDialog` are sitting right there. Adding one is a change to what a billing
 *      action DOES, and Requirement 16.1 puts billing logic out of scope for this spec, so it
 *      is recorded rather than taken. It is the most valuable thing on this page that this
 *      commit is not allowed to fix.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * WHAT MUST NOT BE LOST, AND WHAT PROVES IT WAS NOT
 * ═══════════════════════════════════════════════════════════════════════════
 * Every billing action is carried across with its request unchanged (Requirement 16.1 —
 * presentation only): `api.billing.createCheckout({ tier, currency })` with the same body and
 * the same `window.location.href` redirect, `setCurrency`, `cancelSubscription`,
 * `resumeSubscription` and `openPortal` with the same `window.open(url, '_blank',
 * 'noopener,noreferrer')`. Every `detail`-string failure message is the page's own, verbatim.
 *
 * Three behaviours DID change, each because the old one lost information:
 *
 *   * A failed `setCurrency` was one `console.error` and a control that silently kept the new
 *     code while the server still held the old one. It now says so (Requirement 11.3: logging
 *     is not handling).
 *   * A failed catalogue read was one `console.error` and *Loading plans…* on screen forever.
 *     It is `ds/Panel`'s `error` arm with a retry.
 *   * A 200 carrying zero invoices and a 500 on the invoice read produced the same *No
 *     invoices found.* They are `empty` and `error` now, which is Property 9's whole subject.
 *
 * The currency control keeps its exact rendered form — the trigger reads `{symbol} {code}` and
 * each row reads `{CODE} ({symbol})` — because that is how it is operated, by a trader and by
 * `billingSocketLifecycle.test.jsx`'s section 2. The 21-entry `supportedCurrencies` fallback
 * is kept verbatim: it is a control's option set, superseded by the response's own
 * `supported_currencies`, and a control's options and a reported figure are different things.
 *
 * No key material is on this page. The only secret-shaped value is a card's last four digits,
 * which is the whole of what the server reports and the whole of what is rendered.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bot,
  CheckCircle,
  ChevronDown,
  Cpu,
  CreditCard,
  Crown,
  Database,
  ExternalLink,
  Globe,
  MapPin,
  RefreshCw,
  Shield,
  Star,
  TrendingUp,
  XCircle,
  Zap,
} from 'lucide-react';

import { Alert } from '../components/ds/Alert';
import { CommandButton } from '../components/ds/CommandButton';
import { DataTable } from '../components/ds/DataTable';
import { Metric, NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { StatusBadge } from '../components/ds/StatusBadge';
import { api, isAuthenticated } from '../api';
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from '../design/pageFields';
import { available, fromNullable, unavailable } from '../design/reported';
import { token } from '../design/tokens';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';
import wsClient from '../websocketClient';

// ─────────────────────────────────────────────────────────────────────────────
// The declaration, indexed once
// ─────────────────────────────────────────────────────────────────────────────

/**
 * This page's `pageFields.js` entries by `field`, so a call site names the figure and gets its
 * path and its reason together. The one index; see `Profile.jsx` and `SecurityLogs.jsx`.
 */
const FIELD = Object.freeze(
  Object.fromEntries(PAGE_FIELDS_BY_PAGE[PAGES.BILLING].map((f) => [f.field, f])),
);

/** A dotted path off a response body. `read(body, 'usage.strategies')`. */
const read = (body, path) =>
  path.split('.').reduce((node, key) => (node == null ? undefined : node[key]), body);

/**
 * One declared field, as a `Reported<T>`, from the response that carries it.
 *
 * The entry's own `reason` travels with the absence, which is the half Requirement 19.3 calls
 * load-bearing. A `DERIVED` or `UNAVAILABLE` entry has no path by declaration, so it never
 * reaches `read` at all — which is what stops a future `|| 0` being added to a money figure
 * the backend cannot fill.
 */
const reported = (body, field) =>
  field.verdict === VERDICT.AVAILABLE
    ? fromNullable(read(body, field.path), field.reason)
    : unavailable(field.reason);

/** The leaf of a `[].column` path, which is what one list-row element carries. */
const rowKey = (field) => field.path.split('[].')[1];

/** One declared per-row field of a list response. */
const rowField = (row, field) => fromNullable(row?.[rowKey(field)], field.reason);

/** The em-dash marker, carrying the entry's reason. One marker in the app. */
const Marker = ({ field, reason }) => (
  <NotAvailableMarker label={field.label} reason={reason ?? field.reason} />
);

/**
 * `'pro'` → `'Pro'`. The page's own existing expression, carried across unchanged apart from
 * its fabricated `|| "Free"` arm.
 *
 * It matches `SubscriptionEngine`'s own plan names for all four ids, so this is a
 * presentation of the server's answer rather than a second name for the plan.
 */
const planLabel = (plan) =>
  typeof plan === 'string' && plan.length > 0
    ? `${plan.charAt(0).toUpperCase()}${plan.slice(1)}`
    : String(plan);

/**
 * A date the server sent, through the same `toLocaleDateString` call this page has always
 * made — and `null`, never the literal `"N/A"`, for anything unparseable.
 */
const formatDate = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? null
    : parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
};

/**
 * The 21 display currencies this page falls back to when the catalogue response does not
 * carry `supported_currencies`.
 *
 * Carried across verbatim. It is a CONTROL'S OPTION SET, not a reported figure — the same
 * distinction `pages/Profile.jsx` recorded for `NotificationSettingsRequest`'s switch
 * defaults — and `FXService.get_supported_display_currencies()` supersedes it whenever the
 * response carries one. Frozen so a render cannot mutate it.
 */
const FALLBACK_DISPLAY_CURRENCIES = Object.freeze([
  { code: 'USD', name: 'US Dollar', symbol: '$' },
  { code: 'INR', name: 'Indian Rupee', symbol: '\u20b9' },
  { code: 'EUR', name: 'Euro', symbol: '\u20ac' },
  { code: 'GBP', name: 'British Pound', symbol: '\u00a3' },
  { code: 'JPY', name: 'Japanese Yen', symbol: '\u00a5' },
  { code: 'CAD', name: 'Canadian Dollar', symbol: 'CA$' },
  { code: 'AUD', name: 'Australian Dollar', symbol: 'A$' },
  { code: 'SGD', name: 'Singapore Dollar', symbol: 'S$' },
  { code: 'CHF', name: 'Swiss Franc', symbol: 'CHF' },
  { code: 'AED', name: 'UAE Dirham', symbol: 'AED' },
  { code: 'BRL', name: 'Brazilian Real', symbol: 'R$' },
  { code: 'MXN', name: 'Mexican Peso', symbol: 'Mex$' },
  { code: 'ZAR', name: 'South African Rand', symbol: 'R' },
  { code: 'KRW', name: 'South Korean Won', symbol: '\u20a9' },
  { code: 'HKD', name: 'Hong Kong Dollar', symbol: 'HK$' },
  { code: 'SEK', name: 'Swedish Krona', symbol: 'kr' },
  { code: 'NOK', name: 'Norwegian Krone', symbol: 'kr' },
  { code: 'DKK', name: 'Danish Krone', symbol: 'kr' },
  { code: 'PLN', name: 'Polish Zloty', symbol: 'z\u0142' },
  { code: 'CZK', name: 'Czech Koruna', symbol: 'K\u010d' },
  { code: 'TRY', name: 'Turkish Lira', symbol: '\u20ba' },
]);

/** The plan glyph, by plan id. A treatment, not a figure. */
const PLAN_ICONS = Object.freeze({
  enterprise: Crown,
  pro: Star,
  starter: Zap,
});

/** The four allowance tiles, in the order they have always been rendered. */
const ALLOWANCES = Object.freeze([
  { icon: Database, used: 'strategiesUsed', quota: 'strategiesQuota' },
  { icon: Bot, used: 'botsUsed', quota: 'botsQuota' },
  { icon: Cpu, used: 'mlTrainingsUsed', quota: 'mlTrainingsQuota' },
  { icon: TrendingUp, used: 'marketplacePublishedUsed', quota: 'marketplacePublishedQuota' },
]);

/** `-1` and `Infinity` are the server's unlimited sentinels, and both are readings. */
const isUnlimited = (value) => value === -1 || value === Infinity;

/** A safe, client-facing sentence for a failed WRITE. The page's own `detail` read. */
const writeFailureMessage = (err, fallback) => {
  const detail = err?.response?.data?.detail;
  return typeof detail === 'string' && detail.trim() !== '' ? detail : fallback;
};

/**
 * The four invoice columns, unchanged in order and in heading.
 *
 * `render` is where the marker lives: `ds/DataTable` renders a bare `—` for a blank cell, and
 * Requirement 19.3's point is that the reason is the load-bearing half, so every cell renders
 * through the one marker with its declared sentence. Every cell CAN be absent here — a money
 * table with no denominator for its own amounts is the case §6 is written about.
 */
const INVOICE_COLUMNS = Object.freeze([
  {
    key: 'reference',
    header: FIELD.invoiceReference.label,
    // The truncation survives and the false affordance does not: design.md §2.5's fourth
    // yield mechanism permits truncating an IDENTIFIER with its full value on a `title`, and
    // the old `cursor: pointer` pointed at no handler and no keyboard path.
    render: ({ value, row }) =>
      value.available ? (
        <span
          className="font-mono text-brand"
          title={row?.fullReference ?? undefined}
        >
          {`${String(value.value).slice(0, 8)}…`}
        </span>
      ) : (
        <Marker field={FIELD.invoiceReference} reason={value.reason} />
      ),
  },
  {
    key: 'date',
    header: FIELD.invoiceDate.label,
    render: ({ value }) =>
      value.available ? (
        <span className="text-content-secondary">{value.value}</span>
      ) : (
        <Marker field={FIELD.invoiceDate} reason={value.reason} />
      ),
  },
  {
    key: 'amount',
    header: FIELD.invoiceAmount.label,
    align: 'numeric',
    // Denominated by the ROW's own currency, never by a `$` chosen because the row was not
    // INR. An amount with no denomination does not render as a number.
    render: ({ value, row }) =>
      value.available ? (
        <span className="font-mono font-semibold tabular-nums text-content-primary">
          {`${Number(value.value).toLocaleString()} ${row?.denomination ?? ''}`.trim()}
        </span>
      ) : (
        <Marker field={FIELD.invoiceAmount} reason={value.reason} />
      ),
  },
  {
    key: 'status',
    header: FIELD.invoiceStatus.label,
    // The server's word, verbatim, with the hue coming from `statusToken` — Requirement 4.4
    // finding 1 covers what that group is for `paid`.
    render: ({ value }) =>
      value.available ? (
        <StatusBadge state={String(value.value)} />
      ) : (
        <Marker field={FIELD.invoiceStatus} reason={value.reason} />
      ),
  },
]);

export default function Billing() {
  /**
   * The trader's explicit currency pick. `null` until they make one, which is the whole of
   * item 10's fix: the previous `useState("USD")` was both a fabricated reading and — the
   * moment it reached the reader — a `?currency=USD` on a request that carries no parameters.
   */
  const [currencyChoice, setCurrencyChoice] = useState(null);
  const [isCurrencyDropdownOpen, setIsCurrencyDropdownOpen] = useState(false);
  const [checkoutInFlight, setCheckoutInFlight] = useState('');
  const [isCancelLoading, setIsCancelLoading] = useState(false);
  const [isResumeLoading, setIsResumeLoading] = useState(false);
  const [isPortalLoading, setIsPortalLoading] = useState(false);
  /** One `{ severity, message }`, where `billingError` and `actionSuccess` were two strings. */
  const [notice, setNotice] = useState(null);

  /*
    The currency the catalogue reader asks for, as a ref rather than a dependency.

    This is `currencyRef` from before the migration, at the same lifetime and holding the same
    value — the currency the page is SHOWING, seeded from the server's own answer and
    overwritten by an explicit pick. Two things depend on it staying a ref:

      * `usePanelState`'s `refetch` takes no arguments, so the reader has to read the currency
        rather than be handed it. A `deps: [currency]` read would work too, but it would make
        the GET fire off a state change instead of after the POST that persists the preference,
        which is a different sequence of writes than this page has ever issued.
      * It is `null` on mount, so the mount read is `api.billing.getPlans()` with no argument —
        `GET /api/billing/plans`, unchanged. A billing frame arriving later re-reads in
        whatever currency is on screen, which is what `billingSocketLifecycle.test.jsx` pins
        with `toHaveBeenLastCalledWith`.
  */
  const plansCurrencyRef = useRef(null);

  // ── The four reads (Requirement 4.1) ──────────────────────────────────────
  //
  // `deps: []` on each, so each fires once on mount exactly as the `Promise.allSettled` did.
  // Same four paths, no parameter added or changed.
  const readPlans = useCallback(
    () => api.billing.getPlans(plansCurrencyRef.current ?? undefined),
    [],
  );
  const readEntitlements = useCallback(() => api.billing.getEntitlements(), []);
  const readInvoices = useCallback(() => api.billing.getInvoices(), []);
  const readMethods = useCallback(() => api.billing.getPaymentMethods(), []);

  const plans = usePanelState(readPlans, { deps: [] });
  const entitlements = usePanelState(readEntitlements, { deps: [] });
  const invoices = usePanelState(readInvoices, { deps: [] });
  const methods = usePanelState(readMethods, { deps: [] });

  // Stable by `usePanelState`'s second inversion, so the socket effect below can depend on
  // them without being torn down and rebuilt on every render.
  const refetchPlans = plans.refetch;
  const refetchEntitlements = entitlements.refetch;
  const refetchInvoices = invoices.refetch;
  const refetchMethods = methods.refetch;

  // The envelope both shapes the old code accepted: `res.data ?? res`. Kept, because the shape
  // a deployment answers with is not this commit's question.
  const plansBody = plans.data?.data ?? plans.data;
  const entitlementsBody = entitlements.data?.data ?? entitlements.data;

  /*
    The currency the server answered with becomes the currency the next read asks for.

    An effect rather than a render-time write because it is a ref mutation: the value has to
    survive a render without causing one. A pick sets the ref directly in the handler, so a
    read in flight when the response for the PREVIOUS currency lands cannot walk it backwards —
    `plansBody` is `null` for the duration of a non-background read (`usePanelState` drops the
    previous payload), so `answered` is `undefined` and the ref is left alone.
  */
  useEffect(() => {
    const answered = plansBody?.currency;
    if (typeof answered === 'string' && answered.trim() !== '') {
      plansCurrencyRef.current = answered.trim();
    }
  }, [plansBody]);

  /**
   * Re-read all four. The same four requests the mount issues, and nothing else.
   *
   * `loadBilling` + `loadPlans` before the migration, in one function because the two callers
   * — the socket handler and the Refresh control — both called both.
   */
  const refreshSubscription = useCallback(() => {
    refetchEntitlements();
    refetchInvoices();
    refetchMethods();
    refetchPlans();
  }, [refetchEntitlements, refetchInvoices, refetchMethods, refetchPlans]);

  /*
    Real-time subscription updates, over the one session socket.
    production-launch-hardening task 8.1. Requirements 1.22, 1.23 / 2.22, 2.23.

    This effect used to call `new WebSocket` itself and schedule its own reconnect from inside
    `onclose` — and `onclose` is exactly what the effect's cleanup fires, so tearing the page
    down was the event that armed the next connection. There was no id to clear and no
    `clearTimeout` anywhere in the effect, so five seconds after the page was gone a socket
    opened that nothing held a reference to.

    Both halves are gone, and neither is replaced by a local fix:

    - The socket is the shared client's. `acquire` / `release` are refcounted
      (`websocketClient.js:705-737`), so this page is a *hold* on the one session connection
      rather than a second connection. The last release closes it.
    - The reconnect is the shared client's too. It already owns capped jittered backoff and —
      the part the handler here got wrong — the distinction between an intentional teardown
      (`release` → `disconnect`, which disables reconnection before it closes) and a dropped
      connection. Deleting the scheduling outright is the fix; a `clearTimeout` would have
      kept a second reconnect implementation alive.

    Unchanged on purpose: the route is still `/ws/user/{user_id}` (`ws_routes.py:641`), which
    is where the backend publishes what a Stripe webhook produces. There is a known open
    defect that the route requires an `exchange_id` query parameter it never receives; that is
    a backend concern and Requirement 16.7 puts `backend_app/` outside this spec, so it is
    recorded rather than worked around here.

    ── THE CREDENTIAL AND THE STORE (tasks 8.2, 8.3 — Requirements 1.21 / 2.21, 3.9) ────────

    Neither half of what this comment used to say is true any more, so neither is repeated:
    the credential is no longer read here at all, and it does not travel as `?token=`.
    `wsClient.acquire` mints a single-use ≤ 30 s ticket over HTTPS for every connection
    attempt (`websocketClient.js` §THE SOCKET CREDENTIAL), so this page has no credential to
    hold. What it needs is narrower: *whether a session exists*, and *whose it is*.

    **Whether** comes from `isAuthenticated()` — `apiClient`'s own helper, re-exported by
    `src/api`. That is the point of task 8.3. This effect used to read
    `localStorage.getItem('token')` while the axios request interceptor read
    `sessionStorage.getItem("token")`, which is two stores for one session: every writer in
    the app (`apiClient`'s `onAuthStateChange`, `AuthPage`, sign-out) writes `sessionStorage`,
    so the `localStorage` read was never once satisfied and this effect has never run in a
    real session. Calling the HTTP client's own helper rather than re-reading a store makes
    "one store" structural — there is no second read here to drift.

    **Whose** comes from `GET /api/auth/me` (`api.auth.getMe`, `routers/auth.py:123`), not
    from storage. `localStorage.getItem('userId')` was the second dead guard: nothing in
    `src/` writes `userId`, anywhere, so it was always null. `/api/auth/me` is the right
    replacement rather than `supabase.auth.getUser()` for two reasons — it is authenticated
    by the very token the socket ticket will be minted from, so a resolvable id and a usable
    session are one fact rather than two; and supabase-js persists its own session in
    `localStorage` (`sb-<ref>-auth-token`, which `AccountMenu.jsx:535` clears by hand), so
    asking it would reintroduce exactly the second store this task removes. The `id` it
    returns is the JWT `sub`, which is what `_resolve_ws_subject` compares the path segment
    against (`ws_routes.py:729-743`) — so the id and the route agree by construction.

    The resolve is a request, so it is asynchronous, and the effect may be torn down while it
    is in flight. `cancelled` is checked after the await and `held` records whether a hold was
    ever taken: an unmount mid-resolve therefore acquires nothing and releases nothing. A
    resolve that fails, or that answers without an id, is a *reported* no-subscription — the
    reason is logged and no socket is opened. It is never a placeholder path.

    ── WHAT retail-ui-simplification TASK 7.9 CHANGED HERE ──────────────────────────────────

    One line: the two loaders the frame handler called are now `refreshSubscription`, which is
    the same four reads issued the same way. The effect's dependency is stable where
    `[loadBilling, loadPlans, currency]` was not, so a currency pick no longer tears the
    subscription down and sets it up again — which is a *narrowing* of the lifecycle the two
    socket suites assert, never a widening.
  */
  useEffect(() => {
    // No session, no socket — and no ticket request either. Every route in `ws_routes.py`
    // fails closed, so this is not the client deciding authorisation; it is the client not
    // asking a signed-out user's question.
    if (!isAuthenticated()) return undefined;

    let cancelled = false;
    let held = false;
    let releases = [];

    /*
      The five frame kinds that mean "your entitlements changed, re-read them". Two are keyed
      on `type` and three on `event`, which is the backend's shape, not a choice made here —
      preserved exactly, because these five are the reason the socket exists: a webhook lands,
      the backend publishes, and the page re-reads without the trader refreshing.
    */
    const namesABillingChange = (message) =>
      message.type === 'subscription_update' ||
      message.type === 'plan_changed' ||
      message.event === 'subscription_cancelled' ||
      message.event === 'cancellation_reversed' ||
      message.event === 'payment_failed';

    // One frame, one refresh. `processMessage` hands the same object to an event-type
    // subscriber and then to a channel subscriber (`websocketClient.js:303-330`), and this
    // page holds both routings, so a frame naming the channel would otherwise refresh twice.
    const alreadyRefreshed = new WeakSet();

    const handleBillingFrame = (message) => {
      if (!message || typeof message !== 'object') return;
      if (!namesABillingChange(message)) return;
      if (alreadyRefreshed.has(message)) return;
      alreadyRefreshed.add(message);
      refreshSubscription();
    };

    const subscribe = async () => {
      let userId;
      try {
        const response = await api.auth.getMe();
        // `apiClient`'s `get` returns the body; the `.data` unwrap is the same defensive
        // read the panel bodies above make, for the same reason.
        const profile = (response && response.data) || response;
        userId = (profile && profile.id) || null;
      } catch (err) {
        console.error(
          'Billing: no real-time subscription — GET /api/auth/me failed, so the session has ' +
          `no resolvable user id: ${err?.message || 'unknown error'}`,
        );
        return;
      }

      // Unmounted, or the effect re-ran, while the profile request was in flight. Taking a
      // hold now would be a hold nothing releases.
      if (cancelled) return;

      if (!userId) {
        console.error(
          'Billing: no real-time subscription — GET /api/auth/me answered without an `id`, ' +
          'and /ws/user/{user_id} has no correct value to stand in for it.',
        );
        return;
      }

      wsClient.acquire(`/ws/user/${userId}`);
      held = true;
      releases = [
        // The server-side hold, refcounted per channel by the shared client.
        wsClient.subscribeChannel('billing', handleBillingFrame),
        // And the client-side routing for the frames that name a type rather than a channel,
        // which is how the two `type` kinds above arrive.
        wsClient.subscribe('subscription_update', handleBillingFrame),
        wsClient.subscribe('plan_changed', handleBillingFrame),
      ];
    };

    subscribe();

    return () => {
      cancelled = true;
      releases.forEach((release) => release());
      releases = [];
      if (held) {
        held = false;
        wsClient.release();
      }
    };
  }, [refreshSubscription]);

  // ── The write paths. Requests unchanged (Requirement 16.1) ────────────────

  const handleCheckout = async (planId, planCurrency) => {
    if (checkoutInFlight) return;
    setNotice(null);
    setCheckoutInFlight(planId);
    try {
      /*
        The same request with the same body — `{ tier, currency }` — and the same currency
        value in every reachable case: the plans response sets every plan's `currency` to the
        context currency, which is the same value the page-level state held once a response had
        landed, and a plan card cannot exist before one has. Reading it off the plan the trader
        clicked rather than off page state is narrower, not different: the currency sent is the
        one the price on that card was quoted in. `CheckoutRequest.currency` defaults to
        `"INR"` server-side (`core/schemas.py:30`) if neither is known, which is the
        platform's own declared default and not a value chosen here.
      */
      const data = await api.billing.createCheckout({
        tier: planId,
        currency: planCurrency ?? plansCurrencyRef.current ?? undefined,
      });
      if (data && data.checkoutUrl) {
        window.location.href = data.checkoutUrl;
      } else {
        throw new Error('Payment gateway URL not provided by backend.');
      }
    } catch (err) {
      console.error('Checkout error:', err);
      setNotice({
        severity: 'error',
        message: writeFailureMessage(err, 'Checkout initialization failed.'),
      });
    } finally {
      setCheckoutInFlight('');
    }
  };

  const handleCurrencyChange = async (nextCurrency) => {
    setNotice(null);
    setCurrencyChoice(nextCurrency);
    plansCurrencyRef.current = nextCurrency;
    setIsCurrencyDropdownOpen(false);
    try {
      await api.billing.setCurrency(nextCurrency);
      refetchPlans();
    } catch (err) {
      // The whole of the previous failure handling was one `console.error`, so the control
      // kept the new code while the server still held the old one and nothing said so
      // (Requirement 11.3: logging is not handling).
      setNotice({
        severity: 'error',
        message: writeFailureMessage(
          err,
          'That currency preference could not be saved, so prices may still be shown in the '
            + 'previous currency.',
        ),
      });
    }
  };

  const handleCancel = async () => {
    if (isCancelLoading) return;
    setIsCancelLoading(true);
    setNotice(null);
    try {
      const result = await api.billing.cancelSubscription();
      setNotice({
        severity: 'info',
        message: result?.detail || 'Subscription cancellation scheduled.',
      });
      refetchEntitlements();
    } catch (err) {
      setNotice({
        severity: 'error',
        message: writeFailureMessage(err, 'Failed to cancel subscription.'),
      });
    } finally {
      setIsCancelLoading(false);
    }
  };

  const handleResume = async () => {
    if (isResumeLoading) return;
    setIsResumeLoading(true);
    setNotice(null);
    try {
      const result = await api.billing.resumeSubscription();
      setNotice({
        severity: 'info',
        message: result?.detail || 'Subscription resumed successfully.',
      });
      refetchEntitlements();
    } catch (err) {
      setNotice({
        severity: 'error',
        message: writeFailureMessage(err, 'Failed to resume subscription.'),
      });
    } finally {
      setIsResumeLoading(false);
    }
  };

  const handleOpenPortal = async () => {
    if (isPortalLoading) return;
    setIsPortalLoading(true);
    setNotice(null);
    try {
      const data = await api.billing.openPortal();
      if (data && data.url) {
        window.open(data.url, '_blank', 'noopener,noreferrer');
      } else {
        throw new Error('Billing portal URL not returned by server.');
      }
    } catch (err) {
      setNotice({
        severity: 'error',
        message: writeFailureMessage(err, 'Failed to open billing portal.'),
      });
    } finally {
      setIsPortalLoading(false);
    }
  };

  // ── The declared figures (Requirement 4.2) ────────────────────────────────

  const planName = reported(entitlementsBody, FIELD.planName);
  const subscriptionStatus = reported(entitlementsBody, FIELD.subscriptionStatus);
  const cancelAtPeriodEnd = reported(entitlementsBody, FIELD.cancelAtPeriodEnd);
  /** `fromNullable` over the FORMATTED date, so an unparseable value is an absence. */
  const renewalDate = fromNullable(
    formatDate(read(entitlementsBody, FIELD.renewalDate.path)),
    FIELD.renewalDate.reason,
  );

  /*
    Every one of these was derived from a fabricated `"active"`, which is why item 3 took the
    controls away as well as misreporting the state: a failed read produced `isFreePlan` and
    `!isPaymentFailed` together, so the page hid Cancel, Resume and Manage billing AND
    suppressed the payment-failure banner on an account that was really past due. There is no
    `?? 'active'` here: an unreadable status is none of these.
  */
  const status = subscriptionStatus.available ? String(subscriptionStatus.value) : null;
  const isPaymentFailed = status === 'past_due' || status === 'payment_failed';
  const isCancelled = status === 'cancelled';
  const planIsPaid = planName.available && String(planName.value).toLowerCase() !== 'free';

  /*
    `entitlements.features` is read and DROPPED, exactly as before: the previous version stored
    it on `currentPlan` and rendered it nowhere — the capability lines on screen are each
    catalogue card's own `plans[].features`. It is not declared in `pageFields.js` for two
    reasons: it is a LIST, which `design/reported.js`'s `isReadableValue` refuses by design, and
    it is not rendered, so there is no figure to account for. Recorded rather than quietly left
    out, because "a key the response carries that no panel shows" is the shape a reviewer is
    entitled to ask about on a money surface.
  */
  const PlanIcon = PLAN_ICONS[String(read(entitlementsBody, 'plan') ?? '').toLowerCase()] ?? Shield;

  /*
    The pricing context. A trader's explicit pick outranks the server's answer for the CODE —
    that is a choice, not a reading — and nothing outranks it for the symbol, the region or the
    detection source, which is where the four literals were.
  */
  const displayCurrency =
    currencyChoice === null
      ? reported(plansBody, FIELD.displayCurrency)
      : available(currencyChoice);
  const displayCurrencySymbol = reported(plansBody, FIELD.displayCurrencySymbol);
  const billingRegion = reported(plansBody, FIELD.billingRegion);
  const currencySource = reported(plansBody, FIELD.currencySource);

  /** The response's own list when it sent one; otherwise the fallback option set. */
  const displayCurrencies = useMemo(() => {
    const offered = plansBody?.supported_currencies;
    return Array.isArray(offered) && offered.length > 0 ? offered : FALLBACK_DISPLAY_CURRENCIES;
  }, [plansBody]);

  /** The four allowance tiles: eight figures, each resolved against its own declaration. */
  const allowances = useMemo(
    () =>
      ALLOWANCES.map(({ icon, used, quota }) => ({
        key: used,
        icon,
        usedField: FIELD[used],
        quotaField: FIELD[quota],
        used: reported(entitlementsBody, FIELD[used]),
        quota: reported(entitlementsBody, FIELD[quota]),
      })),
    [entitlementsBody],
  );

  /** One catalogue card, with its price resolved against the declaration's three inputs. */
  const cataloguePlans = useMemo(() => {
    const offered = Array.isArray(plansBody?.plans) ? plansBody.plans : [];
    return offered.map((plan, index) => {
      const denomination =
        typeof plan?.currency === 'string' && plan.currency.trim() !== ''
          ? plan.currency.trim()
          : null;
      const reportedPrice = fromNullable(plan?.localized_price, FIELD.cataloguePlanPrice.reason);
      const checkoutDenomination =
        typeof plan?.checkout_currency === 'string' && plan.checkout_currency.trim() !== ''
          ? plan.checkout_currency.trim()
          : null;
      return {
        id: plan?.id ?? `plan-${index}`,
        recommended: plan?.recommended === true,
        name: fromNullable(plan?.name, FIELD.cataloguePlanName.reason),
        description: fromNullable(plan?.description, FIELD.cataloguePlanDescription.reason),
        // A figure with no denomination is not a price. The declaration lists
        // `plans[].currency` as an input for exactly this reason: a bare `999` on a money
        // surface is worse than a marker, because a trader will read it in whatever currency
        // the rest of the page is showing.
        price: denomination === null ? unavailable(FIELD.cataloguePlanPrice.reason) : reportedPrice,
        // The server's own decimal count. `undefined` means "do not round", which is
        // `ds/Metric`'s documented default and is the opposite of the page's old
        // `currency === "JPY" || currency === "KRW" ? 0 : 2`.
        decimals: Number.isInteger(plan?.decimals) ? plan.decimals : undefined,
        denomination,
        // The conversion note renders only when the server says the charge is in a different
        // currency from the quote. All three keys are real and all three are read.
        showsCheckoutConversion:
          plan?.is_direct_checkout === false
          && checkoutDenomination !== null
          && checkoutDenomination !== denomination,
        checkoutPrice: fromNullable(
          plan?.checkout_price,
          FIELD.cataloguePlanCheckoutPrice.reason,
        ),
        checkoutDenomination,
        features: Array.isArray(plan?.features) ? plan.features : [],
      };
    });
  }, [plansBody]);

  /** The five most recent invoice rows, each field resolved once against its declaration. */
  const invoiceRows = useMemo(() => {
    const body = invoices.data?.data ?? invoices.data;
    const rows = Array.isArray(body) ? body : [];
    return rows.slice(0, 5).map((invoice, index) => {
      const denomination =
        typeof invoice?.currency === 'string' ? invoice.currency.trim().toUpperCase() : null;
      /*
        THE DERIVATION, AND WHY THE THIRD BRANCH IS AN ABSENCE. The row carries two amount
        columns and its own denomination, so the amount is SELECTED rather than read from one
        path. `inv.currency === "INR" ? ₹amtINR : $amtUSD` treated "anything that is not INR"
        as dollars, so a EUR invoice rendered with a dollar sign; and both arms were `|| 0`,
        so an unreadable amount rendered as a settled invoice for nothing.
      */
      const amount =
        denomination === 'INR'
          ? invoice?.amtINR
          : denomination === 'USD'
            ? invoice?.amtUSD
            : undefined;
      return {
        // Only a React key and a `getRowId`; never rendered.
        id: invoice?.id ?? `invoice-${index}`,
        reference: rowField(invoice, FIELD.invoiceReference),
        // The untruncated identifier, for the cell's `title` (design.md §2.5 mechanism 4).
        fullReference:
          invoice?.id === null || invoice?.id === undefined ? null : String(invoice.id),
        date: fromNullable(formatDate(invoice?.date), FIELD.invoiceDate.reason),
        amount: fromNullable(amount, FIELD.invoiceAmount.reason),
        denomination,
        status: rowField(invoice, FIELD.invoiceStatus),
      };
    });
  }, [invoices.data]);

  /** The default stored method, by the page's own existing rule. */
  const defaultMethod = useMemo(() => {
    const body = methods.data?.data ?? methods.data;
    const rows = Array.isArray(body) ? body : [];
    return rows.find((method) => method?.is_default) || rows[0] || null;
  }, [methods.data]);

  const cardBrand = rowField(defaultMethod, FIELD.cardBrand);
  const cardLast4 = rowField(defaultMethod, FIELD.cardLast4);
  /**
   * BOTH parts or neither. A month with no year is not an expiry date, and the previous
   * version rendered `Expires undefined/undefined` for it.
   */
  const cardExpiry =
    defaultMethod?.expiry_month === null
    || defaultMethod?.expiry_month === undefined
    || defaultMethod?.expiry_year === null
    || defaultMethod?.expiry_year === undefined
      ? unavailable(FIELD.cardExpiry.reason)
      : available(`${defaultMethod.expiry_month}/${defaultMethod.expiry_year}`);

  // ── The panel states ──────────────────────────────────────────────────────

  const anyLoading = [plans, entitlements, invoices, methods].some(
    (panel) => panel.state === PANEL_STATES.LOADING || panel.state === PANEL_STATES.REFRESHING,
  );

  /*
    A 200 carrying a pricing context but no plans is a readable response with an empty
    catalogue, and `isEmptyPayload` cannot see that: the collection is under `plans`, which is
    not one of its six envelope keys, so the payload reads as content. Projecting it here keeps
    `empty` distinguishable from `error` — the distinction the old *Loading plans…* collapsed
    into "forever" — while leaving the pricing-context row above the panel readable either way.
  */
  const catalogueState =
    plans.state === PANEL_STATES.READY && cataloguePlans.length === 0
      ? PANEL_STATES.EMPTY
      : plans.state;

  return (
    <div className="flex-1 overflow-y-auto p-5">
      {/* fontSize: the page <h1> and its subtitle are `ds/PageHeader`'s own steps
          (--text-page and --text-body) rather than this page's. */}
      <PageHeader
        title="Subscription & Billing"
        subtitle="Manage your plan, localized pricing, and payment methods"
        actions={
          <CommandButton
            intent="secondary"
            icon={RefreshCw}
            onClick={refreshSubscription}
            loading={anyLoading}
            loadingLabel="Reading your billing profile"
          >
            Refresh
          </CommandButton>
        }
      />

      {/* fontSize: 12 ×2, and the two banner shells → `ds/Alert`, which derives hue, icon and
          live-region role from `severity` and accepts no colour. `ds/Alert` has no `success`
          severity and one is NOT added to a shared primitive from a page commit, so a
          completed billing action reports at `info` with its sentence unchanged. */}
      {notice ? (
        <div className="mt-3">
          <Alert
            severity={notice.severity}
            title={notice.message}
            onDismiss={() => setNotice(null)}
            dismissLabel="Dismiss this message"
          />
        </div>
      ) : null}

      {/* fontSize: 13 → `ds/Alert`'s `title` step, 12 → its body, 11 → its action's
          `ds/CommandButton`. The whole block leaves with its call site. It is reachable only
          from the server's own `past_due` / `payment_failed` now; it used to be computed from
          a status that defaulted to "active", so it never rendered for a failed read and it
          rendered for nobody else either. */}
      {isPaymentFailed ? (
        <div className="mt-3">
          <Alert
            severity="error"
            title="Payment failed — action required"
            action={
              <CommandButton
                intent="secondary"
                icon={ExternalLink}
                onClick={handleOpenPortal}
                loading={isPortalLoading}
                loadingLabel="Opening the billing portal"
              >
                Update payment method
              </CommandButton>
            }
          >
            Your subscription payment failed. Update your payment method to restore full access.
          </Alert>
        </div>
      ) : null}

      {/* ══ THE PRICING CONTEXT AND THE CURRENCY CONTROL ═══════════════════════
          Outside every panel on purpose. It describes the money the CATALOGUE is quoted in
          rather than the account's subscription, it is the only control that changes that, and
          a panel state must not be able to take it off the screen. Every figure in it renders
          its declared marker when the catalogue read cannot fill it — which is where
          *United States (USD)* under an *Auto-detected* chip used to come from. */}
      <div className="mt-4 flex flex-wrap items-center justify-end gap-2.5">
        {/* fontSize: 11 → --text-body (value — Q7, secondary). The container loses its
            monospace (a region name is prose) and the ISO code keeps it (an identifier),
            which is §2.4's one-call-site-two-roles case. */}
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 rounded-lg border border-line-default bg-surface-inset px-3 py-1.5 text-body text-content-secondary">
          <MapPin size={12} aria-hidden="true" className="shrink-0 text-brand" />
          {billingRegion.available ? (
            <span className="truncate">{billingRegion.value}</span>
          ) : (
            <span className="inline-flex items-center gap-1">
              {FIELD.billingRegion.label}
              <Marker field={FIELD.billingRegion} reason={billingRegion.reason} />
            </span>
          )}
          <span aria-hidden="true">(</span>
          {displayCurrency.available ? (
            <span className="font-mono">{displayCurrency.value}</span>
          ) : (
            <Marker field={FIELD.displayCurrency} reason={displayCurrency.reason} />
          )}
          <span aria-hidden="true">)</span>
          {/* fontSize: 9 → --text-micro (chip — Q5), in place. NOT `ds/StatusBadge`:
              "auto-detected" and "preferred" are a provenance, not a health state, and giving
              them one would mean inventing a `statusToken` state for them here — which is the
              decision Requirement 5.3 keeps on `design/semantic.js`. The chip carries the
              declared words and claims no hue. `"ip"` and a saved preference are both
              readings; the absence is not, and it used to default to `"ip"`. */}
          <span className="inline-flex items-center gap-1 rounded-sm border border-line-default px-1.5 py-0.5 text-micro uppercase tracking-wide">
            {currencySource.available ? (
              currencySource.value === 'ip' ? 'Auto-detected' : 'Preferred'
            ) : (
              <>
                {FIELD.currencySource.label}
                <Marker field={FIELD.currencySource} reason={currencySource.reason} />
              </>
            )}
          </span>
        </div>

        {/* Requirement 4.4 finding 2: `ds/` has no disclosure or select control, so this stays
            a page-local button plus an absolutely-positioned list. It keeps its exact rendered
            form — the trigger reads `{symbol} {code}` and each row reads `{CODE} ({symbol})` —
            and gains `aria-haspopup`, `aria-expanded` and an accessible name that does not
            depend on a read. */}
        <div className="relative">
          {/* fontSize: 11 → --text-body (value — Q7, secondary). An ISO 4217 code is an
              IDENTIFIER, so Requirement 3.1 keeps the monospace here. */}
          <button
            type="button"
            aria-haspopup="true"
            aria-expanded={isCurrencyDropdownOpen}
            aria-label="Change the currency prices are shown in"
            onClick={() => setIsCurrencyDropdownOpen(!isCurrencyDropdownOpen)}
            className="flex items-center gap-2 rounded-lg border border-brand bg-surface-inset px-3.5 py-1.5 font-mono text-body font-bold text-brand"
          >
            <Globe size={13} aria-hidden="true" />
            {displayCurrency.available ? (
              <span>
                {displayCurrencySymbol.available ? `${displayCurrencySymbol.value} ` : ''}
                {displayCurrency.value}
              </span>
            ) : (
              <Marker field={FIELD.displayCurrency} reason={displayCurrency.reason} />
            )}
            <ChevronDown size={13} aria-hidden="true" />
          </button>

          {isCurrencyDropdownOpen ? (
            <div
              className="absolute right-0 top-full z-50 mt-1.5 max-h-72 w-56 overflow-y-auto rounded-xl border border-line-default bg-surface-raised p-1.5"
              // `#00000088` was a 53% black at a depth the token layer does not declare.
              // `shadow.overlay` is the declared elevation for a floating layer.
              style={{ boxShadow: token.shadow.overlay }}
            >
              {/* fontSize: 10 → --text-micro (label — Q4). It names the list below it. */}
              <div className="mb-1 border-b border-line-default px-2 py-1 text-micro font-bold uppercase tracking-wide text-content-muted">
                Select currency
              </div>
              {displayCurrencies.map((option) => {
                const selected = displayCurrency.available && displayCurrency.value === option.code;
                return (
                  // fontSize: 11 → --text-body (value — Q7). The row's ISO code keeps the
                  // monospace; its human name is a label at --text-micro below.
                  //
                  // `aria-current` rather than `role="option"` with `aria-selected`: an
                  // explicit `option` role would REPLACE the implicit button role, and these
                  // rows are operated as buttons — by a trader and by
                  // `billingSocketLifecycle.test.jsx`'s `getAllByRole('button')`. The
                  // selection used to be carried by colour alone.
                  <button
                    key={option.code}
                    type="button"
                    aria-current={selected ? 'true' : undefined}
                    onClick={() => handleCurrencyChange(option.code)}
                    className={`flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-body ${
                      selected
                        ? 'bg-brand-wash font-extrabold text-brand'
                        : 'text-content-primary hover:bg-surface-inset'
                    }`}
                  >
                    <span className="font-mono">{`${option.code} (${option.symbol})`}</span>
                    {/* fontSize: 10 → --text-micro (label — Q4: it names the code beside it). */}
                    <span className="truncate text-micro text-content-muted">{option.name}</span>
                  </button>
                );
              })}
            </div>
          ) : null}
        </div>
      </div>

      {/* ══ THE ACCOUNT'S OWN SUBSCRIPTION ═══════════════════════════════════
          Requirement 4.4 finding 4: `money` is NOT passed. It requires an `environment`, and a
          subscription invoice has no trading environment — *ENVIRONMENT UNCONFIRMED* over a
          real charge would state something false. */}
      <Panel
        className="mt-4"
        title="Current plan"
        state={entitlements.state}
        loading={{ kind: 'skeleton-cards', rows: 2, label: 'Reading your subscription' }}
        empty={{
          headline: 'No subscription is recorded yet',
          body: 'Nothing is stored against this account for a plan, its status or its '
            + 'allowances. The plans below are what is on offer.',
          action: { label: 'Read again', onClick: refetchEntitlements },
        }}
        error={{ error: entitlements.error, onRetry: refetchEntitlements }}
        unavailable={{ reason: 'Your subscription cannot be read right now.' }}
        unauthorised={{ error: entitlements.error }}
        actions={
          planIsPaid ? (
            <CommandButton
              intent="secondary"
              icon={ExternalLink}
              onClick={handleOpenPortal}
              loading={isPortalLoading}
              loadingLabel="Opening the billing portal"
            >
              Manage billing
            </CommandButton>
          ) : null
        }
      >
        <div className="flex flex-col gap-5">
          <div className="flex items-start gap-3">
            <div className="flex shrink-0 items-center justify-center rounded-xl bg-brand-wash p-3 text-brand">
              <PlanIcon size={24} aria-hidden="true" />
            </div>
            <div className="min-w-0">
              {/* fontSize: 24 → --text-section (value — Q7, this panel's hero). At 24 it
                  equalled the page <h1>, and `tokens.css:74` reserves --text-page for that,
                  so this takes the highest step below it. The literal `"Free"` is gone. */}
              {planName.available ? (
                <p className="truncate text-section font-extrabold capitalize text-content-primary">
                  {planLabel(planName.value)}
                </p>
              ) : (
                <p className="inline-flex items-center gap-1 text-section text-content-muted">
                  <Marker field={FIELD.planName} reason={planName.reason} />
                </p>
              )}
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {/* fontSize: → `ds/StatusBadge`'s own step. Requirement 4.4 finding 1: the
                    server's status goes to `statusToken` verbatim, so `active` is the live
                    group and `past_due` is calm rather than being coloured by a decision made
                    on this page. It used to default to `"ACTIVE"` in three places. */}
                {subscriptionStatus.available ? (
                  <StatusBadge state={String(subscriptionStatus.value)} dot />
                ) : (
                  <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-content-muted">
                    {FIELD.subscriptionStatus.label}
                    <Marker field={FIELD.subscriptionStatus} reason={subscriptionStatus.reason} />
                  </span>
                )}
                {/* fontSize: 11 ×2 → §2.4's one-call-site-two-roles case: the eyebrow word is
                    a label (Q4 → --text-micro) and the date is a value (Q7 → --text-body), so
                    the container loses its declaration and each child takes its own step.
                    *at period end* is gone: it was a claim about when access ends, rendered
                    when `renewal_date` was null. */}
                {cancelAtPeriodEnd.available ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span
                      className={`text-micro uppercase tracking-wide ${
                        cancelAtPeriodEnd.value ? 'text-status-loss' : 'text-content-muted'
                      }`}
                    >
                      {cancelAtPeriodEnd.value ? 'Cancels' : FIELD.renewalDate.label}
                    </span>
                    {renewalDate.available ? (
                      <span className="text-body text-content-secondary">{renewalDate.value}</span>
                    ) : (
                      <Marker field={FIELD.renewalDate} reason={renewalDate.reason} />
                    )}
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-content-muted">
                    {FIELD.cancelAtPeriodEnd.label}
                    <Marker field={FIELD.cancelAtPeriodEnd} reason={cancelAtPeriodEnd.reason} />
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* ── The four allowance tiles: eight declared figures ────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {allowances.map((allowance) => {
              const AllowanceIcon = allowance.icon;
              const unlimited = allowance.quota.available && isUnlimited(allowance.quota.value);
              /*
                The bar renders ONLY when both figures are readable and the quota is a finite
                positive number. `getUsagePercent` returned `0` for an unknown limit, so an
                unread quota drew an empty bar — a proportion of nothing, presented as a
                proportion. A zero-quota plan has no proportion either, and 0% of 0 is not it.
              */
              const used = Number(allowance.used.value);
              const quota = Number(allowance.quota.value);
              const percent =
                allowance.used.available
                && allowance.quota.available
                && Number.isFinite(used)
                && Number.isFinite(quota)
                && !unlimited
                && quota > 0
                  ? Math.min((used / quota) * 100, 100)
                  : null;
              const barTone =
                percent === null
                  ? ''
                  : percent >= 90
                    ? 'bg-status-loss'
                    : percent >= 70
                      ? 'bg-status-warning'
                      : 'bg-status-profit';
              return (
                <div
                  key={allowance.key}
                  className="rounded-xl border border-line-default bg-surface-inset p-4"
                >
                  <div className="mb-2 flex items-center gap-2">
                    <AllowanceIcon size={16} aria-hidden="true" className="text-content-muted" />
                    {/* fontSize: 11 and 20 → `ds/Metric`'s own label and tier-2 figure steps.
                        `usage.x || 0` is gone: a genuine 0 renders 0, and an unread figure
                        renders the marker with the reason the declaration carries. */}
                    <Metric
                      label={allowance.usedField.label}
                      value={allowance.used}
                      format="integer"
                      tier={2}
                      unavailableReason={allowance.usedField.reason}
                    />
                  </div>
                  {/* fontSize: 12 → --text-micro for the label (Q4) and --text-body for the
                      figure (Q7). The quota keeps the monospace: it is a figure. `∞` is the
                      server's `-1`, which is a reading like any other. */}
                  <p className="flex flex-wrap items-baseline gap-1 text-micro uppercase tracking-wide text-content-muted">
                    {allowance.quotaField.label}
                    {allowance.quota.available ? (
                      <span className="font-mono text-body normal-case tracking-normal tabular-nums text-content-secondary">
                        {unlimited ? '\u221e' : String(allowance.quota.value)}
                      </span>
                    ) : (
                      <Marker field={allowance.quotaField} reason={allowance.quota.reason} />
                    )}
                  </p>
                  {percent === null ? null : (
                    <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-panel">
                      <div
                        className={`h-full rounded-full ${barTone}`}
                        style={{ width: `${percent}%` }}
                      />
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* ── The lifecycle controls ─────────────────────────────────────────
              Requirement 4.4 finding 5: cancelling still takes one click. There was no
              confirmation to preserve, and adding one changes what a billing action DOES,
              which Requirement 16.1 puts out of scope. Recorded, not taken.
              fontSize: 11 ×2 → `ds/CommandButton`'s own step. */}
          {planIsPaid ? (
            <div className="flex flex-wrap gap-2.5 border-t border-line-default pt-4">
              {cancelAtPeriodEnd.available && cancelAtPeriodEnd.value ? (
                <CommandButton
                  intent="live"
                  icon={RefreshCw}
                  onClick={handleResume}
                  loading={isResumeLoading}
                  loadingLabel="Resuming your subscription"
                >
                  Resume subscription
                </CommandButton>
              ) : null}
              {cancelAtPeriodEnd.available
              && !cancelAtPeriodEnd.value
              && !isCancelled
              && !isPaymentFailed ? (
                <CommandButton
                  intent="destructive"
                  icon={XCircle}
                  onClick={handleCancel}
                  loading={isCancelLoading}
                  loadingLabel="Scheduling the cancellation"
                >
                  Cancel subscription
                </CommandButton>
                ) : null}
            </div>
          ) : null}
        </div>
      </Panel>

      {/* ══ THE PRICED CATALOGUE ══════════════════════════════════════════════
          fontSize: 18 → `ds/Panel`'s `--text-title` heading. Three sibling regions on this
          page are panels and `tokens.css:72` gives a panel heading `--text-title`; one of the
          three at `--text-section` would read as a section containing the other two.
          fontSize: 12 → REMOVED. *Loading plans…* was this read's entire loading AND failure
          state, so a 500 left it on screen forever. `ds/Panel`'s `loading` arm is a skeleton
          shaped like the cards and its `error` arm carries a retry; a sentence that meant two
          different things does not survive as either. */}
      <Panel
        className="mt-4"
        title="Available plans"
        state={catalogueState}
        loading={{ kind: 'skeleton-cards', rows: 4, label: 'Reading the plan catalogue' }}
        empty={{
          headline: 'No plans are on offer right now',
          body: 'The catalogue was read and came back with nothing in it. Your current plan '
            + 'and its allowances are unaffected.',
          action: { label: 'Read again', onClick: refetchPlans },
        }}
        error={{ error: plans.error, onRetry: refetchPlans }}
        unavailable={{ reason: 'The plan catalogue cannot be read right now.' }}
        unauthorised={{ error: plans.error }}
        actions={
          plans.state === PANEL_STATES.REFRESHING ? (
            // fontSize: 11 → --text-body (sentence — Q1).
            <p className="text-body text-content-muted">Updating pricing…</p>
          ) : null
        }
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {cataloguePlans.map((plan) => {
            const isCurrent =
              planName.available
              && String(planName.value).toLowerCase() === String(plan.id).toLowerCase();
            const checkoutBlockedReason = isCurrent
              ? 'This is already your plan, so there is nothing to change.'
              : isPaymentFailed
                ? 'Update your payment method first — a plan change cannot be processed while '
                  + 'a payment is outstanding.'
                : checkoutInFlight !== '' && checkoutInFlight !== plan.id
                  ? 'Another checkout is already opening.'
                  : null;
            return (
              <div
                key={plan.id}
                className={`relative flex flex-col rounded-xl border-2 bg-surface-raised p-5 ${
                  plan.recommended || isCurrent ? 'border-brand' : 'border-line-default'
                }`}
              >
                {/* fontSize: 10 ×2 → --text-micro (chip — Q5). `color: "#000"` on both was
                    pure black; `content-inverse` is the declared inverse (#080A0E). */}
                {plan.recommended && !isCurrent ? (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full bg-brand px-3 py-1 text-micro font-extrabold uppercase tracking-wide text-content-inverse">
                    Recommended
                  </span>
                ) : null}
                {isCurrent ? (
                  <span className="absolute -top-3 left-1/2 -translate-x-1/2 rounded-full px-3 py-1 text-micro font-extrabold uppercase tracking-wide text-content-inverse"
                    style={{ background: token.status.profit.fg }}
                  >
                    Current
                  </span>
                ) : null}

                {/* fontSize: 22 → --text-title (heading — Q8). A card heading inside a panel
                    may not outrank the panel's own heading, and the card's dominant element is
                    its price — the relationship the old 22-over-36 pair had, at 14-over-28. */}
                {plan.name.available ? (
                  <h3 className="text-title font-extrabold text-content-primary">
                    {plan.name.value}
                  </h3>
                ) : (
                  <h3 className="inline-flex items-center gap-1 text-title text-content-muted">
                    <Marker field={FIELD.cataloguePlanName} reason={plan.name.reason} />
                  </h3>
                )}

                {/* fontSize: 12 → --text-body (sentence — Q1), and the monospace goes with it:
                    a plan summary is prose. */}
                {plan.description.available ? (
                  <p className="mt-1 text-body text-content-muted">{plan.description.value}</p>
                ) : (
                  <p className="mt-1 inline-flex items-center gap-1 text-body text-content-muted">
                    {FIELD.cataloguePlanDescription.label}
                    <Marker
                      field={FIELD.cataloguePlanDescription}
                      reason={plan.description.reason}
                    />
                  </p>
                )}

                {/* fontSize: 36 and 12 → `ds/Metric` tier 1 (--text-figure) and its `unit`.
                    36 is not one of the seven steps; 28 is the largest the declaration holds
                    and `tokens.css:75` reserves it for a tier-1 figure, which this is. The
                    price is denominated by the server's own ISO code — Requirement 4.4
                    finding 3 — and `precision` is the server's `decimals`, not a currency
                    comparison made here. A genuine 0 renders 0, which IS the free tier. */}
                <div className="mt-4">
                  <Metric
                    label={FIELD.cataloguePlanPrice.label}
                    value={plan.price}
                    format="currency"
                    precision={plan.decimals}
                    unit={plan.denomination === null ? undefined : `${plan.denomination} / month`}
                    tier={1}
                    unavailableReason={FIELD.cataloguePlanPrice.reason}
                  />
                </div>

                {/* fontSize: 10 → --text-body (sentence — Q1), +30%. The hardcoded `$` is
                    gone: the charge is stated in the currency the server named it in, with no
                    glyph in front of it that could belong to a different one. */}
                {plan.showsCheckoutConversion ? (
                  <p className="mt-2 text-body text-content-muted">
                    Billed as{' '}
                    {plan.checkoutPrice.available ? (
                      <span className="font-mono tabular-nums text-content-secondary">
                        {Number(plan.checkoutPrice.value).toLocaleString()}
                      </span>
                    ) : (
                      <Marker
                        field={FIELD.cataloguePlanCheckoutPrice}
                        reason={plan.checkoutPrice.reason}
                      />
                    )}{' '}
                    <span className="font-mono text-content-secondary">
                      {plan.checkoutDenomination}
                    </span>{' '}
                    at checkout.
                  </p>
                ) : null}

                {/* fontSize: 12 → --text-body (sentence — Q1). A capability line wraps as
                    prose, so it loses the monospace with the size. */}
                <ul className="mt-4 flex flex-1 list-none flex-col gap-2">
                  {plan.features.slice(0, 6).map((feature) => (
                    <li key={feature} className="flex items-start gap-2">
                      <CheckCircle
                        size={14}
                        aria-hidden="true"
                        className="mt-0.5 shrink-0 text-brand"
                      />
                      <span className="text-body text-content-secondary">
                        {String(feature).replace(/_/g, ' ')}
                      </span>
                    </li>
                  ))}
                </ul>

                {/* fontSize: 12 → `ds/CommandButton`'s own step, and `color: "#000"` with it.
                    `disabledReason` replaces a `title` attribute that no keyboard or screen
                    reader path reached (Requirement 15.3). */}
                <div className="mt-5">
                  <CommandButton
                    intent={isCurrent ? 'secondary' : 'primary'}
                    icon={isCurrent ? CheckCircle : undefined}
                    onClick={() => handleCheckout(plan.id, plan.denomination)}
                    loading={checkoutInFlight === plan.id}
                    loadingLabel="Opening checkout"
                    disabled={checkoutBlockedReason !== null}
                    disabledReason={checkoutBlockedReason ?? undefined}
                    className="w-full"
                  >
                    {isCurrent ? 'Current plan' : 'Subscribe'}
                  </CommandButton>
                </div>
              </div>
            );
          })}
        </div>
      </Panel>

      <div className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* ══ THE STORED PAYMENT METHOD ═════════════════════════════════════ */}
        <Panel
          title="Payment methods"
          state={methods.state}
          loading={{ kind: 'skeleton-cards', rows: 1, label: 'Reading your payment methods' }}
          empty={{
            headline: 'No payment methods on file',
            body: 'Nothing is stored against this account. A card is added during checkout, '
              + 'or in the payment provider\'s own portal.',
            action: { label: 'Read again', onClick: refetchMethods },
          }}
          error={{ error: methods.error, onRetry: refetchMethods }}
          unavailable={{ reason: 'Your stored payment methods cannot be read right now.' }}
          unauthorised={{ error: methods.error }}
          // In `actions` rather than in the body, so the portal stays reachable in the empty
          // and error states — which is where a trader most needs it. fontSize: 11 →
          // `ds/CommandButton`'s own step.
          actions={
            <CommandButton
              intent="secondary"
              icon={ExternalLink}
              onClick={handleOpenPortal}
              loading={isPortalLoading}
              loadingLabel="Opening the billing portal"
            >
              Manage via provider portal
            </CommandButton>
          }
        >
          <div className="flex items-center gap-3 rounded-xl border border-line-default bg-surface-inset p-4">
            {/* The Visa-blue gradient is gone rather than retokened: it painted one company's
                brand colours behind every stored card, whatever the brand actually was. The
                brand is stated in words beside it, which is the channel that cannot be wrong. */}
            <div className="flex shrink-0 items-center justify-center rounded-lg bg-surface-raised p-2.5 text-content-secondary">
              <CreditCard size={20} aria-hidden="true" />
            </div>
            <div className="min-w-0 flex-1">
              {/* fontSize: 13 → --text-body (value — Q7). A card's last four digits are an
                  IDENTIFIER, so Requirement 3.1 keeps the monospace on them. */}
              <p className="flex flex-wrap items-baseline gap-1.5 text-body text-content-primary">
                {cardBrand.available ? (
                  <span className="font-semibold capitalize">{cardBrand.value}</span>
                ) : (
                  <Marker field={FIELD.cardBrand} reason={cardBrand.reason} />
                )}
                <span aria-hidden="true">••••</span>
                {cardLast4.available ? (
                  <span className="font-mono tabular-nums">{cardLast4.value}</span>
                ) : (
                  <Marker field={FIELD.cardLast4} reason={cardLast4.reason} />
                )}
              </p>
              {/* fontSize: 11 → --text-micro for the label (Q4) and --text-body for the value
                  (Q7). It LOSES the monospace: a date is a value but not an identifier, which
                  is `pages/SecurityLogs.jsx`'s precedent from task 7.4. */}
              <p className="mt-0.5 flex flex-wrap items-baseline gap-1 text-micro uppercase tracking-wide text-content-muted">
                {FIELD.cardExpiry.label}
                {cardExpiry.available ? (
                  <span className="text-body normal-case tracking-normal text-content-secondary">
                    {cardExpiry.value}
                  </span>
                ) : (
                  <Marker field={FIELD.cardExpiry} reason={cardExpiry.reason} />
                )}
              </p>
            </div>
            {defaultMethod?.is_default === true ? (
              <StatusBadge state="ok" label="Default" />
            ) : null}
          </div>
        </Panel>

        {/* ══ THE INVOICE HISTORY ═══════════════════════════════════════════
            fontSize: 11 and 9 → `ds/DataTable`'s `--text-small` body and `--text-micro`
            `<th>`s. §2.4 orders Q4 ahead of Q6 exactly so a `<th>` is read as a label rather
            than as a cell. The table's blanket monospace goes with it — the primitive renders
            the mono treatment per column format — and the reference cell keeps an explicit
            one, because it is an identifier.
            fontSize: 12 → `ds/Panel`'s `empty` arm. *No invoices found.* used to render for a
            500 as well as for an account with no invoices, because the `Promise.allSettled`
            branch had no `else`: those are `error` and `empty` now. */}
        <Panel
          title="Billing history"
          state={invoices.state}
          loading={{ kind: 'skeleton-table', rows: 5, columns: 4, label: 'Reading your invoices' }}
          empty={{
            headline: 'No invoices yet',
            body: 'Nothing has been billed to this account. An invoice appears here after the '
              + 'first charge settles.',
            action: { label: 'Read again', onClick: refetchInvoices },
          }}
          error={{ error: invoices.error, onRetry: refetchInvoices }}
          unavailable={{ reason: 'Your invoice history cannot be read right now.' }}
          unauthorised={{ error: invoices.error }}
        >
          <DataTable
            caption="Invoices billed to this account, newest first"
            columns={INVOICE_COLUMNS}
            rows={invoiceRows}
            pageSize={0}
            density="compact"
          />
        </Panel>
      </div>
    </div>
  );
}
