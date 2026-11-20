# WORK-03 — CONCURRENCY MAP: single-owner execution for a logical bot

**Finding:** WORK-03 — `FleetManager` enforced "one bot per (user, symbol)" with a
per-process Python dict, so two processes could each run a bot for the same account and
pair.
**Status:** CLOSED.
**Artefacts:** `backend_app/migrations/025_work03_bot_ownership_claims.sql`,
`backend_app/core/bot_ownership.py`, `backend_app/backend/fleet_manager.py`,
`tests/test_work03_bot_ownership_claims_postgres.py` (39 tests, 39 passed —
`staging/f_W4A1.txt`), `staging/falsify_work03_fixes.py` (3/3 controls behaved —
`staging/f_W4B2.txt`).

---

## 1. The invariant being enforced

> At most one active execution owner may exist for a logical bot at any instant,
> **including when the two candidate owners are different processes or pods.**

This is a correctness invariant, not a convenience one. Two owners means two `BotRunner`
instances evaluating the same strategy on the same account and pair, each unaware of the
other's fills — double position size, double risk consumption, and a reconciliation
mismatch that looks like venue drift rather than a duplicate runner.

## 2. The logical uniqueness key

| | |
|---|---|
| **Key** | `(user_id, symbol)` |
| **Enforced by** | `pk_bot_ownership_claims PRIMARY KEY (user_id, symbol)` |
| **Source of the shape** | `FleetManager._bot_key` (`fleet_manager.py:34-36`) builds `f"{user_id}_{symbol.replace('/', '_')}"` |

### Why not `strategy_id`

`uq_strategy_deployments_one_live` already existed and is a sound control, but it was not
the control for this defect, on two counts measured rather than assumed:

1. It is a partial unique index on `strategy_id` for live statuses, so it constrains the
   **deployment row**. `FleetManager` neither reads nor writes `strategy_deployments` —
   grepped; there is no reference to that table in the module.
2. Its key is the wrong shape. Two *different* strategies on the same `(user, symbol)`
   would both pass a `strategy_id` constraint and still produce two runners trading the
   same pair on the same account. `(user_id, symbol)` is what decides whether duplicate
   venue side effects are possible.

Both constraints are retained. They answer different questions.

### Tenant independence is structural

The key is prefixed by `user_id`, so two users starting the same symbol are two distinct
rows and can never contend. There is no shared lock name, no global mutex, and no
cross-tenant denial-of-service surface where one tenant's bot blocks another's.
Evidence: `test_independent_tenants_never_contend`,
`test_one_user_can_run_two_different_symbols`.

## 3. Why PostgreSQL and not a Redis lock

A Redis `SET NX EX` is the obvious reach and was rejected. `core/cache/redis_manager.py`
is deliberately **fail-soft**: every method swallows its exception and returns a falsey
default.

* Under that contract a Redis outage makes `SET NX` answer *"not acquired"* for
  everybody — an availability failure wearing a correctness failure's clothes.
* Under a future refactor that returned a truthy default on error, the same call answers
  *"acquired"* for everybody — and that is a silent two-bot bug.

**An availability dependency must not be able to flip a correctness invariant in the
unsafe direction.** PostgreSQL is already a hard dependency of the deployment path, and a
primary key cannot fail open.

WORK-03 does not consult Redis at all. Evidence:
`test_redis_being_down_cannot_grant_ownership`.

## 4. The state machine

Held in `bot_ownership_claims.state`, constrained by
`chk_bot_claim_state CHECK (state IN ('STARTING','ACTIVE','STOPPING'))`.

```
                 acquire()                  heartbeat()
   (no row) ──────────────────► STARTING ──────────────────► ACTIVE
      ▲                            │                           │
      │                            │  release()                │ release()
      │                            ▼                           ▼
      └────────────────────────  (row deleted)  ◄───────────────┘
      ▲
      │   acquire() by another worker, when
      │   heartbeat_at < now() - stale_threshold
      └─── ACTIVE/STARTING ──► STARTING (new owner_token)
```

The absence of a row is the unowned state, so release is a `DELETE` rather than a status
flag. A status flag would require a second mechanism to distinguish "released" from
"never claimed", and `released_at` exists only as a diagnostic column.

