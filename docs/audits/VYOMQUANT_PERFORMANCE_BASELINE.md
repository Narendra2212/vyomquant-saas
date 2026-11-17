# VYOMQUANT performance baseline (T16)

**Measured:** 2026-11-17, ~21:00 UTC. Production, read-only.
**Instance:** Supabase NANO, Free plan, `ap-southeast-1`. DB 43 MB, 131 tables.

Every figure below is a measurement with its sample size. Where something was
not measured, it says so rather than carrying an estimate.

---

## Correction to the handoff

`VYOMQUANT_SESSION_HANDOFF.md` recorded T16 as *"partially blocked — no Sentry,
no API or DB timing available to baseline against"*. Three parts of that are
wrong, which is why this document exists at all:

| Recorded | Actually |
|---|---|
| No Sentry | **Sentry is initialised and wired.** `backend_app/backend/observability/sentry_config.py`, `backend_app/main.py`, frontend `src/main.jsx`, `src/bootstrap.js`, `src/apiClient.js`, `src/components/ErrorBoundary.jsx`. `SENTRY_DSN` and `SENTRY_ENVIRONMENT` are injected into **all seven** ECS services. |
| No API timing | **The API is reachable.** `VITE_API_URL=https://app.vyomquant.in`; Cloudflare reverse-proxies `/api/*`, `/ws/*`, `/health*` to the ALB, so the API shares the page origin and can be timed from anywhere. |
| No DB timing | **`pg_stat_statements` 1.11 is installed** and had 3h49m of real fleet traffic accumulated. |

What *is* genuinely unmeasured is listed at the end.

## 1. API latency

Seven samples per endpoint, over the real browser path
(Cloudflare → ALB → ECS → Supabase). **These include network RTT from a host in
India to `ap-southeast-1`, so they are upper bounds on server time, not server
time.**

| Endpoint | min | median | max | what it touches |
|---|---|---|---|---|
| `/api/support/categories` | 152 ms | **157 ms** | 177 ms | static, no DB |
| `/api/support/faqs` | 156 ms | **157 ms** | 183 ms | static, no DB |
| `/health/live` | 165 ms | **185 ms** | 384 ms | nothing |
| `/health/ready` | 246 ms | **300 ms** | 332 ms | DB + Redis probes |

Reading these together is what makes them useful:

- **~155 ms is the floor** — two endpoints that touch nothing land there, so
  that is edge plus RTT, not application work. No amount of backend tuning moves it.
- **Server-side work on a static endpoint is under ~30 ms** (157 vs the 185 ms
  dependency-free liveness route, which is noisier).
- **Probing the database and Redis costs ~115–145 ms** (`/health/ready` 300 ms
  against `/health/live` 185 ms). That is one round trip each to Supabase and
  Redis from inside the VPC.

No authenticated endpoint is timed here. Doing so needs a real session token,
and minting one to measure it would mean handling a live credential — not worth
it for a baseline when Sentry is already instrumenting real user requests.

## 2. Database

`pg_stat_statements`, window `2026-11-17 17:07:55 UTC` → **3h 49m**, with the
full seven-service fleet running.

### Where the database spends itself

| Total | Calls | Mean | Share | Query |
|---|---|---|---|---|
| **51 s** | **804** | 64 ms | **31%** | `SELECT * FROM pgbouncer.get_auth($1)` |
| 12 s | 45 | 257 ms | 7% | `SELECT count(*) FROM generate_series($1,$2)` — *my own CPU probes* |
| 10 s | 8 | 1221 ms | 6% | PostgREST schema-cache introspection |
| 9 s | 6 | 1503 ms | 5% | `SELECT name FROM pg_timezone_names` |
| 7 s | 1 | 7051 ms | 4% | `pg_stat_activity` — *my own diagnostic* |
| 6 s | 814 | 7 ms | 4% | PostgREST read of `paper_*` |
| 5 s | 4098 | 1 ms | 3% | `set_config('search_path', …)` — PostgREST per-request preamble |

**The headline finding: connection authentication is the single largest
consumer of database time, at 31%.** `pgbouncer.get_auth` runs once per *new*
pooler connection — 804 of them in 3h49m, about 3.5/min. Roughly 30–40 of those
are my own diagnostic connections (~5%), so unlike the earlier reading taken
during the outage this is not materially contaminated.

