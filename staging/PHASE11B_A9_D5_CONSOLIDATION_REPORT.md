# Phase 11B-A9 — D5′ Consolidation and Production-Path Quota Evidence

**Date:** 2026-10-10
**Status: VERIFICATION COMPLETE. BLOCKED at the approval gate. No work duplicated.**

No file outside `staging/` was created, modified, or deleted. No commit, push, PR change,
merge, or CI trigger. Live trading **OFF / NOT CERTIFIED**. QUOTA-01 **OPEN**.

---

## HEADLINE — this brief's work was already done, and I verified it instead of redoing it

Phase 0 turned up reports through **A14**, not A8. The phases this brief describes have
already been executed by parallel sessions:

| Brief phase | Already delivered by | My action |
|---|---|---|
| Phase 2A — gate database `/15`→`/0` | A14 (uncommitted worktree) | **verified correct**, independently |
| Phase 2B — webhook keyspace repair | A12 (uncommitted worktree) | **verified present**, still unexecuted |
| Phase 2C — keep exchange-slot separate | A13/A14 | **all four facts rechecked**, confirmed |
| Phase 3 — production-path quota test | another session, in `test_quota01_metered_reservation_is_hard.py` | **reviewed against all 9 required assertions: all met** |
| Phase 1 — canonical PR decision | A14 proposes PR #6 | **reconfirmed the facts**, decision still unmade |

The single most important thing I did **not** do was write
`test_production_reserve_quota_is_atomic_against_real_redis`. Two tests already cover all
nine of this brief's required assertions (§5). Adding a third would have been the fourth
parallel effort on one feature — exactly the pathology A8 was written to stop.

**What I added that no prior phase has:**

1. **The `/15`→`/0` change is provably correct**, not just plausible: the application's cache
   resolves to **db 0**, the gate before resolved to **db 15**, after to **db 0** (§4).
2. **The two new production-path tests fail closed, not open.** Pointed at a configured but
   unreachable endpoint they **FAIL** (exit 1, 0 skips), and they do not echo the endpoint (§5).
3. **They do not repeat the slot-lock defect.** Every production symbol they call exists at
   `HEAD` and on all three PR heads — checked symbol by symbol (§5). This was a real risk:
   `backend_app/core/cache/redis_manager.py` is **+185/−5 uncommitted**.
4. **Their isolation claim holds in CI.** `_REDIS_SINGLETON_SEAMS` at `HEAD` already contains
   `_dev_mode_bypass`, and the autouse restore fixture is present, so `_bypass_dev_mode(True)`
   cannot leak (§5).
5. **Real Redis is genuinely unavailable on this host** — established four ways, not assumed (§6).
6. **The working tree is a shared scratch space: 120 files differ from `HEAD`, ~59 of them
   production.** Any commit built from it must name specific paths (§7).

One correction to A14: it lists the worktree workflow's jobs as three
(`unit-tests`, `provisioning-and-isolation`, `database-tests`). There are **eight**.

---

## 1. Preflight baseline, and differences from A8

| Measurement | A8 recorded | Now | Same? |
|---|---|---|---|
| Branch / `HEAD` | `main` / `074b772a` | `main` / `074b772a` | yes |
| `origin/main...HEAD` | `0 0` | `0 0` | yes |
| Tracked index entries | 2104 | 2104 | yes |
| Staged paths | 22 | 22 | yes |
| Unstaged tracked | 100 | 100 | yes |
| Untracked | 3259 | 3259 | yes |
| Stash entries | 2 (pre-existing) | 2 | yes |
| `.git/index` raw checksum | `3dbf1cb0…` | `3dbf1cb0…` | yes |
| **Staged-content digest** | `98ae5bdf…` | `98ae5bdf…` | yes |
| **Staged blob-identity digest** | — | `8d9c475b…` | new this phase |

### Differences from A8 worth recording

- **A8's report numbering is superseded.** Reports A10, A11, A12 (×2), A13 (×2) and A14
  exist. A8's "three open PRs, ownership partly resolved" is still true, but A14 has since
  produced the consolidation plan A8 called for.
