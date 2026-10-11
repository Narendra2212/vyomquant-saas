# Phase 11B-A23E6 — Redis Lifecycle P1: Governance Decision Packet

**THIS DOCUMENT IS NOT AN APPROVAL.** It was authored by the implementation session and carries no
authority. It is a **response form**: the fields in §3 are blank and must be completed by an
authorized decision-maker before any code is written. Per A23E6 Phase 3, "a decision packet authored
by the same implementation session is not an approval."

**Not a register.** This does not supersede, duplicate or compete with
`PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` (the Class-A ledger),
`PHASE11B_A3_BLOCKER_REGISTER.md`, `PHASE11A_LAUNCH_GATE_REGISTER.md`, or
`PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md`. No existing record was edited. No status was changed
to `GRANTED`.

**Destination verified ABSENT immediately before writing.** Read-only otherwise: no production file,
test, dependency, config or governance record was modified.

**Status: IMPLEMENTATION NOT STARTED — AUTHORIZATION BLOCKED.**

---

## 1. Baseline (verified fresh, 2026-10-11)

| Invariant | Value | vs prior report |
|---|---|---|
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` | unchanged |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` | unchanged |
| normalized staged digest | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` | unchanged |
| staged / unstaged / unmerged | 22 / 102 / **0** | unchanged |
| untracked | 3580 | concurrent drift only |
| worktrees | 1 | unchanged |
| **`backend_app/backend/redis_manager.py`** | blob `d7b0e811b615ee13ba089b5cbe9d790161176de2`, **clean** | unchanged — **no workstream has claimed it** |
| **`tests/test_a23_redis_pool_lifecycle.py`** | **ABSENT** | unchanged — path still free |
| `backend_app/core/global_safety.py` | blob `4b2a1fc52748f3e90e86083b43f9e8c232629c3d`, ` M` | other workstream |
| `backend_app/core/cache/redis_manager.py` | blob `6c8a3692c35056d0b7ea25d7ce04dfcf1db06a11`, ` M` | other workstream |
| `tests/test_redis_debounce.py` | blob `5a8db7a8afbd57a0dfde35c303b49fb233c3fffc`, ` M` | A22 session |

**New authoritative records since the latest decision packet: none.** The newest `.md` in `staging/`
remain `PHASE11B_A26_DECISION_SHEET.md` and `PHASE11B_W1D14_D5_SIGNOFF_GATE_REPORT.md` (both 13:44);
the only later file is `p11b25_preserve.txt` (13:46), another session's probe output. No broad grant
sweep was repeated — three are already documented complete with zero affirmative grants.

**Identifier collision check (corrected 2026-10-11, A23E8 Phase 1).** `PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md`
defines **`E-1` through `E-6`** (an earlier draft of this note said `E-1`–`E-4`; that was
incomplete). All six meanings are **preserved untouched** here:

| ID | Dashboard meaning | State there |
|---|---|---|
| `E-1` | an owner for `backend_app/backend/redis_manager.py` | UNIDENTIFIED — the gating prerequisite |
| `E-2` | `D-A23C-1` + `D-A23C-7` | UNRESOLVED — "no grantor exists" |
| `E-3` | `D-A23C-8a` core-cache `close()` contract | NOT GRANTED — still required |
| `E-4` | option-B design conflict | UNRESOLVED |
| **`E-5`** | **the third unlocked `initialize()` caller** | **UNRESOLVED** |
| `E-6` | a reachable real Redis to verify against | ABSENT locally |

**`E-5` and `D-A23C-12a/b/c` are the same question at two levels and must not be recorded as
separate decisions.** `E-5` is the dashboard's *prerequisite state*; `D-A23C-12a/b/c` are the three
*dispositions* that would resolve it (accept / expand / defer). Whoever completes §3.F should record
the chosen disposition **against `E-5`**, using `D-A23C-12a/b/c` only as the option label. A
repository search confirms `D-A23C-12` and `D-A23C-13` are otherwise unused, so the option labels
themselves collide with nothing.

The dashboard already states, consistently with §3.F: *"No concurrency-safety claim is made. While
E-5 is unresolved, the Redis accessor path…"*. None of E-1–E-6 is reused for any other purpose here.

---

## 2. Decision matrix

Gate column: **N** = not satisfied. No row is satisfied.

| # | Decision ID | Current state | Decision-maker required | Evidence of that authority | Exact decision required | Affected files / dependencies | Gate |
|---|---|---|---|---|---|---|---|
| 1 | **Target ownership** (dashboard `E-1`) | **UNIDENTIFIED.** `A10:244` and `:367` record the file UNOWNED (label attached to defect F4; A23S §2.2's precision note applies — no record has ever considered ownership of the lifecycle repair specifically) | **UNIDENTIFIED** | **None exists.** No `CODEOWNERS`/`OWNERS`/`MAINTAINERS`; A25: 0 of 9 areas have an individual owner; 464 commits under one author identity, which is **not** authority evidence | Assign a person with authority over this file, and record it in a designated register | prerequisite to rows 2–8 | **N** |
| 2 | **D-A23C-1** (dashboard `E-2`) | **UNRESOLVED — "no grantor exists"** | owner from row 1 | — | Grant or refuse the three changes in §3.B | `backend_app/backend/redis_manager.py` only | **N** |
| 3 | **D-A23C-7** (dashboard `E-2`) | **UNRESOLVED** | owner from row 1 | — | Grant or refuse creating the one test path, and reserve it | `tests/test_a23_redis_pool_lifecycle.py` (ABSENT); `tests/test_redis_debounce.py` unchanged | **N** |
| 4 | **D-A23C-3** (dashboard `E-4` is the adjacent design conflict) | **UNRESOLVED — and a hard P1 prerequisite** | owner from row 1 **jointly with** the `global_safety.py` decision-maker | — | Choose §3.D Option A or Option B. Omitting the unbind is **not** available | reads on 11 `global_safety.py` sites + 5 modules; may require a file outside the P1 grant | **N** |
| 5 | **D-A23C-8a** (dashboard `E-3`) | **NOT GRANTED — still required** | authorized owner of `backend_app/core/cache/redis_manager.py` | role named in records; **no person** | Acknowledge the changed `close()` postcondition consumed at `core/cache/redis_manager.py:619` | acknowledgement only — **no edit to that file under P1** | **N** |
| 6 | **D-A23C-9** | **OPEN in the register — retained, not dropped** | AUTH-01 / CONN-01 / TENANT-01 decision-maker | role referenced; **no person** | Acknowledge rebuild semantics for the 5 `Depends(get_redis_manager)` handlers in `routers/exchange.py` (`:318` `store_keys` behind `require_aal2`, `:534`, `:670`, `:772`, `:1138`) | acknowledgement only — `exchange.py` is `MM` and not edited | **N** |
| 7 | **D-A23C-12a / 12b / 12c** (new, collision-checked) | **ABSENT — no disposition on record** | owner from row 1; **12b additionally** needs the `core/cache/redis_manager.py` owner | — | Select exactly one: accept / expand / defer — §3.F | the unlocked third `initialize()` caller at `core/cache/redis_manager.py:625`, reached from `main.py:370` | **N** |
| 8 | **Implementation authorization** | **ABSENT** | owner from row 1 | — | Explicit permission to execute the approved scope, with conditions | all of the above | **N** |

**Rows explicitly out of scope and unchanged:** `D-A23C-2` (P2 loop identity), `D-A23C-4` (P3
runtime-health invalidation — the R6 blocker), `D-A23C-5` (P4 cooldowns), `D-A23C-6` (real-Redis
lane). **Already discharged, no action needed:** `D-A23C-8b` VOID (dead imports verified),
`D-A23C-10` RESOLVED (`redis==5.0.8`, `aclose()` available, no floor raised), `D-A23C-11` CLOSED.

---

## 3. Response form — to be completed by the authorized decision-maker

Each item is independent. Granting one does **not** grant any other. Blanket language such as
"approved, go ahead" should be avoided: it would be ambiguous as to P2, P3, P4, real-Redis,
dependencies and unrelated work, none of which are requested here.

### A. Ownership assignment

> I assign `backend_app/backend/redis_manager.py` to ____________________, with authority to approve
> ____________________ (precise scope). This assignment supersedes the UNOWNED record at
> `staging/PHASE11B_A10_D5_APPROVAL_READINESS.md:244` and `:367`.
>
> Files this authority additionally covers, if any: ____________________
> *(Leave blank rather than extending it over another workstream's dirty files —
> `core/global_safety.py` and `core/cache/redis_manager.py` are both currently modified by other
> sessions.)*

**Not to be completed from commit authorship or inferred responsibility.**

### B. D-A23C-1 — production implementation

> ☐ **GRANTED** ☐ **REFUSED**
>
> I authorize exactly these three changes to `backend_app/backend/redis_manager.py`:
> 1. During a failed `initialize()`, close every client that attempt created, while preserving the
>    original initialization failure (a secondary cleanup error must not replace it).
> 2. During a successful re-initialization, publish the new client trio **before** closing the
>    outgoing trio, and close each outgoing client exactly once, never closing a newly published one.
> 3. Make `close()` safely repeatable: close the clients, reset `_connected`, then unbind `_cache`,
>    `_queue` and `_events`.
>
> **No other production change is included.** Not P2 loop identity, not P3 runtime-health
> invalidation, not P4 cooldowns, not the third-caller race, no dependency change.

### C. D-A23C-7 — test file

> ☐ **GRANTED** ☐ **REFUSED**
>
> I authorize creating exactly `tests/test_a23_redis_pool_lifecycle.py` with the eight cases in §4,
> and reserve that path for this workstream. `tests/test_redis_debounce.py` is not to be modified.

### D. D-A23C-3 — compatibility-proxy decision *(required before P1 lands)*

Change 3 unbinds the three clients. `RedisManagerCompatProxy.__getattr__`
(`backend_app/backend/redis_manager.py:581-591`) falls through to `_cache` and then `_queue` when the
manager itself does not expose a name. Once those are `None` the fall-through stops matching, so the
proxy **begins raising `AttributeError` where it previously returned a bound method of a closed
client**. Affected: 11 `core/global_safety.py` sites (`:244, 255, 257, 266, 275, 284, 315, 334, 351,
370, 384` — kill-switch get/set/lpush/lrange/expire/smembers/sadd/srem and the transition lock),
plus `core/audit_trail.py`, `core/alerting_system.py`, `core/load_protection.py`,
`core/distributed_rate_limiter.py`, `routers/dag_tasks.py`, and `core/rate_limiter.py:179-180`.

> ☐ **Option A** — accept and document the gap. The proxy raises `AttributeError` after shutdown;
> recorded as a known behaviour change, no further code written.
>
> ☐ **Option B** — introduce a typed error for the unbound/closed manager. *Consistent with
> KILL-01's direction (`global_safety.py:242-248` already raises `RedisUnavailable` on the
> kill-switch read path), but it changes a contract for ~17 call sites while `global_safety.py` is
> `M` at +653/−70.*
>
> Co-decision from the `core/global_safety.py` decision-maker: ____________________
>
> If Option B's implementation needs any file outside the B grant, the session must **stop and
> request separate authorization** rather than expand scope.

**Omitting the unbind is not offered.** A23-01 shows both halves are load-bearing: the un-reset flag
defeats `get_redis_manager()`'s fast path and the still-bound `_cache` defeats
`core/cache.connect()`'s own `not self._redis_manager._cache` guard. Dropping the unbind leaves the
reconnect-after-shutdown defect open.

### E. D-A23C-8a — core-cache acknowledgement

> ☐ **ACKNOWLEDGED**
>
> As authorized owner of `backend_app/core/cache/redis_manager.py`, I acknowledge that change 3
> alters `RedisManager.close()`'s postcondition and that `:619` (`disconnect()`, reached from
> `main.py:607`) consumes that contract. After P1 the manager is correctly unusable until
> re-initialized. **No edit to this file is authorized under the P1 grant.**

### F. Concurrency disposition — select exactly one

`initialize()` has a third caller at `core/cache/redis_manager.py:625` (inside `connect()`, reached
from `main.py:370`) with **no cooldown and no lock**, unlike the two internal callers at `:213` and
`:571`.

> ☐ **D-A23C-12a — accept and document.** P1 makes `initialize()` internally safe (idempotent
> cleanup, atomic publish). "Concurrent initialization is safe" is **not** claimed. *Allows the
> three-change, one-file scope to proceed as written.*
>
> ☐ **D-A23C-12b — expand scope.** Add locking/debounce at the third caller. *Requires a separate
> file-specific grant for `core/cache/redis_manager.py` (` M`, another workstream) and that owner's
> approval. Does **not** fit the current one-file proposal.*
>
> ☐ **D-A23C-12c — defer P1** until a broader concurrency scope is authorized. *G1, G2, G3 and
> A23-01 remain open in the meantime.*

### G. D-A23C-9 and implementation authorization

> **D-A23C-9** ☐ **ACKNOWLEDGED** — rebuild semantics for the 5 `Depends(get_redis_manager)`
> handlers in `routers/exchange.py`. A rebuild is picked up between requests, not during one.
> `exchange.py` is `MM` and is not edited.
> Decision-maker: ____________________
>
> **Implementation authorization** ☐ **GRANTED**, subject to: ____________________
>
> This authorization covers only B, C and the selected D and F options. It does not extend to P2,
> P3, P4, real-Redis integration, QUOTA-01, TENANT-01, dependency changes, CI, deployment, or
> live-trading certification.

---

## 4. What will be built the moment the form is complete

No further investigation is needed. The design is confirmed against live source and agreed by four
independent sessions.

**Production — `backend_app/backend/redis_manager.py` only:**

| Change | Location | Invariant |
|---|---|---|
| A | `initialize()` `:138-184` | track created clients; close them in the `except` inside a nested `try/except`; **original failure wins**; previously installed `self._*` stay in place |
| B | `initialize()` `:167-171` | capture outgoing trio → publish new trio → close outgoing trio; never close a newly published client |
| C | `close()` `:215-224` | close → reset `_connected = False` → unbind all three to `None`; tolerant of already-closed clients |

**Tests — `tests/test_a23_redis_pool_lifecycle.py`, eight cases**, asserting create/close counts on
the client objects rather than on `_connected` alone, server-free fakes only:

| # | Case | Fails today? |
|---|---|---|
| 1 | `ping()` failure closes every client that attempt created — parametrised over the 1st/2nd/3rd position | **yes** — currently 0 closed |
| 2 | A cleanup error does not replace the original initialization error | **yes** — no cleanup exists |
| 3 | Successful re-init closes each outgoing client exactly once | **yes** |
| 4 | Newly published clients remain open (negative control) | no — guards over-closing |
| 5 | `close()` resets `_connected` and unbinds all three | **yes** |
| 6 | Repeated `close()` is safe, no unintended duplicate close | **unverified today** |
| 7 | Failed init cannot leave the manager marked connected (negative control) | no — passes at `:180` |
| 8 | `close()` then reconnect actually rebuilds the trio (A23-01 composite) | **yes — the most valuable** |

**Verification sequence, one continuous run:** new file → `tests/test_redis_debounce.py` serially,
unmodified (expect 9 passed / 1 xfailed) → Redis-adjacent and proxy/safety/cache caller regressions
→ focused follow-ups if the call graph or a failure justifies them → full-diff and preservation
audit. `test_r6_d` stays `XFAIL(strict)`; an XPASS would mean drift into P3 scope and would be
investigated, not hidden. **Real-Redis integration remains separate and unexecuted** — server-free
tests are not real-Redis acceptance, and P1 unit-test success certifies neither production nor live
trading.

---

## 5. Delivery route — the honest limitation

**There is no documented governance channel through which to submit this packet.** Checked, not
assumed:

* The only Class-A approval register, `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:800-804`, holds
  **role** values (`DB owner`, `Code owner`, `Coordinator + DB owner`) — never a person.
* `PHASE11B_A3_BLOCKER_REGISTER.md` §3 has an `Owner` column designed for this, and its values are
  also roles (`SRE + coordinator`, `Billing owner`).
* `PHASE11A_LAUNCH_GATE_REGISTER.md` §A has **no owner column**.
* A26 asks the same question for a different role and answers it: "Can the `DB owner (read access)`
  role be mapped to an authorized person through an existing authoritative record? **No.**"

**This packet has not been submitted, approved or recorded, and preparing it does not resolve the
gate.** It is content for the user to route through a legitimately available channel. The only
channel visible to this session is the operator of this session.

---

## 6. Preservation

Entry and exit identical on every invariant in §1. Files changed by this task: **one** — this
packet. No production file, test, dependency, configuration, migration, schema, CI or governance
record was modified; no existing report overwritten; nothing staged, committed, pushed, reset,
stashed, cleaned, or branch-switched; no index mutation; no CI dispatch; no Redis start; no
database, exchange or network call; no deployment.

The three files adjacent to this work remain under their other sessions' custody at the blobs listed
in §1 and were **read only**.

---

## 7. Status

| Item | State |
|---|---|
| **P1 implementation** | **NOT STARTED — AUTHORIZATION BLOCKED** |
| G1 / G2 / G3 / A23-01 | confirmed by execution and source; **all OPEN, unfixed** |
| Gates satisfied | **0 of 8** (§2) |
| Grants on record | **0** — consistent with `A25 §7.1` "Total granted: 0" |
| Owner identified | **NO** |
| Delivery route for this packet | **NONE documented** (§5) |
| D-A23C-8b / -10 / -11 | VOID / RESOLVED / CLOSED — no action needed |
| P2 / P3 / P4 / real-Redis / QUOTA-01 / TENANT-01 | **separate and untouched** |
| Tests executed this phase | **0** |
| **Live trading** | **OFF / NOT CERTIFIED** |

No approval was inferred from this brief's framing, from the packet's own existence, from the target
file being clean, from role names, from commit authorship, or from silence.