### Ownership is a token, not a boolean

`owner_token uuid` is regenerated on **every** successful claim, and both release and
heartbeat are scoped to it:

```sql
DELETE FROM public.bot_ownership_claims
 WHERE user_id = %(user_id)s AND symbol = %(symbol)s AND owner_token = %(token)s
RETURNING owner_token
```

Without the token, a worker that had been declared stale and had its claim taken over
would, on waking up, delete the **new** owner's claim — and a third worker could then
start a second bot. Evidence: `test_release_is_scoped_to_the_owner_token`,
`test_the_dead_worker_cannot_release_the_successors_claim`.

`heartbeat()` returning `False` is how a reaped worker *learns* it no longer owns the bot
and must stand down. Evidence: `test_heartbeat_reports_lost_ownership`.

## 5. The atomic claim

One statement does the whole thing (`bot_ownership.py::_ACQUIRE_SQL`):

```sql
INSERT INTO public.bot_ownership_claims
       (user_id, symbol, owner_token, worker_name, state, claimed_at, heartbeat_at)
VALUES (%(user_id)s, %(symbol)s, %(token)s, %(worker)s, 'STARTING', now(), now())
ON CONFLICT (user_id, symbol) DO UPDATE
   SET owner_token  = EXCLUDED.owner_token,
       worker_name  = EXCLUDED.worker_name,
       state        = 'STARTING',
       claimed_at   = now(),
       heartbeat_at = now(),
       released_at  = NULL
 WHERE public.bot_ownership_claims.heartbeat_at
       < now() - make_interval(secs => %(stale)s)
RETURNING owner_token, (xmax = 0) AS was_inserted
```

PostgreSQL takes the row lock for the `ON CONFLICT` path and **re-evaluates the `WHERE`
after acquiring it**. That is the serialisation point: of two concurrent claimants
exactly one can match.

| Row state at claim time | Outcome | Caller |
|---|---|---|
| no row | `INSERT` wins | `CLAIMED` — owns the bot |
| live row (recent heartbeat) | `WHERE` false → **zero rows** | `HELD_BY_ANOTHER` — stands down |
| stale row | `WHERE` true → ownership transfers | `RECLAIMED_STALE` — owns the bot |
| store unreachable | exception caught | `STORE_UNAVAILABLE` — **refuses** |

The loser gets **zero rows**: a deterministic no-op, not an exception and not a retry
loop. There is no timeout to tune and no lost-wakeup case.

### `xmax = 0`, and a bug this map exists to record

`(xmax = 0) AS was_inserted` is how PostgreSQL distinguishes the `INSERT` arm of an
upsert from the `DO UPDATE` arm: a freshly inserted tuple has no updating transaction, so
`xmax` is 0, while a row the conflict path updated carries the current transaction id.

The first version returned `claimed_at IS NOT NULL`, which is **always** true — the
`INSERT` sets it. Every claim would have reported itself as a stale takeover. Harmless to
the invariant (both outcomes are `granted`) but it would have made the logs claim a
healthy first start had reaped a dead pod, which is the class of false signal that wastes
an incident response.

## 6. Crash recovery: no permanent lock

The explicit design constraint was *do not create a lock that can strand a bot after
worker death*. A plain `ON CONFLICT DO NOTHING` would have done exactly that: a pod
killed mid-flight leaves a row nobody can delete, and the bot becomes permanently
unstartable.

| | |
|---|---|
| `STALE_AFTER_SECONDS` | `90` |
| `HEARTBEAT_INTERVAL_SECONDS` | `15` |
| `MIN_STALE_TO_INTERVAL_RATIO` | `3` (enforced floor) |
| Effective threshold | `effective_stale_seconds()` = `max(90, 3 × 15)` = `90` |

`effective_stale_seconds()` raises and warns rather than honouring a threshold inside a
few heartbeat periods. A threshold close to the interval does not *detect* worker loss,
it *manufactures* it — it reaps healthy claims mid-heartbeat, which converts a liveness
detector into a bot killer. This is the same rule `training_worker.MIN_STALE_TO_INTERVAL_RATIO`
already states for worker heartbeats. Evidence:
`test_the_stale_threshold_cannot_be_configured_below_its_floor`.

