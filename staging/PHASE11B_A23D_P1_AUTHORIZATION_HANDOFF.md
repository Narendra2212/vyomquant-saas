# Phase 11B-A23D — P1 Authorization Handoff

**READ-ONLY. No production or test file changed. Nothing staged, committed or pushed. No Redis,
database, exchange, CI or network call.** Destination verified ABSENT immediately before writing.

---

## 1. Verdict in one line

**P1 is not blocked on a missing approval. It is blocked on a missing _owner_.**
`staging/PHASE11B_A10_D5_APPROVAL_READINESS.md` records
`backend_app/backend/redis_manager.py` as **UNOWNED** — twice, verified by direct read:

> **:244** — `| backend_app/backend/redis_manager.py | clean | F4, **unowned**. Blast radius is 3
> calls inside initialize(); changing it alters connection behaviour for every deployment. |`
>
> **:367** — `**F4 — get_redis_url discards an explicit database and captures REDIS_URL at import.
> UNOWNED, unfixed.**`

A missing grant can be obtained from a known owner. Here the ownership record is affirmatively
empty, so **there is no party with standing to grant D-A23C-1, D-A23C-7 or the scope decision.**
Assigning an owner is prerequisite to all three. This is `PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md`
§2.2's conclusion and I confirm it from the cited source rather than inheriting it.

**Nine reports now exist on this topic. None contains a grant.** The work needed is one
assignment and three sentences, not more analysis.

| # | Report | Written | On P1 |
|---|---|---|---|
| 1 | `PHASE11B_A22_REDIS_R6_CHARACTERIZATION_REPORT.md` | 10-10 21:39 | approval required |
| 2 | `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md` | 10-10 21:49 | §5 row 1 **NOT GRANTED** |
| 3 | `PHASE11B_A21_REDIS_LOOP_LIFECYCLE_REPORT.md` | 10-10 21:54 | gate FAILED |
| 4 | `PHASE11B_A23_REDIS_POOL_LIFECYCLE_READINESS.md` | 10-10 22:02 | §8 row 1 **NOT GRANTED** |
| 5 | `PHASE11B_A23_REDIS_LOOP_IDENTITY_REPORT.md` | 10-10 22:08 | stopped at gate |
| 6 | `PHASE11B_A23_REDIS_LIFECYCLE_IMPLEMENTATION_REPORT.md` | 10-10 22:12 | §6 rows 1–2 **NOT GRANTED** |
| 7 | `PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md` | 10-10 22:16 | §2 row 1 **NOT GRANTED** |
| 8 | `PHASE11B_A23C_REDIS_RECOVERY_CONSOLIDATION.md` | 10-10 22:19 | D-A23C-1 **NOT GRANTED** |
| 9 | `PHASE11B_A23R_REDIS_LIFECYCLE_READINESS.md` | **10-11 12:37** | A and B **NOT GRANTED**; **owner UNIDENTIFIED** |

**Correction to my own report 8:** it stated "six sessions." The true count at the time of writing
was seven — `PHASE11B_A23B_REDIS_RECOVERY_COVERAGE.md` landed at 22:16, three minutes before mine,
and my file listing predated it. Report 9 then made nine. Recorded rather than quietly fixed.

---

## 2. Decision table

Exact evidence locations. Classified `GRANTED` / `NOT GRANTED` / `REJECTED` / `UNRESOLVED` per the
brief. Owner stated from authoritative records only — **not inferred from commit authorship.**