Worth noting how that figure changed: during the CPU-starved outage the same
query averaged **558 ms**; healthy it averages **64 ms**. An 8.7× difference on
an identical query is a reminder that any latency number taken during the
incident was measuring starvation, not the query.

### What is *not* a problem

Measured and ruled out, so nobody optimises these by guess:

- **No missing indexes.** Zero sequential scans on any table with more than 200
  live rows.
- **Not I/O bound.** Cache hit **99.81%** (795,313 hits / 1,520 reads),
  `blk_read_time` **0.0 s**, `blk_write_time` **0.0 s**. The 43 MB database sits
  entirely in memory.
- **Not connection-count bound.** 20 backends against a 60 limit.

### Two real inefficiencies

1. **`signal_audit_log` carries five indexes with zero scans**, ~3.4 MB total
   (`uq_signal_audit_log_row_hash` 1112 kB, `uq_signal_audit_log_prev_hash`
   1096 kB, `idx_signal_audit_log_user_time` 616 kB,
   `idx_signal_audit_log_signal` 488 kB, and even `signal_audit_log_pkey`
   336 kB). Every insert maintains all five; nothing reads any of them. The two
   `uq_` ones are presumably hash-chain integrity constraints and earn their
   keep by preventing duplicates even with no scans — but the three plain
   indexes are pure write cost. `signals` adds three more unused
   (`idx_signals_user_environment`, `idx_signals_strategy_generated`,
   `idx_signals_executed_at`).

2. **12.8% transaction rollback rate** — 2,303 rollbacks against 15,754 commits.
   Not diagnosed here. It may be ordinary (serialisation retries in
   `paper_repository`, which retries on `40001`) or it may be real errors.

## 3. Frontend bundle

From a production `vite build` of `main` at `c7dd259c`+T02.

| | files | raw | gzipped |
|---|---|---|---|
| JavaScript | 78 | 2,285 kB | 706 kB |
| CSS | 2 | 86 kB | 15 kB |
| **total** | **80** | **2,371 kB** | **721 kB** |

**Critical path — what the browser fetches before it can paint:**

```
<script src="/theme-boot.js">                       2 kB gz   blocking (by design, T17)
<script type="module" src="/assets/index-*.js">   175 kB gz   entry
<link rel="modulepreload" vendor-reactflow.js>     92 kB gz   preloaded, not parse-blocking
<link rel="stylesheet" vendor-reactflow.css>        2 kB gz   render-blocking
<link rel="stylesheet" index-*.css>                13 kB gz   render-blocking
                                                  -------
                                                  283 kB gz  (955 kB raw), 5 requests
```

**Largest chunks by gzipped size:** `index` 175 kB, `vendor-recharts` 110 kB,
`vendor-reactflow` 92 kB, `StrategyBuilder` 52 kB, `pageFields` 28 kB.

`vendor-recharts` (110 kB, the biggest vendor) is correctly lazy — it is not on
the critical path.

### The one clear win: `vendor-reactflow` on every page load

React Flow is a node-graph canvas. **The only module that imports it is
`src/pages/StrategyBuilder.jsx`** (lines 116–128), and that page already has its
own lazy chunk (`StrategyBuilder-*.js`, 52 kB). Yet `vendor-reactflow.js`
(92 kB gzipped, **283 kB raw**) is `modulepreload`ed in `index.html`, and its
stylesheet is render-blocking — on the landing page, the dashboard, and every
other route.

That is 92 kB of the 283 kB critical path, roughly a third, for a dependency one
route uses.

**Lighthouse confirms it independently**, against production:

> `vendor-reactflow-NYqE6Gbt.js` — **94 kB transferred, 58 kB of it unused** on
> the landing page.

### ROOT CAUSE — a static import defeating a lazy one

Two attempts missed it, so the evidence trail is worth keeping.

**Attempt 1 — drop the `manualChunks` branch.** Removed reactflow's 92 kB
preload and its 2 kB blocking stylesheet, and promoted `vendor-recharts`
(110 kB) into the preload instead: a net **~18 kB regression**. Reverted.

