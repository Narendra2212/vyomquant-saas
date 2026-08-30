# Implementation Plan: Marketplace, Subscriptions and Paper Trading

## Overview

This plan implements `design.md` against the existing repository. It is a **correction and
completion effort**: `backend_app/routers/library.py`, `backend_app/routers/billing.py`,
`backend_app/routers/paper_trading.py`, `backend_app/backend/paper_trading_service.py`,
`backend/backtest_runtime.py`, `backend/market_data_latency.py`, `backend/ws_channels.py`,
`api_ws/ws_manager.py`, `core/websocket_auth.py`, `core/audit_trail.py` and
`algo22-terminal/src/api/*` are **extended in place**. Nothing in the reuse map of
`design.md § Architecture` is rebuilt, duplicated or replaced.

Implementation languages: **Python** for the backend, **SQL** for the migrations,
**JavaScript/React** for `algo22-terminal`. The `pascal`-style `FUNCTION`/`STRUCTURE` blocks in
`design.md` are notation for illustrating logic against named Python and JavaScript files — the
design names every file, table, constraint, trigger and test module concretely, so no language
decision is outstanding.

### Hard ordering constraints this plan obeys

1. **Task 1 is first, unconditionally.** `tests/regression/capture_baseline.py` runs against the
   **unmodified** system. After any change lands, the Requirement 25.7 baseline can no longer be
   taken, and `paper_api_shape.json` (Requirement 17.12) and `risk_utilisation.json`
   (Requirement 25.5) become unobtainable.
2. **Migration dependency order** is `006_backtest_evidence_columns.sql` →
   `007_marketplace_submissions.sql` → `008_marketplace_settlement.sql` →
   `009_paper_trading.sql` → `010_signal_environment.sql`. `010` carries
   `fk_signals_paper_session` and therefore requires `paper_sessions` from `009`.
3. **Pure modules before the services that use them.** `money.py`, `subscription_period.py`,
   `submission_state.py`, `subscription_state.py`, `evidence_validator.py`,
   `pricing_evaluator.py`, `listing_projection.py`, `paper_order_state.py`,
   `paper_accounting.py` and `execution_environment.py` import no FastAPI and perform no I/O,
   so each is buildable and property-testable standalone (Tasks 4–9).
4. **`tests/test_schema_as_code_completeness.py` is edited in the same task as the migration
   that adds the tables it must list.** That test fails on
   `app_tables - migration_tables - supabase_system_tables`, so a migration landing without the
   matching edit breaks the suite.
5. **The route-shadowing fix (Task 13) lands before the admin, creator, submission,
   recommendation and favourite routes it unblocks** (Tasks 14, 15, 21).
6. **`paper_repository.py` before the `paper_trading_service.py` repoint; the repoint before
   `routers/risk.py` becomes `await`-aware** (Task 23).
7. **Frontend API modules before the pages that consume them** (Task 31 before Task 32).

### Rules that apply to every task

- **No control is weakened.** No task removes or loosens authentication, authorisation, RLS,
  tenant isolation, webhook signature/IP/timestamp validation, entitlement enforcement,
  idempotency or risk controls (`requirements.md § Non-functional constraints`).
- **No existing CI check is removed, made non-blocking, skipped, xfailed or excluded by
  selector** (Requirements 29.8, 29.9). The `validate-code`, `unit-tests` and `docker-check`
  jobs in `.github/workflows/01-pr-check.yml` keep their current scope.
- **Additive-only schema.** No `DROP TABLE`, `DROP COLUMN`, `ALTER … RENAME`, `DELETE FROM` or
  `TRUNCATE` appears anywhere in the migration set (Requirement 24.7).
- **No production path performs binary floating-point money arithmetic and no module under
  `backend_app/backend/paper/` imports `random`** (Requirements 8.13, 10.3, 16.12, 18.1).
- **`marketplace_listings` and `strategy_subscriptions` stay dormant.** No task reads or writes
  either (Requirement 1.2).
- **Every defect discovered while implementing is fixed at its root cause and covered by a
  regression test that fails against the unfixed code.**

## Tasks

- [x] 1. Capture the pre-change regression baseline — **this task must complete first**

  - [x] 1.1 Create `tests/regression/capture_baseline.py` and write `tests/regression/baseline/*.json`
    - Run against the **unmodified** system; one JSON file per named path and surface, captured
      individually as Requirement 25.7 requires — not one combined file
    - Live paths: `live_order_path.json` (the `core/execution_engine.execute_with_idempotency`
      collaborator call sequence and result shape for a representative intent),
      `credential_vault.json` (the read/decrypt call sequence for a connection fetch),
      `live_runtime.json`, `signal_generation.json`, `signal_trace_live.json` (the recorded
      `signals` row shape and `signal_events` sequence for a live signal)
    - Billing: `billing_plan_checkout.json`, `billing_entitlements.json`,
      `billing_invoices.json`, `billing_payment_methods.json`, `billing_currency.json`,
      `billing_portal.json`, `billing_cancel.json`, `billing_resume.json`
    - Auth: `auth_register.json`, `auth_signin.json`, `auth_token.json`, `auth_2fa.json`,
      `auth_admin_role.json`
    - One file per surface named in Requirement 25.4 (authentication, sign-up, sign-in,
      dashboard, strategy builder, strategies, backtester, signal trace, exchange connection,
      risk settings, portfolio, trade history, billing, notifications, profile, security logs,
      support) recording the response key set and type map of every API call the surface makes
    - `risk_utilisation.json` — the exact numeric semantics of `routers/risk.py` lines 300–311
      and 355–360 against a known in-memory paper account
    - `paper_api_shape.json` — the key sets and value types of `GET /api/paper/account`,
      `/positions`, `/orders`, `/trades`, `/summary` and `POST /account/reset`
    - _Requirements: 25.1, 25.2, 25.3, 25.4, 25.5, 25.6, 25.7, 17.12_

  - [x] 1.2 Create `tests/regression/test_baseline_unchanged.py`
    - Re-runs each capture and asserts per-file equality against `tests/regression/baseline/`
    - Allows **added** keys only where `design.md` declares an additive field
      (`execution_environment`, `is_simulated`, `session_id`, `stale`, `last_price_at`);
      a removed, renamed, retyped or re-meaninged key is a failure
    - Fails with the baseline file name and the differing key path, so a regression names itself
    - _Requirements: 25.7, 25.8, 17.12_
    - Verification: `pytest tests/regression/test_baseline_unchanged.py` passes against the
      unmodified tree (it is a tautology at this point, and the assertion that matters is that
      it keeps passing after every later task)

- [x] 2. Build the shared property-test generators and the coverage guard

  - [x] 2.1 Create `tests/strategies/marketplace_generators.py`
    - `minor_amounts()` over 0…99,999,999,999 with 0, 1, 99, 100, 101 and the maximum explicitly
      in the pool; `backtest_conditions()`; `evidence_sets()`; `utc_instants()` with 31 Jan,
      28/29 Feb, 30 Apr, 30 Nov, 31 Dec and leap years in the pool; `listing_rows()` populating
      every member of `listing_projection.DENIED_LISTING_COLUMNS` with a non-null value;
      `protected_logic_strategies()` whose node ids, indicator names, parameter names, threshold
      values and model path segments are generated strings of at least three characters;
      `tenant_pairs()`
    - _Requirements: 29.3_

  - [x] 2.2 Create `tests/strategies/paper_generators.py`
    - `market_event_streams()` including duplicated `source_event_id` values, reorderings, gaps
      and disconnection points; `order_intents()`; `fill_sequences()`; `equity_series()`
    - _Requirements: 29.3_

  - [x] 2.3 Create `tests/property/test_property_coverage.py`
    - Collects every function matching `test_p(\d+)_` across `tests/property/` and fails if any
      of P-1…P-58 has no function or more than one
    - This is what makes "one property, one property-based test" checkable rather than implied
    - _Requirements: 29.3_
    - Verification: the test fails now (58 properties, 0 functions) and is the running scoreboard
      for every property sub-task below; it must pass at Task 35

- [-] 3. Checkpoint — baseline captured
  - Ensure `pytest tests/ -k "not chaos and not load"` still passes and
    `tests/regression/baseline/*.json` is committed. Ask the user if questions arise.

- [x] 4. Implement the pure money and calendar-period modules

  - [x] 4.1 Create `backend_app/backend/marketplace/money.py`
    - `MINOR_UNIT_EXPONENT` (`USD: 2`, `INR: 2`), `OWNER_SHARE_PERCENT = 90`,
      `MAX_AMOUNT_MINOR = 99_999_999_999`
    - `split_ninety_ten(amount_minor) -> (owner_share, platform_fee)` with
      `owner_share = (amount_minor * 90) // 100` and `platform_fee = amount_minor - owner_share`;
      rejects a non-`int`, a `bool`, a negative amount and an amount above the maximum with
      `InvalidAmount` **before** the division
    - `amount_for_listing(listing_row)` returning `price_minor` unchanged — no `float` anywhere
    - `to_major(amount_minor, currency)` and `from_major_string(text, currency)` via `Decimal`,
      used only at the presentation and ingestion boundaries
    - Creates `backend_app/backend/marketplace/__init__.py`; imports no FastAPI, performs no I/O
    - _Requirements: 8.12, 8.13, 9.2, 10.1, 10.2, 10.3_

  - [x] 4.2 Write property test for split conservation
    - `tests/property/test_money_split.py::test_p1_conservation`
    - **Property P-1 (invariant, conservation)** — `owner_share(a) + platform_fee(a) == a`
    - Generator `minor_amounts()`; oracle: `a*90 - 100*owner_share` lies in `[0, 100)`
    - **Validates: Requirements 10.1, 10.2**

  - [x] 4.3 Write property test for share bounds
    - `tests/property/test_money_split.py::test_p2_share_bounds`
    - **Property P-2 (invariant, bounds)** — `0 <= owner_share(a) <= a` and
      `0 <= platform_fee(a) <= a`
    - **Validates: Requirements 10.1, 10.5**

  - [x] 4.4 Write property test for the ratio bound
    - `tests/property/test_money_split.py::test_p3_largest_whole_unit_not_exceeding_ninety_percent`
    - **Property P-3 (invariant, ratio bound)** — `owner_share(a) * 100 <= a * 90` and
      `(owner_share(a) + 1) * 100 > a * 90`, so the rounding remainder never exceeds one
      Minor_Unit
    - **Validates: Requirements 10.1, 10.2**

  - [x] 4.5 Write property test for split monotonicity
    - `tests/property/test_money_split.py::test_p4_split_is_monotonic`
    - **Property P-4 (metamorphic, monotonicity)** — for `a1 <= a2`,
      `owner_share(a1) <= owner_share(a2)` and `platform_fee(a1) <= platform_fee(a2)`
    - **Validates: Requirements 10.1**

  - [ ]* 4.6 Write unit tests for the presentation and ingestion boundaries
    - `tests/test_marketplace_money_boundaries.py` — `to_major` / `from_major_string` round trips
      for USD and INR, rejection of a `float` argument, rejection of a value with more decimal
      places than the currency exponent
    - Optional: P-1…P-4 already cover the split's whole input space; these are convenience
      boundaries around the conversion helpers
    - _Requirements: 8.12, 8.13_

  - [x] 4.7 Create `backend_app/backend/marketplace/subscription_period.py`
    - `add_one_calendar_month(t)` asserting `t.tzinfo is timezone.utc`, rolling the month,
      clamping the day to `calendar.monthrange(...)[1]`, and using `t.replace` so hour, minute,
      second, microsecond and `tzinfo` are preserved
    - `period_for_activation(confirmation_instant) -> (start, expiry)` and
      `period_for_renewal(current_expiry, confirmation_instant) -> expiry` computed from
      `MAX(current_expiry, confirmation_instant)`
    - _Requirements: 11.4, 11.5_

  - [x] 4.8 Write property test for calendar-month arithmetic
    - `tests/property/test_subscription_period.py::test_p12_calendar_month_clamps_day_and_preserves_clock_time`
    - **Property P-12 (round-trip, calendar month)** — `m(t)` falls in the month following `t`,
      has the same clock time, and day-of-month `min(day_of_month(t), last_day(target_month))`
    - Generator `utc_instants()`; oracle: `dateutil.relativedelta(months=+1)` as an independent
      second implementation
    - **Validates: Requirements 11.4**

  - [x] 4.9 Write property test for renewal monotonicity
    - `tests/property/test_subscription_period.py::test_p14_renewal_expiry_is_strictly_increasing`
    - **Property P-14 (metamorphic, renewal monotonicity)** — the new expiry is strictly greater
      than the previous expiry and is one calendar month after the later of the previous expiry
      and the confirmation instant
    - **Validates: Requirements 11.5**
    - Verification: `pytest tests/property/test_money_split.py tests/property/test_subscription_period.py`

- [x] 5. Implement the pure state-machine and error modules

  - [x] 5.1 Create `backend_app/backend/marketplace/submission_state.py`
    - `SubmissionState` (8 values), `SUBMISSION_TRANSITIONS` (the eleven pairs, `UNPUBLISHED`
      with an empty target tuple, no state listing itself), `PUBLIC_STATES = {PUBLISHED}`,
      `OPEN_STATES = {SUBMITTED, UNDER_REVIEW, APPROVED, PUBLISHED}`, `can_transition`
    - `MODERATION_STATUS_FOR_STATE` and `IS_ACTIVE_FOR_STATE` — the single shared mapping
      Requirement 4.12 demands; `'featured'` is never produced by it
    - _Requirements: 4.1, 4.2, 4.6, 4.7, 4.12, 2.7_

  - [x] 5.2 Create `backend_app/backend/marketplace/subscription_state.py`
    - `SubscriptionState` (7 values), `SUBSCRIPTION_TRANSITIONS` (the twelve pairs),
      `PAYMENT_REQUIRED_TARGETS = {ACTIVE}`, `can_transition`
    - The one mapping between the enum and the persisted lowercase `library_subscriptions.status`
      text; no existing value is renamed, so `.eq("status", "pending")` in
      `_apply_marketplace_entitlement` and `.eq("status", "active")` in
      `check_deployment_permission` keep working
    - _Requirements: 11.1, 11.2, 11.6_

  - [x] 5.3 Create `backend_app/backend/paper/paper_order_state.py`
    - `PaperOrderState` (6 values), `PAPER_ORDER_TRANSITIONS` including the single permitted
      self-transition `PARTIALLY_FILLED → PARTIALLY_FILLED`, `TERMINAL = {FILLED, CANCELLED,
      REJECTED}`, `can_transition`
    - `LEGACY_STATUS_FOR_STATE` mapping the six values onto the retained `PaperOrderStatus`
      spellings (`CREATED → NEW`, `ACCEPTED`/`PARTIALLY_FILLED → OPEN`, the three terminals
      unchanged) so `GET /api/paper/orders?status=OPEN` keeps its meaning
    - Creates `backend_app/backend/paper/__init__.py`
    - _Requirements: 16.1, 16.2, 16.3, 17.12_

  - [x] 5.4 Create `backend_app/backend/execution_environment.py` and install the live-path guard
    - `ExecutionEnvironment` enum (`BACKTEST`, `PAPER`, `LIVE`) — the single place the three
      values are spelled, added as an additive value set
    - `assert_live_environment(env)` raising `UnresolvedExecutionEnvironment` for `None` or an
      out-of-set value (never defaulting to `LIVE`) and `ExecutionEnvironmentMismatch` with an
      audit write for `PAPER`/`BACKTEST`
    - Call it at exactly one place: the top of
      `core/execution_engine.execute_with_idempotency`, **before** `_validate_keys` and before
      any `ccxt` call, so no live order path can bypass it
    - `tests/test_execution_environment_guard.py` asserts the guard refuses each of `PAPER`,
      `BACKTEST`, `None` and `"live "` before any exchange collaborator is invoked, and that the
      audit entry is written
    - _Requirements: 13.1, 13.9, 13.10, 23.1_

  - [x] 5.5 Create `backend_app/backend/marketplace/errors.py` and `backend_app/backend/paper/errors.py`
    - `MarketplaceError(code, http_status, message, details)` and `PaperError(...)`, plus
      `PUBLIC_MESSAGE_FOR_CODE` — the single place a client-facing sentence per code is written
    - Every code in `design.md § The error code catalogue`, with its HTTP status
    - One FastAPI exception handler serialising
      `{"error": {"code", "message", "details"}, "request_id"}`; no stack trace, database error
      string, query, internal path or foreign internal identifier in any body
    - _Requirements: 1.5, 1.7, 22.9, 26.1_
    - Verification: `pytest tests/test_execution_environment_guard.py`

- [ ] 6. Implement `evidence_validator.py` and its properties

  - [x] 6.1 Create `backend_app/backend/marketplace/evidence_validator.py`
    - `THRESHOLDS` = `MIN_CONDITIONS 3`, `MAX_CONDITIONS 10`, `MIN_WINDOW_DAYS 90`,
      `MIN_TRADES 20` (the `backtesting_engine.py` significance threshold), `MIN_BARS 50` (the
      `backtesting_engine.py` bar guard), `MAX_OVERLAP_NUMERATOR 4`
    - `window_days`, `intersection_days` (0 when the windows do not overlap) and `distinct(a, b)`
      evaluated as integer arithmetic with no rounding:
      `intersection_days(a,b) * 4 <= MIN(window_days(a), window_days(b))`, short-circuiting to
      `True` on differing `dataset`
    - `validate(conditions, owner_id, strategy_id) -> List[CriterionOutcome]` emitting
      `EV_COUNT`, `EV_OWNERSHIP`, `EV_COMPLETED`, `EV_PARAMS`, `EV_DURATION`, `EV_TRADES`,
      `EV_BARS`, `EV_ONE_VERSION`, `EV_CHECKSUMS`, `EV_DISTINCT` — every criterion evaluated,
      none short-circuiting, so all failures are reportable in one response
    - A `NULL` `executed_bar_count` or `version_id` fails its criterion; nothing is inferred
      from equity-curve length or `total_trades`
    - Pure: no FastAPI, no I/O
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.12, 3.13, 3.14, 2.10_

  - [x] 6.2 Write property test for distinctness symmetry
    - `tests/property/test_evidence_distinctness.py::test_p33_distinct_is_symmetric`
    - **Property P-33 (invariant, symmetry)** — `distinct(c1, c2) == distinct(c2, c1)`
    - **Validates: Requirements 3.5, 3.6**

  - [x] 6.3 Write property test for distinctness irreflexivity
    - `tests/property/test_evidence_distinctness.py::test_p34_distinct_is_irreflexive`
    - **Property P-34 (invariant, irreflexivity)** — `distinct(c, c)` is false
    - **Validates: Requirements 3.5, 3.6**

  - [x] 6.4 Write property test for the overlap threshold
    - `tests/property/test_evidence_distinctness.py::test_p35_overlap_threshold_and_shrink_preserves_distinctness`
    - **Property P-35 (metamorphic, overlap threshold)** — for equal `dataset` values, `distinct`
      is true iff the calendar-day overlap is at most 25 percent of the shorter window, and
      shrinking a distinct pair's overlap keeps it distinct
    - Oracle: an ordinal-day-set implementation of the intersection, deliberately slower and
      obviously correct
    - **Validates: Requirements 3.5**

  - [x] 6.5 Write property test for dataset-difference sufficiency
    - `tests/property/test_evidence_distinctness.py::test_p36_differing_datasets_are_always_distinct`
    - **Property P-36 (invariant, dataset difference sufficiency)** — differing `dataset` values
      make a pair distinct regardless of window overlap
    - **Validates: Requirements 3.5**

  - [-] 6.6 Write property test for set admission
    - `tests/property/test_evidence_distinctness.py::test_p37_evidence_set_admission_iff_every_criterion`
    - **Property P-37 (invariant, set admission)** — admitted iff at least 3 pairwise-distinct
      conditions sharing one `version_id` with pairwise-different `dataset_checksum` values, each
      satisfying the completeness, duration, trade-count and bar-count criteria
    - **Validates: Requirements 3.1, 3.3, 3.4, 3.6, 3.7, 3.8**

  - [~] 6.7 Write property test for revalidation idempotence
    - `tests/property/test_evidence_distinctness.py::test_p38_revalidation_is_idempotent`
    - **Property P-38 (idempotence, revalidation)** — revalidating `n >= 1` times yields the same
      outcome and the same per-criterion outcomes
    - **Validates: Requirements 3.12**

  - [~] 6.8 Write property test for no metric substitution
    - `tests/property/test_evidence_distinctness.py::test_p39_missing_metric_rejects_and_nothing_is_substituted`
    - **Property P-39 (metamorphic, no substitution)** — a set with a missing metric on any
      condition is rejected and no displayed or stored Listing metric takes a value absent from
      the evidence
    - **Validates: Requirements 3.11, 2.6**

  - [~] 6.9 Write property test for ownership indistinguishability
    - `tests/property/test_evidence_distinctness.py::test_p40_foreign_owned_reference_is_indistinguishable_from_absent`
    - **Property P-40 (error-condition, ownership)** — a reference owned by another user is
      rejected with the same `EV_OWNERSHIP` code and the same response as a non-existent row
    - **Validates: Requirements 3.13**
    - Verification: `pytest tests/property/test_evidence_distinctness.py`

