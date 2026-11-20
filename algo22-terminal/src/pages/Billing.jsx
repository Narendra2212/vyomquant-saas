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
 *      a figure in a DIFFERENT currency from the one being labelled for a genuine `0`:
 *      `base_price` is the published price in `base_currency` (rupees — see
 *      `subscription_engine.PRICE_BASE_CURRENCY`), while `checkout_price` is what the gateway
 *      will charge in `checkout_currency`. The two are the same number only for an Indian
 *      account. It read as USD at the time this was written, because the catalogue then
 *      published a second, unreconciled dollar column; that column is gone, and the substitution
 *      is wrong for the same reason either way.
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
 * BILL-HIST-02 (2026-11-22): THE LEDGER NOW RENDERS WHAT IT IS SENT
 * ═══════════════════════════════════════════════════════════════════════════
 * Three things changed on the billing-history half of this page. The sections below are the
 * task-7.9 record of an earlier migration and are left as they were measured then; this is
 * what is different now.
 *
 *   1. **Three fields were being discarded.** `GET /api/billing/invoices` has carried
 *      `provider`, `providerPaymentId` and `plan` since BILL-HIST-01, and migration 070
 *      declared the five columns behind them — the ones the Razorpay webhook writer had
 *      always named and `billing_invoices` never had, so until it was applied every invoice
 *      write failed silently inside a `try/except logger.warning` and the table was empty by
 *      construction. `invoiceRows` projected six keys and dropped the rest, and
 *      `INVOICE_COLUMNS` had four. So the ledger could state that ₹999 had been taken and
 *      not what it bought, which gateway captured it, or any reference to quote about it.
 *      Two columns were added — *Plan* and *Paid via* — and all three fields are projected.
 *      Each is nullable on purpose and each renders its declared marker when null: a row
 *      written before 070 genuinely does not know its provider, and `'razorpay'` is the one
 *      substitution that would look right on every row and be checkable on none.
 *   2. **The *Payment methods* panel is deleted.** Not hidden: no state renders it. It read
 *      `GET /api/billing/payment-methods`, which answers `[]` on every production account —
 *      VyomQuant stores no payment instrument, Razorpay Checkout collects it at purchase and
 *      never hands it over — so its empty arm was its only reachable state and a standing
 *      panel with that heading told every subscriber this product keeps their card on file
 *      and cannot currently read it. The read went with it; the endpoint and both client
 *      methods did not. The instrument is NOT relocated into the invoice rows: joining the
 *      account's current card onto a historical charge is §6's fabrication one level down,
 *      so it is declared `UNAVAILABLE` per row instead.
 *   3. **The five-row cap is gone.** `rows.slice(0, 5)` cut the ledger with no pagination, no
 *      count and nothing on screen admitting the cut. `ds/DataTable` pages it now.
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
 *   methods      → the stored payment method — GONE, see BILL-HIST-02 below
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
 *      *Subscribe* / *Current Plan* (:767), and the payment-methods panel's own portal
 *      command (:803). All seven were `ds/CommandButton`s.
 *
 *      **The seventh is GONE, and its old name here was wrong twice over.** It was recorded
 *      in this list as *Manage via Stripe Portal*, and it named a provider this platform does
 *      not bill through: Razorpay is the configured one. It later read *Manage via provider
 *      portal*, which was no truer — Razorpay publishes no hosted self-serve portal, so the
 *      control had nothing to open. `POST /api/billing/portal` opened with
 *      `_validate_keys("stripe")`, which raises on a deployment with no live Stripe secret,
 *      so pressing it answered 500 every single time. It is removed rather than relabelled,
 *      because no label makes a button that cannot succeed into one that can. The counts
 *      below are left as the task-7.9 measurement of the file AS IT WAS THEN — they are a
 *      historical record of that migration, and editing them to match today's file would
 *      misstate what the scan measured.
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
 * `openPortal` is the one exception, and it is a REMOVAL of a broken control rather than of a
 * capability: there was no capability behind it. `POST /api/billing/portal` is Stripe-only and
 * Razorpay — the provider this platform actually bills through — publishes no hosted portal at
 * all, so the payment-methods panel's *Manage via provider portal* button could only ever
 * answer 500. It is gone.
 *
 * BILLING-PORTAL-02 (2026-11-15) finished that removal. The sentence that stood here said
 * `handleOpenPortal` "stays for the two call sites outside that panel (the `past_due` alert's
 * *Update payment method* and the current-plan panel's *Manage billing*), which now surface
 * the server's own structured refusal". Surfacing a well-written refusal is still a refusal:
 * both controls answered `501 PROVIDER_HAS_NO_BILLING_PORTAL` every time they were pressed,
 * and one of them was the only control offered to a subscriber whose payment had just failed.
 * Both are gone, and so are `handleOpenPortal`, `isPortalLoading` and
 * `api.billing.openPortal`. The endpoint stays and still answers 501; no URL is invented
 * anywhere, and nothing about subscribe, renew, cancel, resume or webhook handling moved.
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
  // `CreditCard` left with BILL-HIST-02: its only use was the glyph in the *Payment methods*
  // panel's instrument card, and that panel is gone. No icon replaces it in the invoice
  // table — a card glyph beside a gateway name would imply a card was involved, which is
  // exactly what this page does not know.
  Crown,
  Database,
  // `ExternalLink` left with BILLING-PORTAL-02: its only two uses were the icons on
  // *Manage billing* and *Update payment method*, and nothing on this page opens an
  // external destination any more.
  FlaskConical,
  Gauge,
  GitBranch,
  Globe,
  LineChart,
  Link2,
  MapPin,
  RefreshCw,
  Shield,
  ShoppingCart,
  SlidersHorizontal,
  Star,
  TrendingUp,
  Wallet,
  XCircle,
  Zap,
} from 'lucide-react';

import { Alert } from '../components/ds/Alert';
import { CommandButton } from '../components/ds/CommandButton';
import { MfaStepUpDialog } from '../components/MfaStepUpDialog';
import { isMfaStepUpRequired } from '../lib/mfaStepUp';
import { SUBSCRIPTION_CONTROL, subscriptionControlFor } from '../lib/billingControls';
import { DataTable } from '../components/ds/DataTable';
import { Metric, NotAvailableMarker } from '../components/ds/Metric';
import { PageHeader } from '../components/ds/PageHeader';
import { Panel } from '../components/ds/Panel';
import { StatusBadge } from '../components/ds/StatusBadge';
import { api, isAuthenticated } from '../api';
import { overCapacity, resourceLabel } from '../design/entitlements';
import { refreshEntitlements } from '../hooks/useEntitlements';
import { PAGES, PAGE_FIELDS_BY_PAGE, VERDICT } from '../design/pageFields';
import { available, fromNullable, unavailable } from '../design/reported';
import { token } from '../design/tokens';
import { PANEL_STATES, usePanelState } from '../hooks/usePanelState';
import { REFRESH } from '../hooks/useAutoRefresh';
import {
  CheckoutOutcome,
  canOpenRazorpayCheckout,
  runRazorpayCheckout,
} from '../utils/razorpayCheckout';
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

/**
 * The four allowance tiles that have DECLARED `pageFields` entries, keyed by resource.
 *
 * These four predate the five-plan ladder and keep their declarations, so their labels and their
 * absence reasons still come from `design/pageFields.js` exactly as before. The ladder added nine
 * more metered resources — paper and live strategies, exchange connections, trading accounts, ML
 * models, backtests, optimization runs, marketplace subscriptions and marketplace listings — and
 * those are rendered from the server's own vocabulary instead; see {@link allowanceIcon} and the
 * `allowances` memo for why that is not a shortcut.
 */
