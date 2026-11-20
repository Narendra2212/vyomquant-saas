# Phase 11B-A10 — D5′ Approval-Gated Execution Readiness

**Date:** 2026-10-10
**Status: READY. BLOCKED pending approvals. No mutation performed.**

Destination pre-existence was checked before writing: the path was free. No file outside
`staging/` was created, modified, or deleted. No commit, push, PR edit, closure, merge, or
CI trigger. No staging of any file. No infrastructure installed or started.

| Final status | |
|---|---|
| D5′ merge / execution | **BLOCKED pending approvals** |
| QUOTA-01 | **OPEN** |
| Real-Redis production-path verification | **UNEXECUTED** — no required test has run against real Redis |
| Exchange-slot concurrency | **UNPROVEN** |
| Live trading | **OFF / NOT CERTIFIED** |

Nothing in this report constitutes an approval. The recommendations below are proposals
awaiting a human decision.

---

## HEADLINE

The candidate change set is prepared, reproducible, and verified. The gate is empty.

1. **Approval gate confirmed empty first-hand**, not inherited: authenticated `gh` shows all
   three PRs `OPEN`, `reviewDecision=''`, **0 reviews, 0 APPROVED, 0 labels, 0 assignees**,
   and the single comment on each is from the `coderabbitai` bot (§2).
2. **All four prepared blobs re-hash exactly** to A15's recorded ids (§4).
3. **All nine Phase-1 coverage requirements are still met** by the two existing
   production-path tests. **No third test is needed and none was written** (§3).
4. **The biggest trap in this change set: the worktree workflow is NOT the candidate.** It
   lacks PR #6's `d5-redis-diagnostics` job and carries another workstream's
   `provisioning-and-isolation` job. Copying it would silently drop the diagnostic machinery
   and absorb unrelated work (§4).
5. **F5 re-verified from source, with its severity corrected.** The fail-open mechanism is
   real and confirmed line by line, but whether it causes double settlement is **not**
   established — one DB-level replay guard exists, and the rest of the downstream path is
   unverified. A15 stated the risk more strongly than the evidence supports (§6).
6. **Report-name discipline matters here.** `PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md` and
   `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md` both exist and are different
   workstreams. So do two A12s, two A13s, and an `A10_REDIS_FAILURE_DIAGNOSIS` unrelated to
   this phase (§1).

---

## 1. Fresh baseline and digest method

Rebaselined at entry and re-measured at exit. Identical.

| Item | Value |
|---|---|
| Branch | `main` |
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| `origin/main` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` |
| Divergence (L/R) | `0  0` |
| Tracked index entries | 2104 |
| Staged paths | 22 |
| Unstaged tracked | 100 |
| Untracked | 3364 at entry → 3378 at exit (this phase's own `staging/` artifacts) |
| Unmerged | 0 |
| Stash entries | 2 (pre-existing; none pushed or popped) |

### Digest method — stated because three different values are circulating

A value without its serialisation is not comparable. Prior phases quote `98ae5bdf…` (A9),
`7379218b…` (A14) and `CF474F1B…` (A15) for the same repository state, because each hashes
a different thing. All three methods are computed here so any of them can be checked:

| Method | Definition | Value (entry = exit) |
|---|---|---|
| **A** | `sha256` of newline-joined raw `git ls-files -s` lines, file order, 2104 entries | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` |
| **B** | `sha256` of sorted `mode blob stage path` tuples, whitespace-normalised | `cceb77e5c81f396f5f8bc809645fea14187cd0fbb399345b4f1360b4700c6267` |
| **C** | `sha256` of sorted `git diff-index --cached HEAD` rows — the 22 staged paths only | `8d9c475b86a4a71d89759bcfb15ba750073a3bb4ede719279249af22b4192251` |

**Method C is the one to use for "did another workstream's staged work move?"** — it is
scoped to exactly the 22 staged paths and ignores the 2082 unchanged entries. The raw
`.git/index` SHA-256 remains unusable as an invariant: `core.autocrlf=true` lets ordinary
reads rewrite the stat cache.

### Report-name discipline

The brief warns that A8/A9 labels are reused. Confirmed. All five reports it names exist:

| Named by the brief | Present | Size |
|---|---|---|
| `PHASE11B_A9_D5_CONSOLIDATION_REPORT.md` | yes | 27,045 B |
| `PHASE11B_A14_D5_CONSOLIDATION_REPORT.md` | yes | 15,539 B |
| `PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md` | yes | 27,131 B |
| `PHASE11B_A13_D5_REPAIR_REPORT.md` | yes | 16,317 B |
| `PHASE11B_A9_D5_FAILURE_DIAGNOSTICS.md` | yes | 29,410 B |

**Not D5′, must not be cited as such:** `PHASE11B_A9_POSTGRES_AUTHORIZATION_READINESS.md`,
`PHASE11B_PG_PRESERVATION_COORDINATION_REPORT.md`,
`PHASE11B_PG_TOOLCHAIN_PRESERVATION_REPORT.md`, `PHASE10_WSH/WSI/WSJ_POSTGRES_*`.
Also note `PHASE11B_A10_REDIS_FAILURE_DIAGNOSIS.md` is an **earlier, unrelated** A10 — this
report is `PHASE11B_A10_D5_APPROVAL_READINESS.md`.

### A later phase exists than the brief's primary report

`staging/PHASE11B_A15_D5_EXECUTION_REPORT.md` (17,011 B) post-dates A14 and A9. Despite
"EXECUTION" in the name it performed **no mutation** — it reached the same empty approval
gate. Its prepared manifest is the candidate carried forward here. It also discloses an
incident: its own A14 run **overwrote a concurrent workstream's A14 report** by whole-file
writing without an existence check, and that report is unrecoverable because `staging/` is
untracked. That is precisely why this phase checked its destination first. The lost report's
*conclusion* survives in quoted fragments and matches: canonical PR #6.

---

## 2. Current PR comparison

Read-only, via authenticated `gh` (`Narendra2212`, scopes `gist, read:org, repo, workflow`).

| PR | Branch | Head | Tree | State | mergeStateStatus | reviewDecision | Reviews | APPROVED | Latest run |
|---|---|---|---|---|---|---|---|---|---|
| **#6** | `ci/d5-real-redis-quota-evidence` | `d07014d36d` | `d28b6d1a…` | OPEN | UNSTABLE | `''` | 0 | **0** | `38047483583` failure |
| **#7** | `ci/d5-prime-real-redis` | `49e70da23c` | `271f13c2…` | OPEN | UNSTABLE | `''` | 0 | **0** | `38044138718` failure |
| **#8** | `ci/d5-real-redis-switch` | `a624ce89b1` | `271f13c2…` | OPEN | UNSTABLE | `''` | 0 | **0** | `38046625040` failure |

None is draft; none merged (`mergedAt=None`). Each has exactly one comment, authored by the
`coderabbitai` bot — no human review on any PR.

**Remote heads match local shas exactly** (`git ls-remote --heads origin`):
`ci/d5-real-redis-quota-evidence` → `d07014d36d`, `ci/d5-prime-real-redis` → `49e70da23c`,
`ci/d5-real-redis-switch` → `a624ce89b1`, `main` → `074b772ab1`. Nothing moved.

### #7 and #8 are still identical — re-confirmed, as the brief requires

```
git rev-parse 49e70da2^{tree}  ->  271f13c29d3c09a205dbaae959cad002cc0fd0a6
git rev-parse a624ce89^{tree}  ->  271f13c29d3c09a205dbaae959cad002cc0fd0a6
git diff --stat 49e70da2 a624ce89  ->  exit 0, empty
```

Closing exactly one of #7/#8 loses nothing. This was re-measured in this phase, not carried
from A9 or A15.

### What distinguishes #6

| | #6 | #7 | #8 |
|---|---|---|---|
| `d5-redis-diagnostics` job | **yes** | no | no |
| Workflow lines | 812 | — | — |
| `AERORA_TEST_REDIS_URL` occurrences | **2**, both `/15` | 1, `/15` | 1, `/15` |
| Webhook test | 383 lines | 416 | 416 |
| Tree | unique | identical to #8 | identical to #7 |

#6's diagnostic job is the only mechanism that produced per-test JUnit artifacts and the
`state=run` gate proof. A15 notes that job reported `success` only because of
`continue-on-error`, so its JUnit artifacts — not its conclusion — are the authoritative
counts.