- **PR #7 and PR #8 are the same tree, not merely the same five files.** A8 said
  byte-identical across the manifest; the stronger fact is
  `git rev-parse` → both `271f13c29d3c09a205dbaae959cad002cc0fd0a6`, and
  `git diff #7..#8` is **empty**. PR #6's tree is distinct (`d28b6d1a…`).
- **A8's unresolved hypothesis about the 39 pre-existing failures is now settled** by A14,
  not by me: run `37346853502` on `6c5660a1` (`main`'s parent, carrying no D5′ files) failed
  **39** with per-file counts matching PR #6 exactly. A8 listed this as hypothesis 5.
- **The metered test file has grown** 264 → **530** lines since A8 read it, adding the two
  production-path tests.
- **The worktree workflow no longer matches any PR**: 836 lines, `/0` at L248, and
  `d5-redis-diagnostics` is **not** in it (it exists only in PR #6's commit).

### Digest definitions, so these numbers are reusable

A8 showed the raw `.git/index` checksum is unsound as an isolation invariant
(`core.autocrlf=true` lets benign reads rewrite the stat cache). A14 reached the same
conclusion independently and published a digest of `7379218b…`. **Mine is a different number
for the same idea** — we hash different serialisations, so they are not comparable. Both are
valid; the definition must travel with the figure:

- **Staged-content digest `98ae5bdf…`** = SHA-256 of the newline-joined raw `git ls-files -s`
  output, all 2104 entries (mode, blob SHA-1, stage, path).
- **Staged blob-identity digest `8d9c475b…`** = SHA-256 of the sorted, newline-joined
  `git diff-index --cached HEAD` rows — the 22 staged paths with their old and new blob SHAs.
  This is the tighter invariant for "did another workstream's staged work move?".

---

## 2. Canonical PR — facts reconfirmed, decision NOT made

```
PR #6  OPEN  ci/d5-real-redis-quota-evidence  d07014d3  tree d28b6d1a  base main
PR #7  OPEN  ci/d5-prime-real-redis           49e70da2  tree 271f13c2  base main
PR #8  OPEN  ci/d5-real-redis-switch          a624ce89  tree 271f13c2  base main

git diff 49e70da2 a624ce89 -> empty.  PR #7 and PR #8 are the same tree.
latest "01 PR Check" per branch: 38047483583 / 38044138718 / 38046625040 -- all FAILURE.
```

**Work is BLOCKED.** This brief grants no authorization ("Do not close PRs, push branches,
commit, merge, or trigger CI without explicit authorization"), and I have no channel to
request owner approval. A14 stopped at the same gate.

### Decision request

| # | Decision | Recommendation | Why |
|---|---|---|---|
| **1** | Which PR is canonical | **#6**, or **#7** if the diagnostic job is expendable | #7 and #8 are the same tree, so one must close regardless. #6 is the only branch carrying the A11 diagnostic job; choosing #7 means porting 151 lines of YAML. #6's cost is a stale webhook file, fixed by one forward commit. |
| **2** | Exclude the exchange-slot test from D5′ and ship it with its implementation | **Yes** | Its production API is absent from all three PR heads, and its call site cannot be committed without another workstream's staged work (§3). Three independent analyses — A8, A14, this one — agree. |
| **3** | Who owns the webhook test and workflow corrections | webhook test → **A12/A9→A12 workstream**; workflow `/0` → **A14** | Both changes are uncommitted and authored by those sessions. Neither is mine to land. |
| **4** | Does the canonical branch's owner authorize the commit and a CI run | **needed** | This is the first real execution of both repairs. |

Nothing in this table has been acted on.

---

## 3. Exact files changed, and why

**By me: none outside `staging/`.** The table below is the *prepared* change set — A14's plan,
with my verification verdict against each line.

| # | File | Change | Source | My verdict |
|---|---|---|---|---|
| 1 | `tests/test_webhook_duplicate_delivery_real_redis.py` | 383 → 646 lines | worktree | **verified present**; fix unexecuted against a real server |
| 2 | `tests/test_quota01_metered_reservation_is_hard.py` | 264 → 530 lines, +2 production-path tests | worktree | **verified; all 9 assertions met** (§5) |
| 3 | `.github/workflows/01-pr-check.yml` | `AERORA_TEST_REDIS_URL` `/15` → `/0`, in `unit-tests` **and** in PR #6's `d5-redis-diagnostics` | worktree | **verified correct and necessary** (§4) |
| 4 | `tests/test_quota01_exchange_slot_is_atomic.py` | **remove from this PR**, track restoration | — | **recommended**; needs decision 2 |
| 5 | one stale docstring line | webhook test states the CI value as `/15` | — | **confirmed exactly one** genuinely stale factual claim remains |

### Deliberately excluded, with the four facts rechecked

The brief told me to recheck all four before acting. Measured results:

| Fact | Result |
|---|---|
| F1 — slot test byte-identical across four copies after CRLF normalisation | **TRUE**, all four `ed8d2ff855a1c444`, 40,937 normalised bytes |
| F2 — production API absent from the PR commits | **TRUE**: `def exchange_connection_slot_guard` and `def _slot_lock_key` ABSENT at `HEAD`, PR #6, #7, #8 |
| F3 — implementation is worktree-only | **TRUE**: both definitions and the `exchange.py` call site exist only on disk |
| F4 — `exchange.py` hunks do not apply onto `HEAD` | **TRUE**, with a working positive control |

F4, run against a **private** `GIT_INDEX_FILE` seeded by `git read-tree HEAD`, using
`git apply --check` (validates without applying). The shared index was never named:

```
backend_app/core/subscription_dependencies.py   13923-byte patch   --check exit=0  APPLIES   (positive control)
backend_app/routers/exchange.py                 28938-byte patch   --check exit=1  REFUSES
    error: patch failed: backend_app/routers/exchange.py:197
    error: backend_app/routers/exchange.py: patch does not apply
```

The control matters: without it, a refusal could mean a broken harness rather than real
entanglement. `subscription_dependencies.py` applies cleanly, so the harness is sound and
`exchange.py`'s refusal is a genuine conflict. A13/A14 recorded the failure at `:366`; I get
`:197`. The line moved as the worktree evolved; the verdict did not.

So `subscription_dependencies.py` is *separable* but *useless alone* —
`test_store_keys_uses_the_guard_around_its_vault_write` asserts the call site exists, and
that call site is unreachable without the other workstream's staged change.

---

## 4. Redis database and keyspace — before/after evidence

Measured by calling the application's own URL builder and asking redis-py to parse the
results. Offline; no connection attempted.

```
application partitioning : REDIS_DB_CACHE=0  REDIS_DB_QUEUE=1  REDIS_DB_EVENTS=2

effective URLs
  application cache : get_redis_url(0) -> redis://localhost:6379?db=0     -> db=0
  gate BEFORE (all 3 PR heads) : redis://localhost:6379/15                -> db=15
  gate AFTER  (A14, worktree)  : redis://localhost:6379/0                 -> db=0

the override, demonstrated
  redis://localhost:6379/15        -> db=15
  redis://localhost:6379/15?db=0   -> db=0     <-- query beats path
  redis://localhost:6379?db=0      -> db=0
  redis://localhost:6379           -> db=None
  redis://localhost:6379/0         -> db=0
```

**Verdict: the `/15`→`/0` change is correct and necessary.** Before, the gate PINGed db 15
and declared the environment healthy while the application wrote to db 0 — a true statement
about a server but not about the keyspace under test. After, they match.

Note the two URLs are not the same *string* — the application yields the query form
(`?db=0`) because `REDIS_URL` is non-empty, while the gate uses the path form (`/0`). They
resolve to the same database, which is the property that matters. This is also why
deriving the URL from `get_redis_url()` rather than hard-coding `/0` is the right call in
both test files.

### Stale `/15` references — scanned, one real hit

Eight hits in the workflow and three in the webhook test are **explanatory**: A14's new
comment block documenting why `/15` was wrong, and the measured `parse_url` demonstration.
Those are correct and should stay. Exactly one is a stale *factual claim*:

```
tests/test_webhook_duplicate_delivery_real_redis.py (~line 128)
    In CI, `REDIS_URL: redis://localhost:6379` and `AERORA_TEST_REDIS_URL:
    redis://localhost:6379/15`.
```

True for all three PR heads, false against the worktree workflow. Its conclusion is
unaffected — the client derives its URL from the application — but the quoted value needs
one token changed by its owner. `tests/real_redis_gate.py` and the exchange-slot test have
**zero** `/15` references.

---

## 5. Production-path quota test — already implemented, reviewed, not duplicated

`tests/test_quota01_metered_reservation_is_hard.py`, 530 lines, 9 tests. All 7 originals
preserved; 2 added, both calling the production API:

- `test_production_reserve_quota_admits_exactly_the_limit_against_real_redis` (L415)
- `test_production_zero_allowance_writes_no_meter_against_real_redis` (L491)

The brief's requested name `test_production_reserve_quota_is_atomic_against_real_redis` is
**absent**. The substance is covered by the two above, and split more cleanly than one test
would be — the limit proof and the zero-allowance proof have different setups. **I did not
write a third test.**

### The brief's nine required assertions, checked against the code

| # | Required | Met | Evidence in file |
|---|---|---|---|
| 1 | Opt-in gate enabled and reachable | **yes** | `@real_redis_required` + `require_real_redis()` (fails, not skips, when configured-but-unusable) |
| 2 | Real client, not `MockRedisClient`; fail not skip | **yes** | `_app_cache_or_fail()` → `pytest.fail` on `MockRedisClient` or `None` |
| 3 | Bidirectional keyspace probe | **yes** | `_prove_one_keyspace()` — app→test and test→app, before any quota assertion |
| 4 | 50 concurrent calls to real `reserve_quota(..., limit=10)`, isolated ids | **yes** | `racers=50`, `limit=10`, `asyncio.gather`, `user=f"q01-prod-{uuid4().hex[:8]}"`, unique `period` |
| 5 | Exactly 10 succeed, 40 refused | **yes** | `assert len(allowed) == limit`; `assert len(refused) == racers - limit` |
| 6 | `get_quota_usage` and the raw key agree | **yes** | `via_api == limit` **and** `via_key == limit` — both, so self-agreement isn't accepted |
| 7 | Refusals report reverted usage, not inflated | **yes** | `assert 0 <= usage <= limit` per refusal, plus a post-exhaustion retry asserting `usage == limit` |
| 8 | Zero limit reserves nothing, leaves no key | **yes** | `assert await client.exists(key) == 0` — `EXISTS` distinguishes absent from holds-zero |
| 9 | Cleanup scoped to the test's own keys | **yes** | `finally` deletes only `_meter_key(user, resource, period)`; `aclose()` |

### New evidence: the tests fail closed, and keep the endpoint secret

Never checked by any prior phase. With `AERORA_TEST_REDIS_URL` pointed at a closed loopback
port:

```
AERORA_TEST_REDIS_URL=redis://127.0.0.1:6399/0
python -m pytest tests/test_quota01_metered_reservation_is_hard.py -q --tb=line -rfs -k production
  -> EXIT 1   2 failed, 7 deselected, 0 skipped
  real_redis_gate.py:175 Failed: AERORA_TEST_REDIS_URL is set but the server did not answer
  PING (TimeoutError). A configured endpoint that cannot be reached is a BROKEN TEST SERVICE,
  not an absent one, so this fails rather than skipping.
```

Both new tests **fail**; neither skips. And the endpoint is not leaked — occurrences in the
full failure output: `6399` → **0**, `127.0.0.1` → **0**, `redis://` → **0**. CI logs are
world-readable, so that property is load-bearing, and it holds.

### New evidence: they do not repeat the slot-lock defect

This was a live risk. `backend_app/core/cache/redis_manager.py` is **+185/−5 uncommitted** and
`tests/conftest.py` is **+172/−0 uncommitted**, so symbol presence had to be checked at the
committed revisions, not on disk:

| Symbol | File | HEAD | PR #6 | PR #7 | PR #8 |
|---|---|---|---|---|---|
| `_bypass_dev_mode` | `core/cache/redis_manager.py` | ok | ok | ok | ok |
| `_get_active_cache` | `core/cache/redis_manager.py` | ok | ok | ok | ok |
| `MockRedisClient` | `core/cache/redis_manager.py` | ok | ok | ok | ok |
| `reserve_quota` | `core/subscription_engine.py` | ok | ok | ok | ok |
| `get_quota_usage` | `core/subscription_engine.py` | ok | ok | ok | ok |
| `_meter_key` | `core/subscription_engine.py` | ok | ok | ok | ok |
| `get_redis_url` | `backend/redis_manager.py` | ok | ok | ok | ok |
| `REDIS_DB_CACHE` | `backend/redis_manager.py` | ok | ok | ok | ok |
| `_REDIS_SINGLETON_SEAMS` | `tests/conftest.py` | ok | ok | ok | ok |

`backend_app/core/subscription_engine.py` and `backend_app/backend/redis_manager.py` are
**clean** — worktree equals committed — so the production quota API and the DB-0 override
that CI would exercise are exactly what is on `main`.

And the tests' isolation claim holds in CI, not just on disk: `_REDIS_SINGLETON_SEAMS` at
`HEAD` is already `("_test_mode", "_dev_mode_bypass", "_redis_manager", "_mock_client")`,
identical to the worktree, and the autouse `_restore_shared_redis_singleton` fixture is
present at `HEAD`, snapshotting `__dict__` *membership* so that "did not exist" is
restorable rather than replaced by a guess. `_bypass_dev_mode(True)` therefore cannot leak
into another file.

**Still unproven:** assertion 2's mock-rejection branch and assertion 3's probe have never
executed, because both require a reachable server (§6). Their first real run is the CI run
that needs authorization.

---

## 6. Focused tests, skips, and CI evidence

### Opt-out run (fresh, this phase)

```
python -m pytest tests/test_quota01_metered_reservation_is_hard.py \
                 tests/test_webhook_duplicate_delivery_real_redis.py \
                 tests/test_quota01_exchange_slot_is_atomic.py -q --tb=short -rsx
  -> EXIT 0   26 passed, 11 skipped, 8 warnings in 42.12s
```

The 11 skips are the gated real-Redis cases under the legitimate opt-out, enumerated by
line: metered 230 / **414** / **490** (the last two are the new production-path tests),
webhook 464 / 540 / 560 / 583 / 604 / 626, exchange-slot 641 / 700. Each carries the
message "A skip here is NOT a pass." **Skips are not passes**, and this reproduces A14's
26/11 exactly.

### Real Redis is unavailable on this host — established, not assumed

| Check | Result |
|---|---|
| Listener on 127.0.0.1:6379 / 6380 / 6399 | **none** |
| `redis-server`, `redis-cli`, `memurai`, `memurai-cli` on PATH | **all absent** |
| Docker | **not installed** (`docker` not on PATH) |
| WSL | `Ubuntu-24.04` present, but `dpkg -l \| grep -c redis` → **0**, `/usr/bin/redis*` → none |
| `fakeredis`, `redislite`, `pytest_redis` | **not installed** — and would not count as evidence anyway |

Installing Redis needs `sudo` in WSL, which is password-gated on this host. So **no
real-Redis assertion has executed locally**, and I am not claiming otherwise.

### CI evidence (read-only; no run triggered)

| Run | Branch | Result |
|---|---|---|
| `38047483583` | PR #6 `ci/d5-real-redis-quota-evidence` | failure — `62 failed, 12029 passed, 51 skipped` |
| `38046625040` | PR #8 `ci/d5-real-redis-switch` | failure — `62 failed, 12029 passed, 51 skipped` |
| `38044138718` | PR #7 `ci/d5-prime-real-redis` | failure |
| `37346853502` | `ci/unblock-deploy-gate` @ `6c5660a1` (no D5′ files) | **39 failed, 12017 passed** |

The last row is A14's finding and it is what makes the split decidable: **62 = 39
pre-existing + 23 introduced**, with per-file counts matching exactly, and
12 017 + 12 = 12 029 passes. A8 reported 23 attributable / 39 pre-existing and agrees.
Earlier phases that wrote "4 D5′ / 58 unrelated" counted only real-Redis tests as D5′ and
were wrong — the 19 non-Redis slot-lock failures arrive with the PR.

**"Zero failures in the blocking lane" is not achievable by D5′ work.** The achievable,
verifiable criterion is: **62 → 39, with no failing node id in any of the three D5′ test
files.** That needs explicit acceptance (decision 4).

---

## 7. Remaining baseline failures and unresolved risks

1. **39 pre-existing blocking-lane failures** across 8 files, present on `main`'s parent and
   every D5′ branch. `01 PR Check` stays red regardless of this work. Per A8, these should
   not be called permanently stable without fresh evidence — two failures I once labelled
   "pre-existing" earlier in this programme turned out to be load-sensitive flakes. A14's
   run `37346853502` is one fresh data point, not a stability proof.
2. **Both repairs are unexecuted against a real server.** The A12 webhook fix and the two new
   production quota tests have only ever been collected and skipped. Their first real run is
   the CI run awaiting authorization.
3. **Exchange-slot verification remains absent.** Mutual exclusion, exact-limit admission and
   release safety cannot be evidenced while the implementation is off-branch.
4. **QUOTA-01's defect is live.** `store_keys` on `main` still calls the non-atomic
   `check_exchange_connection_slot`; the read-then-write race is real. Removing the test from
   a CI-evidence PR hides the *failing test*, not the *defect* — which is why restoration must
   be tracked, not forgotten.
5. **Three open PRs on one feature.** Until duplicates close, any CI result can be attributed
   to the wrong branch.
6. **The working tree is a shared scratch space, and this is the biggest handling risk.**
   **120 files differ from `HEAD`**, of which ~59 are production, including
   `backend_app/backend/exchange_executor.py` (+1188/−55),
   `backend_app/core/execution_engine.py` (+955/−29),
   `backend_app/routers/billing.py` (+700/−40) and
   `backend_app/core/cache/redis_manager.py` (+185/−5). A commit built from this tree must
   name the specific paths; anything broader would sweep several workstreams' unreviewed work
   into a CI-evidence PR. This also means the three D5′ corrections are one `git checkout`
   away from being lost.
7. **Two uncommitted sessions' edits share the D5′ files** (A12's webhook fix, A14's workflow
   change, another session's metered tests). A commit from the worktree captures all three.
   That is intended, but it belongs in the PR description rather than being discovered by a
   reviewer.

---

## 8. Staged-content fingerprint, `HEAD`, and reflog

Identical at entry and exit of this phase.

| Invariant | Entry | Exit |
|---|---|---|
| Tracked index entries | 2104 | 2104 |
| **Staged-content digest** (`git ls-files -s`) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | **identical** |
| **Staged blob-identity digest** (`git diff-index --cached HEAD`) | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` | **identical** |
| Staged paths | 22 | 22 |
| `.git/index` raw checksum | `3dbf1cb07e58b1e9ffb1a70154f9c008ad539188f45ab2cbe2b367fc1caf5676` | **identical** |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | identical |
| Branch / divergence | `main` / `0 0` | identical |
| Unmerged entries | 0 | 0 |
| Stash entries | 2 (pre-existing) | 2 — none pushed or popped |
| Local branches | 15 | 15 — none created, moved, or deleted |

The 22 staged paths are the tenant-isolation / RLS / credential-vault workstream:
`backend_app/core/tenant_db_context.py`, `scripts/verify_rls_enforcement.py`,
`docs/adr/ADR-0006…`, `ADR-0007-per-tenant-credential-encryption-keys.md`,
`terraform/cloudtrail.tf`, five `tests/test_tenant_*` / `test_sc3_*` files, and
modifications to `auth.py`, `core/dependencies.py` and **`routers/exchange.py`** — the last
being the §3 entanglement.

Reflog, newest three — no commit was created by this phase:

```
074b772a HEAD@{2026-10-05 23:40:19 +0530}: commit: Give the deploy gate permission to read the answer it refuses without
6c5660a1 HEAD@{2026-10-05 22:43:18 +0530}: commit: Construct the interleaving instead of hoping for it: the host was deciding
56297a57 HEAD@{2026-10-05 22:17:10 +0530}: commit: Stop the missing-mock-export defect emerging one CI run at a time
```

The F4 apply test used a private `GIT_INDEX_FILE` under `%TEMP%`, seeded by
`git read-tree HEAD`, with `git apply --check`. The shared index was never named and its
digests are unchanged above.

---

## 9. Mutations performed

**None.**

| Action | Count | Authorization |
|---|---|---|
| Commits | **0** | not requested, not granted |
| Pushes | **0** | — |
| PRs opened / closed / merged / edited | **0** | — |
| CI runs triggered | **0** | — |
| Branches created / moved / deleted | **0** | — |
| `git add` / `checkout` / `reset` / `clean` / `stash` | **0** | — |
| Files written outside `staging/` | **0** | — |
| Production Redis / database / venue access | **0** | — |
| Readiness scores, gate registers | **not updated** | — |

Remote operations were read-only: `git ls-remote`, `git rev-parse`/`cat-file` against local
objects, and the GitHub REST reads recorded in §6. No project code, secrets, or user data
left the host.

### Artefacts, all under `staging/`

```
PHASE11B_A9_D5_CONSOLIDATION_REPORT.md   (this report)
a9_verify_a14.py       a9_verify.txt, a9_exit_fp.txt   A14 claim verification + exit fingerprint
a9_probe_env.py        a9_env.txt                      Redis/Docker/WSL availability
a9_effective_url.py    a9_url.txt                      keyspace equivalence of /15 -> /0
a9_newtest_deps.py     a9_deps.txt                     new tests' dependencies at HEAD and PR heads
a9_seam_contents.py    a9_seams.txt                    _REDIS_SINGLETON_SEAMS at HEAD vs worktree
a9_slot_recheck.py     a9_slot.txt                     F1-F4 recheck incl. private-index apply test
a9_pre_index.txt, a9_focused.txt, a9_failclosed.txt    baseline, opt-out run, fail-closed run
```

---

## 10. Final status

| Item | Status |
|---|---|
| **QUOTA-01** | **OPEN.** Not advanced by this phase. |
| — metered path, Redis INCR/DECR atomicity | proven in CI, but that was never in doubt |
| — metered path, production `reserve_quota` against real Redis | **test now exists and is sound; UNEXECUTED.** Needs one authorized CI run. |
| — exchange-slot concurrency and release safety | **NO EVIDENCE.** Implementation is off-branch. |
| — webhook idempotency against real Redis | 4 of 6 proven in CI; the 2 failures have a fix that is **unexecuted** |
| — `store_keys` read-then-write race | **LIVE on `main`** |
| — order-rate / daily-trade / active-order ceilings | **OPEN**, no gate exists |
| **Live trading** | **OFF / NOT CERTIFIED** — unchanged, and not touched by this phase |
| **Redis gate validity** | change verified correct (§4) but **not yet committed**, so CI still PINGs db 15 |

QUOTA-01 is **not** closed, and specifically is not closed on the strength of Redis INCR/DECR
atomicity. The one thing that would move it is the authorized CI run in decision 4 — at which
point the production-path test either passes against a real server, or reveals a real defect.
Either outcome is progress; neither has happened yet.

**Ready to proceed on approval of decisions 1–4 in §2.** Everything below that gate is
verified and waiting; nothing above it has been touched.