const DECLARED_ALLOWANCES = Object.freeze({
  strategies: { icon: Database, used: 'strategiesUsed', quota: 'strategiesQuota' },
  bots: { icon: Bot, used: 'botsUsed', quota: 'botsQuota' },
  ml_trainings: { icon: Cpu, used: 'mlTrainingsUsed', quota: 'mlTrainingsQuota' },
  marketplace_published: {
    icon: TrendingUp,
    used: 'marketplacePublishedUsed',
    quota: 'marketplacePublishedQuota',
  },
});

/** The glyph for a resource tile. A treatment, not a figure — so a miss is `Database`, not an error. */
const ALLOWANCE_ICONS = Object.freeze({
  strategies: Database,
  paper_strategies: FlaskConical,
  bots: Bot,
  exchange_connections: Link2,
  trading_accounts: Wallet,
  custom_indicators: SlidersHorizontal,
  strategy_versions: GitBranch,
  ml_models: Cpu,
  ml_trainings: Cpu,
  backtests: LineChart,
  optimizations: Gauge,
  marketplace_subscriptions: ShoppingCart,
  marketplace_published: TrendingUp,
});

const allowanceIcon = (resource) => ALLOWANCE_ICONS[resource] ?? Database;

/**
 * What an absent allowance figure says when the server named no reason of its own.
 *
 * Worded about the READ rather than about the account, for the same reason
 * `design/reported.js::UNREPORTED_REASON` is: "you have none of these" and "we could not find out"
 * look identical on screen and mean opposite things, and only one of them is something this page
 * actually knows.
 */
const QUOTA_UNREAD_FALLBACK = 'Your entitlements could not be read, so this allowance is not '
  + 'shown rather than guessed. Refresh reads them again.';

/** `'month'` → `'Monthly'`. The server's own value, or `null` when it recorded none. */
const intervalLabel = (value) => {
  if (value === 'month') return 'Monthly';
  if (value === 'year') return 'Annual';
  return null;
};

/** `-1` and `Infinity` are the server's unlimited sentinels, and both are readings. */
const isUnlimited = (value) => value === -1 || value === Infinity;

/**
 * A safe, client-facing sentence for a failed WRITE. The page's own `detail` read.
 *
 * `err.data` is read FIRST because that is where this app's errors actually carry the body:
 * `apiClient`'s response interceptor rejects with an `ApiError` whose constructor assigns
 * `this.data = config?.data` and which has no `response` property at all. So the original
 * `err?.response?.data?.detail` was `undefined` on every rejection this client produces, and
 * this helper always returned the caller's generic fallback.
 *
 * That is not cosmetic. It is why a production checkout failing because the container had no
 * Razorpay key configured displayed as "Checkout initialization failed." — the server named
 * the cause and the page discarded it. (`billing._validate_keys` now answers "Razorpay is not
 * configured: RAZORPAY_KEY_ID is not set." for that case specifically, rather than one
 * sentence covering every possible cause.)
 * `ds/Alert` renders whatever is returned here, so the server's own sentence reaches the
 * trader instead of a message that could mean anything.
 *
 * The axios-native `err.response.data.detail` shape is still read second, since a caller that
 * bypasses the interceptor (or a future transport) can still produce it.
 */
const writeFailureMessage = (err, fallback) => {
  const candidates = [err?.data?.detail, err?.response?.data?.detail];
  for (const candidate of candidates) {
    if (typeof candidate === 'string' && candidate.trim() !== '') return candidate;
  }
  // `detail` as the CODED OBJECT this repo's routers raise —
  // `HTTPException(status_code=…, detail={"error": "STABLE_CODE", "message": "…"})`, the shape
  // `routers/strategy_operations.py` uses throughout and the shape `POST /api/billing/portal`
  // and `POST /api/billing/payment-methods` now refuse with. Read for the same reason `detail`
  // is read before `response.data.detail` above: without this arm the object fails the
  // `typeof === 'string'` test, falls past the marketplace envelope, and the server's named
  // reason ("Razorpay … publishes no hosted self-serve billing portal") is discarded in favour
  // of the caller's generic fallback — the precise defect the comment above this function was
  // written about, one envelope later. The `error` CODE is deliberately not shown to the
  // trader: it is for a client to branch on, and the `message` is the sentence for a person.
  const coded = [err?.data?.detail?.message, err?.response?.data?.detail?.message];
  for (const candidate of coded) {
    if (typeof candidate === 'string' && candidate.trim() !== '') return candidate;
  }
  // The structured marketplace envelope, `{ error: { code, message } }`, which the shared
  // error handler in `main.py` renders for `MarketplaceError`. Read the same way
  // `StrategyMarketplace.jsx` reads it, so the two pages agree on one envelope.
  const structured = err?.data?.error?.message;
  if (typeof structured === 'string' && structured.trim() !== '') return structured;
  return fallback;
};

/**
 * How many invoices one page of the ledger holds.
 *
 * BILL-HIST-02 REPLACED A HARD `.slice(0, 5)` WITH THIS. The projection used to cut the list
 * to five rows with no pagination, no count and nothing on screen saying it had been cut, so
 * an account with eight charges could read its sixth-oldest nowhere in the product — on the
 * only surface that records what it has been billed. Five is also below one year of monthly
 * invoices, which is the span someone reconciling a year reaches for first.
 *
 * PAGINATED rather than "raise the cap", deliberately. `ds/DataTable` already owns paging and
 * announces `Showing 1–12 of 37` in a live region, so paging costs one `useState` and makes
 * the total visible; raising the cap to some larger number would leave the same silent
 * truncation one order of magnitude further out, and an unbounded list would render every row
 * a long-lived account has. 12 is one year of monthly billing on the first page.
 *
 * The server is the real bound and it is not this page's to choose: `get_invoices` applies
 * `.limit(200)`, so 200 rows is the most this ledger can hold whatever happens here. That is
 * ~16 pages, and it is the one truncation a reader cannot see from the footer — recorded here
 * rather than worked around, because widening it is a backend change.
 */
const INVOICE_PAGE_SIZE = 12;

/**
 * `'razorpay'` → `'Razorpay'`. The server's own value, capitalised for display.
 *
 * Same shape as `planLabel` and for the same reason: this presents the recorded provider
 * rather than mapping it to a nicer name, so a provider this page has never heard of still
 * renders as whatever the row says instead of falling through to a default. There is no
 * fallback arm — an unrecorded provider is an absence, not `'Razorpay'`.
 */
const providerLabel = (provider) =>
  typeof provider === 'string' && provider.trim() !== ''
    ? `${provider.trim().charAt(0).toUpperCase()}${provider.trim().slice(1)}`
    : null;

/**
 * An identifier, shortened for a cell, with its full value kept for the `title`.
 *
 * design.md §2.5's fourth yield mechanism: truncating an IDENTIFIER is permitted as long as
 * the whole value stays reachable. The invoice reference cell has always done this inline;
 * BILL-HIST-02 added a second identifier column (the gateway's payment id) and the two must
 * shorten the same way, so the expression is named once.
 */
const SHORT_ID_CHARS = 8;
const shortId = (value) => `${String(value).slice(0, SHORT_ID_CHARS)}…`;

