## What this changes

One functional line, plus the three real-Redis test files it switches on.

```
+          AERORA_TEST_REDIS_URL: redis://localhost:6379/15
```

`tests/conftest.py` sets `os.environ["REDIS_URL"] = ""` unconditionally at import, so the
11,000-test `unit-tests` lane cannot be re-pointed at a live cache. **That is correct and is
left alone.** Its consequence was that the `redis:7-alpine` service in that job — healthy,
mapped, health-checked — could never reach the tests written to exercise it: they read
`REDIS_URL`, found `''`, and skipped on every PR.

`AERORA_TEST_REDIS_URL` is the second, deliberate name, the same pattern
`AERORA_TEST_DATABASE_URL` already uses for PostgreSQL. `conftest` does not touch it, so it
survives to the shared gate in `tests/real_redis_gate.py`. `REDIS_URL` is retained unchanged
because other tests assert on how the application behaves when it is present.

`/15` pins a dedicated database index. The service container is already ephemeral and
exclusive to this job, so that is belt-and-braces — and no test issues `FLUSHDB` or
`FLUSHALL`.

## Files

| Path | Change |
|---|---|
| `.github/workflows/01-pr-check.yml` | **+27 lines, 1 functional** (the switch); 0 removed |
| `tests/real_redis_gate.py` | new — the shared gate |
| `tests/test_quota01_metered_reservation_is_hard.py` | new |
| `tests/test_quota01_exchange_slot_is_atomic.py` | new |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | new |

The working tree this was built from also contains an unrelated
`provisioning-and-isolation` job belonging to another workstream. **It is deliberately not
in this branch** — verified mechanically: the committed added lines are byte-equal to the
reviewed D5′ hunk, and `provisioning-and-isolation`, `postgres:17-alpine` and
`provision_database.py` appear nowhere in the diff.

## The gate keeps three outcomes apart

| Opt-in | Server | Outcome |
|---|---|---|
| unset | — | **SKIP** — the only legitimate skip |
| set | no answer to PING | **FAIL** — a dead Redis in CI must not look like a green run |
| set | reachable | **RUN** |

Verified locally for the first two (see below). The third is what this PR exists to exercise.
A non-loopback host is refused unless `AERORA_TEST_REDIS_ALLOW_REMOTE=1`, and no reason
string ever echoes the endpoint, host or credential.

## Local evidence

```
# opt-in unset
$ python -m pytest tests/test_quota01_metered_reservation_is_hard.py \
                   tests/test_quota01_exchange_slot_is_atomic.py \
                   tests/test_webhook_duplicate_delivery_real_redis.py -rs
26 passed, 9 skipped          exit 0

# opt-in set, nothing listening on that loopback port
$ AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/15 python -m pytest \
      tests/test_quota01_metered_reservation_is_hard.py
1 failed, 6 passed            exit 1
  Failed: AERORA_TEST_REDIS_URL is set but the server did not answer PING
  (TimeoutError). ... (The endpoint is deliberately not echoed.)
```

The port number appears **nowhere** in the failure output.

## What to look for in this run

The nine previously-dormant tests should all **execute**. Required:

- `test_concurrent_reservations_are_atomic_against_real_redis` → **PASSED**, not skipped
- `test_the_slot_lock_is_mutually_exclusive_against_real_redis` → executed
- `test_the_guard_admits_exactly_the_limit_against_real_redis` → executed
- the six `test_webhook_duplicate_delivery_real_redis.py` tests → executed

Real-Redis skip count must be **0**. A green overall job with these skipped is not evidence.

The six webhook tests have **never executed anywhere**. They were reviewed as safe — no
outbound HTTP, no payment credentials, the handler invoked as a function rather than through
`TestClient`, `payment.authorized` events so the Supabase settlement path is never entered,
and per-key cleanup — but a first-contact failure is a real possibility and would be a
finding, not a surprise.

## Scope: this does NOT close QUOTA-01

The metered concurrency test drives a **raw** `redis.asyncio` client and mirrors
`reserve_quota`'s increment-first-revert inline. It does **not** call
`SubscriptionEngine.reserve_quota`. So a green run proves Redis's server-side `INCR`/`DECR`
is atomic under 50-way concurrency — true and worth having, and **not** the same claim as
"the production reservation path is atomic against a real Redis".

`redis_manager._get_active_cache()` returns `MockRedisClient` whenever `DEV_MODE` is set, and
`conftest` sets it, so the production path cannot reach a real server as currently
configured. A `_bypass_dev_mode` seam exists and the webhook fixture already uses it; the
metered test does not.

Closing QUOTA-01 needs either an authorized follow-up test that drives `reserve_quota`
through that seam, or a recorded decision that Redis-level atomicity plus mock-level logic
coverage is sufficient. **QUOTA-01 remains OPEN.**

No application code, no production path, and no other workstream's changes are included.
**Live trading remains OFF / NOT CERTIFIED.** Please do not merge without review.