| ID | Decision | Owner per records | State | Evidence |
|---|---|---|---|---|
| **D-A23C-1** | Approve the three P1 changes in `backend_app/backend/redis_manager.py` | **UNIDENTIFIED** — file recorded **UNOWNED** | **UNRESOLVED** (not merely NOT GRANTED: no grantor exists) | `A10:244`, `A10:367`; `A22V:331`; report 4 §8 row 1; report 6 §6 row 1; report 7 §2 row 1; report 9 §2 row A |
| **D-A23C-7** | Approve creating `tests/test_a23_redis_pool_lifecycle.py` | **UNIDENTIFIED** (same file's owner) | **NOT GRANTED**; path **ABSENT**, verified | report 9 §2 row B and §8.3; report 4 §8 row 2; report 6 §6 row 2 |
| **D-A23C-8a** | Core-cache owner acknowledges the new `close()` contract at `core/cache/redis_manager.py:619` (production shutdown path, reached from `main.py:607`) | owner of `core/cache/redis_manager.py` (` M` +185/−5, in flight) | **NOT GRANTED — still required** | report 6 §4b; report 9 §2.4 row 3 |
| **D-A23C-8b** | Script owner acknowledges the seven `close()` sites in `scripts/test_*.py` | — | **REJECTED / VOID** — see §4.1 | verified this phase |
| **D-A23C-3** | P1/P2 scope: does recovery cover the synchronous compat proxy? | **UNIDENTIFIED** (same file) **+** `core/global_safety.py` owner | **UNRESOLVED — and a genuine P1 prerequisite**, see §3 | report 7 §3.2–3.3 |
| **D-A23C-10** | May `aclose()` be standardised on, raising the redis-py floor? | — | **RESOLVED — no consent needed**, see §4.2 | report 9 §6/§7 |
| D-A23C-2 (P2) | Loop-identity recovery | UNIDENTIFIED (same file) | **NOT GRANTED** | `A22V:332` |
| D-A23C-4 (P3) | Runtime-health invalidation, `core/cache/redis_manager.py:753-1016` | core-cache owner | **NOT GRANTED — the R6 blocker** | `A22V:333` |

Searched this phase: every `*.md` in `staging/`, the launch-gate register, and files named
`*APPROV*`/`*AUTHORIZ*`/`*DECISION*`/`*GRANT*`/`*SIGNOFF*`. Report 9 §2.1 records that a prior
session (W1D-07) ran the wider repository scan: **181 affirmative-looking `GRANTED` matches, all 181
false positives** — licence texts, SQL `GRANT` prose, and billing/OTP "grant" language. That scan is
adopted, attributed, and **not repeated**, per the brief's instruction not to re-run broad scans.

---

## 3. The prerequisite that genuinely blocks P1

Not a bureaucratic one. P1's change 3 unbinds the clients, and **that silently changes what the
compatibility proxy returns** — including on the kill-switch path.

`RedisManagerCompatProxy.__getattr__` (`backend_app/backend/redis_manager.py:581-591`) falls through:

```
hasattr(_redis_manager, name)          -> manager method
_redis_manager._cache  is not None     -> bound method of the CLIENT
_redis_manager._queue  is not None     -> bound method of the CLIENT
otherwise                              -> raise AttributeError
```

After P1 sets `_cache = _queue = _events = None`, the two client fall-through branches stop matching.
For any name the manager does not itself expose, the proxy **begins raising `AttributeError` where it
previously returned a bound method of a dead client.** Report 7 §3.3 identifies this; I confirm it
from the source above.

Blast radius — the 11 `core/global_safety.py` sites that go through the proxy:
`:244 get`, `:255 setex`, `:257 set`, `:266 lpush`, `:275 lrange`, `:284 expire`, `:315 smembers`,
`:334 sadd`, `:351 srem`, `:370 set` (transition-lock acquire), `:384 delete` (release) — plus
`core/audit_trail.py`, `core/alerting_system.py`, `core/load_protection.py`,
`core/distributed_rate_limiter.py`, `routers/dag_tasks.py`, and `core/rate_limiter.py:179-180`
which reads `_connected` / `.cache` synchronously.

Two things make this urgent rather than academic:

1. **The change is arguably correct** — fail-closed beats silently using a closed client, and
   `global_safety.py:242-248` already raises `RedisUnavailable` on the kill-switch *read* path,
   with a comment naming the old swallow-and-return-`None` as "the root of **KILL-01**". So raising
   is consistent with the direction that file is already moving.
2. **But `core/global_safety.py` is `M` at +653 / −70** — a KILL-01 rewrite is in flight. Changing
   proxy semantics underneath it is the real coordination hazard, and it is not covered by
   D-A23C-1.

**Therefore D-A23C-3 must be answered before P1 lands, not after.** Report 7 reaches the same
conclusion and labels it "0b". Three admissible answers:

* **(i) Accept the gap** — P1 unbinds; proxy consumers get `AttributeError`; documented, not fixed.
  Smallest change, but it ships a behaviour change to the kill switch unannounced.
* **(ii) Keep the proxy's current surface** — P1 resets `_connected` but does **not** unbind.
  **Rejected on evidence:** report 4 §3.3 (A23-01) proves both halves are load-bearing — the
  un-reset flag defeats `get_redis_manager()`'s fast path and the still-bound `_cache` defeats
  `core/cache.connect()`'s own `not self._redis_manager._cache` guard. Unbinding is the half that
  fixes the second. Dropping it leaves the reconnect-after-shutdown dead path open.
* **(iii) Make the proxy raise a typed error** on an unbound/closed manager instead of
  `AttributeError`. Most consistent with KILL-01's direction; needs the `global_safety.py` owner.

**Recommendation: (iii), decided jointly with the `global_safety.py` owner; (i) is acceptable if
documented in the same breath. (ii) is not acceptable.**

---

## 4. Corrections that remove blockers

### 4.1 D-A23C-8b is VOID — the seven script `close()` sites are dead code

Report 6 §4b listed seven `scripts/test_*.py` `close()` call sites as a required consent. Report 9
§3 called the attribution incorrect. **Verified directly this phase** — all five script files import:

```
scripts/test_data_stream_worker.py:11   from backend.services.cache import redis_manager
scripts/test_event_bus.py:15            from backend.services.cache import redis_manager
scripts/test_execution_worker.py:11     from backend.services.cache import redis_manager
scripts/test_order_event_flow.py:12     from backend.services.cache import redis_manager
scripts/test_strategy_worker.py:15      from backend.services.cache import redis_manager
```

The package in this repository is `backend_app`, not `backend`, and there is no
`backend.services.cache` module on any path. These imports raise `ModuleNotFoundError` before any
`close()` is reached, so they **never touch** `backend_app/backend/redis_manager.py`. The consent
guarded a surface that does not exist. **One fewer approval needed.**

D-A23C-8**a** (`core/cache/redis_manager.py:619`, reached from `main.py:607` via `disconnect()`) is
real, live, and still required.

### 4.2 D-A23C-10 is RESOLVED — no consent needed

Installed `redis==5.0.8` at an exact pin. `Redis.aclose` exists (added 5.0.1); `Redis.close` is a
`@deprecated_function` alias that forwards to it, emitting three `DeprecationWarning`s per shutdown.
Standardising on `aclose()` raises an effective floor **only** if someone relaxes the pin below
5.0.1, and nothing proposes that. Per report 9 §6, no release-owner consent is required for change 4
at the current pin. **One fewer open question.**

### 4.3 Test path collision risk

Three names have been independently proposed for the same file: `tests/test_a23_redis_pool_lifecycle.py`
(reports 4, 6, 9 and this brief) and `tests/test_redis_manager_pool_lifecycle.py` (report 7).
**Both verified ABSENT.** Report 9 §2.3 warns that whoever is authorized must claim the path
explicitly or a second writer will clobber the first. **D-A23C-7 should name the path exactly.**
This handoff recommends the brief's name, `tests/test_a23_redis_pool_lifecycle.py`, since three of
the four reports use it.

---

## 5. P1 scope, restated for the approver

Three changes, one file (`backend_app/backend/redis_manager.py`, clean, blob
`d7b0e811b615ee13ba089b5cbe9d790161176de2`). No schema, no dependency, no other module.

| # | Change | Location | Invariant it must preserve |
|---|---|---|---|
| 1 | Collect the three clients as they are created; in the `except`, close each inside a nested `try/except` that swallows secondary errors; then return the **original** failure unchanged | `initialize()` `:138-184` | A cleanup error must never replace the diagnosis of why init failed. Previously-installed `self._*` stay in place — a failed attempt must not withdraw usable clients |
| 2 | Capture the outgoing trio; **publish the new trio; then** close the outgoing trio | `initialize()` `:167-171` | Publish-before-close. Closing first leaves `self._cache` pointing at a closed client while the replacement is built; worst case must be a leaked pool, never a live reference to a dead client. Never close a client that is in the newly published set |
| 3 | `close()` closes → resets `_connected = False` → unbinds all three to `None`; tolerant of already-closed clients so repeated calls are safe | `close()` `:215-224` | Close-then-unbind (unbinding first loses the handles). Both halves are load-bearing — see §3 option (ii) |

**Fixes:** G1/F4 (3 pools leaked per failed attempt, once per 3.0 s cooldown), G2/F3 (3 pools leaked
per successful rebuild), G3 (`close()` leaves health `True` and clients bound), A23-01 (the composite
reconnect-after-shutdown dead path).

**Does not fix:** R6 — a same-loop runtime failure is untouched (that is P3/D-A23C-4). Cross-loop
reuse — that is P2. **`test_r6_d` must remain `XFAIL(strict)` after P1.** An XPASS would mean someone
drifted into P3's scope; report 6 §5 item 6 frames the strict marker as the implementation-time
alarm, and that framing is adopted here.

**Sequencing:** **P1 before P2** — P1 changes `close()`'s postcondition, so a loop-identity rebuild
written first assumes a bound-but-stale trio and written after must tolerate `None`. **P3 is a
separate workstream** in another owner's in-flight file and is not part of this handoff. **P4**
(cooldown reconciliation) likewise.

**Concurrency limitation, to be stated in any P1 completion claim:** `initialize()` has a **third
caller** at `core/cache/redis_manager.py:625` (inside `connect()`, reached from `main.py:370`) with
**no cooldown and no lock**, unlike the two internal callers at `:213` and `:571`. P1 can make
`initialize()` *internally* safe — idempotent cleanup, atomic publish — but **"concurrent
initialization is safe" cannot be claimed** from a target-file-only change. That caller is in
another owner's file.

**Real-Redis requirement:** all P1 acceptance tests are server-free (fake clients, `AsyncMock`).
The real-Redis lane remains **UNEXECUTED** — 0 of 6 webhook assertions run — and is a separate
integration question, not a substitute for any P1 test and not grounds for D5′ acceptance.

### P1 acceptance tests (for `tests/test_a23_redis_pool_lifecycle.py`, 8 cases)

Assert close/create **counts on the client objects**, not `_connected` alone.

| # | Case | Fails today? |
|---|---|---|
| 1 | Third client's `ping()` raises ⇒ all 3 created clients closed exactly once (parametrise 1st/2nd/3rd) | **yes** — 0 closed |
| 2 | Cleanup itself raises ⇒ `initialize()` still returns `False` and the **original** error is what surfaces | **yes** — no cleanup exists |
| 3 | Successful re-init ⇒ the 3 **outgoing** clients closed exactly once | **yes** — 0 closed |
| 4 | Successful re-init ⇒ the 3 **new** clients remain open (negative control) | no — guards against over-closing |
| 5 | `close()` ⇒ `_connected is False` **and** all three attrs `None` | **yes** |
| 6 | `close()` twice ⇒ no exception, no double-close beyond tolerance | **unverified today** |
| 7 | Failed init cannot leave the manager marked connected (negative control) | no — passes at `:180` |
| 8 | **Composite:** `close()` then a `connect()`-shaped reinit actually rebuilds (A23-01) | **yes — the most valuable of the eight** |

Run order: the new file first; then `tests/test_redis_debounce.py` **serially** (10 tests, shared
module globals — expect 9 passed / 1 xfailed, unchanged); then the wider Redis-adjacent unit tests.

---

## 6. Copy-ready approval wording

The owner assignment must come first; items 2–4 are void without it.

> **1. Ownership assignment (prerequisite).**
> "`backend_app/backend/redis_manager.py` is assigned to **\<name/role\>**, who holds authority to
> approve changes to that file. This supersedes the UNOWNED record at
> `staging/PHASE11B_A10_D5_APPROVAL_READINESS.md:244` and `:367`."
>
> **2. D-A23C-1 — P1 implementation.**
> "I approve the three P1 changes described in
> `staging/PHASE11B_A23D_P1_AUTHORIZATION_HANDOFF.md` §5 to
> `backend_app/backend/redis_manager.py`: (1) close every client created during a failed
> `initialize()` while preserving the original failure, (2) publish the new clients before closing
> the outgoing clients exactly once, (3) make `close()` idempotently close, reset `_connected`, and
> unbind all three client references. No other change to this file is approved."
>
> **3. D-A23C-7 — test file.**
> "I approve creating **`tests/test_a23_redis_pool_lifecycle.py`** with the eight cases in §5. This
> path is claimed for P1; no other session may write it. `tests/test_redis_debounce.py` is not to be
> modified."
>
> **4. D-A23C-3 — proxy scope (required before P1 lands).**
> "P1's unbinding of `_cache`/`_queue`/`_events` changes what `RedisManagerCompatProxy` returns for
> the 11 `core/global_safety.py` call sites and five other modules. I choose **\<(i) accept and
> document the gap / (iii) raise a typed error\>**. Option (ii), omitting the unbind, is not
> available — it leaves the A23-01 reconnect-after-shutdown path open."
> *Co-sign required from the owner of `core/global_safety.py` (` M` +653/−70).*
>
> **5. D-A23C-8a — core-cache acknowledgement.**
> "As owner of `backend_app/core/cache/redis_manager.py`, I acknowledge that P1 change 3 alters
> `RedisManager.close()`'s postcondition, and that `:619` (`disconnect()`, reached from
> `main.py:607`) consumes it. After P1 the manager is correctly unusable until re-initialized."