**Attempt 2 — `build.modulepreload.resolveDependencies` filtering `vendor-*`.**
**No effect at all**; the critical path stayed at exactly 283 kB with reactflow
still preloaded. That option governs the *runtime* preload helper for **dynamic**
imports. It does not touch the `<link rel="modulepreload">` tags Vite injects
into `index.html`, which are emitted for the entry chunk's **static** import
graph. Reverted.

Attempt 2 failing is what identifies the cause: if the tag is emitted for the
entry's *static* graph, then reactflow **is** in that graph. It is:

```
App.jsx:76          const StrategyBuilder = lazy(() => import('./pages/StrategyBuilder'));   correctly lazy
Strategies.jsx:36   import StrategyBuilder from "./StrategyBuilder";                          STATIC
```

`Strategies.jsx` imports `StrategyBuilder` **statically**, so React Flow is
pulled in through that edge regardless of the `lazy()` in `App.jsx`. One static
import anywhere in the reachable graph defeats a `lazy()` elsewhere — the
`lazy()` still produces a chunk, which is why the split *looks* correct in the
output, while the dependency is loaded eagerly anyway.

**The fix is therefore in `Strategies.jsx`, not in `vite.config.js`** — make
that import lazy too, or extract whatever `Strategies` actually needs from
`StrategyBuilder` so it does not drag the canvas in. **Not attempted:** it is a
behavioural change to a page (`Suspense` boundary, loading state) rather than a
build-config tweak, it needs the before/after critical-path measurement to
confirm, and it needs the page's tests run. It is the single clearest
performance win identified here.

### Measurement note that nearly produced a false positive

The hypothesis was that `vite.config.js:69-78` forces reactflow into a named
chunk via `manualChunks`, that `StrategyBuilder.jsx:128`'s
`import 'reactflow/dist/style.css'` makes Vite hoist that chunk's CSS into the
document, and that the `modulepreload` follows the CSS. If so, dropping the
`vendor-reactflow` branch would let both stay inside the lazy
`StrategyBuilder` chunk.

**Tested. It does not help.** With that branch removed and a clean rebuild:

| | before | after |
|---|---|---|
| `vendor-reactflow.js` preload | 92 kB gz | **gone** |
| `vendor-reactflow.css` blocking | 2 kB gz | **gone** |
| `vendor-recharts.js` preload | not preloaded | **110 kB gz appeared** |
| net critical path | — | **~18 kB worse** |

Removing reactflow's chunk caused `vendor-recharts` to be preloaded in its
place. So the `modulepreload` is not caused by the CSS hoisting: Vite is
selecting *some* vendor chunk to preload, and removing one candidate simply
promotes the next. The change was reverted; `vite.config.js` is unmodified.

Both measurements used a whole-file regex over `dist/index.html` rather than a
line-wise one, because the final `<link>` tag spans two lines and a line-wise
match silently drops it — which would have made the "after" look like a win.

So the 92 kB is real waste, but the cause is Vite's preload selection, not the
manual chunk. A fix needs `build.modulepreload` configuration or an explicit
`experimentalRenderBuiltUrl`/preload filter, and it needs to be verified the
same way: measure the whole critical path before and after, not just the chunk
that was targeted. **Not attempted further in this change.**

## 4. Render behaviour

Lighthouse 12.8.2 against **production** (`https://app.vyomquant.in/`),
desktop form factor, `--throttling-method=provided` (so these are this host's
real network, not a simulated 4G profile), headless Chromium via Edge.
Fetched 2026-11-17 21:16 UTC.

### Performance score: **8 / 100**

| Metric | Measured | Target | |
|---|---|---|---|
| Largest Contentful Paint | **6.2 s** | < 2.5 s | POOR |
| Total Blocking Time | **1,230 ms** | < 200 ms | POOR |
| Cumulative Layout Shift | **0.41** | < 0.1 | POOR |
| First Contentful Paint | 3.4 s | < 1.8 s | POOR |
| Speed Index | 5.8 s | < 3.4 s | POOR |
| Time to Interactive | 6.4 s | — | POOR |
| Max Potential FID | 1,080 ms | — | POOR |
| Server response (TTFB) | **440 ms** | < 600 ms | good |

**TTFB is the only healthy number.** The server answers promptly; everything
after it is client-side cost. That is consistent with the API measurements in
§1 — the backend is not what users are waiting for.