---

## 3. Existing test coverage, and gaps

`tests/test_quota01_metered_reservation_is_hard.py` — 24,965 B, 530 lines, **9 tests**, of
which two are production-path:

- `test_production_reserve_quota_admits_exactly_the_limit_against_real_redis`
- `test_production_zero_allowance_writes_no_meter_against_real_redis`

Re-verified against the file as it stands now, by pattern, not by trusting the A9 report:

| # | Requirement | Status | Evidence |
|---|---|---|---|
| 1 | Real-Redis opt-in and fail-closed | **OK** | `@real_redis_required`, `require_real_redis()` |
| 2 | Rejects mock / unavailable clients | **OK** | `_app_cache_or_fail`, `MockRedisClient`, `pytest.fail` |
| 3 | Bidirectional keyspace proof | **OK** | `_prove_one_keyspace`, `KEYSPACE SPLIT`, `KEYSPACE SPLIT (reverse)` |
| 4 | Concurrency through production `reserve_quota` | **OK** | `asyncio.gather` over `SubscriptionEngine.reserve_quota` |
| 5 | Exact-limit admission and refusal counts | **OK** | `len(allowed) == limit`, `len(refused) == racers - limit` |
| 6 | `get_quota_usage` agrees with the raw key | **OK** | `via_api == limit` **and** `via_key == limit` |
| 7 | Correct usage after refusals | **OK** | `0 <= usage <= limit`, post-exhaustion `usage == limit` |
| 8 | Zero allowance, absent meter key | **OK** | `limit=0`, `client.exists(key) == 0` |
| 9 | Cleanup limited to test-owned keys | **OK** | `_meter_key(user, resource, period)`, scoped `delete`, `aclose()` |

**No gap. A third test is not needed and was not written.** The brief's suggested name
`test_production_reserve_quota_is_atomic_against_real_redis` remains absent; the substance is
split across the two tests above, which is the cleaner arrangement because the limit proof
and the zero-allowance proof need different setups.

### `/15` vs `/0`, re-confirmed from the current URL builder

Measured by calling the application's own builder and parsing with redis-py, offline:

```
REDIS_DB_CACHE=0, REDIS_DB_QUEUE=1, REDIS_DB_EVENTS=2
get_redis_url(0)                 -> redis://localhost:6379?db=0   -> db=0
gate as shipped in all 3 PRs     -> redis://localhost:6379/15     -> db=15
gate after the candidate edit    -> redis://localhost:6379/0      -> db=0

redis://localhost:6379/15?db=0   -> db=0     (query beats path)
redis://localhost:6379           -> db=None
```

So the edit is necessary and correct. **It is not evidence that a real server was
successfully tested.** Matching database numbers only remove one known cause of false
results; the assertions themselves remain unexecuted (§6).

---

## 4. Exact proposed file boundary

### IN — three paths, nothing else