/**
 * The invoice columns.
 *
 * `render` is where the marker lives: `ds/DataTable` renders a bare `—` for a blank cell, and
 * Requirement 19.3's point is that the reason is the load-bearing half, so every cell renders
 * through the one marker with its declared sentence. Every cell CAN be absent here — a money
 * table with no denominator for its own amounts is the case §6 is written about.
 *
 * BILL-HIST-02 TOOK THIS FROM FOUR COLUMNS TO SIX. `GET /api/billing/invoices` has been
 * sending `provider`, `providerPaymentId` and `plan` since BILL-HIST-01, and migration 070
 * declared the columns behind them in production, so the data was on the wire and this page
 * dropped it: the ledger showed that ₹999 was taken and not what it bought or which charge at
 * the gateway it was. The two new columns are `plan` (what the payment bought) and `payment`
 * (the gateway that captured it, plus that gateway's own payment reference).
 *
 * `priority: 3` on the pair, which `ds/DataTable` drops below `--breakpoint-laptop`. The four
 * original columns answer "was I charged, when, how much, did it settle" and stay at every
 * width; the two new ones answer "what for, and which charge is it" — needed to reconcile,
 * not to read the ledger — and `pages/TradeHistory.jsx` set the same precedent for fees,
 * slippage and strategy. A dropped column is still reachable: the primitive moves it into the
 * row's expandable detail rather than deleting it.
 */