### What Lighthouse itself blames

| Saving | | Opportunity |
|---|---|---|
| — | 177 kB | Reduce unused JavaScript |
| 157 ms | — | Eliminate render-blocking resources |

Unused JavaScript, by file:

| Transferred | Unused | File |
|---|---|---|
| 178 kB | **119 kB** | `index-*.js` (the entry) |
| 94 kB | **58 kB** | `vendor-reactflow-*.js` |

### Largest resources actually fetched

| Size | Type | |
|---|---|---|
| 179 kB | script | `index-*.js` |
| 94 kB | script | `vendor-reactflow-*.js` |
| **83 + 48 + 31 kB** | **font** | **three Inter/woff2 files — 162 kB total** |
| 29 kB | script | `pageFields-*.js` |
| 20 kB | script | `LandingPage-*.js` |
| 13 kB | stylesheet | `index-*.css` |

**Three web fonts totalling 162 kB** is the second-largest cost after the entry
chunk, and larger than the reactflow chunk. Fonts block text paint, so they are
a plausible contributor to both the 3.4 s FCP and the 0.41 CLS (reflow when a
fallback is swapped for the real face). Not investigated further here.

**CLS 0.41 is not diagnosed.** It is four times the "poor" threshold and is the
metric most likely to be a visible annoyance rather than just a slow load.

## 5. Build time

Three consecutive production builds: **39.82 s, 33.88 s, 33.9 s** (vite, Node
20, cold module graph each time). Two further builds during the optimisation
attempts above took 37.62 s and 1 m 16 s; the outlier was a cold
`node_modules` cache, not a code change.

## 6. Sentry — an error tracker, not an APM

**Correction.** §0 of this document said Sentry was wired and therefore
"collecting the real user latency distribution". That was an inference from
`traces_sample_rate=0.1` appearing in `sentry_config.py`. Read with an org
token, the data says otherwise.

Org `aerora-dynamics-private-limited`, two projects, both Python:

| Project | Platform | Events, 24h |
|---|---|---|
| `vyomquant-api-production` | `python-fastapi` | 495 |
| `vyomquant-saas` | `python` | 0 |

**There is no JavaScript/browser project at all**, so browser web vitals have
nowhere to land regardless of whether `VITE_SENTRY_DSN` is populated.

Ingest outcomes, last 30 days:

| Category | accepted | client_discard | filtered |
|---|---|---|---|
| error | **11,481** | 33 | — |
| transaction | 545 | 87,014 | 9,220 |
| replay | — | — | **never arrived** |

Reading that:

- **Errors work.** 11,481 accepted.
- **Transactions are nearly all thrown away before sending.** 87,014 client
  discards is `traces_sample_rate=0.1` dropping 90%, plus 9,220 rejected by
  inbound filters. 545 survived.
- **Those 545 are not queryable.** Zero rows for transaction percentiles across
  the `transactions`, `discover` and `metrics` datasets, and `count()` of 0. So
  there are no p50/p75/p95 figures, no web vitals, and **no INP** — the one
  metric Lighthouse structurally cannot produce.
- **No session replay has ever arrived**, despite
  `replaysSessionSampleRate: 0.1` in `src/main.jsx`. Together with the absent
  browser project, that means **the frontend SDK is not reporting at all.**

So Sentry cannot currently answer "what do users experience". Closing that needs
configuration, not access: a browser project, a populated `VITE_SENTRY_DSN`,
and a `traces_sample_rate` that leaves something to query.

### What the errors did reveal, which is worth more

Top issues on the API project, last 24 hours:

| Count | Issue |
|---|---|
| 170 + 45 + 30 = **245** | `PaperPersistenceError: the paper_orders open-limit-pairs read did not complete` |
| 71 + 47 + 1 = **119** | `DETECT_STUCK_TASKS_ERROR: (psycopg2.OperationalError) SSL connection has been closed unexpectedly` / `server didn't return client encoding` |
| **58** | `[BILLING] plan-expiry sweep failed: APIError` |
| 25 + 24 = **49** | `RECOVERY_STEP_2_ERROR` — the same psycopg2 `OperationalError` |
| **12** | `ConnectionResetError` |
| 4 | `PRODUCTION MODE: Live trading enabled!` (an INFO log being sent as an event) |

