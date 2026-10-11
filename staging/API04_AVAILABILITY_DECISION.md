# API-04 — RATE-LIMIT STORE UNAVAILABILITY: THE AVAILABILITY DECISION

Required deliverable for API-04. The brief asked for a deliberate choice between failing
closed and a safe degraded mode, **not** for the boot failure to be removed.

---

## 0. THE DECISION

> **Option A — FAIL CLOSED — is RETAINED as the shipped behaviour.**
>
> **Option B — degraded mode — is the correct target, is fully specified in §4, and is
> NOT IMPLEMENTED.**

Status: **API-04 has a documented and tested availability decision. The P1 is closed as a
decision; the improvement in §4 is a separate, unimplemented work item.**

---

## 1. WHAT THE CODE DOES TODAY

`backend_app/core/rate_limit.py` resolves a frozen `RateLimitConfig` at import and raises
`RuntimeError` when no shared store is available **and** the environment is not an
explicitly declared local one:

| Environment | Store | Behaviour |
|---|---|---|
| local (`ENV=development` / `DEV_MODE=true`) | any | `memory://`, warned |
| test (`ENV=testing`) | any | `memory://`, warned |
| **paper (`VYOMQUANT_MODE=paper`), `ENV` unset** | **down or absent** | **refuse to start** |
| production | reachable | shared Redis store |
| production | down or absent | **refuse to start** |
| `ENV` unset | absent | **refuse to start** |

Verified across all 15 rows in fresh subprocesses: `staging/rl_matrix_RUNB2.txt`.

**This policy is pre-existing.** The original module already raised for
production-without-Redis; the API-03 fix only corrected *which* environments count as
local, which is what made it actually fire. I did not introduce fail-closed-at-boot.

---

## 2. WHY OPTION A IS SECURE

The rate limiter is the only shared abuse control in front of authentication, checkout,
payment verification and payout endpoints. `core/rate_limit_middleware.py` — the
per-trade limiter — is **not installed** (QUOTA/control sweep), so SlowAPI plus the
per-route `@limiter.limit(...)` decorators are the entire rate-limit surface.

An in-memory limiter across *N* replicas yields an effective limit of
`configured × N`, silently. Serving protected traffic without enforceable limits is a
worse outcome than not serving it, so refusing is correct on the security axis.

---

## 3. WHY OPTION A IS NONETHELESS UNSATISFACTORY

Refusing **at import** takes the entire API down, not just the rate-limited surface. All
14 routers import `limiter` at module scope, so an unreachable store means the process
does not start at all — and that removes:

* `/health/live` and `/health/ready` — the orchestrator cannot even tell *why* the pod is
  unhealthy;
* `POST /api/orders/cancel` and `/cancel-all` — the **risk-reducing** direction;
* `stop_bot` — ditto.

For a trading platform, making a trader unable to flatten exposure because a rate-limit
store is unreachable converts a partial degradation into a total one. The security
argument in §2 does not extend to the endpoints that *reduce* risk.

**That is the open substance of API-04, and it is an availability/product tradeoff rather
than a defect.** Hence: decision documented, improvement specified, not silently
"fixed" by deleting the guard.

---

## 4. OPTION B, SPECIFIED (NOT IMPLEMENTED)

If adopted, degraded mode must satisfy all of:

1. **No fake distributed limiter.** `memory://` must NOT be substituted outside a
   declared local environment. This is the API-03 invariant and is non-negotiable —
   6 concurrent processes currently prove 0/6 ever obtain a memory fake
   (`staging/rl_fc_RUND4.txt`).
2. **Per-request fail-closed, not per-process.** With no shared store, requests to
   rate-limited endpoints answer **503** rather than passing unlimited. The limiter object
   exists but is disabled; a middleware is the gate.
3. **An explicit safety allowlist that stays reachable:** `/health/live`,
   `/health/ready`, `POST /api/orders/cancel`, `/cancel-all`, `POST /api/portfolio/close-all`,
   bot `stop`. Every one is risk-reducing or diagnostic; none can be abused for gain by an
   unlimited caller.
4. **Sensitive endpoints stay protected,** i.e. remain in the 503 set: authentication,
   `billing/checkout`, `verify-payment`, `payment-methods`, `referral/payouts`,
   marketplace checkout.
5. **An explicit operational signal:** `/health/ready` must report NOT_READY while
   degraded, so the pod leaves rotation rather than silently serving a reduced surface.
   The OBS-01 readiness work makes this expressible — Redis is already a required
   dependency there.
6. **Automatic recovery.** A background re-probe restores distributed enforcement without
   a restart. Recovery is already proven at the resolver level: down → REFUSED, up →
   shared store (`staging/rl_matrix_RUNB2.txt`).

**Why it is not built here:** items 2, 3 and 6 are new request-path machinery, and
item 3 is a *product* decision about which endpoints are safety-critical. Shipping a
partial version would mean a middleware that classifies endpoints by a list I invented,
which is the kind of unreviewed policy this engagement has refused elsewhere (MONEY-04's
FX refusal, the TENANT-01 framework decision).

---

## 5. INTERACTION WITH OBS-01

The two findings now compose, and that is deliberate. Redis is a **required** readiness
dependency (`core/readiness.probe_redis`), so with the store down:

* Option A (today): the pod does not start. The orchestrator sees a crash-loop.
* Option B (if adopted): the pod starts, `/health/ready` returns **503**, the pod leaves
  rotation, and the safety allowlist stays reachable for anyone with direct access.

Option B is strictly better on availability **and** equal on security. That is the
argument for adopting it.

---

## 6. EVIDENCE

| Property | Result | Artifact |
|---|---|---|
| 15-row environment matrix, fresh subprocesses | all rows match policy | `staging/rl_matrix_RUNB2.txt` |
| 6 concurrent processes, store unavailable | 6/6 refused, **0/6 memory fake** | `staging/rl_fc_RUND4.txt` |
| 6 concurrent processes, store available | 6/6 same shared store | same |
| Recovery: down → up | REFUSED → shared store | `staging/rl_matrix_RUNB2.txt` |
| Store unreachable *after* startup | limiter keeps its shared store; SlowAPI surfaces the error per request rather than silently degrading | `tests/test_api04_store_failure_after_startup.py` |
| `main` + all 14 routers import | 14/14 under both documented configs | `staging/rl_router_RUNG7.txt` |
| Real router graph under test | 131 passed | `staging/rl_pytest_RUNE5.txt` |

---

## 7. STATUS

```
API-04: CLOSED AS A DECISION (Option A retained, documented, tested)
        Option B specified in §4 — OPEN as a separate improvement
```

Residual risk, stated plainly: an unreachable rate-limit store is still a **total** API
outage rather than a partial one. That is now a recorded, reasoned decision instead of an
accident.
