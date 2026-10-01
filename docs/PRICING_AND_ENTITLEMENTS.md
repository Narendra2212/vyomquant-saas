# Pricing and entitlements

How the VyomQuant plan ladder is defined, enforced and rendered. Read this before changing a
price, a limit, or anything that gates on one.

## The one catalogue

`backend_app/core/subscription_engine.py` is the single authoritative definition: identifiers,
display names, published prices, per-plan capacity and feature sets. Everything else derives from
it — the entitlement dependencies, the billing router, `PricingService`, the marketplace gates,
`GET /api/billing/plans`, `GET /api/billing/entitlements`, and the UI that renders those two.

There is deliberately no second copy of these numbers in the backend. The frontend reads them over
the wire rather than restating them, with one documented exception: the public pricing section
(`algo22-terminal/src/components/landing/Pricing.jsx`) declares the rupee figures as constants so a
published price list carries no loading state and no network dependency for an anonymous visitor.
Those constants and the catalogue are pinned by two independent test transcriptions —
`tests/test_pricing_ladder.py` and
`algo22-terminal/tests/unit/landing_page_pricing_crash_regression.test.jsx` — so they cannot drift
without a test failing.

## The ladder

| stored id | tier key | display name | journey | monthly | annual |
|---|---|---|---|---|---|
| `free` | `FREE` | Free | Explore | ₹0 | — |
| `starter` | `TRADER` | Trader | Automate | ₹499 | ₹4,990 |
| `pro` | `PRO_QUANT` | Pro Quant | Quantify | ₹999 | ₹9,990 |
| `enterprise` | `BUSINESS` | Business | Operate | ₹2,499 | ₹24,990 |
| `scale` | `ENTERPRISE` | Enterprise | Scale | quoted | quoted |

### `enterprise` is the Business plan. This is not a typo.