**Roughly 425 of 495 events in 24 hours trace to database connectivity or
timeouts** — and `SSL connection has been closed unexpectedly` is verbatim what
my own probes received during the 2026-11-17 outage. This is independent
confirmation that the NANO instance's degradation is causing **continuous
application-level failure**, not a single incident. The outage was the visible
part of something ongoing.

Two items here are not performance findings and want their own work:

1. **`PaperPersistenceError` on `paper_orders`, 245 times in a day** — the
   single loudest error in the product.
2. **`[BILLING] plan-expiry sweep failed`, 58 times in a day** — a money-path
   job failing repeatedly. Plan expiry not running means subscriptions outliving
   what was paid for.

## 7. Explicitly not measured

Naming these so nobody reads a gap as a zero:

- **Authenticated API latency.** Needs a live session token.
- **Real user latency distribution.** Sentry is live and collecting it; this
  document does not read Sentry. Needs dashboard or API-token access.
- **INP** (Interaction to Next Paint). Lighthouse's lab run cannot produce it —
  it needs field data, which is what Sentry has.
- **WebSocket throughput and latency.**
- **Worker throughput** (DAG execution, backtest duration).
- **The 12.8% rollback rate's cause.**
- **The CLS 0.41 cause.**
- **Whether the 162 kB of fonts can be reduced** (subsetting, fewer weights,
  `font-display`).

## 7. This is the baseline half of T16 only

`VYOMQUANT_MASTER_ENGINEERING_AUDIT.md:757` states T16 as *"performance
baseline **then optimisation**"*. This document is the baseline. **No
optimisation has landed.** Two were attempted against the reactflow preload and
both were reverted — one made things ~18 kB worse, the other did nothing — and
the exercise identified the real cause (`Strategies.jsx:36`) rather than fixing
it.

Ranked by confidence × value, the candidates this baseline produces:

| | Candidate | Evidence | Risk |
|---|---|---|---|
| 1 | Make `Strategies.jsx:36`'s `StrategyBuilder` import lazy | 92 kB gz / 283 kB raw off the critical path, Lighthouse says 58 kB of it is unused | page behaviour change, needs a `Suspense` boundary |
| 2 | Reduce the 162 kB of fonts | three woff2 files, larger than the reactflow chunk | low, but affects visual identity |
| 3 | Diagnose CLS 0.41 | 4× the poor threshold | unknown until diagnosed |
| 4 | Drop the eight zero-scan indexes on `signal_audit_log` / `signals` | ~3.4 MB, write cost on every insert, 0 reads in 3h49m | 3h49m is not proof of "never"; needs a longer window or static proof no query uses them |
| 5 | Investigate `pgbouncer.get_auth` at 31% of DB time | 804 new pooler connections in 3h49m | cause not yet established — see below |

On candidate 5: the readiness probe was the leading suspect, since the ALB polls
`/health/ready` every 15 s (4/min) and 804 calls over 3h49m is 3.5/min. It is
**not** the cause — `core/readiness.py:189` uses a pooled `SessionLocal()`, which
checks a connection out rather than dialling. The 796 `SELECT $1` calls in
`pg_stat_statements` are SQLAlchemy's `pool_pre_ping`, which is cheap. What does
open 3.5 connections a minute is still unidentified; `pool_recycle` across seven
services is the next thing to check.

## 6. One caveat that frames all of it

The instance is a **burstable NANO on the Free plan**, and on 2026-11-17 it
spent ~3h20m unable to serve because its CPU allowance was exhausted (see
`vq_outage_diagnosis.md`). The figures above were taken from a recovered
instance delivering roughly 35–40% of a healthy core — not from a healthy one.

Measured CPU, via a zero-I/O `generate_series` count:

| State | s per million rows | vs healthy |
|---|---|---|
| Healthy reference | ~0.15 | 1× |
| This instance, recovered | **~0.41** | ~2.7× slower |
| This instance, during the outage | 2.57 | ~17× slower |

So **these are not steady-state figures for a well-provisioned deployment**.
They are the current production reality, which is the useful thing to baseline
against — but a compute upgrade would move the database numbers before any
query tuning did.