const INVOICE_COLUMNS = Object.freeze([
  {
    key: 'reference',
    header: FIELD.invoiceReference.label,
    priority: 1,
    // The truncation survives and the false affordance does not: design.md §2.5's fourth
    // yield mechanism permits truncating an IDENTIFIER with its full value on a `title`, and
    // the old `cursor: pointer` pointed at no handler and no keyboard path.
    render: ({ value, row }) =>
      value.available ? (
        <span
          className="font-mono text-brand"
          title={row?.fullReference ?? undefined}
        >
          {shortId(value.value)}
        </span>
      ) : (
        <Marker field={FIELD.invoiceReference} reason={value.reason} />
      ),
  },
  {
    key: 'date',
    header: FIELD.invoiceDate.label,
    priority: 1,
    render: ({ value }) =>
      value.available ? (
        <span className="text-content-secondary">{value.value}</span>
      ) : (
        <Marker field={FIELD.invoiceDate} reason={value.reason} />
      ),
  },
  {
    // WHAT THE CHARGE BOUGHT. `billing_invoices.plan` — this row's own description of
    // itself, and the column the refund path reads to decide whether a full refund revokes
    // the plan. It is NOT the account's current plan, and `entitlements.plan` is deliberately
    // not read here: an invoice for a tier the account has since left still describes what
    // that charge bought, and joining today's subscription onto last year's money would
    // relabel history to match the present.
    key: 'plan',
    header: FIELD.invoicePlan.label,
    priority: 3,
    render: ({ value }) =>
      value.available ? (
        <span className="capitalize text-content-primary">{planLabel(value.value)}</span>
      ) : (
        <Marker field={FIELD.invoicePlan} reason={value.reason} />
      ),
  },
  {
    key: 'amount',
    header: FIELD.invoiceAmount.label,
    align: 'numeric',
    priority: 1,
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
    /*
      HOW THE CHARGE WAS PAID, AS FAR AS THIS PLATFORM RECORDS IT.

      Two readings in one cell because they are read together and are useless apart: the
      gateway that captured the money, and that gateway's own payment id. The id is the value
      a trader quotes to support or matches against a statement line, and it is unique per
      provider (`uq_billing_invoices_provider_payment`), so it identifies one charge exactly.

      THREE THINGS THIS CELL WILL NOT DO:

        * It does not default `provider` to `'Razorpay'`. Razorpay being the only gateway is
          precisely why that default is dangerous — it is the one value that would look right
          on every row and be checkable on none. A row that predates migration 070 says it
          does not know, through the declared marker, with the reason saying why.
        * It does not name the INSTRUMENT. No card network, masked number or UPI handle is
          recorded against an invoice: Razorpay Checkout collects it at purchase and never
          hands it over. `GET /api/billing/payment-methods` does report a brand and a last
          four, and joining TODAY'S default instrument onto a HISTORICAL charge is the exact
          fabrication §6 is about — it would attribute a payment to a card that may not have
          existed when it was taken. `FIELD.invoiceInstrument` is declared UNAVAILABLE and its
          marker renders here, beside the provider, so the cell states the limit of what is
          known rather than leaving a reader to assume the gateway line covers the card.
        * It does not render `0`, a blank or a placeholder for any of the three.
    */
    key: 'payment',
    header: FIELD.invoiceProvider.label,
    priority: 3,
    /*
      THE CELL'S VALUE IS A COMPOSITE, AND THAT IS WHAT MAKES THE MEMO CORRECT.
      `ds/DataTable` memoises a row on its PROJECTED cell values, so a second reading pulled
      off `row` behind this column's back would not re-render when only that reading changed.
      Both are projected under this one key instead — see `invoiceRows` — so a row whose
      payment id changes is a row whose `payment` value changed. (The `reference` column's
      `row.fullReference` is safe for the opposite reason: it is derived from the same `id`
      the column itself projects, so it cannot change on its own.)
    */
    render: ({ value }) => (
      <span className="flex flex-col gap-0.5">
        <span className="flex flex-wrap items-baseline gap-1.5">
          {value.provider.available ? (
            <span className="text-content-primary">{providerLabel(value.provider.value)}</span>
          ) : (
            <Marker field={FIELD.invoiceProvider} reason={value.provider.reason} />
          )}
          {/* The instrument's absence, stated beside the gateway rather than left to be
              inferred from it. `--text-micro` for the label (§2.4 Q4); the marker carries the
              sentence that explains why nothing will ever fill it.
              `aria-hidden` on the visible label because `ds/NotAvailableMarker`'s accessible
              name is already `"{label}: not available"` — without it a screen reader reads
              "Instrument Instrument: not available". */}
          <span
            aria-hidden="true"
            className="text-micro uppercase tracking-wide text-content-muted"
          >
            {FIELD.invoiceInstrument.label}
          </span>
          <Marker field={FIELD.invoiceInstrument} />
        </span>
        {/* The gateway's payment id: an IDENTIFIER, so Requirement 3.1 keeps the monospace,
            and truncated with the whole value on `title` exactly as the invoice reference is. */}
        {value.reference.available ? (
          <span
            className="font-mono text-micro text-content-secondary"
            title={value.fullReference ?? undefined}
          >
            {shortId(value.reference.value)}
          </span>
        ) : (
          <span className="flex flex-wrap items-baseline gap-1 text-micro text-content-muted">
            {/* `aria-hidden` for the same reason as the instrument label above: the marker's
                own accessible name already names the field. */}
            <span aria-hidden="true">{FIELD.invoicePaymentReference.label}</span>
            <Marker
              field={FIELD.invoicePaymentReference}
              reason={value.reference.reason}
            />
          </span>
        )}
      </span>
    ),
  },
  {
    key: 'status',
    header: FIELD.invoiceStatus.label,
    priority: 1,
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
  /**
   * The billing cycle a checkout from this page will buy. `'month'` or `'year'`.
   *
   * Local state and not a reader parameter: `GET /api/billing/plans` returns BOTH cycles priced in
   * one payload (each plan carries `annual` alongside its monthly figure), so switching the toggle
   * is a re-render of figures already in hand rather than a second request. That matters beyond
   * latency — two requests could be priced at two different exchange rates for a non-published
   * currency, and a toggle that changed the monthly price while the trader was comparing it to the
   * annual one would be showing them two different offers.
   */
  const [checkoutInterval, setCheckoutInterval] = useState('month');
  const [isCancelLoading, setIsCancelLoading] = useState(false);
  const [isResumeLoading, setIsResumeLoading] = useState(false);
  // BILLING-PORTAL-02: no `isPortalLoading`. Nothing opens a portal from this page any more.
  /** One `{ severity, message }`, where `billingError` and `actionSuccess` were two strings. */
  const [notice, setNotice] = useState(null);

  /*
    MFA-STEPUP-02. The pending AAL2 step-up: `{ label, operation }` or `null`.

    `POST /api/billing/resume` carries `require_aal2` (`routers/billing.py`, SEC-15), so a
    session at `aal1` is refused with a 403 the customer CAN clear. Before this, that refusal
    reached the notice band as a flat failure — the customer was told resuming did not work,
    with no way to do the thing the server was actually asking for. `pages/ExchangeManager.jsx`
    already ran the challenge in place for the same refusal; this is that workflow, here.

    `operation` is a thunk, so the retry re-runs the exact request that was refused.

    DELIBERATELY NOT WIRED TO CANCELLATION. `POST /api/billing/cancel` is ungated on purpose
    and asserted so: gating it would mean a customer who never enrolled a factor could not
    stop their own billing. There is no refusal to clear there, so a step-up in front of it
    would be a challenge the server never asks for — see `lib/mfaStepUp.js`.
  */
  const [stepUp, setStepUp] = useState(null);

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

  // ── The three reads (Requirement 4.1) ─────────────────────────────────────
  //
  // `deps: []` on each, so each fires once on mount exactly as the `Promise.allSettled` did.
  // No parameter added or changed on any of them. It was FOUR until BILL-HIST-02 removed the
  // stored-methods read along with the only panel that rendered it.
  const readPlans = useCallback(
    () => api.billing.getPlans(plansCurrencyRef.current ?? undefined),
    [],
  );
  const readEntitlements = useCallback(() => api.billing.getEntitlements(), []);
  const readInvoices = useCallback(() => api.billing.getInvoices(), []);
  /*
    NO `readMethods`. REMOVED BY BILL-HIST-02, with the *Payment methods* panel that was its
    only reader.

    `GET /api/billing/payment-methods` answers `[]` on every Razorpay deployment — VyomQuant
    stores no payment instrument, as `api/modules/billing.js` records against
    `deletePaymentMethod` — so the panel's only reachable state was its own empty arm and the
    read's only effect was one request per mount plus one per billing socket frame. Keeping
    the read with no panel to fill would be a request whose response reaches nothing.

    THE ROUTE AND THE CLIENT METHOD BOTH STAY. `api.billing.getPaymentMethods` and
    `deletePaymentMethod` are untouched: the endpoint is honest, other builds call it, and a
    deployment that one day stores an instrument needs it. This removes a reader, not a
    capability. It does change this surface's API-call set, which is why
    `tests/regression/baseline/surface_billing.json` is re-recorded in the same commit.
  */
  const plans = usePanelState(readPlans, { deps: [] });
  const entitlements = usePanelState(readEntitlements, { deps: [], intervalMs: REFRESH.SLOW });
  const invoices = usePanelState(readInvoices, { deps: [], intervalMs: REFRESH.SLOW });

  // Stable by `usePanelState`'s second inversion, so the socket effect below can depend on
  // them without being torn down and rebuilt on every render.
  const refetchPlans = plans.refetch;
  const refetchEntitlements = entitlements.refetch;
  const refetchInvoices = invoices.refetch;

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
   * Re-read all three. The same three requests the mount issues, and nothing else.
   *
   * `loadBilling` + `loadPlans` before the migration, in one function because the two callers
   * — the socket handler and the Refresh control — both called both. It re-read FOUR until
   * BILL-HIST-02 removed the stored-methods read, so a billing frame no longer issues a
   * request whose response nothing renders.
   */
  const refreshSubscription = useCallback(() => {
    refetchEntitlements();
    refetchInvoices();
    refetchPlans();
    /*
      And the SHARED entitlement snapshot every gate in the app reads
      (`hooks/useEntitlements.js`). Without this line a plan change would be visible on this page
      and nowhere else until the snapshot went stale on its own — the sidebar, the account menu and
      every `FeatureGate` would keep showing the old plan's locked states to a trader who had just
      paid to unlock them. This page is where billing changes happen, so it is where the rest of
      the app has to be told.
    */
    refreshEntitlements();
  }, [refetchEntitlements, refetchInvoices, refetchPlans]);

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
    replacement rather than a `supabase.auth` user lookup for two reasons — it is authenticated
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
        `interval` is the billing cycle the trader selected above the catalogue. It is sent on
        every checkout, including a monthly one, rather than being omitted for the default:
        `CheckoutRequest.interval` defaults to `"month"` server-side, so omitting it would work —
        but sending it makes the request state the cycle it is buying, and the server prices from
        its own published annual figure for that cycle rather than from anything computed here.
        The amount is never calculated in this bundle; see `FXService.localize_plan_price`.
      */
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
        interval: checkoutInterval,
      });

      /*
        Razorpay Standard Checkout, taken whenever the server sent both an `order_id` and the
        publishable `key_id`. Preferred over the hosted link for two reasons: it keeps the
        trader on this page, and the hosted link is the weaker path — when Razorpay's
        payment-link call failed, `billing.py` used to answer with
        `/app/billing?payment=success`, a URL that states a payment succeeded on exactly the
        branch where the gateway call did not. That fallback is gone server-side and
        `checkoutUrl` is now null there, so this branch is what serves those checkouts.

        `amount` and `currency` are not read from `data` for anything but display inside the
        modal: the charge is whatever the order says, and the order was priced server-side
        from `SubscriptionEngine` + `FXService`. Nothing here can alter it.
      */
      if (data?.provider === 'razorpay' && canOpenRazorpayCheckout(data)) {
        const outcome = await runRazorpayCheckout({
          session: data,
          verify: api.billing.verifyPayment,
          description: `Aerora Dynamics — ${String(planId).toUpperCase()}`,
        });

        if (outcome.status === CheckoutOutcome.VERIFIED) {
          /*
            `info`, not a success banner claiming the plan is live. The signature only proves
            the callback was genuine; the subscription moves when the gateway's
            `payment.captured` webhook reaches `_process_razorpay_entitlement`. So the page
            re-reads its own state instead of assuming the new tier — if the webhook has
            already landed, this refresh shows it, and if it has not, the trader sees the
            plan they still have rather than one the server has not granted.
          */
          setNotice({ severity: 'info', message: outcome.message });
          refreshSubscription();
        } else if (outcome.status === CheckoutOutcome.DISMISSED) {
          // A deliberate close is not a failure. Reporting it as one is what produces
          // "payment failed" banners for traders who simply changed their mind.
          setNotice({ severity: 'info', message: outcome.message });
        } else {
          // FAILED (declined instrument), UNVERIFIED (signature did not check out) and
          // UNAVAILABLE (SDK could not load) each carry their own sentence, and each of them
          // already says that nothing was charged where that is true.
          setNotice({ severity: 'error', message: outcome.message });
        }
        return;
      }

      if (data && data.checkoutUrl) {
        // A Razorpay checkout that produced a hosted link rather than only the modal.
        // (This read "Stripe, and any Razorpay checkout that did produce a hosted link";
        // Stripe has been removed from the product in full, so Razorpay is the only
        // source a checkoutUrl can now come from.)
        window.location.href = data.checkoutUrl;
        return;
      }

      throw new Error(
        'The payment gateway returned no way to complete this checkout. Nothing has been charged.',
      );
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
      /*
        MFA-STEPUP-02: the refusal a customer can clear is handled here rather than
        reported as a failure. `require_aal2` answers 403 for an `aal1` session, and
        resuming is a real action the customer is entitled to take — they just have to
        confirm it. The step-up runs the challenge and then re-issues THIS request.

        `setIsResumeLoading(false)` still happens in `finally`, so the control is live
        again behind the dialog and the retry can set its own loading state.
      */
      if (isMfaStepUpRequired(err)) {
        setStepUp({ label: 'resume this subscription', operation: handleResume });
        return;
      }
      setNotice({
        severity: 'error',
        message: writeFailureMessage(err, 'Failed to resume subscription.'),
      });
    } finally {
      setIsResumeLoading(false);
    }
  };

  /*
    BILLING-PORTAL-02 (2026-11-15): `handleOpenPortal` is GONE, and with it the last two
    controls that called it — *Manage billing* in the current-plan panel and *Update payment
    method* in the payment-failed alert.

    An earlier pass (BILLING-PORTAL-01, see the docblock at the head of this file) removed the
    payment-methods panel's portal button and left this handler "for the two call sites outside
    that panel". That was the wrong half to keep. There is no portal behind ANY of the three:
    `POST /api/billing/portal` answers `501 PROVIDER_HAS_NO_BILLING_PORTAL`
    (`routers/billing.py:3042`) because Razorpay publishes no hosted self-serve billing portal
    and the branch that once created a `stripe.billing_portal.Session` went with Stripe. So
    both remaining buttons could only ever open a toast reporting their own failure.

    The endpoint itself STAYS. Removing the control is not removing the provider integration:
    subscribe, renew, cancel, resume and webhook processing are untouched, and the endpoint
    keeps answering 501 for any client that still asks.

    What replaces the payment-failed button is a sentence, not another button. Under Razorpay
    the instrument is supplied to Checkout at the moment of purchase and is never held by
    VyomQuant, so the real remedy for a failed charge is to buy again and choose a different
    instrument at that checkout — which the plan buttons further down this page already do
    through `handleCheckout`. A control that merely restated the remedy in a modal would be
    the same dead end with more steps.
  */

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
    BILL-CANCEL-01. Which subscription control the panel offers, decided in
    `lib/billingControls.js` so the rule can be tested without mounting this page.

    `cancellationIsScheduled` requires the flag to be BOTH reported and true. An unreported
    `cancel_at_period_end` therefore means "not scheduled as far as this page knows", which
    keeps Cancel reachable rather than rendering neither control -- see that module.
  */
  const subscriptionControl = subscriptionControlFor({
    planIsPaid,
    isCancelled,
    cancellationIsScheduled: cancelAtPeriodEnd.available && cancelAtPeriodEnd.value === true,
  });

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

  /**
   * The plan's published name, as the server states it.
   *
   * `null` when the server sent none, which is when the derived `planLabel(plan)` is used instead.
   * Not defaulted to anything: a plan name is a figure like any other on this surface.
   */
  const displayName = (() => {
    const value = read(entitlementsBody, 'display_name');
    return typeof value === 'string' && value.trim() !== '' ? value.trim() : null;
  })();

  /**
   * The billing cycle the account is on, or `null`.
   *
   * `null` is NOT monthly. Every subscription taken before annual billing existed has no recorded
   * cycle (`profiles.billing_interval` is nullable with no default and no backfill — migration
   * 017), so rendering "Monthly" for an absent value would state a fact about the subscription that
   * nothing established. The chip simply does not appear until a renewal records one.
   */
  const billingCycle = intervalLabel(read(entitlementsBody, 'billing_interval'));

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

  /**
   * Every allowance the server reports, each figure resolved to a `Reported` union.
   *
   * DRIVEN BY THE SERVER'S OWN `quotas`, NOT BY A LIST HERE
   * ------------------------------------------------------
   * This used to iterate a fixed array of four. The plan ladder meters thirteen resources, and a
   * hardcoded list would have shown a trader four of their thirteen limits while the backend
   * enforced all thirteen — the page would have been silent about exactly the limits most likely
   * to surprise them (backtests, optimization runs, marketplace subscriptions). Iterating
   * `quotas` means a resource the backend adds appears here without this file changing.
   *
   * THE FOUR DECLARED FIELDS KEEP THEIR DECLARATIONS
   * -----------------------------------------------
   * Where a `pageFields.js` entry exists, the label and the absence reason come from it, unchanged.
   * For the nine newer resources the label comes from `design/entitlements.resourceLabel` and the
   * absence reason comes from the SERVER, in `usage_unavailable[resource]` — which is the same
   * discipline the declarations enforce, sourced one step closer to the cause. Neither path can
   * substitute a zero: an unreadable figure stays unreadable and renders the marker.
   *
   * `isMetered` carries the server's `metered_resources` list, because a monthly allowance that
   * does not say "this month" is read as a lifetime total.
   */
  const allowances = useMemo(() => {
    const quotas = entitlementsBody?.quotas;
    if (!quotas || typeof quotas !== 'object') return [];
    const unreadable = entitlementsBody?.usage_unavailable ?? {};
    const metered = Array.isArray(entitlementsBody?.metered_resources)
      ? entitlementsBody.metered_resources
      : [];

    return Object.keys(quotas).map((resource) => {
      const declared = DECLARED_ALLOWANCES[resource];
      if (declared) {
        return {
          key: resource,
          icon: declared.icon,
          usedField: FIELD[declared.used],
          quotaField: FIELD[declared.quota],
          used: reported(entitlementsBody, FIELD[declared.used]),
          quota: reported(entitlementsBody, FIELD[declared.quota]),
          isMetered: metered.includes(resource),
        };
      }

      // The server's reason for an absent figure, or a statement that it reported none. Never a
      // sentence that guesses at the cause.
      const reason = typeof unreadable[resource] === 'string' && unreadable[resource].trim() !== ''
        ? unreadable[resource]
        : QUOTA_UNREAD_FALLBACK;
      const label = resourceLabel(resource);

      return {
        key: resource,
        icon: allowanceIcon(resource),
        usedField: { label, reason },
        quotaField: { label: 'Limit', reason },
        used: Object.prototype.hasOwnProperty.call(unreadable, resource)
          ? unavailable(reason)
          : fromNullable(entitlementsBody?.usage?.[resource], reason),
        quota: fromNullable(quotas[resource], reason),
        isMetered: metered.includes(resource),
      };
    });
  }, [entitlementsBody]);

  /** The resources this account currently exceeds, after a downgrade. The server's own figures. */
  const exceeded = useMemo(() => overCapacity(entitlementsBody), [entitlementsBody]);

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
      /*
        The annual block the server prices alongside the monthly one. `null` for a plan with no
        published annual price (Free, and the custom Enterprise tier), which is what stops this page
        inventing one by multiplying the monthly figure by twelve — ₹4,990 a year is not ₹499 × 12,
        and only one of those two numbers is charged.
      */
      const annual = plan?.annual && typeof plan.annual === 'object' ? plan.annual : null;
      const annualPrice = annual
        ? fromNullable(annual.localized_price, FIELD.cataloguePlanPrice.reason)
        : unavailable('This plan has no published annual price.');

      return {
        id: plan?.id ?? `plan-${index}`,
        recommended: plan?.recommended === true,
        name: fromNullable(plan?.name, FIELD.cataloguePlanName.reason),
        description: fromNullable(plan?.description, FIELD.cataloguePlanDescription.reason),
        // ── The ladder, as the server publishes it. No plan table in this bundle. ──
        journey: typeof plan?.journey === 'string' ? plan.journey : null,
        tagline: typeof plan?.tagline === 'string' ? plan.tagline : null,
        badge: typeof plan?.badge === 'string' ? plan.badge : null,
        ctaLabel: typeof plan?.cta_label === 'string' ? plan.cta_label : null,
        isCustomPriced: plan?.is_custom_priced === true,
        annualPrice,
        annualSavings: Number.isFinite(Number(annual?.savings_percent))
          ? Number(annual.savings_percent)
          : null,
        annualMonthlyEquivalent: annual
          ? fromNullable(annual.monthly_equivalent, FIELD.cataloguePlanPrice.reason)
          : unavailable('This plan has no published annual price.'),
        hasAnnual: annual !== null,
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

  /**
   * Every invoice the server sent, each field resolved once against its declaration.
   *
   * BILL-HIST-02 REMOVED A `.slice(0, 5)` AND THREE DROPPED FIELDS.
   *
   * The slice cut the ledger to five rows with no pagination and nothing on screen admitting
   * it, so an account with more than five charges could not reach its sixth from anywhere in
   * the product. The whole list is projected now and `ds/DataTable` pages it at
   * `INVOICE_PAGE_SIZE`, which also puts the real total in the footer's live region.
   *
   * The dropped fields were `provider`, `providerPaymentId` and `plan`. All three have been
   * on the response since BILL-HIST-01 and all three are populated in production by the
   * signature-verified Razorpay webhook since migration 070 declared their columns; this
   * projection named six keys and discarded them, so the page could show that money had been
   * taken and not what it bought or which gateway charge it was. Each is `rowField`ed through
   * its own declaration, so a null arrives as an absence carrying its reason rather than as a
   * default — the nullability is deliberate (a row written before 070 genuinely does not know
   * its provider) and `'razorpay'` is the one substitution that would be invisible.
   */
  const invoiceRows = useMemo(() => {
    const body = invoices.data?.data ?? invoices.data;
    const rows = Array.isArray(body) ? body : [];
    return rows.map((invoice, index) => {
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
      const paymentReference = rowField(invoice, FIELD.invoicePaymentReference);
      return {
        // Only a React key and a `getRowId`; never rendered.
        id: invoice?.id ?? `invoice-${index}`,
        reference: rowField(invoice, FIELD.invoiceReference),
        // The untruncated identifier, for the cell's `title` (design.md §2.5 mechanism 4).
        fullReference:
          invoice?.id === null || invoice?.id === undefined ? null : String(invoice.id),
        date: fromNullable(formatDate(invoice?.date), FIELD.invoiceDate.reason),
        plan: rowField(invoice, FIELD.invoicePlan),
        amount: fromNullable(amount, FIELD.invoiceAmount.reason),
        denomination,
        /*
          The gateway and its payment id under ONE key, because the cell that renders them
          renders them together and `ds/DataTable` memoises a row on its projected cell
          values. Two keys with one column reading both would leave the second invisible to
          that compare; one key makes either reading changing a changed cell.
        */
        payment: {
          provider: rowField(invoice, FIELD.invoiceProvider),
          reference: paymentReference,
          fullReference: paymentReference.available ? String(paymentReference.value) : null,
        },
        status: rowField(invoice, FIELD.invoiceStatus),
      };
    });
  }, [invoices.data]);

  /*
    The page of the ledger on screen. **1-based**, as `ds/DataTable` requires.

    Reset to 1 whenever the invoice payload changes, because a refetch can shorten the list:
    the primitive clamps a stale page number for its own render, but leaving this state at 4
    after a read that returned one page would make the next, longer read open on page 4 of a
    ledger the trader had not navigated. The effect depends on `invoices.data`, which is the
    same value `invoiceRows` is memoised on.
  */
  const [invoicePage, setInvoicePage] = useState(1);
  useEffect(() => {
    setInvoicePage(1);
  }, [invoices.data]);

  /*
    NO `defaultMethod`, `cardBrand`, `cardLast4` or `cardExpiry`. REMOVED BY BILL-HIST-02 with
    the panel that was their only reader; see the removal note where that panel stood.

    They are not relocated into the invoice table, and that is the point rather than an
    omission. `defaultMethod` was "the default stored method, or the first one" — a statement
    about the account's instrument NOW. An invoice is a statement about a charge THEN, and
    joining the two would print a card beside a payment that card may not have made. The
    instrument is declared `UNAVAILABLE` per row instead (`FIELD.invoiceInstrument`), which is
    the true answer: `billing_invoices` records no instrument, and Razorpay Checkout never
    hands one over.
  */

  // ── The panel states ──────────────────────────────────────────────────────

  const anyLoading = [plans, entitlements, invoices].some(
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
        // BILL-HIST-02 corrected the third noun. It read "…, and payment methods", which
        // described a panel this page carried and no longer does — and which this product has
        // never actually offered, since no instrument is stored for anyone to manage. The
        // billing history is what the page really does hold.
        subtitle="Manage your plan, localized pricing, and billing history"
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
      {/*
        BILLING-PORTAL-02: the alert STAYS, its button does not. *Update payment method*
        called `POST /api/billing/portal` and so answered 501 to the one person on this page
        with an urgent reason to press it.

        The sentence now states the remedy that actually exists. Razorpay collects the card or
        UPI instrument inside its own Checkout at the moment of purchase and VyomQuant never
        holds it, so there is no stored instrument to edit and nothing to "update" in place —
        a failed charge is retried by buying the plan again and choosing a different
        instrument at that checkout. The plan buttons below this block do exactly that through
        `handleCheckout`, which is why no second control is added here.
      */}
      {isPaymentFailed ? (
        <div className="mt-3">
          <Alert
            severity="error"
            title="Payment failed — action required"
          >
            Your subscription payment failed, so full access is suspended. Your card or UPI
            instrument is collected by Razorpay Checkout at the moment of purchase and is never
            stored by VyomQuant, so there is nothing to update here. Choose your plan below and
            pay again, selecting a different instrument at the checkout if the last one was
            declined.
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
              className="absolute right-0 top-full z-50 mt-1.5 max-h-72 w-56 overflow-y-auto rounded-lg border border-line-default bg-surface-raised p-1.5"
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
          real charge would state something false.

          BILLING-PORTAL-02 (2026-11-15): this Panel takes NO `actions` prop. *Manage billing*
          stood there, behind `planIsPaid`, and called `POST /api/billing/portal` — which
          answers 501 for every caller because Razorpay publishes no hosted portal. A paid
          subscriber's only possible outcome from pressing it was an error toast.

          Cancel and Resume, the two subscription commands that DO reach working endpoints, are
          rendered inside this panel's body and are unaffected. */}
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
      >
        <div className="flex flex-col gap-5">
          <div className="flex items-start gap-3">
            <div className="flex shrink-0 items-center justify-center rounded-lg bg-brand-wash p-3 text-brand">
              <PlanIcon size={24} aria-hidden="true" />
            </div>
            <div className="min-w-0">
              {/* fontSize: 24 → --text-section (value — Q7, this panel's hero). At 24 it
                  equalled the page <h1>, and `tokens.css:74` reserves --text-page for that,
                  so this takes the highest step below it. The literal `"Free"` is gone. */}
              {/* The server's own `display_name` when it sent one, falling back to the derived
                  label. This is load-bearing rather than cosmetic: the stored plan id and the
                  published name diverge deliberately — `enterprise` is the plan sold as
                  "Business" — so capitalising the id would print the wrong one of the two on a
                  paid account's billing page. See `core/subscription_engine.py`'s docstring. */}
              {displayName !== null ? (
                <p className="truncate text-section font-extrabold text-content-primary">
                  {displayName}
                </p>
              ) : planName.available ? (
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
                {/* Rendered only when the server recorded a cycle. See `billingCycle`. */}
                {billingCycle !== null ? (
                  <span className="rounded border border-line-default px-1.5 py-0.5 text-micro uppercase tracking-wide text-content-secondary">
                    {billingCycle}
                  </span>
                ) : null}
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

          {/* ── Over capacity, after a downgrade ─────────────────────────────
              Rendered from the server's own `over_capacity` block, which is computed where both
              the measured usage and the new plan's limits are held. Nothing is deleted when a plan
              goes down — the strategies, models and listings an account built above its new
              capacity stay intact and readable — so this says what IS true: the data is preserved
              and only ADDING more is prevented. A page that said "reduce to 3 strategies" would be
              instructing a trader to destroy their own work to satisfy a limit that does not
              require it. */}
          {exceeded.length > 0 ? (
            <div className="mt-4 rounded-lg border border-status-warning/30 bg-status-warning-wash p-4">
              <p className="text-title font-semibold text-content-primary">
                Some allowances are above your current plan
              </p>
              <p className="mt-1 text-body text-content-secondary">
                Everything you have built is preserved and still readable. While you are over an
                allowance you cannot create or activate more of it.
              </p>
              <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
                {exceeded.map((item) => (
                  <li key={item.resource} className="text-body text-content-secondary">
                    {item.label}{' '}
                    <span className="font-mono tabular-nums text-content-primary">
                      {item.current} / {item.limit}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {/* ── The allowance tiles: every resource the server meters ─────────
              Thirteen, not four, and driven by the server's own `quotas` — see the `allowances`
              memo. A metered allowance is labelled "this month" because a monthly figure read as a
              lifetime total understates consumption by an order of magnitude. */}
          <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
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
                  className="rounded-lg border border-line-default bg-surface-inset p-4"
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
                        {/* The server's `-1` means "set per agreement", which renders as
                            *Custom* and never as ∞. An Enterprise account with no capacity
                            recorded is enforced at the Business figure, so "unlimited" would be
                            a promise the backend does not keep. */}
                        {unlimited ? 'Custom' : String(allowance.quota.value)}
                      </span>
                    ) : (
                      <Marker field={allowance.quotaField} reason={allowance.quota.reason} />
                    )}
                    {allowance.isMetered ? (
                      <span className="normal-case tracking-normal">this month</span>
                    ) : null}
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
              {subscriptionControl === SUBSCRIPTION_CONTROL.RESUME ? (
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
              {/*
                BILL-CANCEL-01. SHOWN UNLESS CANCELLATION IS ALREADY SCHEDULED, and the two
                conditions that used to hide it are gone deliberately.

                `!isPaymentFailed` HID THE CONTROL FROM THE CUSTOMER WHO MOST NEEDS IT. A
                subscription at `past_due`/`payment_failed` is one the platform is still
                trying to collect on; hiding Cancel there left the customer unable to stop
                their own billing from the product, with support as the only route out. That
                is the exact harm `routers/billing.py::cancel_subscription` refuses to
                create -- it is deliberately NOT gated on AAL2 so that "a paying customer
                who never enrolled a factor" can always stop being charged, and
                `test_cancel_subscription_is_not_gated_so_a_customer_can_always_stop_billing`
                enforces it. Hiding the button re-created that trap in the UI instead, which
                the server-side guard cannot see.

                `cancelAtPeriodEnd.available` was the second hole. That field is
                `ABSENCE.UNREPORTED` (`design/pageFields.js`), so any entitlements response
                that omits `cancel_at_period_end` rendered NEITHER button and left the page
                with no cancellation path at all. The condition is now inverted: the control
                is withheld only when the page positively knows a cancellation is already
                scheduled, because that is the only state in which cancelling again means
                nothing. Not knowing is not a reason to strand someone in a subscription.

                `isCancelled` stays: a subscription already in `cancelled` has nothing left
                to cancel, and Resume is the action that state offers.
              */}
              {subscriptionControl === SUBSCRIPTION_CONTROL.CANCEL ? (
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
          <div className="flex items-center gap-3">
            {plans.state === PANEL_STATES.REFRESHING ? (
              // fontSize: 11 → --text-body (sentence — Q1).
              <p className="text-body text-content-muted">Updating pricing…</p>
            ) : null}
            {/* Monthly / annual. Both cycles arrive priced in the same response, so this changes
                which figure is shown and which cycle a checkout names — it issues no request and
                computes no amount. `aria-pressed` rather than a radio group because the two
                buttons change what the panel shows, not a form value it will submit. */}
            <div
              className="inline-flex items-center gap-1 rounded-md border border-line-default bg-surface-inset p-0.5"
              role="group"
              aria-label="Billing cycle"
            >
              {[
                { value: 'month', label: 'Monthly' },
                { value: 'year', label: 'Annual' },
              ].map((option) => (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setCheckoutInterval(option.value)}
                  aria-pressed={checkoutInterval === option.value}
                  className={`rounded px-2.5 py-1 text-small font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand ${
                    checkoutInterval === option.value
                      ? 'bg-brand text-content-inverse'
                      : 'text-content-secondary hover:text-content-primary'
                  }`}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        }
      >
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
          {cataloguePlans.map((plan) => {
            const isCurrent =
              planName.available
              && String(planName.value).toLowerCase() === String(plan.id).toLowerCase();
            /*
              Annual is shown only where the server published an annual price for that plan.
              Falling back to the monthly figure under an "Annual" toggle would quote one cycle
              while labelling another; Free and the custom tier simply stay on their own line.
            */
            const showsAnnual = checkoutInterval === 'year' && plan.hasAnnual;
            const checkoutBlockedReason = isCurrent
              ? 'This is already your plan, so there is nothing to change.'
              : plan.isCustomPriced
                // Refused here AND refused by the server, which answers
                // `PLAN_REQUIRES_SALES_CONTACT` for this tier: it has no published price, so
                // there is no amount a self-serve checkout could charge.
                ? 'Enterprise capacity is priced per agreement. Contact us to arrange it.'
                : isPaymentFailed
                  ? 'Update your payment method first — a plan change cannot be processed while '
                    + 'a payment is outstanding.'
                  : checkoutInFlight !== '' && checkoutInFlight !== plan.id
                    ? 'Another checkout is already opening.'
                    : null;
            return (
              <div
                key={plan.id}
                className={`relative flex flex-col rounded-lg border-2 bg-surface-raised p-5 ${
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
                    comparison made here. A genuine 0 renders 0, which IS the free tier.

                    On the annual toggle this renders the server's published YEARLY figure, not a
                    multiple of the monthly one. The two are different published prices and the
                    gateway is asked for whichever cycle the request names. */}
                <div className="mt-4">
                  {plan.isCustomPriced ? (
                    <p className="text-figure font-black text-content-primary">Custom</p>
                  ) : (
                    <Metric
                      label={FIELD.cataloguePlanPrice.label}
                      value={showsAnnual ? plan.annualPrice : plan.price}
                      format="currency"
                      precision={plan.decimals}
                      unit={
                        plan.denomination === null
                          ? undefined
                          : `${plan.denomination} / ${showsAnnual ? 'year' : 'month'}`
                      }
                      tier={1}
                      unavailableReason={FIELD.cataloguePlanPrice.reason}
                    />
                  )}
                  {/* The per-month equivalent of an annual commitment, computed SERVER-side and
                      read here. A client dividing by twelve would round differently from the
                      charge, which is how a plan page ends up a rupee off the invoice. */}
                  {showsAnnual && plan.annualMonthlyEquivalent.available ? (
                    <p className="mt-1 font-mono text-small tabular-nums text-status-profit">
                      ≈ {Number(plan.annualMonthlyEquivalent.value).toLocaleString()}{' '}
                      {plan.denomination} / month
                      {plan.annualSavings ? ` — save ${plan.annualSavings}%` : ''}
                    </p>
                  ) : null}
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
                    {/* The server's own CTA wording when it sent one ("Start Automating",
                        "Scale to Business", "Talk to Sales"), so the button a trader presses is
                        labelled by the same catalogue that prices it. `Subscribe` remains the
                        fallback for a plan that carries no label. */}
                    {isCurrent ? 'Current plan' : (plan.ctaLabel ?? 'Subscribe')}
                  </CommandButton>
                </div>
              </div>
            );
          })}
        </div>
      </Panel>

      {/* ══ THE STORED PAYMENT METHOD: GONE ═══════════════════════════════════
          BILL-HIST-02 DELETED THE *Payment methods* PANEL. It is removed, not hidden: no
          state, no flag and no collapsed disclosure renders it.

          WHAT STOOD HERE. A panel driven by `GET /api/billing/payment-methods`, rendering a
          card brand, `•••• {last4}` and an expiry inside a bordered card, with a *Default*
          badge. BILLING-PORTAL-01 had already removed its *Manage via provider portal*
          button, leaving a panel that could be read but not acted on.

          WHY IT HAD TO GO RATHER THAN BE CORRECTED. It could never show anything.
          `VyomQuant stores no payment instrument` — Razorpay Checkout collects the card or UPI
          handle at the moment of purchase and never hands it over, which is why
          `POST /api/billing/payment-methods` answers `501 PAYMENT_METHOD_STORAGE_UNSUPPORTED`
          and `DELETE` answers `404` for any id. So the read answered `[]` on every production
          account, the panel's empty arm was its only reachable state, and a standing panel
          headed *Payment methods* told a trader that this product keeps their card on file
          and could not currently read it. That is a false claim about where their payment
          details live, made permanently, in the place they would go to check.

          WHERE THE INFORMATION WENT. Into the billing-history rows, which is where it is
          actually recorded: each invoice now states the gateway that captured the charge and
          that gateway's own payment reference. What is NOT carried across is any instrument
          detail — see `FIELD.invoiceInstrument` and the `payment` column's comment for why
          joining the account's current card onto a past charge is the fabrication §6 names.

          The one true sentence this panel carried — that the instrument is collected inside
          Razorpay Checkout and changed by paying again with a different one — is not lost. It
          is in the `past_due` alert above, where it is read by the person who needs it. */}

      {/* ══ THE INVOICE HISTORY ═══════════════════════════════════════════════
          FULL WIDTH SINCE BILL-HIST-02. It shared an `xl:grid-cols-2` row with the payment-
          methods panel; with that panel gone it is the row, and it has six columns to place
          rather than four, so the grid wrapper went with its second child.
          fontSize: 11 and 9 → `ds/DataTable`'s `--text-small` body and `--text-micro`
          `<th>`s. §2.4 orders Q4 ahead of Q6 exactly so a `<th>` is read as a label rather
          than as a cell. The table's blanket monospace goes with it — the primitive renders
          the mono treatment per column format — and the two identifier cells keep an explicit
          one, because that is what they are.
          fontSize: 12 → `ds/Panel`'s `empty` arm. *No invoices found.* used to render for a
          500 as well as for an account with no invoices, because the `Promise.allSettled`
          branch had no `else`: those are `error` and `empty` now. */}
      <div className="mt-4">
        <Panel
          title="Billing history"
          state={invoices.state}
          /*
            THE EMPTY ARM CARRIES AS MUCH WEIGHT AS THE POPULATED ONE. `billing_invoices` held
            zero rows in production when this was written — migration 070 declared the columns
            the webhook writer had always named, so until it landed every invoice write failed
            silently and the ledger has nothing older than that migration. This body says that
            nothing has been billed, which is a statement about the ACCOUNT, and `ds/Panel`'s
            separate `error` arm is what says the read failed. Collapsing the two is the defect
            Property 9 exists for and it was on this panel once.
          */
          empty={{
            headline: 'No invoices yet',
            body: 'Nothing has been billed to this account. An invoice appears here after the '
              + 'first charge settles, with the plan it paid for and the payment reference '
              + 'that identifies it at the gateway.',
            action: { label: 'Read again', onClick: refetchInvoices },
          }}
          loading={{
            kind: 'skeleton-table',
            rows: 6,
            columns: INVOICE_COLUMNS.length,
            label: 'Reading your invoices',
          }}
          error={{ error: invoices.error, onRetry: refetchInvoices }}
          unavailable={{ reason: 'Your invoice history cannot be read right now.' }}
          unauthorised={{ error: invoices.error }}
        >
          <DataTable
            caption="Invoices billed to this account, newest first"
            columns={INVOICE_COLUMNS}
            rows={invoiceRows}
            page={invoicePage}
            pageSize={INVOICE_PAGE_SIZE}
            onPageChange={setInvoicePage}
            density="compact"
          />

          {/*
            THE RECEIPT, STATED ONCE. `GET /api/billing/invoices` carries nine keys and not one
            of them is a URL, so there is no receipt or invoice document to link and none is
            composed: a Razorpay dashboard link built from the payment id would point at a
            merchant-side console a customer cannot sign in to, which is the same invented
            destination BILLING-PORTAL-02 removed two buttons for.

            It is one sentence under the table rather than a *Receipt* column, because it is a
            fact about the ledger and not about any row — a column would render the same marker
            on every line forever and claim, by sitting there, that some row might carry one.
            `FIELD.invoiceReceipt` holds the copy, so this reason is declared where every other
            absence on this page is declared and cannot drift from it.

            fontSize: --text-micro (label — Q4), inside the panel, so it is not standing prose.
          */}
          <p className="mt-2.5 text-micro text-content-muted">
            {FIELD.invoiceReceipt.reason}
          </p>
        </Panel>
      </div>

      {/*
        MFA-STEPUP-02. The AAL2 step-up, a sibling of the page rather than something either
        write handler owns, so no handler holds overlay state — the same shape
        `pages/ExchangeManager.jsx` uses.

        `onVerified` reads the thunk BEFORE clearing `stepUp`, then runs it. Clearing first
        and reading after hands `undefined` to the retry, and the operation is silently
        dropped after a successful challenge: the customer verifies, the dialog closes, and
        nothing happens. That is the most expensive way this can fail, because it looks like
        it worked.
      */}
      <MfaStepUpDialog
        open={stepUp !== null}
        operationLabel={stepUp?.label}
        onCancel={() => {
          setStepUp(null);
          setNotice({
            severity: 'guidance',
            message:
              'Verification was cancelled, so your subscription was not resumed. Nothing '
              + 'changed — choose Resume again when you are ready to confirm with your '
              + 'authenticator.',
          });
        }}
        onVerified={() => {
          const operation = stepUp?.operation;
          setStepUp(null);
          if (typeof operation === 'function') operation();
        }}
      />
    </div>
  );
}
