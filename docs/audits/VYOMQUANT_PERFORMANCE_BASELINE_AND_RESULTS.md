# VYOMQUANT performance — baseline and results (T16)

`VYOMQUANT_MASTER_ENGINEERING_AUDIT.md:757` states T16 as *"performance
baseline **then optimisation**"*. The baseline is
[`VYOMQUANT_PERFORMANCE_BASELINE.md`](./VYOMQUANT_PERFORMANCE_BASELINE.md).
This file is the results half: one landed change, measured before and after.

---

## PERF-01 — 48 kB and two requests off the critical path

### Result

| | Before | After | Change |
|---|---|---|---|
| **Critical path, gzipped** | **283 kB** | **235 kB** | **−48 kB (−17%)** |
| Critical path, raw | 955 kB | 805 kB | −150 kB |
| Requests before first paint | 5 | **3** | −2 |
| `modulepreload` hints | `vendor-reactflow` 92 kB | **none** | − |
| Render-blocking stylesheets | 2 | **1** | −1 |
| Total bundle, gzipped | 721 kB | 727 kB | +6 kB |

Before:

```
theme-boot.js                  2 kB gz    classic, blocking (by design, T17)
assets/index-*.js            175 kB gz    module entry
assets/vendor-reactflow-*.js  92 kB gz    modulepreload
assets/vendor-reactflow-*.css  2 kB gz    render-blocking
assets/index-*.css            13 kB gz    render-blocking
                             -------
                             283 kB gz    955 kB raw, 5 requests
```

After:

```
theme-boot.js                  2 kB gz
assets/index-*.js            219 kB gz    module entry
assets/index-*.css            13 kB gz    render-blocking
                             -------
                             235 kB gz    805 kB raw, 3 requests
```

### The change

```diff
-    rollupOptions: {
-      output: {
-        manualChunks: (id) => {
-          if (id.includes('node_modules')) {
-            if (id.includes('reactflow') || id.includes('@reactflow')) return 'vendor-reactflow';
-            if (id.includes('recharts')) return 'vendor-recharts';
-          }
-        },
-      },
-    },
```

One deletion in `algo22-terminal/vite.config.js`. No application code.

### Why that is the fix, after two wrong answers

The symptom was `<link rel="modulepreload" href="/assets/vendor-reactflow-*.js">`
in `index.html`: 92 kB gzipped, 283 kB raw, fetched on the landing page, the
dashboard and every other route, for a node-graph canvas only the Strategy
Builder opens. Lighthouse independently reported **58 kB of that 94 kB
transferred was unused**.

Four attempts, because the first three were wrong in instructive ways:

| # | Attempt | Result |
|---|---|---|
| 1 | Drop only the `vendor-reactflow` branch | `vendor-recharts` (110 kB) took the preload slot. **~18 kB worse.** Reverted. |
| 2 | `build.modulepreload.resolveDependencies` filtering `vendor-*` | **No effect at all.** That option governs *dynamic*-import preloading, not the static tags Vite emits for the entry's own graph. Reverted. |
| 3 | `Strategies.jsx:36` static → `lazy()` + `Suspense` | Removed reactflow's preload **and** its blocking CSS exactly as intended — and `vendor-recharts` (161 kB) took the slot. **68 kB worse.** Reverted. |
| 4 | Remove `manualChunks` entirely | **−48 kB.** Kept. |

Attempt 2 failing is what located the cause: if the tag is emitted for the
entry's *static* graph, reactflow was in that graph. Attempt 3 proved that
cutting one path only promotes the other, and that recharts is the **larger**
of the two.

The decisive measurement was that **recharts has no static importer at all.**
All six importers of `ds/Chart` are dynamic, and `ds/index.js` documents why at
length:

> ═══ WHY `Chart` IS NOT HERE ═══ `ds/Chart` is the only module in `src/` that
> imports recharts, which is ~350KB. A barrel re-export would defeat that
> outright. So `Chart` is imported lazily at its call sites instead.

So the source was already correct, and `manualChunks` was undoing it. Forcing a
vendor into a **named** chunk that several lazily loaded chunks share makes
Rollup hoist that chunk into the entry's static graph rather than duplicate it,
and Vite then emits a `modulepreload` for it. The config was fighting the
architecture the code had deliberately built.

Removing it lets Rollup's automatic chunking put each vendor inside the lazy
chunk that uses it:

| Chunk | Before | After |
|---|---|---|
| Strategy Builder | 52 kB + 92 kB vendor = 144 kB | **94 kB**, reactflow inside it |
| recharts | 110 kB named vendor, preloaded | **100 kB** `CartesianChart-*.js`, lazy |

The builder chunk got *smaller* than the sum it replaced, because tree-shaking
works better when the vendor is not pinned into a separate chunk.

### The trade-off, stated

The entry chunk grew **175 → 219 kB gzipped (+44 kB)**: shared vendor code that
had been pinned into named chunks now lands in the entry. Total bundle grew
**+6 kB**. Both are paid for several times over by removing 48 kB and two
round-trips from the path to first paint, and the entry was already being
fetched.

Not claimed: that this fixes the render metrics. The baseline measured LCP 6.2 s,
TBT 1,230 ms and CLS 0.41, and 48 kB will not move those on its own — 162 kB of
web fonts and a 119 kB-unused entry chunk are still there.

### Method, so it can be repeated

Critical path measured by extracting every `<script>` and `<link>` asset
reference from the built `dist/index.html` and gzipping each file at level 9.

**A whole-file regex, not a line-wise one.** The final `<link>` tag spans two
lines, and a line-wise match silently drops it — which would have made every
"after" figure look 13 kB better than it was. The first baseline capture had
this bug; both figures above were re-measured after fixing it.

## Verification status — read before merging

| Check | Status |
|---|---|
| Production build | **passes** (53.9 s) |
| Critical path before/after | **measured**, figures above |
| Total bundle before/after | **measured** |
| Vendors still lazily split | **confirmed** — `CartesianChart-*.js`, `StrategyBuilder-*.js` |
| Frontend unit suite | **NOT COMPLETED LOCALLY** — see below |
| Lighthouse after-run | **not done** — needs a deploy first |

The local `tests/unit` run did not finish within 30 minutes on this machine
after repeated production builds, so **it was not verified locally and CI must
be the authority.** The relevant guard is `guards/dead-tailwind`, which compares
`src/` class usage against the built CSS and therefore does read the build this
change affects; CI runs `npm run build` immediately before the unit tests, so it
exercises exactly this configuration. Two pre-existing failures are expected
and unrelated: `guards/dead-tailwind`'s `scrollbar-hide` inventory assertion
(local only) and `guards/tokens.generated` (Windows `core.autocrlf`).

The Lighthouse after-run needs this deployed; the before-run was against
production, so an after-run against a local preview would not be comparable.

## Still open in T16

- **Lighthouse re-run** once deployed, for the render half of the before/after.
- **162 kB of web fonts** — three woff2 files, larger than either vendor chunk.
- **CLS 0.41** undiagnosed, 4× the "poor" threshold.
- **119 kB of the 219 kB entry chunk is unused** on the landing page per
  Lighthouse. Now the largest single item on the critical path.
- **Sentry has no usable performance data** — see the baseline's §6. Errors
  work (11,481 in 30 days); transactions are 90% client-discarded and the
  survivors are not queryable; no browser project exists, so no web vitals and
  no INP. That is a configuration gap, not an access one.
- **Eight zero-scan database indexes** and a **12.8% transaction rollback rate**.