---

## 7. Is P1 ready to implement?

**No.** The code design is settled and has been confirmed independently by four sessions; the
obstruction is entirely procedural, plus one real design decision.

| Gate | State |
|---|---|
| P1 design agreed | **yes** — four independent sessions, identical three changes |
| G1/G2 evidence | **execution-confirmed** (A22V probe: 3 created, 0 closed) **and** source-confirmed |
| G3/A23-01 evidence | **confirmed**, composite traced across two files |
| Target file available | **yes** — clean, blob `d7b0e811b615…`, zero competing edits |
| Test path available | **yes** — both candidate names ABSENT |
| **Owner identified** | **NO — recorded UNOWNED** |
| **D-A23C-1 granted** | **NO** |
| **D-A23C-7 granted** | **NO** |
| **D-A23C-3 decided** | **NO — and it must precede landing** |
| **D-A23C-8a acknowledged** | **NO** |

**Minimum to start: the owner assignment, then D-A23C-1 + D-A23C-7 + D-A23C-3, with D-A23C-8a
recorded alongside.** Nothing further needs investigating; per the brief I have stopped searching.

---

## 8. Preservation

| Check | Entry | Exit |
|---|---|---|
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` | identical |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | identical |
| normalized staged digest | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` | identical |
| staged / unmerged | 22 / 0 | 22 / 0 |
| unstaged tracked | 102 | 102 |
| untracked | 3553 | 3554 (this report) |
| **target fingerprint** | blob `d7b0e811b615ee13ba089b5cbe9d790161176de2`, **clean** | **identical, clean** |

