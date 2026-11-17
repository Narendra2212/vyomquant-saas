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

### The obvious fix was tried and does not work

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

## 4. Build time

Three consecutive production builds: **39.82 s, 33.88 s, 33.9 s** (vite, Node
20, cold module graph each time).

## 5. Explicitly not measured

Naming these so nobody reads a gap as a zero:

- **Authenticated API latency.** Needs a live session token.
- **Real user latency distribution.** Sentry is collecting it; this document
  does not read Sentry. That is the next step for T16 and it needs dashboard
  access, not code.
- **Render performance / Core Web Vitals.** No LCP, CLS or INP figures. jsdom
  has no layout engine, so the test suite cannot produce them; this needs
  Lighthouse against the deployed site.
- **WebSocket throughput and latency.**
- **Worker throughput** (DAG execution, backtest duration).
- **The 12.8% rollback rate's cause.**

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