Recovery sequence after a pod dies holding a claim:

1. Heartbeats stop. The row stays, with `heartbeat_at` frozen.
2. For up to 90s, other workers get `HELD_BY_ANOTHER` and stand down. The bot is down and
   the trader's restart is refused — a bounded, visible outage, not a silent double-start.
3. After 90s the `WHERE` predicate becomes true. The next claimant transfers ownership and
   gets a **new** `owner_token`.
4. If the dead pod ever resurfaces, its release and heartbeat are scoped to the old token,
   so both are no-ops.

Evidence: `test_a_stale_claim_can_be_taken_over`, `test_a_live_claim_is_not_reclaimable`
(the mirror — the threshold must not reap a healthy claim),
`test_retry_after_recovery_succeeds_exactly_once` (two workers both notice the stale
claim; only one may take it).

## 6a. Claim liveness — the gap that made the stale threshold a 90-second fuse

The first implementation of this finding wired `claim_bot` and `release_bot` and stopped
there. `heartbeat()` was written and unit-tested but **called from nowhere in
`backend_app`** — the only production importers of the module were `claim_bot` and
`release_bot`.

That is not a cosmetic omission. It made the invariant hold for exactly
`STALE_AFTER_SECONDS` and no longer:

1. Worker A claims `(user, symbol)` at T0. `heartbeat_at = T0`, never refreshed.
2. At T0+90s the row is stale *by its own predicate*, while A's bot is still trading.
3. Worker B calls `start_bot` for the same pair. B's local `_active_fleet` check passes
   (different process); `claim_bot` finds the stale row and **grants** B ownership.
4. A is never told, because nothing calls `heartbeat()`.

Two runners, same account, same pair — the original defect, postponed by 90 seconds
rather than prevented. Measured, not inferred: `staging/falsify_work03_fixes.py` case B
drives exactly this sequence and the rival claim comes back `reclaimed_stale, granted =
True`.

### What was added

`FleetManager._heartbeat_claim` (`fleet_manager.py:194`), started as a task alongside the
bot (`:151`) and carried in the fleet record under `"heartbeat"`. It refreshes the claim
every `HEARTBEAT_INTERVAL_SECONDS` through `bot_ownership.heartbeat_bot`, which is called
via `asyncio.to_thread` because psycopg2 is blocking and a direct call would stall the
event loop every other bot in the process runs on.

Two failure modes, handled **oppositely on purpose**:

| Outcome | Meaning | Action |
|---|---|---|
| `False` | the scoped UPDATE matched zero rows — another worker owns this bot | surrender immediately |
| `ClaimStoreUnavailable` | the store could not be reached — *we do not know* | keep running, but only for up to `effective_stale_seconds()` of consecutive failures |

`heartbeat(cursor, claim)` returns `False` for *both*, which is correct for a caller that
holds the cursor and can see the exception itself. `heartbeat_bot` manages its own
connection and cannot, so it **raises** rather than collapsing the two into one `False`.
Without that distinction a transient network blip would stop a healthy bot; with a plain
"keep going on error" it would grant indefinite ownership. The bounded tolerance is the
fail-closed resolution: once we have been unable to refresh for longer than the stale
threshold, another worker is *entitled* to take over, so we must assume one has.

### Surrender does not release