Untracked drift across this and the preceding phase (3537 → 3553 before writing) is **concurrent-session
activity**, not this task: the `p11bd5_*`, `p11b22_*`, `w1d08_*` artifact families plus report 9.
This task created exactly one file.

Digest-method note, for cross-report comparison: reports 1–4 and 7 use
`2e994018e375f318f4e6dc9a67ab248e4622de8c426146ca756d5f47d2767152` (sha256 over sorted
`git ls-files --stage`); I use `65dfc453…` (sha256 over `git diff --cached`, CRLF→LF). Different
functions of identical content; both stable across every session. Reports 4 and 7 decline
`git write-tree` as a shared-object-database write and compute the tree from a copied index in an
isolated object directory, obtaining the same `6f40856c…` — the strict and direct methods corroborate.

Confirmed **not** done: production edit, test edit, report overwrite, dependency change, staging,
commit, push, reset, stash, rebase, branch switch, index mutation, CI trigger, Redis start, database
access, exchange call, deployment.

---

## 9. Status

| Item | State |
|---|---|
| **P1 (G1/G2/G3/A23-01)** | designed, evidenced, **NOT IMPLEMENTED** — owner UNIDENTIFIED |
| **P2 (loop identity)** | **NOT GRANTED**; must follow P1 |
| **P3 (R6 / health invalidation)** | **NOT GRANTED** — separate workstream, core-cache owner, the R6 blocker |
| **P4 (cooldowns, third caller)** | **NOT GRANTED**; concurrency safety unclaimable until then |
| D-A23C-8b (scripts) | **VOID** — dead imports, verified |
| D-A23C-10 (`aclose` floor) | **RESOLVED** — no consent needed at pin 5.0.8 |
| `test_r6_d` | **XFAIL(strict)**, correct; must stay XFAIL after P1 |
| Real-Redis lane | **UNEXECUTED** — 0 of 6 webhook assertions; skips are not passes |
| D5′ acceptance | **NOT CLAIMED** |
| QUOTA-01 / TENANT-01 | **OPEN** / **OPEN** |
| Production Redis reliability | **OPEN** |
| Live trading | **OFF / NOT CERTIFIED** |

No approval was inferred from this prompt, from any of the nine reports' recommendations, from the
target file being clean, from the existing strict xfail, from commit authorship, or from silence.
