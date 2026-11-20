# Phase 11B-D4 — DUP-02 Recovery and Launch-Gate Readiness

**Read-only audit. No production code, schema, test, baseline, credential, allowlist or
CCXT change was made. No commit, push, CI dispatch or Git mutation. No venue was
contacted. No database was migrated or mutated.**

**Live trading: OFF / NOT CERTIFIED.**

Headline: the D3 guard holds, and the audit found **four defects more serious than the gap
it was commissioned to close**. Three are in `OrderWatchdog` — the component D3 named as the
single owner of adjudication — and they mean that component cannot currently perform the
recovery the D3 report relies on. One is a correction to D3's own DUP-01 venue table.

---

## 1. Repository baseline and preservation evidence

### 1.1 Baseline, re-measured

| Property | D3 report | **Now** | Moved? |
|---|---|---|---|
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | **no** |
| Branch | `main` | `main` | no |
| `origin/main...HEAD` | `0 0` | `0 0` | no |
| Staged entries | 22 | **22** | **no** |
| Unstaged modified | 100 | **100** | no |
| `--porcelain` total | 256 | 257 | +1 (see 1.3) |

### 1.2 The D3 fix is intact, established by hash not by inspection

| File | D3-recorded SHA-256 | Measured now | Match |
|---|---|---|---|
| `backend_app/core/models/execution_record.py` | `DE6C95CE…58EB` | `DE6C95CE08AEF3185335A0ACE4EF73CDEFFC944C31B56AC98E8709D9BF6258EB` | **yes** |
| `backend_app/core/execution_engine.py` | `E1D65AA0…71CC` | `E1D65AA0E60868A4DBBE3E9644386BE3C9651CDE89781B7F03AE2BAFADDA71CC` | **yes** |

Both bytes-identical, so **every test result D3 recorded against these two files remains
valid** and was reused rather than re-run wholesale (section 4). `ccxt[async]==4.3.92`
remains the pin and the installed build.

### 1.3 Concurrent work identified and preserved

A different workstream wrote `staging/PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md`
(executed 2026-10-10, ~19:05–19:2x) after D3 finished. It is the `+1` in the porcelain
count. Reading it establishes three things that bear on this phase:

- It independently re-measured the same baseline (`074b772a`, 22 staged, 100 unstaged) and
  recorded a staged-content continuity digest of `4B807EF2…94F`, unchanged. **Nothing in
  this phase or D3 disturbed another workstream's staged set.**
- Its §9 states it made **no** database connection and did **not** run the PostgreSQL lane.
  It read `staging/pg_dsn.txt` but explicitly excludes that file from every step.
- It records a pending unstaged `.gitignore` edit (`+15 / −0`) that adds
  `staging/pgdata*/`, `staging/redis/`, `staging/*.log` and `staging/pg_dsn.txt`, and
  attributes it to the `POST_LAUNCH_IMPROVEMENTS.md` item-12 author. **Not touched here.**

No `checkout`, `reset`, `clean`, `stash` or `restore` was used in this phase. The only
writes are this report and read-only evidence files under `staging/p11bd4_*`.

### 1.4 PostgreSQL cluster — inspected, not started, not stopped

`python staging/bootstrap_pg.py verify` (the read-only subcommand) reports:

```
DSN_FILE: …\staging\pg_dsn.txt
TARGET:   PROVISIONED  public_tables=89  data_directory=…/staging/pgdata_r1
```