- [x] 7. Implement `pricing_evaluator.py`

  - [x] 7.1 Create `backend_app/backend/marketplace/pricing_evaluator.py`
    - `EVALUATOR_VERSION = 'pricing-evaluator/1.0.0-statistical'`, `WEIGHTS` summing to 100,
      per-currency `ANCHORS` (`USD (2000, 18000)`, `INR (150000, 1350000)`),
      `MINIMUM_RATIO_PCT 60`, `MAXIMUM_RATIO_PCT 250`, `ABSOLUTE_MAX_MINOR 100_000_000`
    - `quality_score(conditions)` in `Decimal` under an explicit `localcontext(prec=28)`,
      quantized to an integer 0…100 with `ROUND_HALF_EVEN` before use; `price_range` then in
      integer Minor_Units only, with the closing
      `ASSERT 1 <= mn <= rec <= mx <= ABSOLUTE_MAX_MINOR`
    - `inputs_digest(conditions)` — canonical, order-independent SHA-256 over conditions sorted
      by `source_backtest_id`, `Decimal`s as their `str()` form, dates ISO-8601, JSON with sorted
      keys and no whitespace
    - Raises `MissingEvidenceInputs(names)` when any Requirement 8.2 input is absent
    - Imports neither `ml_models` nor `model_versioning`, and reports no accuracy, confidence,
      precision or error figure; no `float` appears anywhere
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.6, 8.7, 8.13, 8.14_

  - [x] 7.2 Write property test for Price_Range ordering and bounds
    - `tests/property/test_pricing_evaluator.py::test_p51_price_range_is_ordered_bounded_and_integral`
    - **Property P-51** — `1 <= minimum <= recommended <= maximum <= 100000000`, all three are
      Python `int` in Minor_Units, and no intermediate value anywhere in the computation is a
      binary floating-point number (asserted by walking the computation's intermediates)
    - **Validates: Requirements 8.1, 8.4, 8.12, 8.13**
    - Verification: `pytest tests/property/test_pricing_evaluator.py -k p51`

- [x] 8. Implement `listing_projection.py` and the batched alias read

  - [x] 8.1 Create `backend_app/backend/marketplace/listing_projection.py`
    - `PUBLIC_LISTING_FIELDS` (the allow-list), `LISTING_SELECT` (the explicit column list that
      replaces `select("*")`), `DENIED_LISTING_COLUMNS`
    - `project_listing(row, evidence_summaries, creator_alias)` building a **fresh** dict by
      explicit assignment per allow-listed field — never copying the row, never deleting from it;
      omitting `avg_rating` and `rating_count` entirely when `rating_count = 0` or `avg_rating`
      is null; ending with the runtime `assert set(out) <= PUBLIC_LISTING_FIELDS`
    - `condition_summaries` carry `label` (`"Condition 1"`, …) and outcome metrics only — no
      `dataset`, `start_date`, `end_date`, `dag_hash`, `dataset_checksum`, `blueprint`, node,
      indicator, threshold or model identifier
    - `protected_logic_tokens(strategy_row, version_row, backtest_rows)` over
      `deep_scalars` of every Protected_Logic document with a `len(text) >= 3` floor, and
      `assert_contains_no_protected_logic(response, tokens)` doing a substring check at any
      nesting depth including free-text and collection fields
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.9, 7.1_

  - [x] 8.2 Create `backend_app/backend/marketplace/aliases.py`
    - `resolve_aliases(author_ids) -> Dict[str, str]` — one batched
      `profiles.select("id,display_name").in_("id", author_ids)`, replacing the per-row
      `_get_author_alias` N+1 (`library.py` line 234)
    - A failed batched read raises `MarketplaceError(MARKETPLACE_READ_FAILED)`; the fabricated
      `"Anonymous"` fallback is removed, because a substituted alias for a creator whose profile
      read failed is exactly what Requirement 28.5 forbids
    - _Requirements: 6.5, 27.1, 28.5, 1.5_

  - [x] 8.3 Create `tests/test_listing_projection.py`
    - `test_no_star_select_on_listings` — AST walk of `library.py`: no `.select("*")` argument on
      a `.table("library_strategies")` chain
    - `test_every_public_read_projects` — AST walk: every `@router.get` function without an
      ownership dependency contains a `project_listing` call and does not `return` a name bound
      from an `.execute()` result
    - `test_projection_output_is_allow_listed` — Hypothesis `listing_rows()` (every denied column
      populated) through `project_listing`, asserting `set(result) <= PUBLIC_LISTING_FIELDS`
    - _Requirements: 6.1, 6.2, 6.4_
    - Verification: `pytest tests/test_listing_projection.py` — the first two assertions fail
      against the current `library.py` and pass after Task 16

- [ ] 9. Implement `paper_accounting.py` and the reference ledger

  - [x] 9.1 Create `backend_app/backend/paper/paper_accounting.py`
    - `Decimal` only, explicit `localcontext(prec=34)`, rounding mode read from the session
      config; `quantize_money`, `quantize_qty`
    - `position_market_value(positions, prices)` retaining the existing SHORT convention
      `size * (2 * entry_price - price)` from `paper_trading_service._recalculate_account`;
      raises `StalePrice(symbol)` rather than substituting a price
    - `invariants_hold(account, positions, prices)` asserting
      `total_equity == available + locked + position_market_value` as **exact** equality with
      zero tolerance (replacing the existing `Decimal("0.05")` tolerance),
      `available >= 0`, `locked >= 0`, every `size >= 0`, every `side in ('LONG','SHORT')`
    - `total_equity` is the computed sum, never an independently maintained running total
    - `required_funds`, `lock`, `apply_fill`, `recalculate`, realized PnL from closed quantity at
      recorded fill prices under the one `config.cost_basis` convention, unrealized PnL from open
      quantity at the latest validated price with `price_at` recorded, `max_drawdown` from
      persisted snapshots in non-decreasing order (zero below two snapshots), `win_rate` in
      `[0,1]` and **absent** when the closed-trade count is zero, margin recorded only where the
      market type supports it
    - A fully closed position is `Decimal('0')` with `closed_at` set — never deleted, never
      compared against a `0.00000001` tolerance
    - Pure: no FastAPI, no I/O, no `random`
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9, 18.10, 18.12, 18.15_

  - [x] 9.2 Create `backend_app/backend/paper/paper_replay.py::ReferenceLedger`
    - A deliberately naive second implementation of the same rules — no locking, no persistence,
      no partial-fill caching, one Python dict — used as the model oracle for P-25…P-31
    - _Requirements: 15.4, 18.13_

  - [x] 9.3 Write property test for the equity identity
    - `tests/property/test_paper_accounting.py::test_p25_equity_identity_holds_after_every_event`
    - **Property P-25 (invariant, equity identity)** — `total_equity == available_balance +
      locked_balance + position_market_value` after every event
    - **Validates: Requirements 18.3**

  - [x] 9.4 Write property test for non-negativity
    - `tests/property/test_paper_accounting.py::test_p26_balances_and_position_sizes_are_non_negative`
    - **Property P-26 (invariant, non-negativity)** — `available_balance >= 0`,
      `locked_balance >= 0`, every position size `>= 0` after every event
    - **Validates: Requirements 18.4, 18.5**

  - [x] 9.5 Write property test for zero-cost fill conservation
    - `tests/property/test_paper_accounting.py::test_p27_zero_cost_fill_conserves_equity`
    - **Property P-27 (invariant, cash conservation)** — with zero fee and zero slippage,
      `total_equity` is unchanged across the fill, valued at that fill's price
    - **Validates: Requirements 18.6**

  - [x] 9.6 Write property test for fee accounting
    - `tests/property/test_paper_accounting.py::test_p28_equity_decrease_equals_recorded_fees`
    - **Property P-28 (invariant, fee accounting)** — the decrease in `total_equity` attributable
      to costs equals the exact sum of recorded fees, with no residual
    - **Validates: Requirements 18.7**

  - [-] 9.7 Write property test for drawdown bounds
    - `tests/property/test_paper_accounting.py::test_p29_drawdown_bounds_and_peak_append_invariance`
    - **Property P-29 (metamorphic, drawdown bounds)** — reported max drawdown is `>= 0`, at most
      series max minus series min, and unchanged by appending an equity value at or above the
      running peak
    - Oracle: an independent running-peak scan
    - **Validates: Requirements 18.9**

  - [~] 9.8 Write property test for the win-rate range
    - `tests/property/test_paper_accounting.py::test_p30_win_rate_range_and_absent_when_no_closed_trades`
    - **Property P-30 (invariant, win rate range)** — win rate in `[0,1]` and equal to
      winning/closed; **absent**, not zero, for an empty closed-trade set
    - **Validates: Requirements 18.10**

  - [ ]* 9.9 Write unit tests for cost-basis and short-position edge cases
    - `tests/test_paper_accounting_cost_basis.py` — weighted-average basis across a partial
      close, a reversal through zero, and a short position revalued above and below entry
    - Optional: P-25…P-31 already cover the generated input space; these pin the specific
      formulas the existing `Portfolio.jsx` figures depend on
    - _Requirements: 18.8_
    - Verification: `pytest tests/property/test_paper_accounting.py`

- [~] 10. Checkpoint — pure modules complete
  - Ensure all tests pass, `black --check backend_app`, `isort --check-only backend_app` and
    `flake8 backend_app --select=E9,F63,F7,F82` are clean, and no module under
    `backend_app/backend/marketplace/` or `backend_app/backend/paper/` imports FastAPI or
    performs I/O. Ask the user if questions arise.

- [ ] 11. Land the migration set in dependency order

  - [x] 11.1 Create `backend_app/migrations/006_backtest_evidence_columns.sql`
    - The nine additive `strategy_backtests` columns (`version_id` **nullable**, `final_capital`,
      `executed_bar_count`, `engine_version`, `schema_version`, `dag_hash`, `dataset_checksum`,
      `completed_at`, `error_message`), reconciling the two divergent definitions in
      `backend_app/migrations/001_strategy_architecture.sql` line 118 and
      `migrations/006_reconcile_production_database.sql` line 445
    - A column-shape assertion `DO` block after the `ADD COLUMN` group, copied from
      `005b_signal_lifecycle_and_idempotency.sql` section 1.2, because `ADD COLUMN IF NOT EXISTS`
      is silent about a pre-existing column of a different type
    - `chk_sb_executed_bar_count CHECK (executed_bar_count IS NULL OR executed_bar_count >= 0)`
      behind a `pg_constraint` existence guard
    - Indexes on `(user_id, strategy_id, status)` and `(version_id)`; one transaction; no `DROP`
    - _Requirements: 3.4, 3.8, 24.2, 24.4, 24.7, 24.8, 24.9_

  - [x] 11.2 Create `backend_app/migrations/007_marketplace_submissions.sql`
    - `marketplace_submissions`, `marketplace_submission_transitions`,
      `marketplace_backtest_evidence`, `marketplace_price_evaluations`,
      `marketplace_submission_allowed_transitions` + its eleven-pair
      `INSERT … ON CONFLICT DO NOTHING` seed
    - `chk_submission_state`, `chk_submission_rejection_reason`,
      `uq_submission_open_per_strategy` (partial unique on `source_strategy_id` where the state is
      in `SUBMITTED`/`UNDER_REVIEW`/`APPROVED`/`PUBLISHED`),
      `idx_submissions_state (submission_state, submitted_at DESC, id DESC)`,
      `idx_submissions_owner`, `idx_submissions_listing`
    - `uq_evidence_submission_backtest`, `uq_evidence_submission_checksum`,
      `chk_evidence_window`, `chk_evidence_trades`, `chk_evidence_bars`,
      `chk_price_range_ordered`, `idx_price_eval_lookup`
    - `marketplace_submission_guard()` + `trg_submission_transition_guard` (rejects a transition
      absent from the seed table and a `REJECTED` without a 1–2000-character trimmed reason,
      `ERRCODE '23514'`), `trg_submission_projects_moderation_status` (applies
      `MODERATION_STATUS_FOR_STATE` / `IS_ACTIVE_FOR_STATE` to `library_strategies`),
      `trg_submission_transitions_append_only`, `trg_evidence_append_only`, and the matching
      `REVOKE UPDATE, DELETE` grants
    - The additive `library_strategies` columns `price_minor`, `source_cloning_enabled BOOLEAN NOT
      NULL DEFAULT FALSE`, `supported_timeframes`, `market_type`, `condition_count`, plus
      `chk_ls_price_minor` and `chk_ls_featured_requires_published`, and the trigger mirroring
      `price` from `price_minor`
    - RLS enabled with an owner policy and a service-role policy on every new table, following
      `library_subscriptions` in `migrations/006_reconcile_production_database.sql`
    - **In this same task**: add `marketplace_submissions`, `marketplace_submission_transitions`,
      `marketplace_backtest_evidence`, `marketplace_price_evaluations` and
      `marketplace_submission_allowed_transitions` to **both** `app_tables` and
      `migration_tables` in `tests/test_schema_as_code_completeness.py`, and add `price_minor`,
      `source_cloning_enabled`, `supported_timeframes`, `market_type` and `condition_count` to
      `REQUIRED_LIBRARY_COLUMNS` in `tests/test_library_schema_contract.py`
    - _Requirements: 1.2, 2.8, 3.9, 3.10, 4.5, 4.12, 4.13, 7.4, 8.4, 21.2, 21.3, 24.1, 24.2, 24.3, 24.4, 24.5, 24.7, 24.9_

  - [x] 11.3 Create `backend_app/migrations/008_marketplace_settlement.sql`
    - `marketplace_settlements` with `chk_settlement_amounts`, `chk_settlement_conserved`,
      `chk_settlement_currency`, `chk_settlement_provider`,
      `chk_settlement_reversal_reference`, `uq_settlement_reference_reversal UNIQUE
      (provider_reference, is_reversal)`, `idx_settlements_owner_currency`, `ON DELETE RESTRICT`
      on both the subscription and listing references, `trg_settlements_append_only` and the
      `REVOKE UPDATE, DELETE` grants
    - `library_subscription_transitions` (append-only) and
      `marketplace_subscription_allowed_transitions` + its twelve-pair seed
    - The additive `library_subscriptions` columns `owner_id`, `price_minor`,
      `owner_share_minor`, `platform_fee_minor`, `period_start`, `period_expiry`, `provider`,
      `provider_reference`, `provider_session_at`, `failure_cause`, `failed_at`,
      `renewal_enabled`; `valid_subscription_status` widened additively to the seven lowercase
      spellings; `chk_ls_active_has_period`; `chk_ls_split_conserved`;
      `idx_lib_subs_expiry ON (period_expiry) WHERE status IN ('active','suspended')`; the trigger
      mirroring `started_at`/`expires_at` from the period columns
    - `trg_subscription_transition_guard` rejecting a transition absent from the seed table **and**
      any transition into `'active'` for which no non-reversal `marketplace_settlements` row
      matches the subscription with `settled_at` at or after the row's current `period_expiry` —
      so a direct SQL update cannot grant a free month
    - RLS: owner policy, purchaser policy on `marketplace_settlements`, service-role policy
    - **In this same task**: add `marketplace_settlements`, `library_subscription_transitions` and
      `marketplace_subscription_allowed_transitions` to both sets in
      `tests/test_schema_as_code_completeness.py`
    - _Requirements: 1.2, 9.7, 10.5, 10.8, 11.2, 11.3, 11.11, 11.12, 11.13, 11.14, 21.2, 24.1, 24.2, 24.3, 24.4, 24.5, 24.7_

  - [-] 11.4 Create `backend_app/migrations/009_paper_trading.sql`
    - The eleven `paper_*` tables: `paper_sessions`, `paper_accounts`, `paper_orders`,
      `paper_fills`, `paper_positions`, `paper_balance_events`, `paper_trades`,
      `paper_equity_snapshots`, `paper_metrics`, `paper_events`, `paper_market_events`, with
      every column, type, check, unique and index named in `design.md § Data Models`
    - Notably `chk_paper_session_environment CHECK (environment = 'PAPER')`,
      `chk_paper_session_state`, `chk_paper_capital`,
      `uq_paper_account_default` / `uq_paper_account_session`,
      `chk_paper_balances_non_negative`, `chk_paper_order_state`,
      `chk_paper_order_fill_bound`, `chk_paper_order_idem_len`, `uq_paper_order_idem`,
      `uq_paper_fill_event`, `uq_paper_position_open`, `chk_paper_win_rate` (nullable),
      `chk_paper_drawdown_fraction`, `chk_paper_event_type` (16 values),
      `chk_paper_event_sequence`, `uq_paper_event_seq`, `uq_paper_event_id`,
      `uq_paper_market_event`, `idx_paper_sessions_user`, `idx_paper_sessions_running`,
      `idx_paper_orders_session_state`, `idx_paper_events_replay`, `idx_paper_equity`,
      `idx_paper_trades`, `idx_paper_balance_events`, `idx_paper_market_events`
    - `paper_order_allowed_transitions` and `paper_session_allowed_transitions` + seeds,
      `trg_paper_order_transition_guard`, `trg_paper_session_guard`,
      `trg_paper_session_config_immutable`, and the append-only triggers on `paper_fills`,
      `paper_trades`, `paper_equity_snapshots`, `paper_events` and `paper_market_events`
    - `NUMERIC(28,10)` for every quantity and money column; every child cascades from
      `paper_sessions`, which cascades from `auth.users`
    - RLS enabled with an owner policy and a service-role policy on all eleven tables
    - **In this same task**: add all eleven `paper_*` tables plus
      `paper_order_allowed_transitions` and `paper_session_allowed_transitions` to **both**
      `app_tables` and `migration_tables` in `tests/test_schema_as_code_completeness.py`
    - _Requirements: 1.2, 16.1, 16.4, 16.11, 17.1, 17.11, 18.4, 18.5, 18.10, 19.3, 21.2, 21.3, 24.1, 24.2, 24.3, 24.4, 24.5, 24.7_

  - [~] 11.5 Create `backend_app/migrations/010_signal_environment.sql`
    - `signals.environment TEXT` and `paper_session_id UUID` added additively; the column-shape
      assertion `DO` block; `UPDATE … SET environment = 'LIVE' WHERE environment IS NULL`; then
      `SET DEFAULT 'LIVE'` and `SET NOT NULL`, in one transaction, so the three-value vocabulary
      is total from the moment it commits
    - `chk_signals_environment CHECK (environment IN ('BACKTEST','PAPER','LIVE'))` behind a
      `pg_constraint` guard; `fk_signals_paper_session REFERENCES public.paper_sessions(id) ON
      DELETE SET NULL`; `idx_signals_user_environment`; partial `idx_signals_paper_session`
    - The RLS precondition check that raises a **named** error rather than adding columns to a
      table whose row-level isolation is off, as `005b` does
    - Raises a named missing-`paper_sessions` error when applied without `009`
    - Records the operator note that `SET NOT NULL` takes an `ACCESS EXCLUSIVE` lock and scans the
      table, so this file belongs in a maintenance window; re-running it is a no-op
    - _Requirements: 23.1, 23.7, 24.1, 24.2, 24.4, 24.7, 24.8_

  - [~] 11.6 Declare the column manifests and create `tests/test_marketplace_paper_schema_contract.py`
    - `marketplace/__init__.py::COLUMN_CONTRACT` and `paper/__init__.py::COLUMN_CONTRACT` — a
      per-handler data structure naming every column each new or modified handler reads or writes
    - The test parses `CREATE TABLE`, `ALTER TABLE … ADD COLUMN`, `CREATE INDEX`,
      `ADD CONSTRAINT` and `CREATE POLICY` from every file in `migrations/` and
      `backend_app/migrations/` into an in-memory schema model, then asserts: every
      `(table, column)` pair in both manifests exists; every `.select("…")` string literal in the
      new and modified modules names only columns in that module's manifest entry; every
      constraint, index and policy `design.md` names is present; every new table carries
      `ENABLE ROW LEVEL SECURITY` and both policies; no file contains `DROP TABLE`,
      `DROP COLUMN`, `RENAME`, `DELETE FROM` or `TRUNCATE`
    - This is the mechanism that prevents a repeat of the PostgreSQL `42703` condition recorded in
      the header of `migrations/007_add_marketplace_pricing_columns.sql`
    - _Requirements: 1.3, 1.9, 21.2, 24.2, 24.3, 24.4, 24.7, 24.9, 24.10_

  - [~] 11.7 Create `tests/test_marketplace_paper_migrations.py`
    - Apply from empty; apply from a simulated current production revision built by applying
      `migrations/00[1-7]*` and `backend_app/migrations/00[1-5]*` first; assert the
      destructive-statement deny-list; apply `010` alone and expect the named
      missing-`paper_sessions` error; assert the `signals` back-fill sets every pre-existing row
      to `'LIVE'` and changes no other column
    - _Requirements: 23.7, 24.7, 24.8, 24.10_

  - [~] 11.8 Write property test for migration idempotency
    - `tests/test_marketplace_paper_schema_contract.py::test_p58_migration_set_is_idempotent`
    - **Property P-58** — for all application orders consistent with the declared dependency order
      and all repetition counts `n >= 1`, applying the set `n` times to an empty database yields
      the same tables, columns, types, constraints, indexes and policies as applying it once;
      applying it at the current production revision yields that same schema; no application
      deletes a row or drops or renames a column or table
    - **Validates: Requirements 24.7, 24.8, 24.9**
    - Verification: `pytest tests/test_schema_as_code_completeness.py
      tests/test_library_schema_contract.py tests/test_marketplace_paper_schema_contract.py
      tests/test_marketplace_paper_migrations.py`

- [x] 12. Root-cause fix 6 — record the executed bar count

  - [x] 12.1 Thread `executed_bar_count` from the runtime into the persisted row
    - `backend/backtest_runtime.run_backtest` already knows `len(ohlcv_data)` at line 302; pass it
      as `executed_bar_count=len(ohlcv_data)` into
      `backend/backtest_service.update_backtest_results`, which adds it to `update_data`
    - That is the whole code change: the 50-bar guard (`backtesting_engine.py` line 189) and the
      20-trade significance warning (line 380) are untouched
    - _Requirements: 3.8, 3.4, 25.1_

  - [x] 12.2 Create `tests/test_backtest_evidence_columns_regression.py`
    - Asserts, against the **current** code, that a completed backtest persists no
      `executed_bar_count` — the test fails before Task 12.1 and passes after
    - Asserts `version_id`, `final_capital`, `engine_version`, `schema_version`, `dag_hash`,
      `dataset_checksum`, `completed_at` and `error_message` are all writable and readable after
      migration `006`, closing the two-divergent-definitions gap
    - _Requirements: 3.4, 3.8_
    - Verification: `pytest tests/test_backtest_evidence_columns_regression.py`

- [x] 13. Root-cause fix 5 — unshadow the three unreachable `GET /api/library/*` routes

  - [x] 13.1 Declare every literal-segment route before the parameterised ones
    - `library.py` currently registers `GET /creator/{creator_id}` (line 624) and
      `GET /{library_id}` (line 929) **before** `GET /creator/analytics` (line 2338),
      `GET /recommendations` (line 2459) and `GET /favorites` (line 2669), so all three return
      HTTP 422 (`_safe_uuid` on `"analytics"`, and the `{library_id}` match on the other two)
    - Move the admin, creator, subscriber, recommendation, favourite and submission routes into an
      `APIRouter()` declared above `@router.get("/{library_id}")`, changing no path string, no
      handler body and no dependency
    - _Requirements: 1.3, 22.1_

  - [x] 13.2 Create `tests/test_library_route_resolution.py`
    - For every literal path the router exposes, assert `app.router.routes` resolves it to the
      intended endpoint function, so a future reordering cannot silently shadow another one
    - Assert specifically that `GET /api/library/creator/analytics`,
      `GET /api/library/recommendations` and `GET /api/library/favorites` resolve to their own
      handlers and not to `get_creator_profile` or `get_library_detail` — these three assertions
      fail against the current code
    - Assert `GET /admin/pending` and `GET /subscriber/analytics` still resolve as they do today
    - _Requirements: 1.3, 22.1_
    - Verification: `pytest tests/test_library_route_resolution.py tests/test_router_registration_completeness.py`

- [ ] 14. Build the Submission lifecycle, the Eligibility_Gate and the admin review surface

  - [~] 14.1 Create `backend_app/backend/marketplace/eligibility_gate.py`
    - `evaluate(caller, strategy_id, backtest_ids, supabase) -> EligibilityVerdict` performing
      exactly four owner-scoped round trips (`strategies`, `strategy_versions`,
      `strategy_backtests` filtered `.in_("id", backtest_ids)` **and** `.eq("user_id", owner_id)`,
      then the open-state probe over `marketplace_submissions` + `library_strategies`)
    - Emits `MP_OWNERSHIP`, `MP_TENANT`, `MP_VERSION_EXISTS`, `MP_VERSION_VALID`,
      `MP_EXECUTION_OK`, the `evidence_validator.validate` outcomes, `MP_METRICS_COMPLETE` (all
      seven metrics non-null and finite) and `MP_SUBMISSION_OPEN` — **every** criterion evaluated,
      none short-circuiting
    - A failed read short-circuits to `MARKETPLACE_ELIGIBILITY_UNEVALUABLE` (503), distinct by
      code from a criteria failure; it derives nothing from `strategies.backtest_result`
    - Writes `StrategyAuditAction.MARKETPLACE_ELIGIBILITY_EVALUATED` on every outcome, admitted or
      not, with the strategy id, evaluated version id, acting identity, per-criterion outcomes,
      evaluator version and timestamp
    - The `STRATEGY_SHARING` / publish check is **not** in this function — it is the existing
      `Depends(require_marketplace_publish)` on the route, so a caller lacking it gets 403 before
      any read
    - _Requirements: 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.9, 2.10, 2.11, 2.13_

  - [~] 14.2 Create `backend_app/backend/marketplace/submission_service.py`
    - `create_submission` in one transaction: the `marketplace_submissions` insert, one
      `marketplace_backtest_evidence` row per condition carrying `source_backtest_id` and the
      immutable copy of the ten parameters and eight metrics, then the `DRAFT → SUBMITTED` update
      and its transition row. Any failure rolls the whole thing back —
      `MARKETPLACE_EVIDENCE_PERSIST_FAILED`, no partial evidence, no state change
    - Translates `23505` on `uq_submission_open_per_strategy` into
      `MARKETPLACE_SUBMISSION_ALREADY_OPEN` (409), creating no row and changing no state
    - `apply_admin_action(admin, submission_id, action, reason)` with `SELECT … FOR UPDATE`, the
      `can_transition` check against the state read **inside** the transaction, `validate_reason`
      for `REJECTED`, the transition-history insert and the `record_or_raise` audit write — all
      one transaction, rolling back to `MARKETPLACE_ACTION_NOT_RECORDED` when the audit fails
    - `admin_detail` reading `marketplace_backtest_evidence` — the immutable copy — and never
      `strategy_backtests.blueprint`, `strategy_versions.blueprint` or any `strategies` logic
      column
    - A mutation attempt on persisted evidence returns `MARKETPLACE_EVIDENCE_IMMUTABLE` (409)
    - _Requirements: 2.12, 3.9, 3.10, 3.14, 3.15, 4.3, 4.4, 4.6, 4.8, 4.9, 4.10, 4.11, 4.13, 5.3, 5.4, 5.5, 5.6, 5.8, 5.9, 5.10, 5.11, 24.6_

  - [~] 14.3 Extend `backend_app/core/audit_trail.py`
    - Add every new member of `StrategyAuditAction` listed in `design.md § Audit records`
      (`MARKETPLACE_SUBMISSION_CREATED`, `_TRANSITIONED`, `MARKETPLACE_ADMIN_ACTION`,
      `MARKETPLACE_PRICE_EVALUATED`, `MARKETPLACE_CHECKOUT_CREATED`,
      `MARKETPLACE_PAYMENT_CONFIRMED`, `MARKETPLACE_SETTLEMENT_CREATED`,
      `MARKETPLACE_SUBSCRIPTION_TRANSITIONED`, `MARKETPLACE_PERIOD_EXTENDED`,
      `MARKETPLACE_ENTITLEMENT_GRANTED`/`_REVOKED`, `MARKETPLACE_ELIGIBILITY_EVALUATED`,
      `MARKETPLACE_ACCESS_REFUSED`, `MARKETPLACE_CROSS_TENANT_ATTEMPT`,
      `MARKETPLACE_SETTLEMENT_UNMATCHED`/`_MISMATCHED`/`_DUPLICATE_IGNORED`/`_PERSIST_FAILED`,
      `EXECUTION_ENVIRONMENT_MISMATCH`, `PAPER_SIMULATOR_MISCONFIGURED`,
      `PAPER_FEED_REFUSED_MOCK_INTERFACE`) and the matching `StrategyAuditRecord` fields
    - Add `StrategyAuditLogger.record_or_raise(...)` sharing the whole body of `record` and
      re-raising instead of swallowing; `record` keeps its never-raises contract for every
      existing caller
    - No second audit facility is introduced
    - _Requirements: 2.11, 5.6, 5.11, 7.12, 10.9, 11.12, 13.9, 13.11, 14.8, 21.4, 26.2_

  - [~] 14.4 Add the submission and admin routes to `library.py`
    - `POST /api/library/submissions` (`Depends(require_marketplace_publish)`, 10/60s),
      `GET /api/library/submissions/{id}` (own Submission only, another owner's answered
      identically to unknown)
    - The six admin routes `GET /api/library/admin/submissions` (state filter, all states when
      unfiltered, `submitted_at DESC, id DESC`, `page_size` 1–100 default 25 clamped to 100,
      `total` with each page), `GET /api/library/admin/submissions/{id}`, and
      `POST …/{id}/approve|reject|publish|suspend|unpublish`, each carrying
      `admin: dict = Depends(get_admin_user)` — no inline role check, no client-supplied
      capability
    - All declared **inside the literal-segment router of Task 13.1**
    - _Requirements: 2.12, 4.8, 4.9, 4.10, 5.1, 5.2, 5.3, 5.4, 5.5, 5.7, 5.9, 5.10, 22.1, 22.2, 22.4, 22.10_

  - [~] 14.5 Narrow the existing `admin_moderate_strategy` (line 1687)
    - Retained, because it is the only route that sets `is_featured` and because
      `tests/test_marketplace_pipeline.py` tests 04, 05, 17 and 18 assert on it
    - It may set `is_featured` and `moderation_notes`; its `moderation_status` parameter is
      accepted only when the value agrees with
      `MODERATION_STATUS_FOR_STATE[current submission state]`, otherwise 409
      `MARKETPLACE_USE_SUBMISSION_ACTIONS` — so it can no longer change the effective lifecycle
      behind the state machine's back
    - _Requirements: 4.2, 4.12_

  - [~] 14.6 Create the state-agreement tests
    - `tests/test_submission_state_agreement.py` reads the seed statements of
      `marketplace_submission_allowed_transitions` from `007_marketplace_submissions.sql` and the
      `SUBMISSION_TRANSITIONS` Python constant and asserts they are the same set — the technique
      `tests/test_version_consumer_agreement.py` already uses
    - Sibling tests in the same file family for `marketplace_subscription_allowed_transitions` vs
      `SUBSCRIPTION_TRANSITIONS`, `paper_order_allowed_transitions` vs
      `PAPER_ORDER_TRANSITIONS`, and `paper_session_allowed_transitions` vs the session state
      machine
    - Also asserts `MODERATION_STATUS_FOR_STATE` never yields `'featured'` and that no handler
      writes `moderation_status` directly
    - _Requirements: 4.1, 4.2, 4.12, 11.1, 11.2, 16.1, 16.2, 17.7_

  - [~] 14.7 Create `tests/test_admin_review_surface.py`
    - Applies `listing_projection.assert_contains_no_protected_logic` to both admin responses
    - Asserts the list ordering, the page-size clamp and the returned `total`; asserts a
      non-Admin_Reviewer gets 403 from `get_admin_user` before any read, with a body that does
      not reveal whether the named Submission exists; asserts an unknown Submission and a rejected
      transition carry distinct codes; asserts a failed audit write rolls the state change back
    - _Requirements: 5.1, 5.2, 5.3, 5.7, 5.8, 5.9, 5.11_

  - [~] 14.8 Write property test for database-enforced transition refusal
    - `tests/property/test_db_transition_guards.py::test_p49_database_refuses_illegal_transitions`
    - **Property P-49** — for all four state machines and all ordered pairs absent from the
      permitted set, an `UPDATE` issued **directly to the Persistence_Layer**, bypassing every
      service module, leaves the stored value unchanged, raises an error naming the rejected
      transition, records no transition-history row, and (for Submission_State) leaves the
      Listing's visibility unchanged
    - Oracle: the four Python transition tables
    - **Validates: Requirements 4.4, 11.3, 16.4, 17.14, 24.2**

  - [~] 14.9 Write property test for Listing visibility
    - `tests/property/test_listing_visibility.py::test_p50_listing_visible_iff_published`
    - **Property P-50** — a Listing appears in every public catalogue and detail response iff its
      Submission_State is `PUBLISHED`; in any other state a non-owner's response is identical in
      status and body to the response for an identifier existing in no tenant
    - Oracle: `submission_state.PUBLIC_STATES`
    - **Validates: Requirements 4.6, 4.7, 6.10**

  - [~] 14.10 Update `tests/test_marketplace_pipeline.py` tests 01, 14, 15, 16
    - `publish_strategy` no longer admits a strategy on the strength of one
      `strategies.backtest_result` blob, no longer computes `eval_score`, and no longer returns
      `suggested_price`; the publish path becomes `POST /api/library/submissions` carrying three
      backtest ids
    - No assertion is weakened, skipped, xfailed or excluded by selector
    - _Requirements: 1.6, 2.5, 3.1, 8.11, 25.8, 29.9_

  - [~] 14.11 Update `tests/test_marketplace_pipeline.py` tests 04, 05, 17, 18
    - `admin_moderate_strategy` is narrowed to `is_featured` and `moderation_notes`; lifecycle
      changes move to the submission actions of Task 14.4
    - _Requirements: 4.2, 4.12, 25.8_

  - [~] 14.12 Extend `tests/test_marketplace_concurrency.py` with the submission battery
    - N concurrent `POST /api/library/submissions` for one strategy: exactly one
      `marketplace_submissions` row, N−1 `MARKETPLACE_SUBMISSION_ALREADY_OPEN`, and the winner's
      state unchanged by the losers
    - _Requirements: 2.8, 25.8_
    - Verification: `pytest tests/test_marketplace_pipeline.py tests/test_marketplace_concurrency.py
      tests/test_admin_review_surface.py tests/test_submission_state_agreement.py
      tests/property/test_db_transition_guards.py tests/property/test_listing_visibility.py`

- [ ] 15. Implement server-side pricing enforcement

  - [~] 15.1 Add the Price_Range and price endpoints and delete the hardcoded price points
    - `POST /api/library/submissions/{id}/price-range` (30/60s per authenticated caller) computing
      the range, persisting a `marketplace_price_evaluations` row with `inputs_digest`,
      `evaluator_version` and a timestamp, and returning the triple; a rate-limited request
      computes nothing and persists nothing
    - `POST /api/library/submissions/{id}/price {price_minor, currency}` as the single enforcement
      point: look up the persisted evaluation by `(submission_id, currency, inputs_digest)`,
      recompute when the digest does not match, accept only
      `minimum <= price_minor <= maximum`, otherwise 400 `MARKETPLACE_PRICE_OUT_OF_RANGE`
      returning the permitted range and creating no Listing price change
    - Delete `publish_strategy`'s `199.99 / 99.99 / 49.99 / 29.99` price points (lines 2298–2308),
      the `eval_score` computation (lines 2260–2280) and the `min_score_for_free = 50` /
      `min_score_for_paid = 70` gate from the publication path; `evaluation_score` remains a
      column, is written `NULL` by the new path, and stays in `DENIED_LISTING_COLUMNS`
    - _Requirements: 8.8, 8.9, 8.10, 8.11, 8.12, 8.14, 8.15, 22.2, 22.4_

  - [~] 15.2 Write property test for Price_Range determinism and enforcement
    - `tests/property/test_pricing_evaluator.py::test_p52_price_range_is_deterministic_and_enforced`
    - **Property P-52** — evaluating `n >= 1` times under one evaluator version yields an
      identical Price_Range and an identical inputs digest; and the server accepts `p` iff
      `minimum <= p <= maximum`, rejecting `minimum - 1` and `maximum + 1`, accepting both
      boundaries, and changing no Listing price on rejection
    - **Validates: Requirements 8.3, 8.8, 8.9**
    - Verification: `pytest tests/property/test_pricing_evaluator.py`

- [ ] 16. Root-cause fix 4 — the public Listing projection

  - [~] 16.1 Rewrite `get_library_detail` (`library.py` lines 929–1010)
    - Replace `.select("*") … .single()` + `dict(resp.data)` + `pop("author_id")` with
      `.select(listing_projection.LISTING_SELECT)` and
      `return listing_projection.project_listing(row, evidence_summaries, creator_alias)`
    - Narrow the `except Exception: pass` around the user-rating enrichment so that it **omits**
      `user_rating` rather than setting it to `None` — an omitted field and a null field mean
      different things
    - A Listing whose Submission is not `PUBLISHED` answers to a non-owner exactly as a
      non-existent Listing does, disclosing neither existence, owner nor Submission_State
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.9, 6.10, 28.5_

  - [~] 16.2 Repoint every remaining public read at `project_listing` and the batched alias read
    - `browse_library`, `get_featured_strategies`, `get_trending_strategies`,
      `get_creator_profile`, `compare_strategies`, `get_recommendations`, `get_user_favorites` and
      `my_library`'s non-owner view all call `project_listing` with a pre-resolved
      `alias_by_author_id` map from `aliases.resolve_aliases`, replacing the per-row
      `_get_author_alias` call (51 round trips for a 50-Listing page today)
    - Apply the same projection to authenticated and unauthenticated catalogue, search and detail
      responses
    - _Requirements: 6.1, 6.2, 6.7, 27.1_

  - [~] 16.3 Create `tests/test_library_detail_projection_regression.py`
    - Asserts, against the **current** code, that `GET /api/library/{id}` returns
      `source_strategy_id`, `moderation_notes`, `moderated_by`, `moderated_at`,
      `evaluation_score`, `deployment_requirements`, `version_history`,
      `equity_curve_snapshot`, `risk_stop_loss_pct`, `risk_take_profit_pct`,
      `risk_max_position_size`, `risk_max_drawdown_pct` and `has_ml_model` — the test fails
      before Task 16.1 and passes after
    - Asserts `author_id`, email and authentication identity never appear, and that the creator is
      represented by a display alias only
    - _Requirements: 6.4, 6.5_

  - [~] 16.4 Write property test for Protected_Logic containment
    - `tests/property/test_protected_logic_containment.py::test_p47_no_response_or_event_contains_protected_logic`
    - **Property P-47** — for generated Listings backed by strategies containing DAG nodes,
      indicator parameters, risk configuration and bound ML models, no field of any
      Marketplace_API response reachable by a non-owner, and no field of any Paper_Channel event,
      contains any substring of the strategy's Protected_Logic serialisation — at any nesting
      depth, for both an authenticated non-owner and an unauthenticated caller, and including
      error and diagnostic bodies
    - Generator `protected_logic_strategies()`; oracle
      `listing_projection.protected_logic_tokens` + `assert_contains_no_protected_logic`
    - **Validates: Requirements 6.3, 6.8, 7.1, 19.7**

  - [~] 16.5 Create `tests/test_marketplace_error_surface.py`
    - For each of connection failure, query timeout, undefined column and permission denial,
      assert every Marketplace read endpoint answers 500 or 503 with a stable code and **no**
      numeric payload — never a zero-filled 200
    - Assert every `PUBLIC_MESSAGE_FOR_CODE` message is free of `SELECT`, `library_strategies`,
      `strategy_backtests`, `Traceback`, `psycopg`, `42703`, `23505` and of any digit sequence
      appearing in `evidence_validator.THRESHOLDS` or `pricing_evaluator.WEIGHTS`
    - _Requirements: 1.5, 1.7, 2.10, 4.8, 22.9_

  - [~] 16.6 Create `tests/test_no_dormant_schema_references.py`
    - AST-walk every module reachable from `backend_app/routers/library.py`,
      `backend_app/backend/marketplace/` and `backend_app/backend/paper/`; collect every string
      literal passed to `.table(...)`, `.from_(...)` or `.rpc(...)`; fail if it contains
      `marketplace_listings` or `strategy_subscriptions`
    - `*.sql` and `tests/test_schema_as_code_completeness.py` are excluded by path
    - _Requirements: 1.1, 1.2, 1.8_
    - Verification: `pytest tests/test_listing_projection.py
      tests/test_library_detail_projection_regression.py tests/test_marketplace_error_surface.py
      tests/test_no_dormant_schema_references.py
      tests/property/test_protected_logic_containment.py -k p47`

- [ ] 17. Implement the Entitlement_Resolver, clone gating and subscriber-safe deployment

  - [~] 17.1 Create `backend_app/backend/marketplace/entitlement_resolver.py`
    - `resolve(caller, listing_id, supabase, now) -> Entitlement` in **one** round trip joining
      `library_strategies`, `marketplace_submissions` and the caller's own
      `library_subscriptions` row; identity from the authenticated server-side session only
    - Reasons `OWNED`, `SUBSCRIBED`, `NOT_SUBSCRIBED`, `EXPIRED`, `LISTING_UNAVAILABLE`,
      `SUBSCRIPTION_SUSPENDED`, with `NOT_SUBSCRIBED` and `EXPIRED` as distinct wire codes
    - Non-entitling whenever `period_expiry` is null or `now >= period_expiry`, **independent of
      whether the expiry sweep has run**; `SUSPENDED` and `UNPUBLISHED` Listings still entitle an
      `ACTIVE` Subscription until its expiry; an unresolvable `source_strategy_id` gives
      `LISTING_UNAVAILABLE` → 409, disclosing no Protected_Logic and no owner identity
    - This is the single admission decision for both deployment and Paper_Session start
    - _Requirements: 4.11, 7.5, 7.7, 7.10, 7.11, 11.7, 11.10, 21.1_

  - [~] 17.2 Gate `clone_strategy` on owner consent and add the settings endpoint
    - In order: `source_cloning_enabled` false → 403 `MARKETPLACE_CLONING_DISABLED` with no
      `strategies` row created, `clone_count` unchanged and an Audit_Log entry; else
      `entitlement_resolver.resolve` must return entitling with reason `SUBSCRIBED`; self-clone
      stays 409; the existing idempotency check, ML-model check, `clone_count` increment and
      `library_ratings` verified-clone marker are unchanged
    - Narrow the `except Exception` around the `clone_count` increment and the verified-clone
      marker to the specific `APIError`, logged at `error`
    - `PATCH /api/library/{library_id}/settings {source_cloning_enabled}` — owner-scoped, 20/60s,
      audited
    - _Requirements: 7.2, 7.3, 7.4, 7.12, 22.4, 30.5_

  - [~] 17.3 Repoint `deploy_marketplace_strategy` (line 2057) at the subscriber-safe path
    - Replace the `check_deployment_permission`-only gate with `entitlement_resolver.resolve`
    - Stop inserting a `strategies` row carrying the owner's `buy_logic`, `sell_logic`, `risk`,
      `indicators` or `ml_model_path` for a subscriber; instead create a `strategy_deployments`
      row bound to the **owner's** `version_id`, with `marketplace_listing_id` recorded and
      `user_id` = the subscriber — the additive marketplace-sourced deployment source
      `.kiro/specs/trading-lifecycle-integration/` Requirement 27 reserved
    - Accept from the subscriber only symbol, timeframe, capital and session options; any other
      field is a 422 that echoes no supplied value
    - _Requirements: 7.1, 7.5, 7.6, 7.8, 11.10_

  - [~] 17.4 Return server-derived `ownership` and `allowed_actions` from `GET /api/library/my-strategies`
    - Each entry carries `ownership: "OWNED" | "SUBSCRIBED"`,
      `subscription: {state, period_expiry, renewal_state}` and a server-computed
      `allowed_actions: string[]`
    - A `SUBSCRIBED` entry's `allowed_actions` may contain only `view_listing`, `run_backtest`
      (where the Listing permits it), `deploy_live`, `start_paper`, `view_performance`,
      `view_subscription`, `renew`, `cancel_renewal`, and never `edit`, `open_in_builder`,
      `view_graph`, `edit_blocks`, `view_indicator_params`, `edit_indicator_params`,
      `view_risk_config`, `edit_risk_config`, `export_definition`, `download_definition`,
      `view_model_params`, `re_version` or `delete`
    - Served in three round trips independent of the entry count: owned `strategies`,
      `library_subscriptions` with an embedded `library_strategies!inner(…)` resource, and the
      `paper_sessions` running count grouped by strategy
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.6, 27.2_

  - [~] 17.5 Create `tests/test_subscriber_restricted_operations.py`
    - Call all thirteen restricted operations with a subscriber token holding an `ACTIVE`
      Subscription and assert each returns 403 with a stable code — not 404 — even when the client
      offers no such affordance
    - Assert the Signal Trace read path returns only the Requirement 23.2 fields to a subscriber
    - Assert each refusal writes a `MARKETPLACE_ACCESS_REFUSED` Audit_Log entry within 5 seconds
      carrying no Protected_Logic
    - _Requirements: 7.1, 7.8, 7.9, 7.12, 12.7, 23.3_

  - [~] 17.6 Write property test for clone gating
    - `tests/property/test_protected_logic_containment.py::test_p48_clone_is_refused_when_source_cloning_is_disabled`
    - **Property P-48 (invariant, clone gating)** — for all Listings with source cloning disabled
      and all non-owner callers, the clone operation is refused and creates no `strategies` row
    - **Validates: Requirements 7.3, 7.4**

  - [~] 17.7 Write property test for access agreement
    - `tests/property/test_access_agreement.py::test_p16_admission_agrees_with_the_resolver`
    - **Property P-16 (invariant, access agreement)** — the deployment admission decision and the
      Paper_Session admission decision each equal `entitlement_resolver.resolve`'s decision for
      the same Subscription at the same instant
    - **Validates: Requirements 11.10, 11.16, 7.10**

  - [~] 17.8 Update `tests/test_marketplace_pipeline.py` test 07
    - Cloning now additionally requires `source_cloning_enabled` **and** an entitling
      Subscription; the fixture sets both, and a companion case asserts the default-disabled 403
    - _Requirements: 7.2, 7.3, 7.4, 25.8_
    - Verification: `pytest tests/test_subscriber_restricted_operations.py
      tests/test_marketplace_pipeline.py tests/property/test_access_agreement.py
      tests/property/test_protected_logic_containment.py`

- [ ] 18. Root-cause fix 1 — `create_marketplace_checkout`

  - [~] 18.1 Create `backend_app/backend/marketplace/checkout_service.py`
    - Reads `price_minor`, `currency`, `author_id` and the Submission state; refuses with 409
      `MARKETPLACE_LISTING_NOT_PURCHASABLE` (not `PUBLISHED`, or already `ACTIVE`) and 400
      `MARKETPLACE_OWN_LISTING`
    - One transaction, **committed before any provider session is requested**, inserting the
      `PENDING` `library_subscriptions` row with `price_minor`, `currency`, `owner_id`,
      `owner_share_minor` and `platform_fee_minor` from `money.split_ninety_ten`, and **omitting
      `expires_at` entirely** rather than inserting `NULL`
    - Stripe/Razorpay client construction moves here, so the handler stops importing `stripe` and
      `razorpay` inline and stops reading `STRIPE_SECRET_KEY` with a `"sk_test_dummy"` default on
      a production path; the provider call is wrapped in `asyncio.wait_for` with a 30-second
      deadline
    - On success record `provider_reference` and `provider_session_at` against the `PENDING` row
      before returning; on failure or timeout `UPDATE` the row to `PAYMENT_FAILED` with
      `failure_cause` and `failed_at` — the row is **never deleted** — and return 502
      `MARKETPLACE_CHECKOUT_UNAVAILABLE`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.10, 9.12, 9.13, 10.1, 10.3, 24.6_

  - [~] 18.2 Rewrite `create_marketplace_checkout` (`library.py` lines 1806–1968)
    - `strat = resp.data` (a dict), keeping the `if not resp.data` guard ahead of it and retaining
      `.single()` because the `id` predicate is unique — this is the `resp.data[0]` →
      `KeyError: 0` line that has meant the endpoint never completed for any Listing
    - The amount comes from `money.amount_for_listing` on `price_minor`; `int(float(price) * 100)`
      and the `amount_paise` twin are deleted, so a `19.99` Listing is charged 1999 rather than
      1998
    - Delete the `except: pass` cleanup that issued
      `svc.table("library_subscriptions").delete().eq("id", sub_id)`; narrow and log the remaining
      `except`
    - Remove `check_deployment_permission`'s "no expiry date means perpetual subscription" branch:
      an `ACTIVE` row without a period is now unrepresentable, and a `NULL` expiry on a
      non-`ACTIVE` row is not entitling
    - _Requirements: 9.2, 9.4, 9.11, 9.13, 11.7, 30.5_

  - [~] 18.3 Create `tests/test_marketplace_checkout_regression.py`
    - Four assertions that each fail against the current code: the `.single()`-indexed-as-a-list
      path completes rather than raising; the charged amount for a `19.99` Listing is exactly 1999
      Minor_Units; the `PENDING` insert records `owner_share_minor` and `platform_fee_minor` and
      no `expires_at`; a provider failure leaves the row present in `PAYMENT_FAILED` with a
      `failure_cause` rather than deleted
    - A fifth asserts the 30-second deadline abandons the attempt and applies the same
      `PAYMENT_FAILED` path
    - _Requirements: 9.2, 9.3, 9.4, 9.11, 9.13, 10.1_
    - Verification: `pytest tests/test_marketplace_checkout_regression.py`

- [ ] 19. Root-cause fix 2 — settlement, and renewal that requires payment

  - [~] 19.1 Create `backend_app/backend/marketplace/settlement_service.py`
    - `settle(provider_reference, provider, amount_minor, currency, subscription_id,
      confirmation_instant, is_reversal=False)` in one transaction with
      `SELECT … FOR UPDATE` on the subscription
    - An unmatched reference and an amount/currency mismatch each make **no** transition, write
      **no** Settlement_Record, grant **no** entitlement, and write
      `MARKETPLACE_SETTLEMENT_UNMATCHED` / `_MISMATCHED`
    - Insert `marketplace_settlements`; a `UniqueViolation` on
      `uq_settlement_reference_reversal` is a no-op plus `MARKETPLACE_SETTLEMENT_DUPLICATE_IGNORED`
    - Then, in the same transaction: the Subscription_State transition to `active`, the period
      write from `period_for_activation` or `period_for_renewal`, the
      `library_subscription_transitions` row, `grant_deployment_permission(...,
      expires_at=period_expiry)` (its `"expires_at": None` is gone), and
      `MARKETPLACE_SETTLEMENT_CREATED` — all of it or none of it
    - Reversals record an additional row with `is_reversal = TRUE` and `reverses_reference` set,
      and transition the Subscription to `REFUNDED`; nothing is ever updated or deleted
    - On persistence failure retry up to 5 times within 60 seconds with bounded backoff; on total
      failure leave the ledger unchanged, exclude the payment from every earnings figure, and
      write `MARKETPLACE_SETTLEMENT_PERSIST_FAILED` with the provider reference
    - _Requirements: 9.5, 9.6, 9.14, 10.1, 10.2, 10.3, 10.4, 10.8, 10.9, 10.10, 10.11, 11.4, 11.5, 11.6, 11.12, 24.6_

  - [~] 19.2 Wire settlement into `billing.py` without adding a second webhook path
    - Extend `_apply_marketplace_entitlement` only — the branch both providers already funnel into
      via `_apply_billing_entitlement`'s `item_key.startswith("marketplace_")` — with one
      `settlement_service.settle(...)` call
    - Add one `settle(..., is_reversal=True)` call to the existing `charge.refunded` (Stripe) and
      `refund.processed` / `refund.failed` (Razorpay) branches when the refunded payment's
      `item_key` began with `marketplace_`
    - `_validate_webhook_signature`, `_validate_webhook_ip`, `STRIPE_WEBHOOK_IPS`,
      `_validate_webhook_timestamp` and the two-phase Redis idempotency lock are **untouched**
    - _Requirements: 9.1, 9.5, 9.6, 9.9, 10.4, 10.8, 22.5, 22.6, 25.2_

  - [~] 19.3 Rewrite `renew_subscription` (`library.py` lines 2258–2337) as checkout-only
    - Delete the `update({"status": "active", "cancelled_at": None, "expires_at": None})`
      statement, the `grant_deployment_permission` call and the `subscriber_count` increment —
      today they convert a cancelled subscription into unlimited free access with no payment
    - `POST /api/library/subscriptions/{sub_id}/renew` returns a provider session for the renewal
      amount and makes **no state change of its own**; the transition into `ACTIVE` and the new
      expiry happen only in `settlement_service.settle`
    - `cancel_subscription` retains entitlement until the unchanged current expiry, records
      `cancelled_at`, performs no further renewal, and no longer revokes deployment permission
      immediately
    - _Requirements: 11.6, 11.9, 11.14, 11.16_

  - [~] 19.4 Create `tests/test_subscription_renewal_regression.py`
    - Asserts, against the current code, that `renew_subscription` sets `status='active'` and
      `expires_at=NULL` with no payment and grants deployment permission — the test fails before
      Task 19.3 and passes after
    - Asserts after the fix that a renewal request creates a provider session and changes no
      stored state, and that a direct SQL `UPDATE … SET status='active'` without a matching
      `marketplace_settlements` row is refused by `trg_subscription_transition_guard`
    - _Requirements: 11.6, 11.14, 11.16_

  - [~] 19.5 Write property test for ledger sums
    - `tests/property/test_settlement_ledger.py::test_p5_reported_totals_equal_ledger_sums`
    - **Property P-5 (invariant, ledger sum)** — reported owner earnings equal the exact integer
      sum of `owner_share` over persisted Settlement_Records and the platform total the sum of
      `platform_fee`, per currency, with reversals subtracted and currencies never combined
    - Oracle: independent per-currency accumulation over the generated sequence
    - **Validates: Requirements 10.5, 10.6, 10.7**

  - [~] 19.6 Write property test for duplicate-confirmation idempotence
    - `tests/property/test_settlement_idempotence.py::test_p6_duplicate_confirmation_is_idempotent`
    - **Property P-6 (idempotence, duplicate webhook)** — applying the same provider transaction
      reference `n >= 1` times yields exactly one Settlement_Record and the same period expiry as
      applying it once
    - **Validates: Requirements 9.6, 9.7, 10.10**

  - [~] 19.7 Write property test for invalid amount refusal
    - `tests/property/test_settlement_ledger.py::test_p7_invalid_amounts_are_refused`
    - **Property P-7 (error-condition)** — negative, non-integer and over-maximum amounts are
      refused and write no Settlement_Record
    - **Validates: Requirements 10.1, 10.5**

  - [~] 19.8 Write property test for Subscription_State reachability
    - `tests/property/test_subscription_state_machine.py::test_p8_every_subscription_state_is_reachable_from_pending`
    - **Property P-8 (invariant, reachability)** — every persisted Subscription_State is reachable
      from `PENDING` by the Requirement 11.2 transitions, and no persisted transition lies outside
      that set
    - **Validates: Requirements 11.1, 11.2**

  - [~] 19.9 Write property test for illegal Subscription transition rejection
    - `tests/property/test_subscription_state_machine.py::test_p9_illegal_subscription_transition_leaves_state`
    - **Property P-9 (invariant)** — for all pairs absent from the permitted set, the attempt
      leaves the stored state at `s1` and returns an error
    - **Validates: Requirements 11.3**

  - [~] 19.10 Write property test for `ACTIVE` period well-formedness
    - `tests/property/test_subscription_state_machine.py::test_p10_active_subscription_period_is_well_formed`
    - **Property P-10 (invariant)** — an `ACTIVE` Subscription has a non-null period start, a
      non-null expiry, and `expiry > start`
    - **Validates: Requirements 11.13**

  - [~] 19.11 Write property test for history preservation
    - `tests/property/test_subscription_state_machine.py::test_p15_subscription_and_settlement_history_never_shrinks`
    - **Property P-15 (invariant)** — across generated cancel/expire/refund/renew sequences the
      count of persisted Subscription rows never decreases and no Settlement_Record is removed
    - **Validates: Requirements 10.8, 11.11**

  - [~] 19.12 Write property test for the expiry boundary
    - `tests/property/test_entitlement_expiry_boundary.py::test_p11_entitlement_is_decided_by_the_expiry_instant`
    - **Property P-11 (metamorphic, expiry boundary)** — entitling for `t < e`, non-entitling for
      `t >= e`, including `t == e` exactly, independent of whether the sweep has run
    - **Validates: Requirements 11.7**

  - [~] 19.13 Update `tests/test_marketplace_concurrency.py::TestConcurrentRenewal`
    - It asserts today that two concurrent renewals are idempotent **and grant access**. Renewal
      no longer grants access without a payment, so it now asserts that two concurrent renewal
      requests create at most one provider session and produce no state change
    - Add the duplicate-webhook battery: N deliveries for one provider reference yield exactly one
      `marketplace_settlements` row, one period extension and N−1 duplicate audit entries
    - _Requirements: 9.6, 10.10, 11.16, 25.8_

  - [~] 19.14 Update `tests/test_billing_e2e.py`
    - `_apply_marketplace_entitlement` now also writes a Settlement_Record and a period; extend
      the marketplace assertions accordingly and leave every plan-checkout, entitlement, invoice,
      payment-method, currency, portal, cancel and resume assertion untouched
    - _Requirements: 9.5, 10.4, 25.2, 25.8_
    - Verification: `pytest tests/test_subscription_renewal_regression.py tests/test_billing_e2e.py
      tests/test_marketplace_concurrency.py tests/property/test_settlement_ledger.py
      tests/property/test_settlement_idempotence.py
      tests/property/test_subscription_state_machine.py
      tests/property/test_entitlement_expiry_boundary.py`

- [ ] 20. Implement the expiry sweep and its worker

  - [~] 20.1 Create `backend_app/backend/marketplace/expiry_sweep.py` and `backend_app/workers/marketplace_expiry_worker.py`
    - One `UPDATE … WHERE period_expiry IS NOT NULL AND period_expiry <= now AND status IN
      ('active','suspended')` returning the affected rows, then one
      `library_subscription_transitions` insert and one `SUBSCRIPTION_EXPIRED` audit entry per row
    - `stop_running_sessions_and_deployments(rows)` stops every running deployment and
      Paper_Session for that Listing's strategy owned by that purchaser within 60 seconds
    - Hosted on a 30-second interval so the Requirement 11.8 bound holds with margin; supported by
      `idx_lib_subs_expiry`; exposes `marketplace.expiry_sweep.last_run_at` as a health-check input
    - The sweep is not the authority on entitlement — `entitlement_resolver` compares `now` with
      `period_expiry` on every call
    - _Requirements: 11.8, 11.10, 11.12, 11.15, 24.4, 26.2_

  - [~] 20.2 Write property test for sweep idempotence
    - `tests/property/test_expiry_sweep_idempotence.py::test_p13_expiry_sweep_is_idempotent`
    - **Property P-13 (idempotence, sweep)** — running the sweep `n >= 1` times produces the same
      set of Subscription_States as running it once
    - **Validates: Requirements 11.8**
    - Verification: `pytest tests/property/test_expiry_sweep_idempotence.py`

- [ ] 21. Root-cause fix 3 — creator and subscriber analytics from the ledger

  - [~] 21.1 Rewrite `creator_analytics` (`library.py` lines 2338–2380)
    - Delete `.select("id, name, clone_count, monthly_price, rating_average")` — `monthly_price`
      and `rating_average` do not exist on `library_strategies` and the query fails with
      PostgreSQL `42703`, the identical condition migration 007's header records for `/trending`
      and `/featured`
    - Delete `mrr = sum(clone_count * float(monthly_price))` and `round(mrr * 0.90, 2)`:
      `clone_count` counts clones, not payments, and the arithmetic is binary floating point
    - Delete the bare `except Exception: return {…zeros…}` — a read failure now answers 500/503
      with a stable code and no numeric payload
    - Implement `creator_earnings(owner_id)` as one `marketplace_settlements` read where
      `owner_id = caller`, accumulated in Python per currency in integer Minor_Units with
      reversals subtracted, never combining currencies
    - Read `avg_rating` from `library_strategies.avg_rating` and report it as **absent** when
      `rating_count = 0`; delete the fabricated
      `payout_schedule: "Monthly auto-transfer (Stripe Connect)"` string
    - Apply the same treatment to `subscriber_analytics`
    - _Requirements: 1.4, 1.5, 1.7, 6.9, 10.3, 10.6, 10.7, 27.1, 28.2, 30.5_

  - [~] 21.2 Create `tests/test_creator_analytics_regression.py`
    - Asserts, against the current code, that `GET /api/library/creator/analytics` returns HTTP
      200 with all-zero figures when the underlying query fails — the test fails before Task 21.1
      and passes after (and, with Task 13.1 in place, that it is reachable at all rather than
      answering 422 from `_safe_uuid("analytics", "creator_id")`)
    - Asserts the reported owner total equals the exact integer sum of `owner_share_minor` over
      non-reversal rows minus reversal rows, per currency, and that no figure is derived from
      `clone_count` or `subscriber_count` multiplied by a price
    - _Requirements: 1.4, 1.5, 1.7, 10.6, 10.7_
    - Verification: `pytest tests/test_creator_analytics_regression.py`

- [~] 22. Checkpoint — marketplace backend complete
  - Ensure all tests pass, including `tests/regression/test_baseline_unchanged.py`, and that the
    six root-cause regression tests each fail on a revert of their fix. Ask the user if questions
    arise.

- [ ] 23. Move paper trading onto durable storage without changing what users already see

  - [~] 23.1 Create `backend_app/backend/paper/paper_repository.py`
    - The single module that reads and writes every `paper_*` table; no other module issues a
      `.table("paper_…")` call, so the storage decision has one place to live
    - The **default account** is the row where `session_id IS NULL` — that is the account the six
      existing `/api/paper/*` endpoints serve, and `uq_paper_account_default` (partial unique on
      `(user_id, currency) WHERE session_id IS NULL`) is what keeps it single. Session-scoped
      accounts carry a non-null `session_id` and `uq_paper_account_session`
    - `get_or_create_account(user_id, currency='USD', session_id=None)`, `get_positions`,
      `get_orders`, `get_trades`, `get_equity_snapshots`, `get_metrics`, plus the write helpers
      the simulator calls: `lock_account_for_update`, `insert_order`, `probe_idempotency_key`,
      `insert_fill`, `upsert_position`, `insert_balance_event`, `insert_trade`,
      `insert_equity_snapshot`, `bump_version`
    - The migration probe: one `SELECT id FROM paper_accounts LIMIT 1` per process through the
      caller's own RLS-scoped client, cached, following the convention
      `backend/strategy_service.py` and `backend/signal_service.py` already establish. An absent
      relation makes every `/api/paper/*` endpoint answer **503 `PAPER_PERSISTENCE_UNAVAILABLE`**
      naming `backend_app/migrations/009_paper_trading.sql`
    - It does **not** fall back to memory. A balance served from process memory after this change
      is the fabricated figure Requirement 28.3 forbids, and Requirement 17.2's
      survive-a-restart guarantee would be silently false while still reading as true
    - _Requirements: 17.1, 17.2, 17.11, 21.2, 21.5, 24.10, 28.3_

  - [~] 23.2 Repoint `backend_app/backend/paper_trading_service.py` at the repository
    - Delete `self._accounts` (line 52), `self._positions` (53), `self._orders` (54),
      `self._trades` (55), `self._idempotency_cache` (56) and `self._user_locks` (59), and the
      `_get_user_lock` helper that reads them; the module-level `_paper_service_instance`
      singleton and `get_paper_trading_service()` stay, because `routers/risk.py` and
      `routers/paper_trading.py` both import it by name
    - Keep every public method name and return shape: `get_or_create_account`, `reset_account`,
      `get_positions`, `get_orders`, `get_trades`, `get_performance_summary`, `place_order`,
      `cancel_order`, `check_limit_orders`, `verify_accounting_invariants`
    - Keep the five read methods **synchronous**, using the synchronous `supabase-py` client:
      `risk.py` calls `get_performance_summary(uid)`, `get_positions(uid)` and
      `get_or_create_account(uid)` synchronously from inside `async def` handlers, so making them
      `await`-only would break both handlers. Only the write paths stay `async`, as they already
      are
    - `_recalculate_account` delegates to `paper_accounting.recalculate(account, positions,
      prices)`; the SHORT convention `size * (2 * entry_price - price)` it holds today moves into
      `paper_accounting.position_market_value` unchanged
    - Idempotency moves from `self._idempotency_cache` onto `paper_orders.idempotency_key` +
      `uq_paper_order_idem`; per-user serialisation moves from `self._user_locks` onto
      `SELECT … FOR UPDATE` plus the `version` optimistic check
    - `verify_accounting_invariants`' `Decimal("0.05")` tolerance becomes the exact zero-tolerance
      equality of `paper_accounting.invariants_hold`; a fully closed position persists with
      `size = 0` and `closed_at` set instead of the dict entry being deleted
    - `PaperOrderStatus` (`NEW, OPEN, FILLED, CANCELLED, REJECTED`) is **retained** and written to
      `paper_orders.legacy_status` through `paper_order_state.LEGACY_STATUS_FOR_STATE`, so
      `GET /api/paper/orders?status=OPEN` keeps its exact meaning
    - _Requirements: 17.1, 17.2, 17.12, 16.8, 16.10, 16.11, 18.3, 18.5_

  - [~] 23.3 Add the additive response fields, and only the additive ones
    - `execution_environment: "PAPER"` and `is_simulated: true` on every `/api/paper/*` response
      body; `session_id: null` on default-account responses
    - `stale: false|true` and `last_price_at` on every figure derived from a market price —
      unrealized PnL, position market value, total equity, current price
    - No existing key is removed, renamed, retyped or re-meaninged. `Portfolio.jsx` lines 111–150
      and `TradeHistory.jsx` lines 44–48 read these bodies today and are not touched by this task
    - _Requirements: 13.6, 17.12, 18.15, 28.1_

  - [~] 23.4 Create `tests/test_paper_api_shape_compatibility.py`
    - Asserts each of `GET /api/paper/account`, `/positions`, `/orders`, `/trades`, `/summary` and
      `POST /api/paper/account/reset` against the frozen
      `tests/regression/baseline/paper_api_shape.json` captured in Task 1.1: every frozen key
      still present, same type, and the only difference is the five added keys of Task 23.3
    - Asserts `GET /api/paper/orders?status=OPEN` returns the same orders it returns today for an
      account with one `ACCEPTED` and one `PARTIALLY_FILLED` order
    - Asserts that with `paper_accounts` absent every one of the six answers 503
      `PAPER_PERSISTENCE_UNAVAILABLE` naming `009_paper_trading.sql`, and that none of them
      answers 200 with a memory-derived figure
    - _Requirements: 17.2, 17.12, 25.7, 28.3_

  - [~] 23.5 Repoint `backend_app/routers/risk.py` at the persisted account
    - `get_risk_status` (lines 300–311) keeps `realized_loss = -min(0, realized_pnl)`,
      `loss_utilization_pct = realized_loss / max_daily_loss * 100` rounded to 2, and
      `open_pos_count = len(get_positions(uid))`; `get_margin_health` (lines 355–360) keeps
      `margin_ratio = locked / total * 100`, `free_margin = avail / total * 100` and
      `risk_score = min(100, int(margin_ratio * 0.8 + (100 - free_margin) * 0.2))`
    - The only change is where the numbers come from. `open_pos_count` now counts persisted
      positions, which under Task 23.2 include closed rows at `size = 0` — so the count filters
      `size > 0`, which is what the in-memory dict expressed structurally by deleting the entry.
      That is the one place the storage change would otherwise alter a reported figure
    - Asserted against `tests/regression/baseline/risk_utilisation.json`; the risk-level
      thresholds (100 / 85 / 60, `BLOCKED` / `CRITICAL` / `WARNING` / `SAFE`) are untouched
    - _Requirements: 17.12, 25.1, 25.5, 25.7_

  - [~] 23.6 Write property test for persistence round-tripping
    - `tests/property/test_paper_persistence_roundtrip.py::test_p32_session_state_round_trips_without_precision_loss`
    - **Property P-32 (round-trip, persistence)** — for all generated Paper_Session states,
      persisting the state and reading it back yields balances, positions, orders, fills, trades
      and equity snapshots equal to the values persisted, with no precision loss
    - Compared as exact `Decimal`, not as `float` and not with a tolerance; every in-process cache
      is cleared between the write and the read, so the assertion is about `NUMERIC(28,10)` and
      not about a memoised object
    - **Validates: Requirements 17.2, 17.11, 18.1**
    - Verification: `pytest tests/test_paper_api_shape_compatibility.py
      tests/test_paper_trading_lifecycle.py tests/regression/test_baseline_unchanged.py
      tests/property/test_paper_persistence_roundtrip.py`

- [ ] 24. Implement `paper_market_feed.py` — real market data, or none

  - [~] 24.1 Implement source selection and its two refusals
    - `open_feed(session_config)` calls the **existing**
      `market_data_latency.choose_market_data_source(measurement_for(primary),
      measurement_for(fallback))`; no second selection rule is written. The correctness floor is
      already evaluated inside it, so `FLOOR_NOT_MEASURED` and `FLOOR_METRICS_MISSING` fail the
      floor before the p99 comparison is reached and an unmeasured source cannot be selected
    - `decision.outcome == OUTCOME_BLOCKED` raises `PaperMarketDataUnavailable(decision.rule,
      decision.reason)` → 409 `PAPER_MARKET_DATA_UNAVAILABLE`; the session stays `CREATED` and no
      subscription is opened
    - A connection whose interface was installed by
      `connection_engine._apply_mock_interface` (line 188, the `DEV_MODE` path) is refused with
      the same code plus a `PAPER_FEED_REFUSED_MOCK_INTERFACE` audit entry — detected by
      inspecting the resolved exchange object for the marker the mock installer sets, not by
      reading `DEV_MODE`, because the flag and the installed interface can disagree
    - `session.market_data_source` records `decision.selected.source`
    - _Requirements: 14.1, 14.3, 14.4, 14.8, 28.3_

  - [~] 24.2 Subscribe through the existing MDS pipeline — no new poller
    - `PUBLISH {'action':'subscribe','exchange':ex,'symbol':sym}` to `mds:commands`, which
      `mds/main.py::handle_commands` already listens on, and subscribe to
      `mds:data:{exchange_id}:{symbol}` — the channel `broadcast_ohlcv` (line 22) already
      publishes to. No `fetch_ohlcv` loop, no second websocket, no third market-data path
    - `feed_transport: 'WEBSOCKET' | 'REST'` and `feed_state = 'FALLBACK_REST'` derived from a
      `transport` field added to the payload `broadcast_ohlcv` publishes; the `watch_ohlcv` →
      `fetch_ohlcv` fallback on `NotSupported` already exists there and is only being observed,
      not rebuilt
    - `paper.feed.latency_ms` measured as processing time minus the event's own timestamp
    - _Requirements: 14.2, 14.6, 14.10, 26.6_

  - [~] 24.3 Implement `next_validated_event` — identity, validation, dedupe, ordering
    - `source_event_id = sha256(f"{exchange}|{symbol}|{timeframe}|{timestamp}|{close}|{volume}")`,
      because the OHLCV payload carries no id of its own; stable for a republished candle and
      different for a revised one, which is the dedupe semantics Requirement 14.7 needs
    - Normalisation through `market_data_contract` and validation through
      `market_data_validation`; a failing event increments `paper.feed.invalid` and is dropped,
      never repaired
    - A 10,000-entry LRU of seen `source_event_id` per session for the hot path, with
      `uq_paper_market_event` as the durable guarantee — the LRU is a cache, not the arbiter
    - An event whose timestamp is below `session.last_timestamp[symbol]` increments
      `paper.feed.out_of_order` and is dropped, so the processed timestamp sequence per symbol is
      non-decreasing
    - Every accepted event is written to `paper_market_events` with its sequence,
      `source_event_id` and payload, which is what makes `paper_replay.replay` possible
    - _Requirements: 14.7, 15.5, 26.6_

  - [~] 24.4 Implement disconnection, backoff and the no-fill-while-degraded rule
    - Bounded exponential backoff 1s, 2s, 4s, 8s, 16s then 30s capped, **jitter-free** so a replay
      of the session reproduces the same reconnection points; `asyncio.sleep`, never
      `time.sleep`
    - On drop: `paper_sessions.feed_state = 'DEGRADED'` and one `paper_error` event with
      `code: 'FEED_DISCONNECTED'`
    - `paper_simulator` reads `session.feed_state` **inside its transaction** and, when it is not
      `'HEALTHY'`, neither accepts a market order nor fills a resting limit order. A
      pre-disconnection price is never treated as current
    - On reconnection the session resumes from the first event that passes validation; nothing is
      interpolated across the gap
    - _Requirements: 14.5, 14.9, 18.15_

  - [~] 24.5 Write property test for dedupe and monotonicity
    - `tests/property/test_market_event_dedupe.py::test_p54_market_events_are_deduplicated_and_monotonic`
    - **Property P-54 (invariant, dedupe and ordering)** — for all generated streams containing
      duplicated `source_event_id` values and out-of-order timestamps, each `source_event_id` is
      processed at most once per session, the processed timestamp sequence per symbol is
      non-decreasing, and the `paper_market_events` row count equals the distinct processed count
    - Generator `market_event_streams()`; oracle: the distinct-and-sorted projection of the stream
    - **Validates: Requirements 14.7, 15.5**

  - [~] 24.6 Write property test for the no-stale-fill rule
    - `tests/property/test_no_stale_fill.py::test_p56_no_fill_at_a_pre_disconnection_price`
    - **Property P-56 (invariant, stale price)** — for all generated streams and all disconnection
      points within them, no fill is applied while `feed_state != 'HEALTHY'`, and every fill after
      a reconnection uses a price from an event that passed validation **after** that reconnection
    - Generator `market_event_streams()` × disconnection points; oracle: the post-reconnection
      validated-event set
    - **Validates: Requirements 14.5, 18.15**

  - [~] 24.7 Write property test for price provenance
    - `tests/property/test_no_synthesised_price.py::test_p55_no_price_is_synthesised`
    - **Property P-55 (invariant, provenance)** — for all generated Paper_Sessions, every
      `paper_fills.price`, every `paper_positions.current_price`, every price used in a
      `paper_equity_snapshots` valuation and every price in a `market_tick` payload is derivable
      by the session's recorded fee and slippage configuration from a price present in that
      session's `paper_market_events`; no recorded price is drawn from a random source,
      interpolated between two events, or extrapolated beyond the last one
    - Oracle: the set of prices in `paper_market_events`, closed under the frozen config's
      fee/slippage transform
    - **Validates: Requirements 14.9, 16.12, 28.3**
    - Verification: `pytest tests/property/test_market_event_dedupe.py
      tests/property/test_no_stale_fill.py tests/property/test_no_synthesised_price.py`

- [ ] 25. Implement `paper_simulator.py` and the order lifecycle

  - [~] 25.1 Install the two simulator guards before anything calls a simulator
    - `paper_simulator.assert_paper_simulator(candidate)` compares
      `(candidate.__module__, candidate.__qualname__)` against
      `FORBIDDEN_SIMULATORS = {('backend_app.backend.exchange_simulator',
      'PaperTradingExchange')}` — a tuple comparison, so the forbidden module is **never
      imported** to perform the check. A match writes `PAPER_SIMULATOR_MISCONFIGURED` and raises
      500, with a message naming why: its prices are `random.gauss` (lines 319, 753) and its fills
      are `random.random() > self.fill_probability` (line 369)
    - `tests/test_paper_no_random.py` AST-walks every module under `backend_app/backend/paper/`
      and fails on `import random`, `from random import …`, any `numpy.random` attribute access,
      and any import of `backend_app.backend.exchange_simulator`
    - `exchange_simulator.py` itself is **not** modified — it stays available to staging
      self-tests; it is kept out of the request path, not deleted
    - _Requirements: 13.11, 15.6, 16.12, 18.1_

  - [~] 25.2 Freeze the session configuration at start
    - `paper_sessions.config JSONB` written once and never updated, carrying `fee_rate`,
      `slippage_rate`, `participation_rate`, `rounding_mode`, `cost_basis`, `price_precision`,
      `quantity_precision`, `minor_unit_exponent`, `max_order_quantity`, `supported_order_types`
      `["market","limit"]`, `supported_sides`, `validated_symbols`, `market_data_source`,
      `simulator` and `schema_version: "paper.v1"`
    - `price_precision`, `quantity_precision` and `max_order_quantity` are read from the
      **exchange market metadata** for the session's symbol at start, so Requirement 16.5's
      precision checks compare against a recorded value rather than a guess
    - `trg_paper_session_config_immutable` raises on any `UPDATE` that changes it, so a
      mid-session fee change is unrepresentable rather than merely discouraged
    - _Requirements: 16.5, 16.12, 17.5, 18.2_

  - [~] 25.3 Implement `submit_intent`
    - One transaction per attempt, `SELECT … FROM paper_accounts WHERE session_id = :id FOR
      UPDATE` first, then in this order:
      (1) the idempotency probe **inside** the transaction — an existing row whose
      `fingerprint = sha256(symbol|side|order_type|quantity|limit_price|time_in_force)` matches is
      returned unchanged with no second order and no balance movement; a mismatch raises
      `PAPER_IDEMPOTENCY_CONFLICT` (409);
      (2) static validation producing a persisted `REJECTED` order from `CREATED` with one of the
      named reasons `QUANTITY_NOT_POSITIVE`, `QUANTITY_ABOVE_MAX`, `QUANTITY_PRECISION`,
      `SYMBOL_NOT_VALIDATED`, `ORDER_TYPE_UNSUPPORTED`, `SIDE_UNSUPPORTED`,
      `LIMIT_PRICE_NOT_POSITIVE`, `LIMIT_PRICE_PRECISION` — balances and positions untouched;
      (3) the reference price, `NO_VALIDATED_PRICE` when there is none — never a synthesised one;
      (4) the funds check against the balance read **inside this transaction**, rejecting with
      `INSUFFICIENT_FUNDS` and locking nothing;
      (5) the accept path: insert at `CREATED`, transition to `ACCEPTED`, and for a limit order
      `accounting.lock(account, required)` moving `available → locked`
    - The idempotency key is validated as 1–128 characters before it reaches the database, so
      `chk_paper_order_idem_len` is a backstop rather than the error surface
    - A market order's `apply_fill` happens **after** the commit, so an accepted order is durable
      before it is filled
    - _Requirements: 16.5, 16.6, 16.8, 16.15, 14.9_

  - [~] 25.4 Implement `apply_fill` as the single write path for every fill
    - `FOR UPDATE` on the account **and** the order, then the four guards in order: a terminal
      order commits and returns unchanged; an existing `(order_id, fill_event_id)` commits and
      returns unchanged; an over-fill rolls back with `PAPER_OVER_FILL`; and the closing
      `ASSERT accounting.invariants_hold(...)` runs **inside** the transaction so a violation
      rolls back the balances, positions, realized PnL and equity series together and the error
      names the violated invariant
    - Writes, all in that transaction: the `paper_fills` row with `fee_minor` and
      `slippage_minor`; the account update with `version = version + 1`; the `paper_positions`
      upsert; the `paper_balance_events` row; the transition to `PARTIALLY_FILLED` or `FILLED`
      according to whether `filled_quantity + quantity == quantity`; a `paper_trades` row when a
      position reaches size zero; and the `paper_equity_snapshots` row with `cause='FILL'`
    - No fill is applied while `session.feed_state != 'HEALTHY'` (Task 24.4)
    - _Requirements: 16.3, 16.7, 16.9, 16.11, 16.13, 16.14, 18.6, 18.7, 18.11, 18.14_

  - [~] 25.5 Implement the deterministic fill model
    - Market: fills immediately on acceptance at `reference × (1 ± slippage_rate)`, adverse
      direction only — the convention `paper_trading_service._execute_fill` already applies —
      where `reference` is the latest validated event's `ask`/`bid` when the source supplies them
      and its `close` otherwise
    - Limit: fills on the first validated event where `buy: low <= limit` / `last <= limit` or
      `sell: high >= limit` / `last >= limit`, at **exactly** `limit`. No favourable slippage on a
      resting order, because an improvement on a resting limit is an invented price
    - `fillable_quantity(order, event, config) = MIN(remaining, quantize(event.volume ×
      participation_rate))`, and the whole remaining quantity when the event reports no volume.
      A deterministic participation cap, **not** a fill probability — there is no
      `random.random()` comparison anywhere in this module
    - `check_resting_orders(session, event)` is the one caller for limit orders and routes through
      `apply_fill`; it does not write a fill itself
    - _Requirements: 16.12, 16.13, 16.14, 18.1_

  - [~] 25.6 Implement the retry and conflict path
    - Three attempts on `SerializationFailure` or a stale `version`, with bounded backoff, then
      `PAPER_CONCURRENCY_CONFLICT` (409). Applies to both `submit_intent` and `apply_fill`, which
      is why the retry loop is written once and shared
    - _Requirements: 16.10_

  - [~] 25.7 Write property test for order-state reachability
    - `tests/property/test_paper_order_lifecycle.py::test_p17_every_paper_order_state_is_reachable_from_created`
    - **Property P-17 (invariant, reachability)** — for all generated sequences of intents, market
      events and cancellations, every persisted Paper_Order_State is reachable from `CREATED` by
      the Requirement 16.2 transitions
    - Oracle: the `PAPER_ORDER_TRANSITIONS` reachability closure
    - **Validates: Requirements 16.1, 16.2**

  - [~] 25.8 Write property test for terminality
    - `tests/property/test_paper_order_lifecycle.py::test_p18_terminal_paper_order_states_are_final`
    - **Property P-18 (invariant, terminality)** — for all orders reaching `FILLED`, `CANCELLED` or
      `REJECTED`, no subsequent event changes the order's state, its filled quantity or its
      recorded fees
    - **Validates: Requirements 16.3**

  - [~] 25.9 Write property test for illegal transition rejection
    - `tests/property/test_paper_order_lifecycle.py::test_p19_illegal_paper_order_transition_leaves_state`
    - **Property P-19 (invariant, illegal transition)** — for all ordered pairs absent from the
      permitted set, the attempt is rejected and the stored state is unchanged
    - Distinct from P-49, which issues the same attempt directly to the Persistence_Layer; this one
      goes through `paper_simulator`
    - **Validates: Requirements 16.2, 16.4**

  - [~] 25.10 Write property test for fill accumulation
    - `tests/property/test_paper_order_lifecycle.py::test_p20_fill_sum_is_bounded_and_filled_iff_equal`
    - **Property P-20 (invariant, fill accumulation)** — the sum of fill quantities is at most the
      order quantity, and the state is `FILLED` if and only if that sum equals it
    - Oracle: the fill sum recomputed from `paper_fills` rather than read from
      `paper_orders.filled_quantity`, so a drift between the two is a failure
    - **Validates: Requirements 16.7, 16.13, 16.14**

  - [~] 25.11 Write property test for duplicate fills
    - `tests/property/test_paper_idempotence.py::test_p21_duplicate_fill_event_changes_nothing`
    - **Property P-21 (idempotence, duplicate fill)** — for all fill events `f` and repetition
      counts `n >= 1`, applying `f` with the same `fill_event_id` `n` times produces the same order
      state, filled quantity, position and balance as applying it once
    - **Validates: Requirements 16.9, 16.11**

  - [~] 25.12 Write property test for duplicate intents
    - `tests/property/test_paper_idempotence.py::test_p22_duplicate_order_intent_yields_one_order`
    - **Property P-22 (idempotence, duplicate intent)** — for all intents carrying the same
      idempotency key within one Paper_Session, exactly one order exists and every response
      returns that order
    - **Validates: Requirements 16.8, 16.11, 16.15**

  - [~] 25.13 Write property test for concurrent confluence
    - `tests/property/test_paper_confluence.py::test_p23_concurrent_intents_match_a_sequential_order`
    - **Property P-23 (confluence, concurrent submission)** — for all sets of concurrently
      submitted **distinct** intents against one Paper_Account, the resulting balances, positions
      and equity are identical to those produced by applying the same intents in some sequential
      order, and no intent is applied twice or lost
    - Oracle: every sequential permutation, asserting one common result
    - **Validates: Requirements 16.10, 18.3**

  - [~] 25.14 Write property test for the rejection conditions
    - `tests/property/test_paper_order_lifecycle.py::test_p24_invalid_intents_are_rejected_without_side_effects`
    - **Property P-24 (error-condition)** — for all intents with quantity at or below zero, a symbol
      outside the session's validated set, a limit price at or below zero, an unsupported order
      type, an unsupported side, or required funds exceeding the available balance: the order is
      persisted `REJECTED` with a recorded reason and no balance, position or equity value changes
    - **Validates: Requirements 16.5, 16.6**

  - [~] 25.15 Write property test for model-based agreement
    - `tests/property/test_paper_replay_model.py::test_p31_simulator_agrees_with_the_reference_ledger`
    - **Property P-31 (model-based, replay agreement)** — for all generated market-event and
      order-intent sequences, the simulator's final balances, positions, realized PnL, equity
      series and order states equal those produced by `paper_replay.ReferenceLedger` on the same
      inputs
    - Generators `market_event_streams()` × `order_intents()`; oracle the naive second
      implementation from Task 9.2. This is the property that makes the whole simulator's
      correctness checkable, so a disagreement is a defect in whichever side the reading of the
      requirement does not support — not a reason to relax the comparison
    - **Validates: Requirements 15.4, 18.13**
    - Verification: `pytest tests/test_paper_no_random.py
      tests/property/test_paper_order_lifecycle.py tests/property/test_paper_idempotence.py
      tests/property/test_paper_confluence.py tests/property/test_paper_replay_model.py`

- [ ] 26. Implement `paper_events.py` and register the Paper_Channel

  - [~] 26.1 Declare the sixteen event types and their payload schemas
    - `PaperEvent` with exactly sixteen values: `paper_session_started`, `_paused`, `_resumed`,
      `_stopped`, `market_tick`, `signal_generated`, `paper_order_created`, `_accepted`,
      `_partially_filled`, `_filled`, `_rejected`, `paper_position_updated`,
      `paper_balance_updated`, `paper_pnl_updated`, `paper_drawdown_updated`, `paper_error`;
      `PAPER_CHANNEL_EVENTS` derived from it, and `PAPER_EVENT_SCHEMA_VERSION = 'paper.v1'`
    - One Pydantic model per type, exactly the fields the design's payload table lists.
      `signal_generated` carries `signal_id, decision, symbol, side, quantity, price,
      order_lifecycle_state, generated_at, environment:'PAPER'` and **no** node id, indicator
      value, feature value or ML detail. No payload carries a credential, token or payment
      reference
    - `tests/test_paper_event_schemas.py` asserts the count is sixteen, that
      `chk_paper_event_type`'s value list and `PAPER_CHANNEL_EVENTS` are the same set, and that
      every model rejects an unlisted field
    - _Requirements: 19.2, 19.7_

  - [~] 26.2 Implement the envelope and the sequence allocator
    - Every frame carries `schema_version`, `channel`, `session_id`, `type`, `sequence`,
      `event_id`, a microsecond-resolution UTC `emitted_at` and `payload`
    - `next_sequence(session_id)` is `UPDATE paper_sessions SET event_sequence = event_sequence + 1
      WHERE id = :id RETURNING event_sequence`, issued **inside the emitting transaction** — the
      row lock is what makes the sequence contiguous across instances, so an in-process counter is
      not an acceptable substitute
    - `event_id` is a UUID, unique per session by `uq_paper_event_id`; `uq_paper_event_seq` makes a
      duplicated sequence unrepresentable
    - _Requirements: 19.3, 26.3_

  - [~] 26.3 Register `PAPER_FAMILY` and its owner relation
    - `backend_app/backend/ws_channels.py` gains `PAPER_FAMILY = OwnedChannelFamily(namespace=
      "paper", resource="session_id", events=frozenset(PAPER_CHANNEL_EVENTS))` and one entry in
      the `OWNED_CHANNEL_FAMILIES` tuple, following `SIGNAL_FAMILY` exactly
    - `backend_app/core/websocket_auth.py::_owner_relations()` gains one
      `_OwnerRelation(table="paper_sessions",
      migration="backend_app/migrations/009_paper_trading.sql", noun="paper session")` keyed on
      `PAPER_FAMILY.namespace`
    - That is the **entire** authorisation change. `authorize_channel_subscription` already parses
      through `parse_owned_channel`, resolves `user_id`, and refuses through the shared `_forbidden`
      / `_unresolved` frames — which is why another user's session and a non-existent one are
      already indistinguishable. No new authorisation code is written here
    - _Requirements: 19.4, 19.5, 21.4, 21.6_

  - [~] 26.4 Implement replay, heartbeat, slow-consumer and cleanup behaviour
    - Replay from `paper_events` for `sequence > last_received ORDER BY sequence ASC`, capped at
      5000 rows per request. `paper_events` is the durable buffer, so the 1000-events-for-5-minutes
      floor is met with margin and survives a restart, which an in-memory ring would not
    - An unrecoverable gap — a negative `last_sequence`, or a session whose events went with it —
      emits `paper_error{code:'HISTORY_INCOMPLETE'}`, replays nothing partial, and continues from
      the current sequence
    - The existing `api_ws/ws_manager.py` heartbeat is used unchanged; a connection failing two
      consecutive heartbeats is classified stale and closed within 5 seconds, and `unsubscribe` /
      `disconnect` already sweep `_all_connections`, `_user_connections` and every per-channel store
    - `ws_manager` gains a per-connection pending-queue depth counter; above 1000 pending events the
      connection is closed with `paper_error{code:'CLIENT_FELL_BEHIND'}` and removed. Delivery to
      every other connection continues in order, because `_broadcast`'s existing loop awaits each
      send independently and collects failures into `dead` rather than aborting
    - A raising handler is caught **per handler**, logged with session id and event type, and leaves
      the connection registered with its remaining handlers
    - Session stop and delete release every subscription for that session
    - _Requirements: 19.8, 19.9, 19.10, 19.11, 19.12, 19.13, 27.5_

  - [~] 26.5 Re-verify ownership before each emit
    - `broadcast(session_id, frame)` re-reads `paper_sessions.user_id` from a 5-second per-session
      cache keyed on the session id, invalidated on any `paper_sessions` write, and compares it with
      the authenticated identity recorded on each subscribed connection. A mismatch emits nothing
      further on that subscription and closes it
    - The identity compared is the one recorded at subscribe time from the authenticated session,
      never one read from the subscribe message
    - _Requirements: 21.1, 21.7_

  - [~] 26.6 Write property test for sequence contiguity
    - `tests/property/test_paper_channel_sequence.py::test_p53_channel_sequence_is_contiguous_from_one`
    - **Property P-53 (invariant, contiguity)** — for all sessions and all generated emission
      sequences, including emissions interleaved from two concurrent producers, the set of
      `sequence` values emitted for that session is exactly `{1, 2, …, k}`, each value appears once,
      each `event_id` appears once, and events delivered on a subscription arrive in ascending
      `sequence` order with no gap and no repetition
    - Oracle: `{1..k}` set equality
    - **Validates: Requirements 19.3, 19.8**

  - [~] 26.7 Write property test for WebSocket tenant isolation
    - `tests/property/test_tenant_isolation_matrix.py::test_p44_paper_channel_refuses_foreign_sessions`
    - **Property P-44 (invariant, WebSocket isolation)** — for all Paper_Sessions owned by `u2` and
      all subscription attempts authenticated as `u1`, the subscription is refused and no event for
      that session is delivered to `u1`
    - Lives in the tenant-isolation matrix module with P-41…P-46 (its siblings share the
      non-existent-record oracle), but is written here, next to the channel it constrains, so the
      registration of Task 26.3 is covered as it lands rather than nine tasks later
    - **Validates: Requirements 19.4, 19.6, 21.4**
    - Verification: `pytest tests/test_paper_event_schemas.py
      tests/property/test_paper_channel_sequence.py
      tests/property/test_tenant_isolation_matrix.py -k p44 tests/test_websocket_auth*.py`

- [ ] 27. Implement `paper_session_service.py` and `paper_replay.replay`

  - [~] 27.1 Implement `start_session` as the Requirement 17.9 pipeline, in order
    - Validate first, create nothing: `entitlement_resolver.resolve` (the same single admission
      decision deployment uses), `assert_paper_simulator(PaperSimulator)`, the version resolve and
      its deployable-lifecycle check, `validate_capital`, the exchange market metadata lookup, the
      timeframe check, the strategy/symbol/timeframe compatibility check, then the per-user
      concurrent cap
    - Only then, in one transaction: the `paper_sessions` insert at `CREATED` with the frozen
      config and `event_sequence = 0`, the session-scoped `paper_accounts` insert at `version = 1`,
      and the opening `paper_equity_snapshots` row with `cause='SESSION_START'`
    - Then `paper_market_feed.open_feed`; a refusal leaves the session at `CREATED` with no
      subscription. Then transition to `RUNNING`, emit `paper_session_started`, and spawn the loop
    - A refused start therefore leaves no session, no account, no order, no balance and no
      market-data subscription — which is why the order is load-bearing and not merely tidy
    - `MAX_CONCURRENT_PAPER_SESSIONS_PER_USER` defaults to 3, configurable 1–20, checked in one
      round trip against `idx_paper_sessions_running`; the refusal is
      `PAPER_SESSION_LIMIT_REACHED` (429) with a reason
    - _Requirements: 17.3, 17.4, 17.5, 17.6, 17.9, 17.13, 27.4_

  - [~] 27.2 Implement the session loop
    - An `asyncio` task, not a thread and not a process. Every I/O step is `await`ed; the one
      CPU-bound step — the DAG evaluation and indicator computation for a bar — is offloaded with
      `starlette.concurrency.run_in_threadpool`, so a slow indicator cannot stall the HTTP event
      loop. Nothing in the loop calls `time.sleep`
    - Reuses the **existing** DAG runtime and the existing signal-generation path; no second
      evaluation path and no paper-specific indicator implementation is introduced
    - Per event: emit `market_tick`, step the DAG, record each signal through
      `signal_trace_recorder` with `environment='PAPER'` and `paper_session_id`, emit
      `signal_generated` with the safe projection, submit the intent, check resting orders, then
      revalue and snapshot equity with the PnL and drawdown emissions
    - _Requirements: 17.10, 23.1, 23.5, 27.3_

  - [~] 27.3 Implement the session state machine and its four operations
    - `CREATED`, `RUNNING`, `PAUSED`, `STOPPED`; `start` from `CREATED`, `pause` from `RUNNING`,
      `resume` from `PAUSED`, `stop` from `RUNNING` or `PAUSED`, `reset` from `STOPPED`
    - Enforced three ways, as the order machine is: `chk_paper_session_state`,
      `paper_session_allowed_transitions` + `trg_paper_session_guard`, and this service before it
      writes. Each operation is recorded in `paper_events` with the requesting user and the
      resulting state
    - An operation from a state that does not permit it returns 409
      `PAPER_SESSION_OPERATION_REJECTED` naming both the current state and the rejected operation,
      and changes nothing
    - _Requirements: 17.7, 17.14_

  - [~] 27.4 Implement stop and reset
    - Stop: final orders, positions, balances, trades, metrics and the closing equity snapshot are
      committed in one transaction; **then** the market-data subscription is released
      (`PUBLISH {'action':'unsubscribe',…}` to `mds:commands` plus the local unsubscribe); then the
      Paper_Channel registration is closed; and only then does the endpoint report the stop
      complete. The history stays readable through `/api/paper/sessions/{id}/*` after the stop and
      after a restart
    - Reset: balances return to `initial_capital_minor`, every open order becomes `CANCELLED` and
      every open position closes to size zero, and a new equity series begins at
      `paper_equity_snapshots.series_index = previous + 1`. Nothing is deleted — the pre-reset
      orders, fills, trades, metrics and snapshots stay readable, distinguished by `series_index`
    - _Requirements: 17.2, 17.8, 17.15, 19.12_

  - [~] 27.5 Implement `paper_replay.replay(session_id)`
    - Reconstructs the session from `(paper_sessions.config, paper_market_events, the recorded
      order intents in paper_orders)` against a fresh in-memory store and returns the final order
      states, fills, balances, positions, realized PnL and equity series
    - Byte-identical because the simulator reads no clock for any decision — every timestamp it
      writes comes from the event payload, not from `now()` — reads no `random`, and takes fees,
      slippage and participation from the frozen config
    - This is the audit and replay path Requirement 15.5 asks for, and the harness P-31 drives
    - _Requirements: 15.4, 15.5_

  - [ ]* 27.6 Write unit tests for the pipeline's refusal ordering
    - `tests/test_paper_session_pipeline.py` — one case per validation in Task 27.1, each asserting
      the named `details.validation` value **and** that no `paper_sessions`, `paper_accounts` or
      `paper_equity_snapshots` row was created and no `mds:commands` message was published
    - A case asserting a feed refusal leaves the session at `CREATED` rather than `RUNNING`
    - A case asserting stop is not reported complete until the subscription and the channel
      registration are both released
    - Optional: P-17…P-32 cover the execution and accounting space; these pin the ordering, which
      is a sequence property rather than a quantified one
    - _Requirements: 17.9, 17.13, 17.8_
    - Verification: `pytest tests/test_paper_session_pipeline.py`

- [ ] 28. Add the Paper_Trading_API session endpoints

  - [~] 28.1 Add the session lifecycle routes to `backend_app/routers/paper_trading.py`
    - `POST /api/paper/sessions` (10/60s) with an `extra="forbid"` Pydantic body accepting **only**
      strategy or listing reference, `initial_capital_minor`, `currency`, `symbol` and `timeframe`
      (plus the optional idempotency key). Any other field — a definition, a graph, a plan, a
      version id, a user id — is a 422 that echoes **no** supplied value
    - `GET /api/paper/sessions` (120/60s, caller-scoped in the query, not filtered after
      retrieval) and `GET /api/paper/sessions/{id}` (120/60s, including `feed_state`,
      `market_data_source` and `event_sequence`)
    - `POST /api/paper/sessions/{id}/pause` | `/resume` | `/stop` | `/reset` (60/60s each,
      idempotent per the state machine)
    - Every route carries `_safe_uuid` on the path parameter and the ownership scope in the query;
      another user's session answers exactly as an unknown one does
    - _Requirements: 17.3, 17.7, 17.8, 17.14, 17.15, 21.1, 21.4, 21.5, 22.2, 22.3_

  - [~] 28.2 Add the seven sub-resource reads
    - `GET /api/paper/sessions/{id}/orders` | `/fills` | `/positions` | `/trades` | `/equity` |
      `/metrics` | `/events` (120/60s each), one select per sub-resource scoped by `session_id`,
      no per-row round trip
    - `/events?since_sequence=` is the REST equivalent of the WebSocket replay, sharing the same
      5000-row cap and the same `HISTORY_INCOMPLETE` answer, so a client that cannot hold a socket
      is not served a different history
    - No response carries a plan, node, indicator or version field
    - _Requirements: 17.2, 19.8, 19.9, 21.5, 27.2_

  - [~] 28.3 Leave the six existing endpoints alone
    - `GET /api/paper/account`, `/positions`, `/orders`, `/trades`, `/summary`,
      `POST /account/reset`, `POST /orders` and `DELETE /orders/{id}` keep their paths, methods,
      bodies, rate limits (120/60s reads, 60/60s writes, 30/60s reset) and their default-account
      semantics. This task adds routes; it removes, renames and retypes nothing
    - `tests/test_paper_session_api.py` asserts the `extra="forbid"` 422 echoes no supplied value,
      the rate limits, the caller scoping, and that the six existing endpoints still resolve to
      their own handlers
    - _Requirements: 17.12, 22.2, 22.4_
    - Verification: `pytest tests/test_paper_session_api.py
      tests/test_paper_api_shape_compatibility.py tests/test_router_registration_completeness.py`

- [ ] 29. Extend Signal Trace for the three environments

  - [~] 29.1 Extend `signal_service.py`'s projections behind the existing probe
    - `SIGNAL_SUMMARY_COLUMNS` and `SIGNAL_TRACE_COLUMNS` gain `environment` and
      `paper_session_id`, appended — the other thirty-four columns are not re-spelled
    - Conditional on the same migration probe `signal_service` already performs for 005b's
      `order_lifecycle_state` / `idempotency_key` pair (`SIGNAL_LIFECYCLE_COLUMNS` and its cached
      verdict): the new pair is probed as a set, for the same reason PostgREST reports only the
      first missing column. An absent pair degrades with a warning **naming
      `010_signal_environment.sql`**, exactly as the existing warning names 005b
    - No second probe mechanism and no second projection constant is introduced
    - _Requirements: 23.1, 23.7, 24.10_

  - [~] 29.2 Record PAPER signals through the existing write path
    - `SignalTraceEngine` and the `signals` / `signal_events` tables stay the only signal store. A
      paper signal travels the same `signal_service` write path as a live one, with
      `environment='PAPER'` and `paper_session_id` set, and `deployment_id` left **null** — a
      Paper_Session is not a deployment, and `paper_session_id` is the applicable identifier
    - `signals.order_lifecycle_state` carries the unchanged vocabulary from
      `backend_app/backend/order_lifecycle_state.py`: `GENERATED → PENDING → SUBMITTED →
      PARTIALLY_EXECUTED / EXECUTED`, or `REJECTED`. No paper-specific state vocabulary is added
    - _Requirements: 23.1, 23.5_

  - [~] 29.3 Add the environment filter to `routers/signal_trace.py`
    - `GET /signals`, `/signals/export` and `/signals/{id}` gain an `environment` filter declared
      as a list like the other Requirement 17.2 filter categories, so `?environment=PAPER&
      environment=LIVE` collects into both
    - The default is unchanged: the caller's own signals across all environments, scoped by the
      authenticated identity
    - No aggregate, total or chart series mixes `PAPER` with `LIVE` without an explicit environment
      label — the list, detail and export paths group by `environment`, and the frontend renders
      one series per environment
    - _Requirements: 23.2, 23.4, 23.6_

  - [~] 29.4 Add the subscriber-safe projection
    - `signal_service.SUBSCRIBER_SIGNAL_FIELDS` is the allow-list, and
      `build_signal_trace_detail` gains a viewer-role parameter. When the viewer is not the
      strategy owner — decided server-side by comparing the authenticated identity with
      `strategies.user_id` for the signal's `strategy_id`, never from a client claim — the response
      is restricted to exactly the Requirement 23.2 fields
    - `indicators`, `market_info` and `ml_info` are omitted **entirely**, as are the risk-rule
      internals `risk_reason`, `drawdown_check`, `exposure`, `expected_loss` and `expected_reward`
    - `tests/test_signal_trace_subscriber_projection.py` applies
      `listing_projection.assert_contains_no_protected_logic` to the subscriber view over
      generated protected-logic strategies, and asserts the owner's own view is unchanged
    - _Requirements: 7.9, 23.2, 23.3_
    - Verification: `pytest tests/test_signal_trace_subscriber_projection.py
      tests/test_signal_trace*.py tests/regression/test_baseline_unchanged.py -k signal_trace`

- [~] 30. Checkpoint — paper trading backend complete
  - Ensure all tests pass, including `tests/regression/test_baseline_unchanged.py` and
    `tests/test_paper_api_shape_compatibility.py`, that `tests/test_paper_no_random.py` is clean,
    and that `tests/property/test_property_coverage.py` now reports P-17 through P-32 and P-44,
    P-53 through P-56 present. Ask the user if questions arise.

- [ ] 31. Frontend API modules — one client, no scattered calls

  - [~] 31.1 Create `algo22-terminal/src/api/modules/library.js`
    - Imports `{ get, post, put, del, publicGet }` from `../../apiClient` — the shared client every
      other module uses; it constructs no client, no base URL and no host
    - Every method the design lists: `browse`, `browsePublic`, `featured`, `featuredPublic`,
      `trending`, `trendingPublic`, `categories`, `categoriesPublic`, `detail`, `detailPublic`,
      `myStrategies`, `submissions.{create,get,list,priceRange,setPrice,withdraw}`,
      `admin.{listSubmissions,getSubmission,approve,reject,publish,suspend,unpublish}`,
      `checkout`, `subscriptionStatus`, `cancelSubscription`, `renewSubscription`, `clone`,
      `rate`, `settings`, `creatorAnalytics`, `subscriberAnalytics`
    - JSDoc `@param`/`@returns` on each, matching the convention `modules/paper.js` already uses
    - _Requirements: 20.2, 20.10, 30.1_

  - [~] 31.2 Register it as `api.library` in `src/api/index.js`
    - One `import { libraryApi } from './modules/library';` beside the existing seventeen imports
      and one `library: libraryApi,` entry in the consolidated export, next to `paper: paperApi`
    - _Requirements: 20.2, 30.2_

  - [~] 31.3 Widen `algo22-terminal/src/api/modules/paper.js` with the `sessions` object
    - The existing eight methods (`getAccount`, `resetAccount`, `getPositions`, `getOrders`,
      `placeOrder`, `cancelOrder`, `getTrades`, `getSummary`) are **untouched** — `Portfolio.jsx`
      and `TradeHistory.jsx` call three of them today
    - Adds `sessions: { create, list, get, pause, resume, stop, reset, orders, fills, positions,
      trades, equity, metrics, events }`, with `events(id, since)` carrying `?since_sequence=`
    - _Requirements: 20.2, 30.2_

  - [~] 31.4 Create `libraryApi.test.js` and `paperApi.test.js`
    - Under `algo22-terminal/src/api/modules/__tests__/`, run with `vitest --run` (single
      execution, never watch mode)
    - Each mocks `../../apiClient` and asserts, for **every** method on both modules, that it calls
      the shared `get`/`post`/`put`/`del`/`publicGet` with the expected path — and that no method
      references `import.meta.env`, `fetch`, `axios.create`, `http://` or `https://`
    - Asserts `api.library` and `api.paper.sessions` are both reachable from `src/api/index.js`
    - _Requirements: 20.2, 29.2_
    - Verification: `cd algo22-terminal && npx vitest --run src/api`

- [ ] 32. Frontend pages

  - [~] 32.1 Create `algo22-terminal/src/pages/PaperTrading.jsx` and route it
    - `const PaperTrading = lazy(() => import('./pages/PaperTrading'));` beside the other
      authenticated page imports in `src/App.jsx`, and one
      `<Route path="/app/paper-trading" element={<Suspense fallback={PAGE_FALLBACK}>
      <PaperTrading /></Suspense>} />` inside the existing `<AuthGuard>` → `<AppShell>` tree, plus
      the `'paper-trading': '/app/paper-trading'` entry in `AppShell`'s `PATH_MAP` and a
      `components/Sidebar.jsx` navigation entry
    - Uses the existing design system, `useAppState`, `window.showToast(type, message)` — the
      notification mechanism `AppShell` installs — and `api.paper`. It constructs no HTTP client,
      no base URL and no host
    - Controls: strategy selection from `api.library.myStrategies()` (so a `SUBSCRIBED` entry can be
      started without the subscriber ever holding the definition), initial simulated capital,
      symbol, timeframe, and start / pause / resume / stop / reset
    - _Requirements: 20.1, 20.2, 20.3_

  - [~] 32.2 Implement all sixteen displays
    - Market-data status (`feed_state`, `feed_transport`), session status, current price, equity,
      cash, unrealized PnL, realized PnL, total return, max drawdown, win rate, trade count, open
      positions, open orders, completed trades, the equity curve, the PnL chart, the drawdown
      chart, trade markers on the price series, the signal stream, execution events, and feed
      latency and health
    - The equity curve is drawn from `api.paper.sessions.equity(id)` — the persisted
      `paper_equity_snapshots` — **not** from accumulated live event state, so a reconnect or a
      remount redraws the same curve
    - Every displayed figure carries a simulated label; a figure whose `stale` flag is true renders
      its `last_price_at` rather than presenting itself as current
    - _Requirements: 18.11, 18.15, 20.4, 20.6_

  - [~] 32.3 Implement the eight states and the reconnecting indicator
    - Each panel renders one of `loading`, `empty`, `error-with-retry`, `disabled`, `unauthorised`,
      `expired-subscription`, `unavailable-strategy`, `feed-disconnected`, and shows a reconnecting
      indicator while `websocketClient.onStatusChange` reports `'connecting'` or `'reconnecting'`
      (`onStatusChange(listener)` and `onOpen(listener)` already exist for exactly this)
    - No panel renders a stale or fabricated value in place of an error state — an error state is
      what the panel shows, not a zero
    - _Requirements: 20.5, 20.6, 28.1_

  - [~] 32.4 Implement the realtime wiring, its cleanup and its bounds
    - One `useEffect` keyed on `sessionId` holding
      `const release = websocketClient.subscribeChannel(\`paper.${sessionId}\`, onFrame)` and
      returning `() => { release(); clearInterval(poll); }`. `release()` is `subscribeChannel`'s
      ref-counted releaser, which tells the server to unsubscribe when the last hold goes
    - `onFrame` discards a frame whose `event_id` is already in a bounded `Set`, applies frames in
      `sequence` order, and on reconnect calls
      `api.paper.sessions.events(sessionId, lastSequence)` to close the gap
    - Retained state bounded at 500 events, 1000 ticks and 2000 chart points per series, oldest
      discarded first. Every `setInterval` and every listener the route creates is cleared on
      unmount
    - _Requirements: 19.8, 20.8, 27.5_

  - [~] 32.5 Implement the responsive layout
    - CSS grid with a single-column breakpoint below 768 px, tables switching to stacked cards
      below 640 px, and charts using a `ResizeObserver`-driven width, so primary content does not
      overflow horizontally at any width from 360 px to 1920 px
    - _Requirements: 20.7_

  - [~] 32.6 Move `StrategyMarketplace.jsx` onto `api.library`
    - Delete `import client, { publicGet } from '../apiClient'` and replace all eleven direct calls
      — lines 41, 44, 58, 61, 75, 78, 98, 101, 131, 134 (the `client.get`/`publicGet` pairs behind
      the token check), 150 (`clone`) and 164 (`checkout`) — with `api.library.*`, keeping the
      authenticated/anonymous split as the `*Public` method pairs
    - Replace the three `alert(...)` calls in `handleClone` (lines 151, 155) and `handleSubscribe`
      (lines 170, 176) with `window.showToast`
    - Owner-supplied `name`, `description`, `tags`, review text and rejection reason are rendered as
      React text children — never `dangerouslySetInnerHTML`, never through a markdown renderer
    - The `BACKTEST` / `PAPER` / `LIVE` performance sections are visually separated, each omitted
      entirely when it has no data rather than rendering zeros, and the `BACKTEST` section carries
      the historical-results statement
    - _Requirements: 6.6, 20.9, 20.10, 22.8, 28.1_

  - [~] 32.7 Render server-derived ownership in `Strategies.jsx`
    - Adds the `api.library.myStrategies()` read beside the existing `endpoints.strategies.list()`
      call, and renders `ownership: "OWNED" | "SUBSCRIBED"` and the affordances in
      `allowed_actions` **as returned** — no client-side inference of either, so the page cannot
      offer an action the server would refuse
    - A `SUBSCRIBED` entry offers at most `view_listing`, `run_backtest`, `deploy_live`,
      `start_paper`, `view_performance`, `view_subscription`, `renew`, `cancel_renewal`
    - An entry whose Subscription does not entitle renders in an explicit expired state with every
      execution action disabled and renewal offered; the page renders the loading, empty,
      error-with-retry, unauthorised, expired-subscription and unavailable-strategy states rather
      than a stale list
    - _Requirements: 12.2, 12.3, 12.4, 12.5, 12.6, 12.8_

  - [~] 32.8 Add the simulated indicator to `Portfolio.jsx` and `TradeHistory.jsx`
    - Both keep `api.paper.getSummary()`, `api.paper.getPositions()` and `api.paper.getTrades(100)`
      and their existing `environment === "paper"` toggle. The only change is that the PAPER branch
      renders the additive `is_simulated` / `execution_environment` fields as a visible simulated
      indicator **inside the same region as the figures**, not in a page header a scrolled user
      cannot see
    - Neither sums a PAPER figure into a LIVE total, which the existing toggle already prevents
      structurally since the two branches never run together
    - _Requirements: 13.6, 20.6, 28.1_

  - [~] 32.9 Create the three page test suites
    - `PaperTrading.test.jsx` — renders each of the eight states of Task 32.3; asserts the equity
      curve is drawn from `sessions.equity()` and not from event state; asserts that on unmount the
      `subscribeChannel` releaser and every `clearInterval` are called; asserts retained events,
      ticks and chart points are capped at 500 / 1000 / 2000; asserts every figure carries a
      simulated label
    - `StrategyMarketplace.test.jsx` — `vi.spyOn(window, 'alert')` with **zero** calls on every
      path; every call goes through `api.library`; owner-supplied text containing
      `<img src=x onerror=alert(1)>` renders no element (`container.querySelector('img')` is null);
      the three environment sections are separated, omitted when empty, and the `BACKTEST` section
      carries the historical-results statement
    - `Strategies.test.jsx` — `OWNED` and `SUBSCRIBED` labels come from the response; a
      `SUBSCRIBED` entry offers exactly the eight permitted actions and none of the thirteen
      forbidden ones; a non-entitling entry renders the expired state with execution disabled and
      renewal offered
    - Run with `vitest --run` against the existing `@testing-library/react`, `jsdom` and `vitest`
      dev dependencies already in `algo22-terminal/package.json`; nothing new is added
    - _Requirements: 12.2, 12.3, 12.4, 12.5, 12.8, 20.5, 20.6, 20.8, 20.9, 20.10, 22.8, 27.5, 29.2_
    - Verification: `cd algo22-terminal && npx vitest --run && npm run build`

- [ ] 33. Cross-cutting security, observability and performance

  - [~] 33.1 Build the tenant-isolation matrix from a registry, not a list
    - `tests/property/test_tenant_isolation_matrix.py` collects its **rows** from
      `app.router.routes` filtered by the `/api/library` and `/api/paper` prefixes plus every member
      of `ws_channels.OWNED_CHANNEL_FAMILIES`, so an endpoint or channel added later without a
      matrix entry fails the completeness assertion rather than passing silently
    - **Columns** are the ten resource kinds Requirement 21.8 names: strategy, submission,
      listing-private record, subscription, settlement record, paper account, paper session, paper
      order, paper position, user
    - Each cell authenticates as `u1`, references `u2`'s identifier, and asserts three things: the
      response equals the non-existent-record response in status **and** body; no row in any table
      this spec introduces changed (a full before/after snapshot comparison); and no field value
      belonging to `u2` appears anywhere in the response at any nesting depth
    - The test fails if any row×column cell is unattempted, and reports an overall failure if any
      single attempt fails any of the three assertions
    - Records, in the module docstring, that this verifies the **application-layer** guarantee:
      no PostgreSQL runs in CI, as `tests/security/test_builder_tenant_isolation.py` already
      records for migrations 004–004e, so the RLS text is asserted by
      `tests/test_marketplace_paper_schema_contract.py` and the runtime RLS behaviour is verified
      in the production sequence of Task 35, not here. Stated rather than implied
    - _Requirements: 21.2, 21.8_

  - [~] 33.2 Add the rate-limit key helper and apply the per-route limits
    - `backend_app/core/rate_limit_keys.py::caller_or_address(request)` returning `'u:' + sub` from
      a locally decoded token or `'ip:' + get_remote_address(request)`, with no database read and no
      exception escape — the global limiter's `key_func=get_remote_address` cannot express a
      per-authenticated-caller limit
    - Passed explicitly per route: `@limiter.limit("120/minute", key_func=caller_or_address)`. The
      catalogue routes carry two stacked limits whose keys differ, giving Requirement 6.7's
      120/60s per authenticated caller and 60/60s per source address
    - Every limit in the design's per-endpoint table applied; a rate-limited request computes
      nothing and persists nothing, and answers `MARKETPLACE_RATE_LIMITED` (429)
    - _Requirements: 6.7, 22.4_

  - [~] 33.3 Create `backend_app/backend/marketplace/media.py` for the SSRF surface
    - `validate_cover_reference(reference)` accepting only a reference whose host or storage prefix
      is in `ALLOWED_COVER_PREFIXES` — the project's Supabase storage bucket prefix and the CDN
      origin — and rejecting everything else with 422
    - Applied at **write** time on `library_strategies.cover_image`. No handler dereferences a
      caller-supplied URL; the frontend renders it in an `<img src>` and nothing server-side fetches
      it. `tests/test_cover_reference_ssrf.py` drives it with `http://169.254.169.254/…`,
      `file:///etc/passwd`, an internal hostname, a redirect chain and a prefix that is a string
      prefix of an allowed one but a different host
    - _Requirements: 22.7_

  - [~] 33.4 Create `backend_app/core/observability.py`
    - The `contextvars` `request_id` fallback (`uuid4()` per request) used when
      `asgi_correlation_id.CorrelationIdMiddleware` is absent, so Requirement 26.1's "every log
      record and every error response" holds without adding a dependency; `main.py` already imports
      the middleware conditionally, and that import is not changed
    - For a Paper_Session the per-event log key is `(session_id, sequence)`, which is what lets an
      operator line a log record up with the exact WebSocket frame a client received
    - `redact(record)` deny-listing by key name and value pattern: `api_key`, `apiKey`, `secret`,
      `api_secret`, `password`, `passphrase`, `token`, `access_token`, `refresh_token`,
      `authorization`, `stripe_signature`, `x_razorpay_signature`, `card`, `pan`, `cvv`,
      `client_secret`, `encrypted_api_key`, `encrypted_secret_key`, `encrypted_password`, plus every
      key in `listing_projection.DENIED_LISTING_COLUMNS` and every Protected_Logic document key
      (`buy_logic`, `sell_logic`, `indicators`, `risk`, `ml_model_path`, `blueprint`, `graph_json`,
      `execution_graph`, `compiled_plan`)
    - Another user's identifiers are excluded structurally: a log record's identity fields are
      populated from the authenticated context only, never from a request-supplied identifier
    - `tests/test_log_redaction.py` drives every new log call site with a record containing each
      deny-listed key and asserts the emitted text contains none of the values
    - _Requirements: 26.1, 26.3, 26.4_

  - [~] 33.5 Emit the metrics through the existing collector
    - Through `routers/metrics.py`; no second collector. Latency and error rate per introduced or
      modified endpoint (`marketplace.http.{route}.*`, `paper.http.{route}.*`), per WebSocket event
      type plus `paper.ws.queue_depth` and `paper.ws.slow_consumer_disconnects`, the feed metrics
      (`paper.feed.latency_ms`, `.events`, `.invalid`, `.duplicate`, `.out_of_order`, `.reconnects`,
      `.state{HEALTHY,DEGRADED,FALLBACK_REST}`), the signal metrics, the order metrics
      (`paper.order.submit_latency_ms`, `paper.fill.apply_latency_ms`,
      `paper.order.rejected{reason}`, `.concurrency_conflicts`, `.retries`), the database metrics
      including **`.round_trips`** — which is what P-57 asserts against — and the sweep metrics
      including `.last_run_at` as a health-check input
    - Requirement 27.6 is satisfied by these being measured and recorded, not by an assertion
    - _Requirements: 26.6, 27.6_

  - [~] 33.6 Create the two code-quality guard tests
    - `tests/test_no_broad_except_on_new_modules.py` — AST check failing on a bare `except` or a
      bare `except Exception` without a `raise` anywhere under
      `backend_app/backend/marketplace/` or `backend_app/backend/paper/`
    - `tests/test_no_duplicate_services.py` — asserts no new module re-implements a function whose
      name already exists in `core/entitlement_engine.py`, `routers/billing.py`,
      `backend/backtest_service.py`, `backend/market_data_*`, `api_ws/ws_manager.py` or
      `src/api/modules/*`
    - _Requirements: 30.2, 30.5_

  - [~] 33.7 Write property test for cross-tenant reads
    - `tests/property/test_tenant_isolation_matrix.py::test_p41_cross_tenant_read_returns_no_field`
    - **Property P-41 (invariant, cross-tenant read)** — for all distinct `(u1, u2)`, all endpoints
      introduced or modified by this spec, and all identifiers owned by `u2`, a request
      authenticated as `u1` returns no field of `u2`'s resource
    - Generator `tenant_pairs()`; oracle: the non-existent-record response captured from a fresh
      random UUID
    - **Validates: Requirements 21.4, 21.8**

  - [~] 33.8 Write property test for cross-tenant writes
    - `tests/property/test_tenant_isolation_matrix.py::test_p42_cross_tenant_write_leaves_rows_byte_identical`
    - **Property P-42 (invariant, cross-tenant write)** — under the same quantification, a request
      authenticated as `u1` leaves every row owned by `u2` byte-identical
    - **Validates: Requirements 21.4, 21.8**

  - [~] 33.9 Write property test for indistinguishability
    - `tests/property/test_tenant_isolation_matrix.py::test_p43_foreign_and_absent_responses_are_indistinguishable`
    - **Property P-43 (invariant, indistinguishability)** — for all resource identifiers, the
      response to a request for another user's existing resource is indistinguishable, in status
      code and body, from the response for a non-existent resource of the same kind
    - **Validates: Requirements 21.4, 5.7, 6.10**

  - [~] 33.10 Write property test for identity-source invariance
    - `tests/property/test_tenant_isolation_matrix.py::test_p45_supplied_identity_does_not_change_the_decision`
    - **Property P-45 (invariant, identity source)** — for all requests carrying a user, tenant,
      owner, subscriber or session identity in the body, query, path or WebSocket message, the
      authorisation decision is identical to the decision for the same request with that field
      absent
    - **Validates: Requirements 21.1**

  - [~] 33.11 Write property test for list scoping
    - `tests/property/test_tenant_isolation_matrix.py::test_p46_every_listed_row_is_owned_by_the_caller`
    - **Property P-46 (invariant, list scoping)** — for all generated multi-tenant datasets and all
      list endpoints, every returned row's owner equals the authenticated identity
    - Also asserts the scoping is in the query: the counting `FakeDB` of Task 33.12 records that no
      list endpoint retrieved a row it then filtered out
    - **Validates: Requirements 21.5**

  - [~] 33.12 Write property test for fixed round trips
    - `tests/property/test_fixed_round_trips.py::test_p57_round_trips_are_independent_of_row_count`
    - **Property P-57 (invariant, fixed round trips)** — for all Listing counts 0…50 and all
      combined owned-and-subscribed entry counts 0…200, the number of Persistence_Layer round trips
      recorded while serving one catalogue page is a constant independent of the Listing count, and
      the number recorded while serving the Strategies_Page combined list is a constant independent
      of the entry count
    - Oracle: a counting `FakeDB` wrapper; the constants are 2 and 3 per the design's round-trip
      table. Counting is the only way to test an N+1's **absence** rather than its current absence
    - **Validates: Requirements 27.1, 27.2**
    - Verification: `pytest tests/property/test_tenant_isolation_matrix.py
      tests/property/test_fixed_round_trips.py tests/test_log_redaction.py
      tests/test_cover_reference_ssrf.py tests/test_no_broad_except_on_new_modules.py
      tests/test_no_duplicate_services.py`

- [ ] 34. CI, end-to-end journey and the full concurrency battery

  - [~] 34.1 Add the `frontend-tests` job to `.github/workflows/01-pr-check.yml`
    - One new job running `npm ci`, `npm run build` and `npx vitest --run` with
      `working-directory: ./algo22-terminal`, `actions/setup-node@v4` with
      `cache-dependency-path: 'algo22-terminal/package-lock.json'`, triggered for changes under
      `algo22-terminal/` — the same path filter `06-frontend-deploy.yml` already uses
    - `validate-code`, `unit-tests` and `docker-check` keep their current scope, their current
      selectors and their blocking status. Nothing is removed, made non-blocking, or narrowed
    - `--run`, never watch mode, so the job terminates
    - _Requirements: 29.8, 29.9_

  - [~] 34.2 Create `tests/e2e/test_marketplace_paper_journey.py`
    - One test executing the twenty-four steps **in order**: register and sign in; create and save a
      strategy; run three backtests satisfying Requirement 3; verify three distinct
      Backtest_Conditions; submit a Listing; administrative review; approval and publication; public
      catalogue visibility; a second account viewing the Listing; subscribing; verifying the 90/10
      split in the Settlement_Ledger to the Minor_Unit; the Subscription becoming `ACTIVE` with a
      one-calendar-month expiry; the strategy appearing on the subscriber's Strategies_Page as
      `SUBSCRIBED`; confirming Protected_Logic is unreachable by the subscriber via
      `assert_contains_no_protected_logic` over **every** response the subscriber can reach;
      starting a Paper_Session; receiving real market data; generating signals; producing simulated
      orders and fills; updating positions, PnL and drawdown; rendering the charts (asserted through
      the frontend suite against the same fixtures); stopping the session; confirming persistence
      across a simulated restart; confirming `signals.environment = 'PAPER'`; expiring the
      Subscription; confirming access becomes non-entitling; and confirming a renewal payment
      restores access
    - _Requirements: 29.4_

  - [~] 34.3 Extend `tests/test_marketplace_concurrency.py` with the remaining batteries
    - N concurrent order intents against one Paper_Account: P-23's confluence, no intent applied
      twice or lost, and the equity identity holding after every one
    - N concurrent fills carrying the same `fill_event_id`: exactly one `paper_fills` row, one
      balance movement, one equity snapshot
    - Two concurrent Paper_Sessions of one user on the same strategy, symbol and timeframe: no write
      to one changes any row of the other
    - Two instances allocating `paper_events.sequence`: P-53's contiguity, with no gap and no
      duplicate
    - Concurrent expiry sweep and entitlement check at the expiry instant: the resolver's answer is
      `instant < expiry` regardless of the interleaving
    - Adds to the five batteries already in the file plus the two added in Tasks 14.12 and 19.13; it
      replaces none of them
    - _Requirements: 16.10, 16.9, 17.6, 19.3, 11.7, 11.8_

  - [~] 34.4 Extend the existing isolation and lifecycle suites additively
    - `tests/test_tenant_isolation_*` gains the new `/api/library/*` and `/api/paper/sessions/*`
      endpoints and the `paper.{session_id}` channel; `tests/sandbox_lifecycle/` gains the
      Paper_Session lifecycle
    - No existing assertion is weakened, skipped, xfailed or excluded by selector
    - _Requirements: 21.8, 25.8, 29.9_
    - Verification: `pytest tests/e2e/test_marketplace_paper_journey.py
      tests/test_marketplace_concurrency.py tests/test_tenant_isolation_*.py
      tests/sandbox_lifecycle/`

- [ ] 35. Final verification

  - [~] 35.1 Close the property-coverage scoreboard
    - `tests/property/test_property_coverage.py` (Task 2.3) passes: every one of P-1…P-58 has
      exactly one `test_p{n}_` function, none missing and none duplicated
    - Cross-checked against the design's per-property test-function table so every name matches
      character for character — a renamed test is a coverage gap the scoreboard would otherwise
      report as satisfied
    - _Requirements: 29.3_

  - [~] 35.2 Run the full local gate
    - `pytest tests/ -k "not chaos and not load"`, `black --check backend_app`,
      `isort --check-only backend_app`, `flake8 backend_app --select=E9,F63,F7,F82`,
      `python scripts/import_audit.py backend_app`, `python scripts/dependency_audit.py`,
      `tests/test_marketplace_paper_schema_contract.py`,
      `tests/test_schema_as_code_completeness.py`, `tests/test_library_schema_contract.py`,
      `tests/test_marketplace_paper_migrations.py` and
      `tests/regression/test_baseline_unchanged.py` all pass
    - `cd algo22-terminal && npx vitest --run && npm run build` passes
    - Assert each of the six root-cause regression tests fails on a revert of its fix:
      `tests/test_marketplace_checkout_regression.py`,
      `tests/test_subscription_renewal_regression.py`,
      `tests/test_creator_analytics_regression.py`,
      `tests/test_library_detail_projection_regression.py`,
      `tests/test_library_route_resolution.py`,
      `tests/test_backtest_evidence_columns_regression.py`. A regression test that passes against
      the unfixed code is not evidence of anything
    - Remove every temporary artifact created during verification
    - _Requirements: 25.7, 25.8, 29.1, 29.9, 29.10, 30.4_

  - [~] 35.3 Execute the Requirement 29.6 production sequence, in order
    - Local validation and the full test suite; static analysis; security scan; frontend build;
      backend build; container image build, validation and push; backend deployment; ECS rollout
      with target-group health and ALB health checks; health endpoint responses; frontend
      deployment; CloudFront invalidation and propagation; authenticated production API checks; the
      browser end-to-end journey; console, network and WebSocket inspection; ECS log inspection;
      database error inspection; and the Requirement 25 regression checks
    - The migration set is applied **by hand** before the backend deploy —
      `.github/workflows/03-deploy.yml` has no migration step — in the order
      `006 → 007 → 008 → 009 → 010`, with `010` in a maintenance window because its `SET NOT NULL`
      takes an `ACCESS EXCLUSIVE` lock and scans `signals`
    - This is where genuine PostgreSQL row-level-security behaviour is verified, since no
      PostgreSQL runs in CI and Task 33.1 asserts the application layer only
    - _Requirements: 24.8, 29.6, 21.2_

  - [~] 35.4 Record the clean-console evidence and diagnose any deployment failure at its root
    - On each of the six surfaces — Marketplace, Listing detail, Strategies, Paper Trading, Signal
      Trace, Billing — record zero of each: uncaught exceptions, unhandled promise rejections,
      application error dialogs, unexpected console warnings, failed API requests, unexpected 4xx
      or 5xx responses, mixed-content requests, cross-origin errors, WebSocket errors, and requests
      to a `localhost` address. Recorded as measured evidence, not asserted without measurement
    - On any deployment failure, diagnose the cause **before** redeploying, examining the container
      service events, the task stopped reason, the exit code, the application logs, the task
      definition, the environment variables, the execution and task roles, the security groups, the
      target group and health checks, the startup command, the migration status, the database
      connectivity, the secret resolution, the image reference, the architecture compatibility, the
      resource limits and the dependency startup order — including for a rollback
    - _Requirements: 29.5, 29.7_
    - Verification: the recorded evidence for Requirements 29.5 and 29.6, and a passing
      `pytest tests/property/test_property_coverage.py`

- [~] 36. Final checkpoint — the whole change is verified
  - Ensure the full suite, the frontend suite, the schema contract, the migration tests, the
    regression baseline and `tests/property/test_property_coverage.py` all pass, that no CI check
    was removed or weakened, and that the Requirement 29.5 and 29.6 evidence is recorded. Ask the
    user if questions arise.

## Notes

- Tasks marked with `*` are optional. They are the unit tests whose input space a property test
  already covers; skipping one costs the specific formula it pins, not a correctness guarantee.
  No core implementation sub-task is marked optional.
- Every task references the specific requirement clauses it satisfies, not just the user story, so
  a reviewer can check coverage clause by clause.
- Checkpoints are at Tasks 3, 10, 22, 30 and 36 — after the baseline, after the pure modules, after
  the marketplace backend, after the paper backend, and at the end.
- `tests/property/test_property_coverage.py` (Task 2.3) is the running scoreboard for the fifty-eight
  properties. It fails from the moment it is written and must pass at Task 35.1; it is what makes
  "one property, one property-based test" checkable rather than implied.
- Property tests validate universal properties over generated inputs; unit tests validate specific
  examples and edge cases. Neither substitutes for the other.
- Task 1 must complete before any other task. After the first change lands, the Requirement 25.7
  baseline can no longer be taken.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0,  "tasks": ["1.1"] },
    { "id": 1,  "tasks": ["1.2"] },
    { "id": 2,  "tasks": ["2.1", "2.2", "2.3"] },
    { "id": 3,  "tasks": ["4.1", "4.7", "5.1", "5.2", "5.3", "5.4", "5.5", "6.1", "7.1", "8.1", "8.2", "9.1", "12.1"] },
    { "id": 4,  "tasks": ["4.2", "4.8", "6.2", "7.2", "8.3", "9.2", "9.3", "12.2", "13.1"] },
    { "id": 5,  "tasks": ["4.3", "4.9", "6.3", "9.4", "11.1", "13.2"] },
    { "id": 6,  "tasks": ["4.4", "6.4", "9.5", "11.2"] },
    { "id": 7,  "tasks": ["4.5", "6.5", "9.6", "11.3"] },
    { "id": 8,  "tasks": ["4.6", "6.6", "9.7", "11.4"] },
    { "id": 9,  "tasks": ["6.7", "9.8", "11.5"] },
    { "id": 10, "tasks": ["6.8", "9.9", "11.6", "11.7"] },
    { "id": 11, "tasks": ["6.9", "11.8", "14.1"] },
    { "id": 12, "tasks": ["14.2", "14.3"] },
    { "id": 13, "tasks": ["14.4"] },
    { "id": 14, "tasks": ["14.5", "14.6"] },
    { "id": 15, "tasks": ["14.7", "14.8"] },
    { "id": 16, "tasks": ["14.9", "14.10"] },
    { "id": 17, "tasks": ["14.11", "14.12"] },
    { "id": 18, "tasks": ["15.1"] },
    { "id": 19, "tasks": ["15.2", "16.1"] },
    { "id": 20, "tasks": ["16.2", "16.3"] },
    { "id": 21, "tasks": ["16.4", "16.5", "16.6", "17.1"] },
    { "id": 22, "tasks": ["17.2"] },
    { "id": 23, "tasks": ["17.3"] },
    { "id": 24, "tasks": ["17.4", "17.5", "17.6"] },
    { "id": 25, "tasks": ["17.7", "17.8", "18.1"] },
    { "id": 26, "tasks": ["18.2"] },
    { "id": 27, "tasks": ["18.3", "19.1"] },
    { "id": 28, "tasks": ["19.2", "19.5"] },
    { "id": 29, "tasks": ["19.3", "19.6", "19.7"] },
    { "id": 30, "tasks": ["19.4", "19.8"] },
    { "id": 31, "tasks": ["19.9", "19.12"] },
    { "id": 32, "tasks": ["19.10", "19.13"] },
    { "id": 33, "tasks": ["19.11", "19.14", "20.1"] },
    { "id": 34, "tasks": ["20.2", "21.1"] },
    { "id": 35, "tasks": ["21.2", "23.1"] },
    { "id": 36, "tasks": ["23.2"] },
    { "id": 37, "tasks": ["23.3"] },
    { "id": 38, "tasks": ["23.4", "23.5", "24.1"] },
    { "id": 39, "tasks": ["23.6", "24.2"] },
    { "id": 40, "tasks": ["24.3"] },
    { "id": 41, "tasks": ["24.4", "25.1"] },
    { "id": 42, "tasks": ["24.5", "25.2"] },
    { "id": 43, "tasks": ["24.6", "25.3"] },
    { "id": 44, "tasks": ["24.7", "25.4"] },
    { "id": 45, "tasks": ["25.5"] },
    { "id": 46, "tasks": ["25.6", "26.1"] },
    { "id": 47, "tasks": ["25.7", "26.2"] },
    { "id": 48, "tasks": ["25.8", "26.3"] },
    { "id": 49, "tasks": ["25.9", "26.4"] },
    { "id": 50, "tasks": ["25.10", "26.5"] },
    { "id": 51, "tasks": ["25.11", "26.6"] },
    { "id": 52, "tasks": ["25.12", "26.7"] },
    { "id": 53, "tasks": ["25.13", "27.1"] },
    { "id": 54, "tasks": ["25.14", "27.2"] },
    { "id": 55, "tasks": ["25.15", "27.3"] },
    { "id": 56, "tasks": ["27.4", "28.1"] },
    { "id": 57, "tasks": ["27.5", "28.2"] },
    { "id": 58, "tasks": ["27.6", "28.3", "29.1"] },
    { "id": 59, "tasks": ["29.2", "31.1"] },
    { "id": 60, "tasks": ["29.3", "31.2"] },
    { "id": 61, "tasks": ["29.4", "31.3"] },
    { "id": 62, "tasks": ["31.4", "32.1"] },
    { "id": 63, "tasks": ["32.2", "32.6"] },
    { "id": 64, "tasks": ["32.3", "32.7"] },
    { "id": 65, "tasks": ["32.4", "32.8"] },
    { "id": 66, "tasks": ["32.5", "32.9", "33.1"] },
    { "id": 67, "tasks": ["33.2", "33.3", "33.4", "33.5", "33.6"] },
    { "id": 68, "tasks": ["33.7", "34.1"] },
    { "id": 69, "tasks": ["33.8", "34.2"] },
    { "id": 70, "tasks": ["33.9", "34.3"] },
    { "id": 71, "tasks": ["33.10", "34.4"] },
    { "id": 72, "tasks": ["33.11", "33.12"] },
    { "id": 73, "tasks": ["35.1"] },
    { "id": 74, "tasks": ["35.2"] },
    { "id": 75, "tasks": ["35.3"] },
    { "id": 76, "tasks": ["35.4"] }
  ]
}
```