`_surrender_bot` (`:180`) cancels the bot task and **deliberately does not release the
claim**. Release is token-scoped, so the `DELETE` would match zero rows anyway — but if
that scoping were ever relaxed, releasing here would delete the **new** owner's claim and
let a third worker start yet another bot. Asserted structurally
(`test_surrender_is_structurally_forbidden_from_releasing`) so the protection does not
depend on two files staying in agreement, and behaviourally
(`test_surrender_does_not_delete_the_successors_claim` — the table still holds exactly one
row, bearing the successor's token).

### Evidence

| Test | Role |
|---|---|
| `test_an_unrefreshed_claim_becomes_takeable_while_the_bot_still_runs` | **negative control** — the gap, stated as a passing test |
| `test_a_heartbeated_claim_stays_ours_and_is_not_takeable` | positive control — same aged claim, refreshed, rival now refused |
| `test_the_heartbeat_loop_refreshes_a_real_claim` | end to end through `_heartbeat_claim` against real PostgreSQL |
| `test_heartbeat_bot_reports_a_takeover_as_false` | the dispossessed owner learns it |
| `test_an_unreachable_store_raises_rather_than_denying_ownership` | the store-error / takeover distinction |
| `test_the_heartbeat_loop_stops_the_bot_when_ownership_is_lost` | loss cancels the runner, it does not merely log |
| `test_the_heartbeat_loop_tolerates_a_transient_store_outage` | a blip does not kill a live bot |
| `test_the_heartbeat_loop_surrenders_after_a_prolonged_outage` | …but a sustained one does |
| `test_start_bot_wires_a_claim_heartbeat` | AST — taking a claim is not enough if nothing keeps it alive |

## 7. Side-effect ordering in `FleetManager`

The claim is worthless if it is taken after the thing it protects. Measured positions in
`backend_app/backend/fleet_manager.py`:

```
start_bot()                                                 line  45
├─ async with self._lock
├─ if bot_key in self._active_fleet → refuse                line  62   (cheap local short circuit)
├─ MAX_SYSTEM_BOTS capacity check
├─ per-user tier / DEPLOYMENT_LIMITS check
├─ ownership = claim_bot(user_id, symbol)                   line 110   ◄── THE CLAIM
│    └─ if not ownership.granted → return False
├─ bot = BotRunner(...)                                     line 122   ◄── FIRST CONSTRUCTION
│    └─ on exception: release_bot(ownership)                line 134
├─ task = asyncio.create_task(self._boot_and_run(...))      line 139
├─ heartbeat_task = asyncio.create_task(                    line 151   ◄── CLAIM LIVENESS
│        self._heartbeat_claim(bot_key, ownership))
└─ self._active_fleet[bot_key] = {..., "claim": ownership,  line 159
                                  "heartbeat": heartbeat_task}
```

Two ordering decisions, both deliberate:

* **After the local checks.** A same-process duplicate short-circuits without a database
  round trip. The local dict remains a valid fast path; it is simply no longer the only
  control.
* **Before `BotRunner` is constructed.** No bot object, no task, and no venue connection
  exists if the claim is refused. Had the claim come after construction, a refused claim
  would have had to tear down a partially live runner.

### Every exit path releases the claim — there were four, not three

A claim that is never released strands the bot for a full stale interval: the trader
retries, is told "another process owns it", and nothing owns it at all.

| Exit | Location | Release |
|---|---|---|
| `BotRunner` construction raised | `start_bot` except branch | `release_bot(ownership)` — line 134 |
| **Boot failure** (`_recover_live_state` raised) | `_boot_and_run` except branch | `release_bot(record.get("claim"))` — line 275 |
| Clean stop | `stop_bot` after `bot.shutdown()` | `release_bot(fleet_record.get("claim"))` — line 306 |
| Crash / cancel | `_cleanup_dead_bot` after the pop | `release_bot(record.get("claim"))` — line 341 |

Each release is preceded by `_cancel_claim_heartbeat(record)` (lines 274, 305, 340), so
the heartbeat task cannot refresh a claim that is about to be deleted.

**The boot-failure path was leaking.** `_boot_and_run` pops the fleet record *itself* on a
boot failure and returns normally; the done-callback that performs the release then found
`None` and did nothing, so the claim survived with nothing running behind it. Found by
counting release sites against exit paths — `test_every_exit_path_releases_the_claim` had
asserted over three methods when there were four. Measured:
`staging/falsify_work03_fixes.py` case A neutralises the release and the claim row
survives, confirming the new test's oracle is load-bearing rather than incidental.

`_cleanup_dead_bot` releases only when `record is not None`. `stop_bot` and the boot-
failure branch have already popped the record, so the callback is a no-op there and the
claim is not released twice — and even a double release is safe, because the second
`DELETE` matches zero rows.

Evidence: `test_every_exit_path_releases_the_claim` (AST over all **four** exits),
`test_a_boot_failure_releases_the_claim` (behavioural, against real PostgreSQL — the row
must be gone),
`test_start_bot_is_wired_to_claim_bot` (AST, not substring — the comments in this region
mention `claim_bot` repeatedly, so a text scan would pass against an unwired module),
`test_start_bot_refuses_when_the_claim_is_refused` (the protected side effect, `BotRunner`
construction, must not happen).

## 8. Why duplicate exchange side effects cannot occur

Chaining the above:

1. A venue write originates inside a `BotRunner` trading loop. The only construction site
   for `BotRunner` in the fleet path is `fleet_manager.py:122`.
2. Line 122 is unreachable unless `claim_bot` returned `granted` — the refusal at line 110
   returns first.
3. `granted` is true only for `CLAIMED` or `RECLAIMED_STALE`, both of which mean this
   process holds the row for `(user_id, symbol)` with a token it generated.
4. The primary key means that row is unique, and the `ON CONFLICT ... WHERE` means a
   second holder can only appear once the first stops heartbeating for 90s.
5. The first does **not** stop heartbeating while it runs: `_heartbeat_claim` refreshes
   the claim every 15s for the bot's whole lifetime (§6a). So the stale predicate does not
   become true under a healthy owner.
6. If the claim is taken over anyway — because this process was partitioned from the
   database for longer than the threshold — the owner discovers it at the next heartbeat
   and `_surrender_bot` cancels the runner.

So the window for two live runners is bounded by one heartbeat interval after a takeover,
and a takeover can only occur when the original owner has already been unable to reach the
claim store for 90s. In that state it also surrenders on its own clock, without needing to
observe the takeover at all.

Additionally, the redelivery case that made this reachable is covered directly: the WORK-02
`XAUTOCLAIM` reaper can deliver the same `start_bot` command to a different consumer.
Evidence: `test_duplicate_delivery_of_the_same_start_is_idempotent` — twice must mean once.

## 9. Fail-closed behaviour, and the one deliberate exception

`acquire()` returns `STORE_UNAVAILABLE` with `granted == False` on any store error. The
asymmetry is the justification: the cost of a false refusal is a bot that does not start;
the cost of a false grant is two bots trading the same pair on one account.

`claim_bot()` must also decide the case where **no PostgreSQL claim store is configured at
all** (a SQLite or empty `DATABASE_URL`). It reuses
`backend_app.core.runtime_env.local_state_is_acceptable()` — the function written for
API-03's rate limiter — rather than introducing a second detector, because it is the same
question: *is there a second process to coordinate with?*

| Environment | Behaviour |
|---|---|
| Declared local/test (`DEV_MODE=true` / `ENV=development`) | Claim skipped, warning logged, falls back to `_active_fleet`. One process, no pods — process-local state genuinely is sufficient. Without this a developer on the default SQLite DSN could not start a paper bot. |
| Anything else, **including unset** | `STORE_UNAVAILABLE` → refuse. An unconfigured deployment is protected rather than exposed. |

Two definitions of "may this process substitute local state for shared state?" would
eventually drift apart; one cannot. Evidence:
`test_the_no_store_policy_fails_closed_outside_a_local_environment`,
`test_the_no_store_policy_permits_a_declared_local_environment`,
`test_the_no_store_policy_uses_the_same_detector_as_the_rate_limiter` (AST assertion that
it calls that exact function).

## 10. Who may write a claim

```sql
GRANT SELECT ON public.bot_ownership_claims TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.bot_ownership_claims TO service_role;
REVOKE INSERT, UPDATE, DELETE ON public.bot_ownership_claims FROM authenticated, anon;
REVOKE ALL ON public.bot_ownership_claims FROM anon;
```

Read-only for clients (the UI shows which bots are running); writes are the backend's
alone. A caller who could `INSERT` a claim could deny *themselves* — or, with a forged
`user_id`, somebody else — the ability to start a bot. RLS is enabled with an
`auth.uid() = user_id` owner policy mirroring `009_paper_trading.sql`, plus a
`service_role` policy. The migration's postflight block raises rather than reporting
success if the table, the primary key, or the grant revocation is missing.

Evidence: `test_a_client_cannot_write_a_claim`, `test_the_primary_key_is_the_invariant`
(asserted against `pg_constraint`, because without that constraint there is no uniqueness
invariant at all).

> Note: no table in this database has `FORCE ROW LEVEL SECURITY`, so the owner/SQLAlchemy
> connection bypasses RLS — as it does everywhere else in this schema (see
> `TENANT_ISOLATION_MAP.md`). The *grants* above are the control that binds the
> `authenticated` role; RLS here is defence in depth for a future request-scoped read.

## 11. How the concurrency claim was tested

The stated standard was that two coroutines in one process is insufficient. The suite
therefore runs real concurrency at three widths, plus a control:

| Test | Width | Mechanism |
|---|---|---|
| `test_two_connections_racing_produce_exactly_one_owner` | 2 connections | Two independent psycopg2 connections, barrier-synchronised |
| `test_many_connections_racing_still_produce_exactly_one_owner` | N connections | Widened, because a two-way race can pass by luck |
| `test_two_separate_processes_produce_exactly_one_owner` | 2 OS processes | The case `_active_fleet` could never have handled |
| `test_four_separate_processes_produce_exactly_one_owner` | 4 OS processes | Wall-clock `start_at` rendezvous |
| `test_the_harness_detects_a_process_local_implementation` | **negative control** | Swaps in a process-local implementation and asserts the harness **fails** |
| `staging/falsify_work03_fixes.py` | **falsification harness** | Neutralises each fix and confirms the oracle notices: 3/3 controls behaved |

The negative control is what makes the other four meaningful. Without it, a race test that
always observes one winner proves nothing — it could be measuring a harness that never
actually races. This test substitutes a dict-based claim and requires the multi-process
assertions to break; if they do not, the harness is not testing concurrency.

`acquire`/`release`/`heartbeat` take a caller-supplied cursor precisely so the tests can
drive genuinely separate connections; `claim_bot`/`release_bot` manage their own
short-lived connection for `FleetManager`, which holds no database handle.

The database is real in every test that makes a claim; the only monkeypatched function is
`heartbeat_bot` in the four loop-behaviour tests, where the subject under test is
`FleetManager`'s *reaction* to each outcome and the outcomes themselves are covered
separately against real PostgreSQL.

**Result: 39 passed** (`staging/f_W4A1.txt`).
Falsification harness: **3/3 controls behaved** (`staging/f_W4B2.txt`).
Fleet/worker/execution regression after wiring: see §13.

---

## 12. Residual risk

* **No reaper deletes abandoned rows.** They are harmless — the stale predicate makes them
  reclaimable, and a reclaim overwrites in place — but they accumulate.
  `idx_bot_claims_heartbeat` exists for that query. P3, housekeeping.
* **`shutdown_all` releases via `stop_bot`**, so a `SIGKILL`ed pod leaves its claims to the
  stale path rather than releasing them. That is the designed behaviour (the stale path is
  exactly what covers an unclean exit), not a gap.
* **The heartbeat interval sets the blast radius, not zero.** A database partition longer
  than 90s permits one other worker to claim the bot, and the original surrenders within
  one further heartbeat. Two runners can therefore overlap for up to ~15s in that
  scenario. Shrinking it means shortening the interval, which raises the floor on the
  stale threshold proportionally; the current 15s/90s pair is the tradeoff as shipped.
* **`BotRunner` itself does not check the claim before placing an order.** The claim is
  checked at start and maintained by the loop, so an order is placed while the heartbeat
  task believes we own the bot — but it is not re-verified at the venue boundary. The
  TRADE-04 live-execution gate is what guards that boundary; adding a claim check there
  would be defence in depth. P3.

---

## 13. Verification record

| Run | Scope | Result | Artefact |
|---|---|---|---|
| Targeted | `test_work03_bot_ownership_claims_postgres.py` | 39 passed | `staging/f_W4A1.txt` |
| Falsification | `staging/falsify_work03_fixes.py` | 3/3 controls behaved | `staging/f_W4B2.txt` |
| Regression | fleet / worker / execution suites | see `staging/f_W4C3.txt` | `staging/f_W4C3.txt` |