`profiles.subscription_tier` is a live production column written by
`billing._apply_billing_entitlement` with the raw checkout `item_key`. The historic `enterprise`
plan was the ₹2,499 top tier, which is exactly the commercial position Business occupies — so the
**display name moved and the identifier did not**, and every existing ₹2,499 subscriber lands on
Business with no migration and no capacity loss (Business raises every one of that plan's limits).

The alternative — making `enterprise` mean the new custom tier and mapping the legacy value forward
— was rejected because `migrate_plan_key` resolves a live `Plan` value to itself before consulting
the alias table. A stored `enterprise` would then be ambiguous forever: either a legacy ₹2,499
subscriber or a new custom account. Ambiguity on a billing column is not an acceptable trade for a
tidier name. The genuinely new tier takes a new id, `scale`.

Annual prices are **published figures, not derived ones**. ₹4,990 is not ₹499 × 12 × 0.83, and only
the published figure is ever charged. A client showing a per-month equivalent must divide the
server's annual figure rather than discount the monthly one.

The USD column (`$5` / `$10` / `$25`) is unchanged from the previous catalogue. It is regional
pricing, not a conversion of the rupee figure, and altering it would change what existing Stripe
subscribers are billed.

## Capacity

| Resource | Free | Trader | Pro Quant | Business | Enterprise |
|---|---|---|---|---|---|
| Active strategies | 1 | 3 | 10 | 25 | custom |
| Paper strategies | 1 | 3 | 10 | 25 | custom |
| Live strategies (`bots`) | 0 | 3 | 10 | 25 | custom |
| Exchange connections | 1 | 2 | 5 | 8 | custom |
| Trading accounts | 1 | 1 | 3 | 5 | custom |
| Backtests / month | 10 | 100 | 500 | 1500 | custom |
| Custom indicators | 3 | 10 | 30 | 75 | custom |
| Strategy versions (per strategy) | 3 | 10 | 25 | 50 | custom |
| ML models | 0 | 0 | 5 | 15 | custom |
| ML training runs / month | 0 | 0 | 50 | 200 | custom |
| Optimization runs / month | 0 | 25 | 100 | 400 | custom |
| Marketplace subscriptions | 0 | 3 | 10 | 25 | custom |
| Marketplace listings | 0 | 0 | 5 | 15 | custom |
| Creator revenue share | — | — | 90% | 90% | 90% |

**"custom" is not "unlimited."** The catalogue stores `-1` for the Enterprise tier, meaning "agreed
per contract". The real numbers come from `profiles.plan_limit_overrides`, and an Enterprise account
with no override recorded is enforced at the **Business** figure — so forgetting to provision a
contract is safe rather than uncapped. Overrides are honoured **upward only**: a value below the
plan's own limit is logged and ignored, because a JSONB column must not be able to silently strip
capacity from a paying account.

### Not metered, on any plan

Historical data, tick data, basic market data, alerts, webhooks, basic analytics and marketplace
browsing. Technical protections for them stay (rate limits, the `TenantQuota` infrastructure
ceilings); none is exposed as a commercial gate.

### Not pricing dimensions at all

Team members, workspaces, collaboration, RBAC, organisation management, multi-user permissions,
team billing, and customer API tiers. The product is single-user and has no customer-facing API
tier, so advertising any of them would be selling something that does not exist. No plan grants
`api_access`, and a caller asking for it gets a truthful `False`.

## Counted vs metered — two kinds of limit, two mechanisms

`core/usage_ledger.py` holds both.

**Counted** resources are point-in-time facts about rows that exist: strategies, live and paper
deployments, exchange connections, active ML models, marketplace listings and subscriptions. They
are counted from the persistence layer at check time. A maintained counter is the wrong instrument:
it drifts the first time a row is deleted by a path that forgot to decrement, by an admin, or by a
foreign-key cascade — and a drifted counter either locks a paying customer out of capacity they own
or hands them capacity they do not. Deleting a row releases the slot immediately, because the row
*is* the record.

**Metered** resources are consumption over a calendar month: backtests, optimization runs, ML
training runs. There is no row to count — the point is that the run happened, whatever became of
its output. They accumulate in a period-scoped meter and are **never returned on completion**,
successful or failed, which is what stops "delete the model and train again" from resetting the
month. A reservation is only released when the work never started.

The month boundary is one function, `subscription_engine.usage_period()`, returning `YYYY-MM` in the
`PLAN_USAGE_TIMEZONE` zone (UTC by default). The period is part of the Redis key
(`quota:{user}:{resource}:{YYYY-MM}`), so a new month is a new key holding zero and no scheduled
reset job is required. The previous implementation used an unscoped key with a 24-hour TTL, so a
"monthly" allowance silently reset every day.

`plan_usage_ledger` (migration `017_plan_entitlements.sql`) is the durable record behind the Redis
meter. Redis is a cache: flush it and every account would silently receive a fresh month. Every
successful reservation appends a row, and a missing meter key is **rebuilt from the ledger** before
it is trusted. `idempotency_key` is unique, so a retried request cannot be counted twice.

### Resources with no source of truth

`custom_indicators` has **no per-account persistence** in this platform — indicators are a static
code registry (`backend/indicators_backend.AVAILABLE_INDICATORS`) plus a JSONB configuration array
on each strategy row. The limit is carried in the catalogue so it is auditable and reported, and
`usage_ledger.count` **refuses** to answer for it rather than returning `0`, which would render as
`0 / 3` and read as a measurement. See `UNCOUNTABLE_RESOURCES`.

`trading_accounts` is counted from `paper_accounts`, the only account-like object the schema holds.
There is no `trading_accounts` table; live venue credentials are governed by `exchange_connections`.

## Enforcement

`core/subscription_dependencies.py` is the **only** enforcement path. Nothing in it trusts the
client — not a header, not a body field, not a token claim about a tier. The plan is read from
`profiles.subscription_tier` on every request that needs it, behind a 15-second Redis cache that
`billing._apply_billing_entitlement` invalidates on any plan change.

It **fails closed**: an unreadable profile raises rather than defaulting to Free, and an unreadable
usage figure answers `503 ENTITLEMENT_USAGE_UNREADABLE` rather than assuming zero. A read that did
not complete is not evidence of an empty account.

An `expired` subscription loses paid entitlement for the request without any write. A `cancelled`
subscription **keeps** it until the period it has already paid for ends — the same rule the
marketplace applies. `past_due` / `payment_failed` are left to the existing dunning behaviour;
turning either into an instant downgrade is a billing-policy decision and does not belong in the
entitlement layer.

### Refusals are structured

A gated refusal is a `403` whose `detail` is an `EntitlementRefusal`:

```json
{
  "code": "STRATEGY_LIMIT_REACHED",
  "resource": "strategies",
  "current": 3,
  "limit": 3,
  "plan": "starter",
  "tier": "TRADER",
  "required_plan": "pro",
  "required_tier": "PRO_QUANT",
  "message": "You've reached your Trader capacity for active strategies.",
  "upgrade_message": "Upgrade to Pro Quant to run up to 10 active strategies.",
  "cta_label": "Upgrade to Pro Quant — ₹999",
  "contact_sales": false
}
```

Every number is measured, not assumed. `contact_sales` is what the top of the ladder sets instead of
naming a higher plan, because there isn't one to name. The `code` values are a stable API contract
and are asserted by `tests/test_pricing_ladder.py`.

`GET /api/billing/entitlements` carries the same payloads for everything the account *cannot*
currently do, as `locked_features[feature]` and `limit_refusals[resource]` — so a frontend gate
renders the server's words without a request per gate. Composing that copy in the browser would
need a local plan ladder and a local price list, both free to drift from the catalogue that enforces
the limit and the gateway that takes the money.

### Where the gates sit

| Action | Route | Gates |
|---|---|---|
| Create strategy | `POST /api/strategies` | `check_strategy_quota` |
| Clone a listing | `POST /api/library/{id}/clone` | `check_strategy_quota` |
| Deploy live | `POST /api/strategies/{id}/deploy` | `require_live_trading`, `check_bot_quota` |
| Backtest | `POST /api/strategies/backtest`, `…/backtests`, `…/backtests/execute` | `check_backtest_quota` |
| Optimize / Monte Carlo / walk-forward | `POST /api/strategies/optimize`, `/monte-carlo`, `/walk-forward`, `…/{id}/optimize` | `require_optimization`, `check_optimization_quota` |
| Train ML | `POST /strategy-operations/training/jobs` | `require_ml_training`, `check_ml_model_quota`, `check_ml_quota` |
| Connect an exchange | `POST /api/exchange/keys` | `check_exchange_connection_slot` |
| Subscribe to a listing | `POST /api/library/{id}/checkout` | `require_marketplace_subscribe`, `check_marketplace_subscription_quota` |
| Publish a listing | `POST /api/library`, `POST /api/library/submissions` | `require_marketplace_publish`, `check_marketplace_publish_quota` |
| Listing settings | `PATCH /api/library/{id}/settings` | `require_marketplace_publish` |

`check_exchange_connection_slot` is a function rather than a dependency because whether it consumes
a slot depends on *which* venue: `exchange_keys` holds one row per `(user, exchange_id)`, so
re-storing credentials for a venue already connected is a **rotation** and must not be refused. An
account at its limit can still rotate every key it owns.

Cloning carries no marketplace plan gate. Cloning is not subscribing: a clone of a freely-published
listing involves no payment and no settlement, and the limit that actually applies is strategy
capacity. Requiring the subscribing entitlement there was tried and reverted — it refused a Free
account a copy of a free strategy, a restriction the price list does not state.

## Marketplace

| | Browse | Subscribe | Publish | Creator share |
|---|---|---|---|---|
| Free | yes | 0 | no | — |
| Trader | yes | 3 | no | — |
| Pro Quant | yes | 10 | 5 | 90% |
| Business | yes | 25 | 15 | 90% |
| Enterprise | yes | custom | custom | 90% |

Browsing is open to **every** plan. `/marketplace` is a public, unauthenticated route, so gating it
for a signed-in Free account refused them something a stranger can see — and the marketplace is the
surface a Free account is meant to convert from. The capability was therefore split:
`marketplace_browse` is universal, and `marketplace_subscribe` / `marketplace_publish` are the
gates. `marketplace_access` survives as a deprecated alias of browse so existing route declarations
keep resolving.

A **cancelled but unexpired** marketplace subscription still occupies a subscription slot, because
it still entitles its holder.

### Revenue share

90% creator / 10% VyomQuant. The arithmetic is
`backend/marketplace/money.split_ninety_ten`, which computes the creator's share with integer
arithmetic and takes the platform fee as the **residual**, so the two always sum to the amount
charged. `chk_settlement_conserved` on `marketplace_settlements` makes a row that does not sum
unrepresentable. `settlement_service.settle` is the single writer of that table and the only path
permitted to set `library_subscriptions.status = 'active'`, enforced by
`trg_subscription_transition_guard` in the database rather than by convention.

`subscription_engine.CREATOR_REVENUE_SHARE_PERCENT` exists so the catalogue can state the share a
plan earns; `tests/test_pricing_ladder.py` asserts it equals `money.OWNER_SHARE_PERCENT` rather than
letting it be a second source of truth. The share is calculated on net revenue — after the
marketplace's payment, refund and chargeback handling — and reversals are recorded as their own
settlement rows that net out of a creator's earnings. It is not 90% of gross payment.

## Billing

Razorpay for INR, Stripe for everything else (`FXService.resolve_checkout_provider_and_currency`).
Neither integration was replaced. Webhook IP allowlisting, signature verification, payment
verification, renewals, cancellation and resume are unchanged.

`POST /api/billing/checkout` takes an `interval` (`month` | `year`, defaulting to `month` so an
existing client that sends none keeps producing exactly the monthly subscription it did). The amount
comes from `FXService.localize_plan_price`, which charges the **published** figure for a currency
the catalogue publishes and converts only for one it does not, flagging which via `price_source`.

Before this, every amount was an FX conversion of the USD base — so `GET /api/billing/plans`
answered roughly ₹865 for the plan published at ₹999, and the marketing page hardcoded its figures
precisely because the endpoint disagreed with them. That is fixed at the root: the endpoint, the
billing page and the gateway now quote one number.

The custom `scale` tier **cannot** go through self-serve checkout — it has no published price, so
there is no amount to charge — and is refused with `PLAN_REQUIRES_SALES_CONTACT`. It remains
grantable through the normal entitlement path by an operator, which is why it is in
`VALID_ITEM_KEYS`.

`VALID_ITEM_KEYS` is **derived** from the `Plan` enum rather than listed by hand. It is the gate a
paid webhook passes through, so a plan present in the catalogue but missing from that set would take
a customer's money and then refuse to grant anything — the handler raises 400 and the webhook is
retried into the same 400. Deriving it makes that unreachable.

## Downgrade behaviour

A plan going down **deletes nothing**. Strategies, models and listings built above the new capacity
stay intact and readable. What a lower plan prevents is creating or activating *more*.

`GET /api/billing/entitlements` reports `over_capacity` as `{resource: {current, limit}}`, computed
server-side because that is the one place holding both the measured usage and the new plan's limits —
a client subtracting the two could disagree with the gate actually refusing the writes. The billing
page renders it as a notice.

Live strategies are never terminated automatically by a plan change. No code path in this change
stops a running strategy.

Exchange connections are never disconnected or invalidated by a plan change; a downgraded account
keeps its stored credentials and can still rotate them.

## Frontend

The frontend decides what to **show**. It never decides what is **allowed** — every gated action is
independently refused by the backend, so editing the bundle, the response in flight, local storage
or React state unlocks a rendering and nothing behind it.

- `src/design/entitlements.js` — a pure reader over the entitlements payload. No plan table, no
  price, no rule about which plan may do what.
- `src/hooks/useEntitlements.js` — one shared, single-flight read of
  `GET /api/billing/entitlements`. Four surfaces used to call that endpoint independently, each able
  to show a different plan than the others for as long as they disagreed.
- `src/components/gates/` — `FeatureGate`, `PlanGate`, `UsageLimit`, `CapacityNotice`,
  `UpgradePrompt`, `LockedCommand`, plus the `useFeature` / `useAllowance` hooks.

Gates **fail closed**: while entitlements are unknown, or after a read that failed, a gate renders
its locked state. The two directions of error are not symmetric — a wrongly-locked control shows a
trader an upgrade panel they can question, while a wrongly-unlocked one invites them to press a
button that 403s for reasons they were given no warning of.

An unreadable usage figure renders the server's **reason**, never `0`. `0 / 10` is a claim about
consumption and an unread figure is the absence of one; they look identical on screen and mean
opposite things. This is the `Reported<T>` discipline from `src/design/reported.js`.

Prefer `FeatureGate` over `PlanGate`. A plan name is a proxy for a capability and goes stale the
moment a capability moves between plans, with nothing failing loudly when it does.

## Changing a price or a limit

1. Edit `backend_app/core/subscription_engine.py`. Only there.
2. Update the transcribed expectations in `tests/test_pricing_ladder.py` — deliberately a separate
   transcription of the published table, so a change has to be made twice and agreed.
3. If a rupee figure changed, update `algo22-terminal/src/components/landing/Pricing.jsx` and
   `algo22-terminal/tests/unit/landing_page_pricing_crash_regression.test.jsx`.
4. Update the tables in this file.
5. Run `pytest tests/test_pricing_ladder.py tests/test_saas_entitlements_gating_audit.py` and
   `npx vitest run` in `algo22-terminal/`.

Adding a `Plan` member is safe. **Renaming or removing one is not**: the values are persisted in
`profiles.subscription_tier` and travel through the payment gateway as the `item_key`.