Same provisioned cluster D3 started, same table count, `127.0.0.1:51805/postgres`,
`pg_dsn.txt` mtime unchanged at 17:55:23 (D3's start). **It was not started, stopped or
re-pointed, and `pg_dsn.txt` was not rewritten.** It was used read-only and for the gated
suites in section 4, which build their own isolated state.

---

## 2. Current execution and watchdog call graphs

### 2.1 Submission

```
BotRunner tick
 └─ ExecutionEngine.execute_with_idempotency              execution_engine.py:657
     ├─ check_idempotent_execution(allow_failed_retry=True)        :893
     │    └─ ExecutionRecordRepository.check_idempotent_execution
     │                                             execution_record.py:331
     │         CASE 1 COMPLETED        → skip_return_result        :413
     │         CASE 2 EXECUTING        → skip_already_running      :438
     │         DUP-02 ambiguity guard  → skip_ambiguous_submission :456-507
     │         CASE 3 FAILED           → allow_retry | skip_failed_no_retry :509
     │         CASE 4 PENDING          → allow_retry               :556
     │         CASE 5 absent           → INSERT PENDING, execute
     ├─ branch table                                              :896-962
     ├─ claim_execution / claim_execution_within_capacity  execution_record.py:740 / :875
     └─ _execute_trade_internal
          ├─ mark_submitted  (own session, own commit)   execution_record.py:1203
          └─ place_order                              exchange_executor.py:1400
               └─ self._exchange.create_order                      :1645
```

### 2.2 Recovery, as actually wired

```
main.py:421-437  OrderWatchdog(SessionLocal(), WatchdogConfig(30s, 60s, 300s)).start()
                 — failure is swallowed; service_status = "INACTIVE" and the app serves on
 └─ _monitoring_loop (every 30 s)                       order_watchdog.py:173
     ├─ _reconciliation_loop (separate task, 5–10 s)              :220
     │    └─ _reconcile_open_orders → _find_open_orders           :249 / :341
     │           status IN (PENDING, EXECUTING) AND order_id IS NOT NULL
     ├─ _check_stale_orders → _find_stale_orders                  :387 / :670
     │           status IN (PENDING, EXECUTING)
     │           AND (last_exchange_sync < now-60s OR IS NULL)
     │           AND order_id IS NOT NULL
     │    └─ _refresh_order_status                                :700
     └─ recover_unsubmitted_executions                            :404
          └─ classify_stale_executions                 execution_record.py:1312
                 WHERE status = 'EXECUTING'
                   AND created_at < now() - make_interval(secs => 900)
                 (PostgreSQL-only; returns [] on any other dialect)
               ├─ failed_before_submission  → resolve_stale_execution(FAILED)
               └─ ambiguous_submission      → _adjudicate_ambiguous_execution  :489
```

`resolve_stale_execution` (`execution_record.py:1416`) is the recovery lease. Its CAS is
`… WHERE execution_id = :x AND tenant_id = :t AND upper(status::text) = 'EXECUTING'`
(`:1488`) and its allow-list is `{FAILED, COMPLETED, UNKNOWN}` (`:1452-1458`).

Other reconciliation present in the repository: `ReconciliationScheduler`
(`core/reconciliation_scheduler.py:50`) **is** started (`main.py:454-461`) but reads
`reconciliation_mismatches`, never `execution_records`, and never contacts a venue.
`ExchangeReconciliationService` (`backend/exchange_reconciliation.py:74`) has the right
primitives and **is not instantiated by any running process**. A second, unrelated
`ReconciliationScheduler` in `backend/distributed_execution/exchange_reconciliation_engine.py:110`
has no reference from `main.py` or any router.

---

## 3. Findings — workstreams A to D

Severity is this phase's assessment. Every item is read-only evidence; nothing was changed.

### A. Ambiguous-row visibility and alert delivery

**A-1 (P0, CONFIRMED). `_get_executor_for_order` is a synchronous `def` that is `await`ed
at three call sites, so every ambiguous row is adjudicated as "cannot ask".**

Defined `def` (not `async def`) at `order_watchdog.py:957`. Awaited at `:274`, `:506`,
`:709`. `await` on a non-awaitable raises `TypeError`.

At `:506` the await sits inside `try/except Exception`, which logs "could not build an
executor" and leaves `executor = None` — so `_adjudicate_ambiguous_execution` **always**
takes the first branch (`:519-534`): write `UNKNOWN`, raise an alert, never call
`find_order_by_client_order_id`. The entire venue-lookup design TRADE-07 built, and which
D3's report names as the adjudication owner, is unreachable. At `:709` and `:274` the
`TypeError` propagates into the caller's `except`.

**A-2 (P0, CONFIRMED). `_persist_order_update` does not exist.**

Called at `order_watchdog.py:728` and `:774`. A repo-wide search for
`def _persist_order_update` across `backend_app/**/*.py` returns **0** definitions, and
`OrderWatchdog` has no base class. Both calls raise `AttributeError`, swallowed by
`_check_stale_orders`' handler at `:397-403`. The preceding `order.status =
ExecutionStatus.UNKNOWN` dirties the ORM instance on the watchdog's long-lived session, so
whether the UNKNOWN persists depends on an unrelated later `self.db.commit()`.
**Unknown from code alone; not established.**

**A-3 (P0, CONFIRMED). `execution_records.order_id` is never written, so two of the
watchdog's three selectors and the websocket fill path select nothing on the live path.**

TRADE-12 correctly moved the venue identifier to `venue_order_id` because `order_id`
carries `FOREIGN KEY → orders(id)`. Nothing was updated to read the new column:

- `execution_engine.py:1103` is the only id write on the success path and it writes
  `venue_order_id=`. There is **no `order_id=` anywhere in the engine.**
- `resolve_stale_execution` writes `venue_order_id = COALESCE(:venue_order_id, …)`
  (`execution_record.py:1482`). The watchdog's `order_id=venue_order_id` arguments at
  `order_watchdog.py:590/611/626` are that keyword, landing in that column.
- A repo-wide search for an assignment to `.order_id` on an execution record returns
  exactly one hit, and it is a **read** comparison (`exchange_reconciliation.py:290`).

Therefore `order_id IS NULL` for every live execution, and:

| Consumer | Predicate | Effect |
|---|---|---|
| `_find_stale_orders` | `order_id IS NOT NULL` (`:694`) | selects **nothing** |
| `_find_open_orders` | `order_id IS NOT NULL` (`:359`) | selects **nothing** |
| `event_router._find_execution_by_order_id` | `order_id == order_id` (`:128-136`) | **never matches** |

So `_refresh_order_status`, `_reconcile_open_orders` and `_update_order_from_status` are
dead on the live path, and a websocket `ORDER_FILLED` event cannot find its execution row.
`classify_stale_executions` is unaffected — with `order_id` always NULL every stale
`EXECUTING` row classifies as `failed_before_submission` or `ambiguous_submission`, which
is the one recovery path that still selects rows. It is also the path A-1 disables.

**A-4 (P0, CONFIRMED). Nothing can move a row out of `UNKNOWN`, and the D3 guard refuses
`UNKNOWN` unconditionally. Together they make `UNKNOWN` a permanent block.**

`submission_may_have_reached_venue` returns `True` for `UNKNOWN` regardless of
`submitted_at` (`execution_record.py:66-78`, documented at `:58-62`), so the guard returns
`skip_ambiguous_submission` on every subsequent tick for that deterministic
`execution_id`. The only writers of `execution_records.status` are:

| Writer | Reachability for an UNKNOWN row |
|---|---|
| `resolve_stale_execution` (`:1488`) | pinned to `upper(status::text) = 'EXECUTING'` → no-op, logs "no longer EXECUTING… Left alone", returns False |
| `_update_order_from_status` (`order_watchdog.py:815/853/885/892`) | only reached from the two selectors that require `order_id IS NOT NULL` → unreachable (A-3) |
| `claim_execution`, `claim_execution_within_capacity` | `PENDING`-only |
| `event_router._handle_filled/_cancelled/_rejected` (`:152/:218/:233`) | has no status filter, **but** finds the row by `order_id`, which is NULL (A-3) → unreachable |
| `exchange_reconciliation.py:379/401/535` | service not started |

**This is a liveness defect, not a safety one** — the row is correctly never resubmitted —
but the strategy/symbol/bucket is blocked indefinitely with no automatic exit.

**A-5 (P1, CONFIRMED). The refused row is in no recovery population at all.**
`classify_stale_executions`' filter is the bare literal `status = 'EXECUTING'`
(`execution_record.py:1383`), with a hard non-PostgreSQL gate above it returning `[]`.
A row the D3 guard refuses stays `FAILED` or `UNKNOWN` and is therefore invisible. This is
the gap D3 recorded as residual risk 6.1; it is confirmed, and A-1/A-3 make it worse than
D3 described, because even widening the filter would not help while adjudication is broken.

**A-6 (P1, CONFIRMED). The documented alert configuration surface cannot configure the
alert system the watchdog uses. The two sets of variable names do not intersect.**

`get_alert_system()` (`backend/alert_system.py:479-516`) registers channels only from env:

| Variable read | Line |
|---|---|
| `ALERT_TELEGRAM_BOT_TOKEN` | `:492` |
| `ALERT_TELEGRAM_CHAT_ID` | `:493` |
| `ALERT_SMTP_HOST` / `_USER` / `_PASSWORD`, `ALERT_FROM_EMAIL`, `ALERT_TO_EMAILS` | `:500-504` |

`.env.example` declares `ALERT_SLACK_WEBHOOK_URL`, `ALERT_TELEGRAM_ENABLED`,
`ALERT_EMAIL_ENABLED`. Measured consumers:

| `.env.example` key | Consumer |
|---|---|
| `ALERT_SLACK_WEBHOOK_URL` | **none in `backend_app`** |
| `ALERT_TELEGRAM_ENABLED` | `deployment_config.py:63,141` only |
| `ALERT_EMAIL_ENABLED` | `deployment_config.py:64,142` only |

`WebhookAlertChannel` is defined (`alert_system.py:266`) and **never instantiated**.
`.env` contains no `ALERT_*` and no `SENTRY_DSN`. With no channels, `send_alert` logs
`"Failed to send alert: {title}"` and returns `False` (`:413`).

**A-7 (P2, CONFIRMED). Alert deduplication collapses distinct executions.** The rate-limit
key is `f"{level.value}:{title}"` with `_min_interval_seconds = 60` (`alert_system.py:349`,
key built at `:371`), and the watchdog's titles are the constants
`"Stale Execution Requires Reconciliation"` and `"Order Stuck Alert"`. Two different
executions alerting within 60 s collapse to one; the second is **dropped, not queued**.
There is no acknowledgement, no escalation and no persistence (history is a 1000-entry
process-local list).

**A-8 (P2, CONFIRMED). `logger.critical` reaches an operator only through Sentry.** The
only logging configuration is `logging.basicConfig(level=INFO, …)` at `main.py:224-227`.
No `dictConfig`, no `SMTPHandler`. The one promotion is
`LoggingIntegration(level=INFO, event_level=ERROR)` in
`backend/observability/sentry_config.py:109-113`, gated on the `sentry_sdk` import and on
`SENTRY_DSN` being set (`main.py:273-277`). **Whether any Sentry rule pages anyone is
outside this repository and is UNKNOWN.** Without a DSN, `DUP-02_AMBIGUOUS_SUBMISSION_NOT_RETRIED`
is a container log line and nothing more.

**A-9 (P2, CONFIRMED). There is no operator surface for `execution_records`.** A grep for
`ExecutionStatus`, `execution_records`, `ExecutionRecordModel` and `list_by_tenant` across
`backend_app/routers/**/*.py` finds no endpoint. `backend_app/backend/routers/` does not
exist. `list_by_tenant` (`execution_record.py:665-690`) is exactly the needed query — it
filters tenant, optional status, optional symbol, with limit/offset — and its only callers
are `scripts/attack_*.py` and tests. `/health/services` (`main.py:1067-1085`) exposes only
`order_watchdog: ACTIVE|INACTIVE`, and is a boot-time snapshot by its own admission.
`routers/orders.py:1066` lists from a QuestDB `executions` telemetry table — a different
store, no status filter.

**A-10 (P1, CONFIRMED). The "ordinary reconciliation path takes it from here" comment is
false.** `order_watchdog.py:835-841` records the venue id and claims the row rejoins
`_find_stale_orders`' population. It does not: that branch writes status `UNKNOWN`, both
selectors require `PENDING|EXECUTING`, and the id lands in `venue_order_id` which neither
selector reads (A-3).

**Smallest safe design, recommended not implemented.** Fix A-1 and A-2 first — they are
two-line corrections (`async def`, and a real persist) that restore the adjudication the
rest of the design already assumes, and they need no schema change, no new worker and no
new recovery owner. Then A-3 (teach the existing selectors `venue_order_id`). Only after
those, consider widening `classify_stale_executions` to include
`FAILED`-with-`submitted_at` and `UNKNOWN`, because widening it today feeds rows into a
broken adjudicator and into `resolve_stale_execution`'s `EXECUTING`-only CAS, which would
discard the result silently. **A-4 needs an explicit exit rule for `UNKNOWN` and that is an
ownership decision, not a code tidy-up.** No row was transitioned and no recovery behaviour
was added in this phase.

### B. Indeterminate outcome and state-model audit

**B-1 (CONFIRMED). The status column is a native PostgreSQL enum, so a new status is a
migration, not a code change.** `ExecutionStatus` (`execution_record.py:143-149`) has five
members with **lowercase** values; the physical type is `executionstatus` with **UPPERCASE**
labels, declared only in Alembic
(`alembic/versions/d97ffff9c3bb_execution_schema_rebuild.py:59`), `nullable=False`. **No
CHECK constraint exists** and **no SQL migration creates or alters this column** — 030 is
the only SQL migration touching the table and it adds `venue_order_id` (varchar NULL, no
FK, with a postflight that aborts if an FK is ever added). `submitted_at` is nullable.
The lowercase/uppercase divergence is a measured defect already documented in-code
(TRADE-06, `execution_record.py:772-790`), which is why every raw-SQL site uses an
uppercase literal or `upper(status::text)`.

**B-2 (CONFIRMED). A timeout is not distinguishable from a venue rejection anywhere in the
chain, and the classifier is worse than D3 described.** `create_order` is wrapped by a
single `except Exception` (`exchange_executor.py:1681`), so every exception type is treated
identically. `_handle_ccxt_error` (`:1223-1257`) then **substring-matches `str(error)`** in
a fixed order:

```
insufficient|balance → InsufficientFundsError
rate limit|too many requests → RateLimitError
symbol|market → InvalidSymbolError          ← third rung
network|timeout|connection → NetworkError   ← fourth rung
authentication|apikey|key → AuthenticationError
```

**A `RequestTimeout` whose message embeds the request URL — which ccxt messages routinely
do, e.g. `…?symbol=BTCUSDT…` — matches `"symbol"` at the third rung and is classified
`InvalidSymbolError` before it ever reaches the fourth.** So a timeout can be labelled a
determinate venue rejection inside the classifier itself. The repository already documents
this classifier's imprecision independently
(`tests/exchange_sandbox/test_sandbox_order_lifecycle.py:393-397`;
`scripts/verify_exchange_connectivity.py:318-322`). `place_order` then collapses every
class to `success=False, status="rejected"` (`:1690-1699`), and `error.retryable` — computed
at `:1256` — is **discarded**, as `OrderResult` has no such field. The engine reads only
`.success` (`execution_engine.py:1557-1575`) and writes `FAILED` at `:1134` and `:1162`.
The only surviving signal is `submitted_at` plus free-text inside the `result` JSON, which
nothing branches on.

**B-3 (CONFIRMED). Blast radius of a new status, enumerated.** The single most serious item:

> `check_idempotent_execution` is a chain of positive equality tests with **no `else`** —
> COMPLETED `:413`, EXECUTING `:438`, the ambiguity guard `:485`, FAILED `:513`, PENDING
> `:557`. A row carrying a new status **with `submitted_at IS NULL`** matches none of them,
> falls out of `if existing:`, and reaches "CASE 5: NOT EXISTS → INSERT pending". The INSERT
> violates the primary key, the `except` handler walks the same ladder again (`:643-660`),
> matches nothing, and ends at `raise e`. **The function raises `IntegrityError` instead of
> deciding.**

Other consumers, grouped:

| Group | Behaviour with an unrecognised status |
|---|---|
| Retry/idempotency | `submission_may_have_reached_venue` falls through to the `submitted_at` test — safe post-submission, unsafe-by-omission pre-submission. Claims are `PENDING`-only → never claimable, fails safe. |
| Capacity | `claim_execution_within_capacity` sums `EXECUTING` only (`:1048-1056`) → a new status contributes **zero** committed notional even if it holds live exposure. |
| Recovery | invisible to `classify_stale_executions`; **refused** by `resolve_stale_execution`'s allow-list (`:1452-1458`) and by its `EXECUTING`-only CAS. This choke point is deliberate and works. |
| Reporting | `get_stats` (`:1569-1595`) counts PENDING/EXECUTING/COMPLETED/FAILED plus `total_count`. **`UNKNOWN` already has no bucket**, so the parts already do not sum to the total today. `metrics_system.py:163-193` likewise. |
| API | **no router imports or branches on `ExecutionStatus`** — the only hits are a prose comment in `routers/signals.py` and the status-independent timestamp map in `routers/strategies.py:937-975`. `main.py:87-88` imports the model only to register the table. |
| Accounting | `event_router.py:218` already conflates cancelled into `FAILED` with the in-code note `# Or a new CANCELLED status`. `fee_records` keys on `execution_id`, not status (`migrations/026…sql:227-233`). |
| Watchdog | `_update_order_from_status` has **no `else`** — an unrecognised *venue* status leaves the local status unchanged while still committing `last_exchange_sync`. |

**B-4 (CONFIRMED). There is no `ALTER TYPE … ADD VALUE` precedent in the repository**, and
the highest migration on disk is `032`. The ledger is `public.schema_migrations` keyed on
`filename`, written by `scripts/provision_database.py` with `ON CONFLICT DO NOTHING`.
Adding an enum label is therefore a first-of-its-kind migration here. **Not attempted.**

**B-5 (CONFIRMED). The existing indeterminacy vocabulary is `UNKNOWN`,
`AMBIGUOUS_SUBMISSION_ACTION` and `RECOVERY_AMBIGUOUS_SUBMISSION`.** Nothing named
`indeterminate`, `needs_reconciliation`, `pending_confirmation`, `in_doubt` or
`unconfirmed` exists. `_adjudicate_ambiguous_execution` (`order_watchdog.py:489-634`) is the
fullest six-way treatment of an unacknowledged submission already written — and A-1 means
only its first branch executes.

**Assessment.** The existing model *can* represent the indeterminate outcome: `UNKNOWN`
plus `submitted_at` is sufficient, and `resolve_stale_execution`'s allow-list already
treats `UNKNOWN` as the honest third option. What is missing is not a status — it is (i) an
exit rule for `UNKNOWN` (A-4) and (ii) a classifier that does not relabel a timeout as a
rejection (B-2). **Recommendation: fix B-2 and A-1/A-2/A-3 before considering a new status
at all.** A new enum member would require an `ALTER TYPE` with no precedent, and would land
in a `check_idempotent_execution` ladder that raises rather than decides (B-3).

### C. Venue duplicate-order safety

**All six venues receive the unified ccxt key `clientOrderId` and nothing else.**
`exchange_executor.py:1622-1623` sets `extra_params["clientOrderId"] = self._consumed_execution_id`;
there is **no per-venue branching** for order parameters anywhere in `backend_app/` (the
only `exchange_id` branch in the file is symbol formatting, `:536-543`). The value comes
from `verify_and_consume_token` (`:1145-1171`), which raises `ValueError` if the id is
absent — so the `if` at `:1622` is defensive, not a reachable "send without a client id".

**Measured client-order-id format.** `generate_execution_id` returns
`f"exec_{sha256[:16]}"`. Executed: `exec_ae35c6048fe800e0`, **21 characters**, contains an
underscore, **not** matching `[a-z0-9]{1,32}`, **not** numeric.

| Venue | ccxt 4.3.92 mapping | Evidence | Our id vs documented constraint |
|---|---|---|---|
| binance | `clientOrderId`→`newClientOrderId`, honoured; auto-generates `brokerId + uuid22()` only when absent (`binance.py:5604, 5686-5693`) | (b) ccxt source | 21 chars, `_` permitted by Binance's charset — OK |
| bybit | →`orderLinkId`, honoured (`bybit.py:3555-3557`); source caps it at 36 chars (`:3484`) | (b) | OK |
| okx | →`clOrdId`, honoured (`okx.py:2581, 2755-2761`); source documents **`[a-z0-9]{1,32}`** (`:2548`, `:3473`) | (b) | **our `_` violates the documented charset** — acceptance UNKNOWN |
| kucoin | →`clientOid`, honoured, mandatory, defaults to `self.uuid()` only if absent (`kucoin.py:2229-2232`) | (b) | OK |
| coinbase | **IGNORED AND OVERWRITTEN.** `'client_order_id': id + '-' + self.uuid()` set unconditionally (`coinbase.py:2619`) and our key stripped at `:2748` | (b) | our id never reaches the venue |
| kraken | **→`userref`, HONOURED** via `order_request` (`kraken.py:1703-1707`), shared by `create_order` (`:1420`) and `edit_order` (`:1818`) | (b) | Kraken documents `userref` as a 32-bit signed integer; our id is non-numeric and ccxt performs **no validation** — acceptance UNKNOWN |

**C-1 (CORRECTION TO THE D3 REPORT, P1).** D3 §7.2 states kraken has "no `clientOrderId`
in any `create*`/`edit*` method". **That is wrong.** `kraken.py:1703-1707` maps
`clientOrderId` (or `userref`) onto `request['userref']`, and both `create_order` and
`edit_order` route through that helper. The real kraken exposure is different and narrower:
ccxt forwards our **non-numeric 21-character string** verbatim into a field Kraken
documents as a 32-bit signed integer, with no width, type or numeric check anywhere in
ccxt 4.3.92. Whether Kraken rejects the order outright, coerces the value, or ignores it
**cannot be established from code** and is the single most important thing to measure first
in a kraken sandbox. D3's conclusion that kraken offers no usable venue-side idempotency
today still stands, but for this reason, not the one given.

**C-2 (P1, CONFIRMED). coinbase has no venue-side idempotency and no client-id recovery.**
Established from ccxt source, not from venue behaviour. The `VENUE-SIDE IDEMPOTENCY`
comment at `exchange_executor.py:1616-1621` is false for coinbase: no idempotency key
reaches the venue. `coinbase.fetch_order` has **no client-id branch** —
`request = {'order_id': id}` (`coinbase.py:3072-3075`) — and `parse_order` returns ccxt's
own uuid (`:2913`), so `_matches` can never be true.

**C-3 (P1, CONFIRMED). `find_order_by_client_order_id` can adopt the wrong order.**
`exchange_executor.py:1865`:

```python
if isinstance(order, dict) and (_matches(order) or order.get("id")):
    return _as_status(order)
```

The `or order.get("id")` accepts **any** dict carrying an `id` without verifying identity.
Combined with C-2 — where our client id is passed as coinbase's `order_id` positional
(`:1860-1862` → `coinbase.py:3073`) — a coinbase response would be adopted unchecked. In
practice that request should 404, after which the scans run; if the scans then succeed the
method returns a confident `status="not_found"` for an order that may exist.

**C-4 (P2, CONFIRMED). bybit's direct lookup is dead code.** `has['fetchOrder'] = False`
(`bybit.py:117`); `fetch_order` raises `NotSupported` for UTA accounts (`:4534`) and
`fetch_order_classic` raises `NotSupported` for spot (`:4507`). Recovery still works via the
open/closed scans, because `parse_order` echoes `orderLinkId` as `clientOrderId`
(`:3266`) — but only inside the venue's visibility windows.

**Fail-closed approach for coinbase and kraken, designed not implemented.** Neither venue
can be relied on to refuse a duplicate, so the refusal must be local and must not depend on
a venue answer. The minimal shape: keep the D3 guard exactly as it is (it already refuses
without consulting a venue), and add a **pre-submission venue-capability gate** that refuses
a *live* order on a venue whose client-order-id handling has not been certified — reusing
the existing `assert_exchange_routable` / `assert_sandbox_supported` pattern in
`exchange_executor.py` rather than introducing a new gate. That is a routing-policy change
and is **out of scope for a readiness audit**; it is item V-1 in the matrix.

**Sandbox evidence required to certify each venue** (none of which exists today):

| Venue | Minimum evidence |
|---|---|
| all six | place one order with a known `clientOrderId`; re-place the identical id; record whether the venue refuses it and with what error |
| okx | whether `exec_…` (underscore) is accepted as `clOrdId` at all |
| kraken | whether a non-numeric `userref` is accepted, coerced or rejected; and what `parse_order` echoes back |
| coinbase | what the venue returns for `order_id=<our client id>`, to size the C-3 misadoption risk |
| bybit | the open/closed visibility window, since the direct lookup is unusable |
| binance, kucoin | that uniqueness is enforced rather than merely accepted |

### D. Regression baseline and test reliability

**D-1 (CONFIRMED, NO ACTION NEEDED). The D3 baseline edit is accurate, minimal and
additive.** Compared against `staging/p11bd3_backup/live_order_path.json.before`:

| Check | Result |
|---|---|
| `git diff --no-index --numstat` | **`7  0`** — seven lines added, **zero removed** |
| Hunks | **2** |
| `contract.returns` | **13 → 14** entries |
| Added entry | `{"execution_id":"computed","message":"str","result":"computed","status":"str"}` — shape-identical to the existing refusal returns |
| `_baseline_note_ambiguous_submission_refusal_dup02` | present, 955 chars, root level |
| `_baseline_note_collaborator_sequence_trade01` | present, **byte-identical** (case-sensitive compare, 1845 chars) |
| `_baseline_note_update_status_venue_order_id_trade12` | present, **byte-identical** (1248 chars) |
| `contract._baseline_note_parameters_risk_verdict` | present, **byte-identical** (1609 chars) |

All three pre-existing provenance notes survive unchanged; one note was added. The record
matches the intended contract change: the live-order path genuinely gained a
`skipped_ambiguous_submission` outcome. **Assessment: accurate, and it followed the
procedure `tests/regression/test_baseline_unchanged.py`'s own docstring prescribes.** It
still rests on this workstream's authority rather than a requirement id, so it remains an
owner-review item (B-1 in the matrix) — but there is no evidence of a papered-over
regression, and the pre-edit file is retained for direct reversion.

**D-2 (CONFIRMED). The 51 failures in `staging/p11bd3_broad2.txt` separate into 50
order-dependent and 1 pre-existing stale assertion. Zero genuine regressions.**

| File | Failures in the 14,311-test run | In isolation |
|---|---|---|
| `test_marketplace_deployment_subscriber_safe.py` | 25 | pass |
| `test_entitlement_matrix.py` | 24 | pass |
| `test_kill03_authoritative_global_halt.py` | 1 | pass |
| `test_exchange_credential_log_redaction.py` | 1 | **fails** |

`staging/p11bd3_triage_iso.txt`: **1 failed, 108 passed** for those four files run together.
Both marketplace files have **zero** grep hits for `execution_engine`, `execution_record`,
`check_idempotent_execution` or `execute_with_idempotency`, so they cannot be caused by the
D3 change. The reproducible one is a **stale assertion**, not a secret leak: the test
expects `Failed to connect to binance` and instead gets
`EXCH-01: refused a sandbox connection to binance that could not be proven to have left
production` (`exchange_executor.py:1337`) — the EXCH-01 guard short-circuits before the
log line the test asserts on. No credential appears in the emitted line. Its subject file
carries no delta from D3 or this phase.

**D-3 (NEW, P1). The three most serious findings in this audit are completely untested.**
Measured across `tests/*.py`:

| Symbol | Occurrences in tests |
|---|---|
| `_persist_order_update` | **0** |
| `_get_executor_for_order` | **0** |
| `_refresh_order_status` | **0** |

So A-1 and A-2 are not merely unfixed, they are unobserved — which is why a 14,184-passing
broad run says nothing about them. Only five test files reference `OrderWatchdog` at all
(`test_dup02_…`, `test_exchange_safety_fix`, `test_p10d_exchange_reconciliation_fails_safely`,
`test_position_delta_race_condition_fix`, `test_trade07_stale_execution_recovery_postgres`),
and TRADE-07's suite exercises `classify_stale_executions` / `resolve_stale_execution`
directly rather than through `_adjudicate_ambiguous_execution`'s executor path.
**No test was deleted and no baseline was updated to make anything green.**

---

## 4. Focused test results

Python `.venv\Scripts\python.exe` 3.12.10, pytest 9.1.1, `AERORA_TEST_DATABASE_URL`
pointed at the verified provisioned cluster (section 1.4). **No skip is counted as a pass.**

### 4.1 This phase's run

`staging/p11bd4_focused.txt` — **EXIT=0, 300 passed, 0 failed, 0 skipped** (140 s), twelve
suites covering every area the brief names:

| Area | Suite |
|---|---|
| DUP-02 refusal + legitimate-retry negative controls; every action handled explicitly | `tests/test_dup02_ambiguous_submission_is_not_retried.py` |
| Watchdog selection and recovery behaviour | `tests/test_trade07_stale_execution_recovery_postgres.py` |
| Baseline provenance and contract drift | `tests/regression/test_baseline_unchanged.py` |
| Idempotency fail-closed | `tests/test_idempotent_submission_fails_closed.py` |
| Venue client-order-id mapping | `tests/test_dup01_client_order_id_mapping.py` |
| Reconciliation fail-closed | `tests/test_p10d_exchange_reconciliation_fails_safely.py`, `tests/test_reconciliation_engine_fail_closed.py`, `tests/test_exchange_reconciliation.py`, `tests/test_exchange_reconciliation_startup_assessment.py` |
| Order state transitions | `tests/test_order_state_engine.py` |
| Alerting | `tests/test_task_9_2_builder_alerts.py` |
| Watchdog position-lock statement | `tests/test_positions_table_column_agreement.py` |

### 4.2 Evidence reused from D3, justified by hash

Both D3-owned production files are byte-identical (section 1.2) and ccxt is unchanged, so
these were **not** re-run:

| Scope | D3 result | Artifact |
|---|---|---|
| DUP-02 suite before the fix | 12 failed, 7 passed | `staging/p11bd3_before.txt` |
| TRADE-07 on real PostgreSQL | 27 passed, 0 skipped | `staging/p11bd3_reg3_pg.txt` |
| All previously-skipping PostgreSQL suites | 218 passed, 0 skipped | `staging/p11bd3_pg_full.txt` |
| 74-suite execution/risk surface | 1,791 passed / 9 skipped / 1 pre-existing failure | `staging/p11bd3_reg5a.txt`, `…5b.txt` |
| Broad suite | 14,184 passed, 51 triaged failures | `staging/p11bd3_broad2.txt` |

### 4.3 Read-only evidence produced by this phase

`staging/p11bd4_status.txt`, `p11bd4_numstat_unstaged.txt`, `p11bd4_numstat_staged.txt`,
`p11bd4_pgverify.txt`, `p11bd4_focused.txt`.

### 4.4 What the tests do NOT establish

- **Nothing** about A-1, A-2 or A-3: zero coverage (D-3).
- **No venue-side behaviour** on any of the six venues. No sandbox credential exists; Redis
  is unreachable so `enforce_kill_switch` fails closed before an order can be placed.
  Scenario 8 of the 11B matrix remains unexecuted.
- Whether the dirtied `UNKNOWN` attribute in `_refresh_order_status` is incidentally
  flushed — **UNKNOWN**, not determinable from code.
- Whether any Sentry rule pages an operator — **UNKNOWN**, outside the repository.

---

## 5. Ranked blockers and approval matrix

### 5.1 Ranked

| # | Finding | Sev | Why it ranks here |
|---|---|---|---|
| 1 | **A-1** `_get_executor_for_order` awaited but sync | P0 | Disables all ambiguous adjudication; two-line fix |
| 2 | **A-3** `order_id` never written → 2 selectors + fill path dead | P0 | Silently removes three recovery/fill mechanisms on the live path |
| 3 | **A-2** `_persist_order_update` undefined | P0 | UNKNOWN persistence is incidental; operator reason never stored |
| 4 | **A-4** nothing exits `UNKNOWN`; guard refuses it forever | P0 | Permanent per-bucket trading block; needs an ownership decision |
| 5 | **B-2** timeout relabelled as a determinate rejection by substring order | P0 | Corrupts the stored outcome upstream of every consumer |
| 6 | **A-6** alert variable names do not intersect `.env.example` | P0 | The only automatic signal for a refused row cannot be configured |
| 7 | **C-1** kraken `userref` typing (and D3 correction) | P1 | Could fail every live kraken order, or silently lose the key |
| 8 | **C-2/C-3** coinbase has no key, and lookup can adopt the wrong order | P1 | Duplicate undetectable and recovery can mis-attribute |
| 9 | **A-5** refused rows in no recovery population | P1 | D3's known gap; blocked behind 1–3 |
| 10 | **D-3** zero coverage for A-1/A-2/A-3 | P1 | Why a 14,184-pass run proved nothing here |
| 11 | **A-10** false in-code claim about rejoining the reconciliation path | P1 | Misleads the next implementer |
| 12 | **A-7/A-8/A-9** dedup collapse, Sentry-only promotion, no operator surface | P2 | Operability, not correctness |
| 13 | **C-4** bybit direct lookup dead | P2 | Scans still work |
| 14 | **B-3** `check_idempotent_execution` raises on an unknown status | P2 | Latent; only matters if a status is added |

### 5.2 Approval matrix

Every row is a **proposal**. None was implemented.

| ID | Finding | Files / existing owner | Proposed minimal change | Authorization required | Tests / evidence needed | Rollback | Isolatable from concurrent work? |
|---|---|---|---|---|---|---|---|
| **W-1** | A-1 | `backend/order_watchdog.py:957` — watchdog owner | `def` → `async def` (or drop the three `await`s) | Watchdog owner | New unit test asserting `_adjudicate_ambiguous_execution` reaches `find_order_by_client_order_id` with a stub executor; re-run TRADE-07 | Revert one line | **Yes** — file has no D3 or staged delta |
| **W-2** | A-2 | `order_watchdog.py:728, 774` | Define `_persist_order_update`, or replace both calls with the existing commit path | Watchdog owner | Test that the UNKNOWN status **and** `exchange_status` reason both persist | Revert | **Yes** |
| **W-3** | A-3 | `order_watchdog.py:359, 694`; `backend/event_router.py:128-136` | Teach the selectors and the event lookup `venue_order_id` (coalesce, do not replace `order_id`) | Watchdog owner **+** execution-state owner (event_router) | PostgreSQL test proving a live completed row is selected; fill-event test | Revert predicates | **Yes**, but touches two owners |
| **W-4** | A-4 | `execution_record.py` guard + `resolve_stale_execution` | Define an explicit, bounded exit rule for `UNKNOWN` | **Execution-state owner + Coordinator** — this is a safety-semantics decision | Test that an `UNKNOWN` row can leave the state by exactly one sanctioned route and no other | Revert | **Yes**, but conflicts with D3's guard semantics — must be decided with W-6 |
| **W-5** | A-5 | `execution_record.py:1382` | Widen `classify_stale_executions` to `FAILED`-with-`submitted_at` and `UNKNOWN` | **Execution-state owner** | Must land **after** W-1/W-2/W-3; TRADE-07 plus a new ambiguous-FAILED case | Revert the predicate | **No** — pointless and possibly harmful before W-1 |
| **W-6** | B-2 | `exchange_executor.py:1223-1257, 1690-1699` | Classify on the ccxt exception **type**, not substrings; carry the indeterminate outcome on `OrderResult` | **Execution-state owner + Coordinator** | Table test over every ccxt exception class, including a timeout whose message contains `symbol=` | Revert the classifier | **Yes** — `exchange_executor.py` has no D3 delta, but it carries another workstream's `+1188/−55`; **coordinate** |
| **A-1x** | A-6 | `.env.example`; `backend/alert_system.py:479-516` | Align the names, and register `WebhookAlertChannel` so `ALERT_SLACK_WEBHOOK_URL` has a consumer | **Platform / deployment owner** | Test asserting every `.env.example` `ALERT_*` key has a consumer | Revert | **Yes** |
| **A-2x** | A-7 | `alert_system.py:348, 404-412` | Include the execution id in the dedup key; queue rather than drop | Platform / alerting owner | Test that two executions inside 60 s both deliver | Revert | **Yes** |
| **A-3x** | A-9 | new router over the existing `list_by_tenant` | Read-only operator endpoint filtered by status | **API owner + Coordinator** (new public surface, needs authz review) | Authz + tenant-isolation tests; must not expose cross-tenant rows | Remove the route | **Yes**, but `routers/exchange.py` is in the **22 staged entries** — do not touch that file |
| **V-1** | C-1, C-2 | `exchange_executor.py` routing policy | Pre-submission gate refusing *live* orders on an uncertified venue, reusing `assert_exchange_routable` | **Coordinator + risk owner** — routing policy | Sandbox evidence per the table in §3.C first | Revert the gate | **Yes** |
| **V-2** | C-3 | `exchange_executor.py:1865` | Drop `or order.get("id")`; require an identity match | Execution-state owner | Test that a non-matching order is not adopted | Revert one clause | **Yes** |
| **S-1** | B-1, B-4 | new Alembic/SQL migration | **Not recommended now.** If a status is ever added: `ALTER TYPE executionstatus ADD VALUE`, uppercase label, plus an `else` in `check_idempotent_execution` (B-3) | **DB owner + Coordinator** | Migration must be proven idempotent and reversible; `ALTER TYPE … ADD VALUE` cannot be rolled back inside a transaction on older PostgreSQL | **Hard** — enum labels are not droppable | **No** — 022–025 are applied-but-unledgered per the concurrent report; the ledger must be settled first |
| **T-1** | D-3 | `tests/` | Tests for A-1/A-2/A-3 | **Stream D (tests)** | — | Delete the tests | **Yes** |
| **B-1r** | D-1 | `tests/regression/baseline/live_order_path.json` | **Ratify or revert** the D3 re-record | **Coordinator** | Already evidenced in §3.D-1; pre-edit file retained | Copy `staging/p11bd3_backup/live_order_path.json.before` back | **Yes** |

### 5.3 Read-only findings that need no approval

A-10 (false comment), A-8 (Sentry-only promotion — a deployment fact, not a code change),
C-4 (bybit dead code — scans work), D-2 (triage conclusion), and the D3 DUP-01 correction
in C-1, which is recorded here rather than by editing the D3 report.

---

## 6. Recommended implementation sequence and rollback

Strictly ordered. Each step is independently revertible, and no step requires the next.

1. **W-1 + W-2** (one PR, watchdog owner). Two defects in one file, no schema, no new
   worker, no ownership change. This is what makes `OrderWatchdog` actually able to
   adjudicate — everything downstream assumes it already can. Add T-1 coverage in the same
   change. *Rollback: revert the file.*
2. **W-3** (watchdog + execution-state owners). Restores the two selectors and the
   websocket fill path. Needs the PostgreSQL lane. *Rollback: revert two predicates.*
3. **A-1x** (platform). Align the alert names and register the webhook channel. Independent
   of 1–2 and it is what turns the existing `logger.critical` into a delivered signal.
   *Rollback: revert.*
4. **W-6** (execution-state owner + coordinator). Type-based error classification, so a
   timeout stops being stored as a rejection. Do this **before** W-4/W-5: both depend on the
   stored outcome being honest. *Rollback: revert the classifier; `OrderResult` gains a
   field, which is additive.*
5. **V-2**, then **V-1** once sandbox evidence exists. *Rollback: revert.*
6. **W-4** (coordinator decision), then **W-5**. The `UNKNOWN` exit rule must be decided
   before the recovery population is widened, or rows will be selected into a state nothing
   can resolve. *Rollback: revert the predicate; no data migration is involved because no
   step above writes a new status.*
7. **A-2x**, **A-3x** last — operability, not correctness.
8. **S-1 only if a new status is genuinely required.** On current evidence it is not:
   `UNKNOWN` + `submitted_at` already expresses the indeterminate outcome, and steps 1, 2
   and 4 close the real gaps without touching the enum.

**Global rollback properties.** Steps 1–7 are code-only, additive or predicate-level, and
revert by file. No step writes a new status value, so no row can be left carrying a value a
reverted build cannot parse. Step 8 is the only one that is not cleanly reversible —
PostgreSQL enum labels cannot be dropped — which is the main reason it is last and
conditional. The D3 baseline entry (B-1r) reverts by restoring one retained file.

**Preservation.** Every proposal above avoids the 22 staged entries and the pending
`.gitignore` edit. `routers/exchange.py` is staged by another workstream and A-3x must not
touch it. `exchange_executor.py` carries a large unstaged delta from another workstream, so
W-6 and V-1/V-2 need coordination even though they do not conflict with D3.

---

## 7. Live-trading certification verdict

**NOT CERTIFIED. Live trading remains OFF.** The verdict is stronger than D3's, not weaker,
because this audit found that the component D3 designated as the recovery owner cannot
currently perform recovery.

| Gate | Status |
|---|---|
| DUP-02 client-side retry guard | **Holds.** Hash-verified intact; 300 focused tests pass, 0 skipped |
| Ambiguous rows reach an adjudicator | **NO** — A-1 makes every row take the "cannot ask" branch |
| Adjudication outcome persists | **UNKNOWN** — A-2; not determinable from code |
| Live orders are reconcilable at all | **NO** — A-3; `order_id` is never written, so two selectors and the fill path select nothing |
| A refused row can ever be cleared | **NO** — A-4; nothing exits `UNKNOWN` |
| Stored outcome distinguishes timeout from rejection | **NO** — B-2 |
| Any automatic operator signal exists | **NO** — A-6; the documented `ALERT_*` names have no consumer, and `SENTRY_DSN` is unset |
| Operator can list or resolve a refused row | **NO** — A-9; no endpoint, no CLI |
| Venue refuses a duplicate `clientOrderId` | **UNVERIFIED on all six.** coinbase provably cannot (C-2); kraken's `userref` typing is unresolved (C-1); the other four are code-level only |
| Our client-order-id format is venue-legal | **UNKNOWN** — `exec_…` violates okx's documented `[a-z0-9]{1,32}` and is non-numeric for kraken's `userref` |
| Sandbox credentials | **Absent** — blocks all of 11B scenario 8 |
| Redis reachable (`enforce_kill_switch`) | **Absent** — fails closed |
| Contract-drift guard green | **Yes** — 60 passed; D3 re-record verified additive (+7/−0) with all prior notes intact |
| Broad regression | **14,184 passed**; 51 failures all triaged — 50 order-dependent, 1 pre-existing stale assertion, **0 regressions** |

**The minimum to reopen the live-trading question is steps 1, 2, 4 and 6 of section 6, plus
sandbox evidence for kraken and coinbase.** Until W-1 and W-2 land, the D3 guard is the only
thing standing between a lost venue response and a duplicate position — it works, and it is
alone.

### Facts explicitly marked UNKNOWN

- Whether the dirtied `UNKNOWN` ORM attribute at `order_watchdog.py:719/768` is incidentally
  flushed by a later commit on the shared session. *Evidence needed: an instrumented
  PostgreSQL run of `_refresh_order_status`.*
- Whether any Sentry alert rule pages an on-call engineer. *Evidence needed: the Sentry
  project configuration, outside this repository.*
- Whether `ALERT_*` or `SENTRY_DSN` are set in the deployed ECS/production environment.
  *Evidence needed: the deployment's task definition. Only `docker-compose.yml:26-28` and
  `deploy-archive/.../docker-compose.staging.yml:48-50` set Telegram in-repo.*
- Whether okx accepts `exec_…` as `clOrdId`, and whether kraken accepts a non-numeric
  `userref`. *Evidence needed: one authorized sandbox order per venue.*
- Whether `backend/distributed_execution/exchange_reconciliation_engine.py` is started by
  any non-`backend_app` entrypoint. *Evidence needed: a search of worker/CLI entrypoints.*

---

Live trading **OFF / NOT CERTIFIED** · no venue contacted · no credential, allowlist or CCXT
change · no migration or data mutation · no watchdog or status implementation change · no
test removed · no baseline edited · no commit, push or CI dispatch · D3 fix hash-verified
intact · 22 staged entries and 100 unstaged files preserved.