| Path | New blob | Replaces | Change | Verified |
|---|---|---|---|---|
| `.github/workflows/01-pr-check.yml` | `a2f39ebb` (derived) | `57e2cee8` | rewrite **both** `AERORA_TEST_REDIS_URL` `/15`→`/0` (PR #6 lines 216 and 735); expand the comment block; retain `d5-redis-diagnostics`; do **not** add `provisioning-and-isolation` | structural, §below |
| `tests/test_webhook_duplicate_delivery_real_redis.py` | `3ffbccde68c3b57485a9971d4977c34aff598b47` | `105b6567` | adopt the webhook owner's keyspace fix **unchanged** | **hash MATCH** |
| `tests/test_quota01_metered_reservation_is_hard.py` | `7baff0b64f0c7441f4192a9a6b2df53e7a68dce9` | `f48103ac` | +266 lines: 3 helpers + 2 production-path tests | **hash MATCH** |

Unchanged but part of the manifest, both re-hashed and matching:
`tests/real_redis_gate.py` → `d40a1bb7766307f067d914ebc8e6fd705e04a222`;
`tests/test_quota01_exchange_slot_is_atomic.py` → `2d7ccbbb04b3af04675b365809d89b4429db4888`.

### The workflow trap — read this before building the commit

The worktree workflow must **not** be copied as the candidate. Measured:

| | PR #6 | Worktree |
|---|---|---|
| Lines | 812 | 836 |
| `AERORA_TEST_REDIS_URL` | 2 × `/15` | 1 × `/0` |
| `d5-redis-diagnostics` | **present** | **absent** |
| `provisioning-and-isolation` | absent | **present** (another workstream) |
| Env lines left on `/15` | 2 | 0 |

Adopting the worktree file would drop the diagnostic job and import unrelated work in one
move. The candidate must be **derived**: take PR #6's file, rewrite both `/15` occurrences,
and port only the comment block. A15 reports this rebuild is deterministic and reproduces
blob `a2f39ebb`. I verified the structural preconditions above but did **not** rebuild the
blob, because doing so is a construction step and this phase is read-only — so
`a2f39ebb` is **carried from A15, not independently reproduced here.** It must be
re-derived and re-checked at commit time.

### OUT — excluded by design

| Path | State | Why excluded |
|---|---|---|
| `backend_app/core/subscription_dependencies.py` | ` M` +253/−0 | F1 guard implementation. Applies cleanly to `HEAD` but is useless alone — the slot test asserts the `exchange.py` call site exists. |
| `backend_app/routers/exchange.py` | `MM` +268/−15 staged, +154/−14 unstaged | F1 call site, entangled with the tenant-isolation / RLS / credential-vault workstream. `git apply --cached --check` against a private index seeded from `HEAD` **refuses** it, while `subscription_dependencies.py` applies as a positive control. Must not be cherry-picked, reconstructed, or hand-rewritten. |
| `backend_app/backend/redis_manager.py` | clean | F4, unowned. Blast radius is 3 calls inside `initialize()`; changing it alters connection behaviour for every deployment. |
| `backend_app/core/cache/redis_manager.py` | ` M` +185/−5 | F5 fail-soft proxies. Billing-critical, needs its own review (§6). |
| `backend_app/routers/billing.py` | ` M` +700/−40 | Another workstream; does not address F5. |
| `tests/test_quota01_exchange_slot_is_atomic.py` **removal** | — | Needs decision B. Not removed. |
| All other worktree changes | 120 files differ from `HEAD`, ~59 production | Unrelated. A commit must name paths explicitly. |

### The exchange-slot test does not silently disappear

Per the brief, recorded as an explicit dependency rather than a deletion:

| Field | Value |
|---|---|
| **Artifact** | `tests/test_quota01_exchange_slot_is_atomic.py` (867 lines, blob `2d7ccbbb`, byte-identical across worktree and all three PRs after CRLF normalisation) |
| **Proposed disposition** | Deferred from the D5′ evidence PR — **pending decision B; not removed by this phase** |
| **Restoration owner** | The workstream holding `backend_app/routers/exchange.py` — tenant-isolation / RLS / credential-vault (identified from the 22 staged paths, **not** confirmed by that owner) |
| **Restoration trigger** | The commit that lands `_slot_lock_key` + `exchange_connection_slot_guard` **and** the `store_keys` call site together |
| **Acceptance test** | All 22 node IDs in this file, with `test_store_keys_uses_the_guard_around_its_vault_write` and the two `*_against_real_redis` cases passing against real Redis |
| **Underlying defect, preserved** | `store_keys` on `main` still calls the non-atomic `check_exchange_connection_slot`. The read-then-write race is **LIVE**. Deferring the test hides the failing test, not the defect. |
| **Status if B is not granted** | Decision **UNRESOLVED**; the test stays in the manifest and its 21 failures persist |

---

## 5. Four decision requests, and their unresolved owners

All four are **UNAPPROVED**. Each is independent; granting one does not imply the others.

### A — Canonical PR

- **Request:** designate **#6** (`ci/d5-real-redis-quota-evidence`) canonical.
- **Condition the brief sets:** only if #6's owner confirms the `d5-redis-diagnostics` job
  should be retained. **That confirmation does not exist.** If the job is not wanted, #7 is
  preferable and the diagnostic job need not be ported.
- **Pre-checked:** #7 ≡ #8 re-confirmed this phase, so closing one is lossless (§2).
- **Owner:** unidentified. All three PRs have 0 assignees and 0 reviews.
- **Status:** **ABSENT.** No PR was closed or edited.

### B — Exchange-slot boundary

- **Request:** agree that the slot-lock implementation and its test land **together** in the
  owning workstream, and that the test is deferred from D5′ with the restoration dependency
  in §4 recorded.
- **Owner:** the `exchange.py` / `subscription_dependencies.py` holder. No signal from them
  anywhere in the repository.
- **Status:** **ABSENT → decision left UNRESOLVED**, as instructed.

### C — Ownership of the two carried files

- **Request:** confirmation from (i) the webhook-test owner that blob `3ffbccde` may be
  carried in the canonical PR, and (ii) the workflow-change owner that the `/15`→`/0` edit
  may be included.
- **Known tension:** `PHASE11B_A12_WEBHOOK_KEYSPACE_FIX.md` targets `ci/d5-prime-real-redis`
  (**#7**) and records "no remote action of any kind". That is a stated preference, **not**
  an approval to carry the file in #6. If #6 is chosen, this consent is required.
- **Attribution caveat, per the brief:** these owners are inferred from report authorship and
  file contents. **That is not proof of ownership and is not approval.** No approval has been
  obtained from anyone.
- **Status:** **ABSENT.**

### D — CI acceptance criterion

- **Request:** adopt this scoped criterion for the one validation run.

> 1. The three D5′ test files have **zero failing node IDs**.
> 2. The blocking-lane failure count is compared against the latest valid baseline
>    (run `37346853502`, `6c5660a1`, which predates D5′ and failed **39**).
> 3. **A result of 39 remaining failures is NOT a green build.** It is "no D5′-introduced
>    regression", and must be reported in exactly those words.
> 4. All real-Redis tests execute against the intended database, with **zero gated skips**
>    for the required cases.
> 5. The production-path quota tests and the webhook tests **pass against real Redis**.
> 6. **Any unexpected failure blocks the repair.**

- **Honest expectation, so point 3 is not misread later:** if B is **not** granted, the 21
  slot-lock failures persist by construction and criterion 1 **cannot** be met. The run would
  then validate only the webhook fix and the production-path quota tests.
- **Status:** **ABSENT.** No CI run was triggered to test this proposal.

---

## 6. Real-Redis execution prerequisites

### Not satisfiable on this host

| Check | Result |
|---|---|
| Listener on 127.0.0.1:6379 / 6380 / 6399 | **none** |
| `redis-server`, `redis-cli`, `memurai`, `memurai-cli` on PATH | **all absent** |
| Docker | **not installed** (not on PATH) |
| WSL | `Ubuntu-24.04` present; `dpkg -l \| grep -c redis` → **0**; `/usr/bin/redis*` → none |
| `fakeredis`, `redislite`, `pytest_redis` | not installed — and would not be evidence |

Installing requires `sudo` in WSL, which is password-gated. Per the brief, no infrastructure
was installed or started. **So the only path to real-Redis evidence is the authorized CI run
(decision D).**

### What CI must satisfy for the result to count

1. `AERORA_TEST_REDIS_URL` resolves to the database the application's cache uses — i.e. the
   `/15`→`/0` edit must be in the commit, **both** occurrences on #6.
2. The gate reaches `state=run`. Verified classification: `unset → skip`; `/0`,
   unreachable-port, and non-loopback all **→ fail**. No configured endpoint can produce a
   silent skip.
3. Zero gated skips among the required cases. Locally, opt-out yields **26 passed, 11
   skipped** — and skips are **not** passes.
4. The real client is not a mock: `_app_cache_or_fail()` fails rather than skips on
   `MockRedisClient`. **This branch has never executed** — it needs a reachable server.
5. `_prove_one_keyspace` passes in both directions. **Also never executed.**

### Local evidence that does exist

| Run | Result |
|---|---|
| Opt-out, 3 D5′ files | exit 0 — **26 passed, 11 skipped** (3 metered incl. both new, 6 webhook, 2 slot) |
| `AERORA_TEST_REDIS_URL` → closed loopback port, production tests | exit 1 — **2 failed, 0 skipped**, via `real_redis_gate.py:175` |
| Endpoint secrecy in that failure output | `6399` → 0, `127.0.0.1` → 0, `redis://` → 0 occurrences |
| Collection across the 3 D5′ files | 37 tests (9 metered + 6 webhook + 22 slot) |

The fail-closed result is the useful one: it proves a configured-but-dead Redis cannot
masquerade as a green run.

### F4 and F5 — tracked separately, neither in D5′

**F4 — `get_redis_url` discards an explicit database and captures `REDIS_URL` at import.
UNOWNED, unfixed.** `backend_app/backend/redis_manager.py` is clean, so CI exercises exactly
`main`'s behaviour. Production blast radius is three calls, all in `initialize()`. The
`/0` alignment and the derived-URL probes are **compensations, not a fix**.

**F5 — webhook idempotency is asymmetric under cache faults. UNOWNED. Mechanism confirmed;
severity corrected.**

Verified from source. The proxies in `backend_app/core/cache/redis_manager.py` are fail-soft:

```
757  async def get(...):   ... except Exception: logger.warning(...)   -> return None
767  async def set(...):   ... except Exception: logger.warning(...)   -> return False
```

And `razorpay_webhook` in `backend_app/routers/billing.py`:

```
2846  idempotency_key = f"webhook:razorpay:{event_id}" if event_id else None
2848  cached_val = await redis_manager.get(idempotency_key)
2849  if cached_val in ("completed", "1", "processing"):  return {"status": "duplicate"}
2852  acquired = await redis_manager.set(idempotency_key, "processing", nx=True, ex=60)
2853  if not acquired:                                    return {"status": "duplicate"}
```

A cache **read** fault returns `None`, which is indistinguishable from "key absent", so the
handler proceeds — **fails OPEN**. A cache **write** fault returns `False`, read as a lock
collision — **fails CLOSED**. Opposite directions from one outage. Confirmed.

**Severity correction.** A15 describes the fail-open path as a double-settlement risk. The
*mechanism* is confirmed, but the *consequence* is not established, and the difference
matters for prioritisation:

- A genuine DB-level replay guard exists for the referral-obligation write: a unique
  constraint on `(provider, provider_payment_id)` with a SQL function returning `NULL` on
  replay (`billing.py:840`, `:3070-3071`). That path is replay-safe.
- The main settlement writes pass `provider_payment_id` (`:2460`, `:2673`, `:2978`) but I
  found **no `ON CONFLICT` in `billing.py`**, and I did **not** inspect the database schema.
  So I can neither claim those writes are protected nor claim they are not.

**Accurate statement: the cache-layer fail-open is confirmed; whether it reaches double
settlement depends on DB-level idempotency of the downstream writes, which is unverified.**
F5 is billing-critical and needs its owner's review either way. It is **not** part of D5′.

---

## 7. Command / action log and preservation checks

All read-only. Exit codes as observed.

| # | Action | Exit | Result |
|---|---|---|---|
| 1 | destination pre-existence check | 0 | path **free** — safe to write |
| 2 | `git rev-parse` HEAD / branch / origin-main, `rev-list --count` | 0 | `074b772a`, `main`, `0 0` |
| 3 | `git ls-files -s`, `diff-index --cached HEAD`, `ls-files -u`, `stash list` | 0 | 2104 / 22 / 0 / 2; digests A, B, C |
| 4 | `git hash-object` ×4 | 0 | all four prepared blobs **MATCH** |
| 5 | `git cat-file blob` PR #6 workflow + worktree compare | 0 | job-set and `/15` divergence (§4) |
| 6 | coverage pattern scan of the metered test | 0 | **9/9 requirements met** |
| 7 | `get_redis_url` + redis-py `ConnectionPool.from_url` (offline) | 0 | app db 0, gate `/15`→15, `/0`→0 |
| 8 | `git rev-parse <sha>^{tree}` ×3, `git diff --stat #7 #8` | 0 | **#7 ≡ #8**, diff empty |
| 9 | `git ls-remote --heads origin` | 0 | all remote heads match local |
| 10 | `gh auth status` | 0 | authenticated as `Narendra2212` |
| 11 | `gh pr view 6/7/8 --json …` | 0 | all OPEN, **0 reviews, 0 APPROVED** |
| 12 | read `redis_manager.py` proxies, `billing.py` webhook, replay guards | 0 | F5 confirmed, severity bounded |
| 13 | Redis/Docker/WSL availability probe | 0 | **none available** |
| 14 | exit re-baseline | 0 | digests A and C **identical to entry** |

**No CI run was triggered.** CI runs referenced (`38047483583`, `38044138718`,
`38046625040`, `37346853502`) are pre-existing and were read, not started.

### Preservation checks

| Check | Result |
|---|---|
| Digest A, entry → exit | `98ae5bdf…` → **identical** |
| Digest C (22 staged paths), entry → exit | `8d9c475b…` → **identical** |
| Staged paths | 22 → 22 |
| `HEAD` / `refs/heads/main` / `origin/main` | `074b772a`, unmoved |
| D5′ branch heads, local and remote | `d07014d3` / `49e70da2` / `a624ce89`, **unmoved** |
| PRs #6 / #7 / #8 | all still OPEN; none edited, closed, re-targeted, or merged |
| Unmerged entries | 0 → 0 |
| Stash | 2 → 2; none pushed or popped |
| Concurrent `p11b15_*` artifacts | **24 preserved**, none modified or deleted |
| Concurrent `p11b14_*` artifacts | **16 preserved**, none modified or deleted |
| Existing `staging/` reports | none overwritten; destination verified free first |
| Tracked files modified this phase | **none** |

---

## 8. What was not changed

Explicitly, this phase did **not**:

- modify, create, or delete any file outside `staging/`;
- overwrite any existing `staging/` report — the destination was checked first, which is the
  control A15 omitted when it destroyed a concurrent agent's A14 report;
- run `git add`, `commit`, `push`, `checkout`, `reset`, `clean`, `stash`, `cherry-pick`, or
  `apply` outside a discarded private index used with `--check` only;
- stage, unstage, or alter any of the 22 pre-existing staged paths;
- edit, close, re-target, label, assign, review, or merge any PR;
- trigger, re-run, or cancel any CI workflow;
- reconstruct or transfer the `exchange.py` call-site integration across the conflicting
  workstream change;
- remove `tests/test_quota01_exchange_slot_is_atomic.py` or any other test;
- add a third production-path quota test — the existing two cover all nine requirements;
- rebuild the workflow candidate blob (construction, not inspection) — `a2f39ebb` is carried
  from A15 and **must be re-derived and re-verified at commit time**;
- install, start, or connect to Redis, PostgreSQL, any shared service, or production;
- touch readiness scorecards, gate registers, or launch-blocker documents;
- claim QUOTA-01 is closed, or that any approval has been obtained.

---

## 9. Disposition

| Item | Status |
|---|---|
| **D5′ merge / execution** | **BLOCKED pending approvals A–D** (§5) |
| **QUOTA-01** | **OPEN** |
| — D5′ opt-in reaches CI, gate executes rather than skipping | PROVEN (`38047483583`, `state=run`) |
| — Configured-but-unreachable Redis fails, never skips | PROVEN locally (2 failed / 0 skipped) |
| — Redis INCR/DECR atomicity | proven — a Redis property, not application evidence |
| — Production `reserve_quota` against real Redis | **prepared, UNEXECUTED** — not in any PR |
| — Webhook idempotency against real Redis | 4/6 pass, **1 of those vacuously**; 2 fail; fix prepared, UNEXECUTED |
| — Webhook behaviour under cache fault | **F5 — fail-open confirmed at the cache layer**, downstream impact unverified; UNOWNED |
| — `get_redis_url` explicit-database handling | **F4 — unfixed, UNOWNED** |
| — `store_keys` read-then-write race | **LIVE on `main`** |
| — order-rate / daily-trade / active-order ceilings | **OPEN** — no gate exists |
| **Exchange-slot concurrency** | **UNPROVEN** — implementation in no commit (F1) |
| **Real-Redis production-path verification** | **UNEXECUTED** |
| **Live trading** | **OFF / NOT CERTIFIED** |

The candidate is ready and reproducible except for the workflow blob, which must be
re-derived at commit time. Everything else is verified and waiting on decisions A–D.

**No decision in §5 is approved by this report.**
