# Phase 11B / A25 — Owner Map

**Date:** 2026-10-11
**Scope:** read-only coordination. **Destination verified ABSENT before writing** (`staging/PHASE11B_A25_OWNER_MAP.md` did not exist; no PHASE11B_A25* artifact existed). No report was overwritten.
**Primary planning source:** `staging/PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md`
**Also read (newer than A24):** `staging/PHASE11B_W1D11_CLASSIFIER_APPROVAL_HANDOFF.md` (20,054 B, 12:51:40)

This task does **not** grant implementation, CI, credential, database or deployment
authorization, and nothing in this document is an approval.

---

## 0. The headline

| Measure | Result |
|---|---|
| Areas mapped | **9** |
| Areas with an **individual** owner identified | **0 of 9** |
| Areas with a **role** owner recorded in a register | **3 of 9** (classifier, PostgreSQL lane, core-cache proxy) |
| Areas with **no ownership record of any kind** | **6 of 9** |
| Areas **APPROVED** | **0 of 9** |

**No individual is identifiable as an owner anywhere in this repository.** Verified directly
this phase, not inherited:

- **No `CODEOWNERS` file exists** — repository-wide filename search including ignored files
  returned zero matches. This is the canonical ownership record for a GitHub repository and
  it is absent.
- **`git log --all` has exactly one author identity** across **464 commits**:
  `Narendra2212` under two addresses (`nt3441449@gmail.com` and the GitHub noreply form).
  A single-committer history carries no ownership signal that distinguishes one file from
  another.
- All three open D5′ PRs (#6, #7, #8) carry **0 assignees, 0 reviewers, 0 labels** and
  `reviewDecision: ''` (recorded A17/A18, unchanged).

So every row below that is not `UNIDENTIFIED` names a **role**, never a person.

---

## 1. Method, and the three evidence classes I kept separate

The brief forbids inferring authority from commit authorship, file modification, report
authorship, or silence. Applying that strictly splits the available evidence into three
classes, only the first of which is ownership:

| Class | What it is | Counts as authority? |
|---|---|---|
| **A — Authority** | A register entry stating who may approve a change to this area | **Yes** |
| **B — Role** | A report naming a role (`Code owner`, `DB owner`, `reconciliation owner`) with no person attached | **Partially** — identifies *what kind* of owner is needed, not *who* |
| **C — Custody** | Who currently has uncommitted edits in the file | **No** |

Class C is the largest body of evidence in this repository and the easiest to mistake for
ownership. `staging/PHASE11B_A3_BASELINE_AND_OWNERSHIP.md` §8 is titled "Concurrent
workstream ownership map" and has a column headed "Owner / contents", but its cell values
are `mine (WS-C)`, `another tab`, `Other tabs`, `Workstream E`, `SHARED`. Those answer
*"who is editing this right now"*, not *"who may approve a change"*. **I am treating A3 §8
as custody evidence only.** It is cited below for conflict risk, never for authority.

The only Class A register in the repository is the W1D approval ledger
(`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, lines 800–804), and even that names
roles rather than people.

### Status vocabulary as applied

| Status | Meaning here |
|---|---|
| `IDENTIFIED` | An owner is explicitly recorded in a register for this area |
| `UNIDENTIFIED` | No ownership record of any kind exists for this area |
| `AWAITING_DECISION` | An owner role is recorded **and** a decision has been formally put, but not answered |
| `APPROVED` | An affirmative grant exists |

---

## 2. The owner map

### A-1 · `backend_app/backend/redis_manager.py` — **UNIDENTIFIED**

| | |
|---|---|
| **Status** | `UNIDENTIFIED` |
| **Ownership record** | **Affirmatively negative.** Recorded UNOWNED, twice. |
| **Evidence (verbatim, verified by direct read)** | `staging/PHASE11B_A10_D5_APPROVAL_READINESS.md:244` — "`backend_app/backend/redis_manager.py` \| clean \| **F4, unowned.** Blast radius is 3 calls inside `initialize()`…"<br>`:367` — "**F4 — `get_redis_url` discards an explicit database and captures `REDIS_URL` at import. UNOWNED, unfixed.**" |
| **Current state** | **clean** (blob `d7b0e811b615ee13`, byte-identical to HEAD, 593 lines). No concurrent custody. |
| **Pending decisions** | `D-A23C-1` (three P1 lifecycle changes), `D-A23C-7` (create `tests/test_a23_redis_pool_lifecycle.py`) — both recorded **UNRESOLVED**, and per `staging/PHASE11B_A23D_P1_AUTHORIZATION_HANDOFF.md:53` *"not merely NOT GRANTED: no grantor exists"* |

**Precision carried forward, not glossed over.** `staging/PHASE11B_A23S_REDIS_LIFECYCLE_OWNERSHIP_HANDOFF.md`
§2.2 (lines 102–117) correctly observes that in both `A10` citations the "unowned" label is
attached to **F4** — a *different* defect in the same file (URL/database handling at import) —
not to the G1/G2/G3 pool-lifecycle defect. Its own conclusion is that this does not weaken the
finding, because ownership attaches to the file: *"a file unowned for F4 is unowned for
G1/G2/G3"*. I adopt that reading, and adopt its recommended wording: the register should say
**the file carries no ownership record, as documented for F4**, rather than implying a
lifecycle-specific ownership finding exists. No record has ever considered ownership of the
lifecycle repair specifically.

**This is the single most-blocked area in the engagement: five prior sessions have now halted
at this same gate** (A23, A23C, A23D, A23R, A23S), and the blocker is a missing *owner*, not a
missing *approval*. A missing grant can be obtained from a known owner; here there is no one
to ask.

---

### A-2 · `backend_app/core/cache/redis_manager.py` — **UNIDENTIFIED** (role named)

| | |
|---|---|
| **Status** | `UNIDENTIFIED` — a role is referenced, but no register entry and no person |
| **Role referenced** | "owner of `core/cache/redis_manager.py`", and "core-cache owner" |
| **Evidence** | `staging/PHASE11B_A23D_P1_AUTHORIZATION_HANDOFF.md:54` — decision `D-A23C-8a`: *"Core-cache owner acknowledges the new `close()` contract at `core/cache/redis_manager.py:619` (production shutdown path, reached from `main.py:607`)"*, state **NOT GRANTED — still required**<br>`staging/PHASE11B_A10_D5_APPROVAL_READINESS.md:245` — "F5 fail-soft proxies. **Billing-critical**, needs its own review" |
| **Current state** | ` M` **+185/−5**, blob `6c8a3692c35056d0`, 1488 lines — **in flight under another session's custody** (A3 §8: "**SHARED** — another tab", `A3 may edit? **No**`) |
| **Why it matters to A25** | This file is the hinge for the Redis option-A repair. `A22V:332` records option A / core-proxy invalidation as **NOT GRANTED**. `close()` has 9 call sites, one of which is this file at line 619. |

The role is named but was never entered in a register, so there is no path from "core-cache
owner is required" to "here is who that is".

---

### A-3 · `backend_app/routers/exchange.py` — **UNIDENTIFIED** (role named)

| | |
|---|---|
| **Status** | `UNIDENTIFIED` |
| **Role referenced** | "execution/venue-boundary owner" — `staging/EXCHANGE_SANDBOX_CERTIFICATION.md:331` (EXCH-01), with "coordinator sign-off since preflight is a shared gate" at `:387` (EXCH-04) |
| **Custody (Class C — not authority)** | A3 §8: `MM`, *"CONN-01 eviction + AUTH-01 `require_aal2` + SC-27 error classification"*, marked `mine (WS-C)` by that session |
| **Current state** | `MM` — **+422/−29** unstaged, **+268/−15 staged**, blob `4b40973568edfa4d`. Three workstreams' changes are interleaved in one file across both index layers. |
| **Bearing on A25** | Carries 5 × `Depends(get_redis_manager)` call sites (lines 318 `store_keys`, 534 `list_exchanges`, 670 `delete_connection`, 772 auth-schema, 1138) — so it is the blast radius for any Redis accessor change, while being simultaneously the most entangled file in the tree. `A10:243` records that `git apply --cached --check` against a private index seeded from HEAD **refuses** this file's diff, and that it "must not be cherry-picked, reconstructed, or hand-rewritten." |

---

### A-4 · `backend_app/routers/billing.py` — **UNIDENTIFIED**

| | |
|---|---|
| **Status** | `UNIDENTIFIED` |
| **Ownership record** | **Affirmatively negative.** F5 recorded UNOWNED. |
| **Evidence** | `staging/PHASE11B_A15_D5_EXECUTION_REPORT.md:202` — "**F5 — webhook idempotency asymmetry on cache faults. UNOWNED, confirmed from current code.**"<br>`staging/PHASE11B_A16R_D5_EVIDENCE_RECONCILIATION.md:162` — "F5 unowned \| **holds** — billing-critical; the concurrent `billing.py` change does not close it"<br>`A10:246` — "Another workstream; **does not address F5**" |
| **Current state** | ` M` **+700/−40**, blob `cb3cfda2ec3e3261` — largest concurrent diff in the tree, and it does **not** touch F5 |
| **Note** | A3 §8 records billing's diff as changing **0** `limiter.limit` lines. The 700-line change and the open F5 defect are disjoint. |

---

### A-5 · `backend_app/main.py` — **UNIDENTIFIED**, and requires a coordinator

| | |
|---|---|
| **Status** | `UNIDENTIFIED` — no owner; explicitly flagged as needing coordination |
| **Evidence** | A3 §8: `main.py` \| ` M` \| "**SHARED** — lifespan; D5′/WS-C added an 8-line pool drain, other tabs added more" \| `A3 may edit? **coordinate**`<br>A3 §8 overlap hazard 2: "**`main.py` lifespan** — WS-C (pool drain), reconciliation wiring (**RECON-01**) and readiness (**OBS-01**) all land in the same block." |
| **Current state** | ` M` **+289/−36**, blob `a3849d4acd7afcf4` |
| **Contending workstreams** | **three**, all targeting the same lifespan block: WS-C pool drain, RECON-01 reconciliation wiring (Workstream C of this phase), OBS-01 readiness refresh |

`main.py` is the convergence point for the launch critical path and the one file where three
independent changes must be serialised. It has no owner and no coordinator is named.

---

### A-6 · W1C-02 classifier code — **AWAITING_DECISION** (role `IDENTIFIED`)

This is the **only** area in the map with a genuine Class A register entry.

| | |
|---|---|
| **Status** | `AWAITING_DECISION` for `AP-W1D-3`; `UNIDENTIFIED` (no register entry at all) for `D1`–`D5` |
| **Owner role** | **`Code owner`** — recorded explicitly |
| **Register** | `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, **line 802**: `AP-W1D-3` \| "Implement the 4-way drift classification + the 10 regression tests" \| owner role **`Code owner`** \| **NOT GRANTED** |
| **Additional stakeholder** | `D5` also needs the **production-audit stakeholder** — `verify_production_schema.py`'s three-tier severity policy (a hard-coded 10-dimension critical list) is a product decision about audit criticality (W1D11 §4) |
| **Decisions outstanding** | **6** — `AP-W1D-3`, `D1`, `D2`, `D3`, `D4`, `D5`. **GRANTED: 0. REJECTED: 0. UNRESOLVED: 6.** (W1D11 §3) |
| **Process gap recorded** | `D1`–`D5` "appear nowhere except in my own W1D-09 and W1D-10 reports… They have never been put to an owner in a tracked register." (W1D11 §2.2) |

Two negative searches worth preserving, because they prevent a future session re-running them:

- W1D11 §2.1 swept repository-wide for affirmative grant language filtered to AP-W1D /
  classifier / drift contexts. **Affirmative grants: 0.** It names its own false positives —
  `staging/p11b24_extract.txt` ×2 and `PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md`, all
  matching "is granted" inside *conditional* clauses — and explicitly warns that "the raw
  count of 3 is not a signal and should not be quoted."
- This is the third independent confirmation of the same pattern. Two earlier sessions each
  scanned repo-wide and each found 181 affirmative-looking `GRANTED` hits, and both concluded
  all 181 were false positives (licence texts). **Three sweeps, three sessions, zero grants.**

---

### A-7 · Order watchdog integration — **UNIDENTIFIED** (role named)

| | |
|---|---|
| **Status** | `UNIDENTIFIED` |
| **Role referenced** | "execution/recovery owner" — `staging/EXCHANGE_SANDBOX_CERTIFICATION.md:412` (EXCH-07) |
| **Current state** | ` M` **+435/−11**, blob `a67092359cb74ad3` |
| **Wired?** | **Yes** — `main.py:432` `await _order_watchdog.start()`, `service_status["order_watchdog"] = "ACTIVE"` (interval 30 s, stale 60 s, alert 300 s) |
| **Open defect** | `staging/PHASE11B_D5_WATCHDOG_REPAIR_REPORT.md`: W-1, W-2, W-2b **FIXED**; **W-1b NOT FIXED**. 18 tests in `tests/test_d5_watchdog_adjudication_repair.py` |

The watchdog is one of only **two** workstreams in the engagement that actually shipped — and
A24's diagnosis holds: the two that shipped (watchdog D5, QUOTA-01 workstream A) are the two
with unambiguous sole custody. That correlation is the strongest available evidence that the
ownership vacuum, not technical difficulty, is what is blocking the rest.

**Bearing on Workstream C:** the watchdog "already runs a reconciliation loop over the same
`execution_records` rows" (guard-test docstring, see C below). It is the incumbent, and the
reason wiring a second reconciler is contested rather than obviously correct.

---

### A-8 · PostgreSQL verification lane — **AWAITING_DECISION** (roles `IDENTIFIED`)

| | |
|---|---|
| **Status** | `AWAITING_DECISION` — roles recorded, all four approvals withheld |
| **Register** | `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` lines 800–804 |

| Approval | Scope | Owner role (verbatim) | State |
|---|---|---|---|
| `AP-W1D-1` | Per-statement completeness predicates for 022–025 under an enforced read-only session | **`DB owner (read access)`** | **NOT GRANTED** |
| `AP-W1D-2` | Insert 4 ledger rows for migrations verified FULLY APPLIED | **`DB owner`** | **NOT GRANTED** |
| `AP-W1D-4` | Add the `_provenance` envelope and recapture the snapshot | **`DB owner + code owner`** | **NOT GRANTED** |
| `AP-W1D-5` | Install `pgserver`; create the isolated cluster and the `aerora_app` role | **`Coordinator + DB owner`** | **NOT GRANTED** |

Two facts that bound what this lane can deliver:

- **No production DSN exists in any phase.** The scorecard's `prod_dsn_verify` is `NOT_DONE`
  with evidence "no production DSN in any phase"; `production_track` is **0% / NOT CLASSIFIED**.
- Declared-key uniqueness is verified for **0 of 16** dimensions. W1D11 §4 records that
  `AP-W1D-1` is *"the single next action needing nothing from the Code owner"* — making it the
  one lane that can advance without the most-contended role.

---

### A-9 · Sandbox credential provisioning — **UNIDENTIFIED**, no role recorded anywhere

| | |
|---|---|
| **Status** | `UNIDENTIFIED` |
| **Ownership record** | **None.** No register entry, and — uniquely in this map — **not even a role is named.** |
| **What is specified** | Only the *mechanism*: the harness names the exact variables it requires and refuses to run without them. Nothing anywhere names who obtains them. |
| **Why this is the top blocker** | `paper_sandbox_e2e_evidence` carries **0.15** of the paper track — the single largest unmet weight — and is `BLOCKED_ON_CREDENTIAL` |

This is the most consequential gap in the map. The highest-weighted unmet launch gate has no
owner **and no owner role**, while the technical work is complete and waiting. Full
specification in §3 below and in the dashboard's Workstream B section.

---

## 3. Workstream B resolved: the exact variables required

Taken from **code**, not from reports — `tests/exchange_sandbox/harness.py`. Reports agreed,
with one documented exception noted below.

| Purpose | Variable | Required? |
|---|---|---|
| Collection gate | `AERORA_EXCHANGE_SANDBOX_CERT=1` | **Yes** — absent, the whole directory skips (`GATE_ENV`, harness L30) |
| Venue selector | `AERORA_SANDBOX_VENUE` | One venue per run; `binance` \| `bybit` \| `okx` (lifecycle suite L59, defaults `binance`) |
| Instrument | `AERORA_SANDBOX_SYMBOL` | Optional, defaults `BTC/USDT` (L62) |

Credentials resolve through `CREDENTIAL_TEMPLATE` (harness L58–62) and are read at L115–122:

| Venue | Variables | Notes |
|---|---|---|
| **binance** | `AERORA_SANDBOX_BINANCE_API_KEY`, `AERORA_SANDBOX_BINANCE_SECRET` | testnet key — `testnet.binance.vision` |
| **bybit** | `AERORA_SANDBOX_BYBIT_API_KEY`, `AERORA_SANDBOX_BYBIT_SECRET` | testnet key |
| **okx** | `AERORA_SANDBOX_OKX_API_KEY`, `AERORA_SANDBOX_OKX_SECRET`, `AERORA_SANDBOX_OKX_PASSPHRASE` | demo-trading key; **passphrase required** |

**No credential value is recorded in this report, and none was read, printed, copied or
committed.** Variable *names* only.

**One precision correction.** `staging/p11b_prerequisites.json` lists
`AERORA_SANDBOX_BINANCE_PASSPHRASE` among binance's `expected_names`. That comes from
`credential_env_names()`, which returns the full three-item template. Harness L60 marks the
passphrase **"optional overall; OKX requires it"**, and L118 gates only on
`not key or not secret`. **Binance and bybit need two variables each, not three.** A request
built from that JSON would ask for a credential binance does not issue.

### 3.1 Sandbox-vs-live separation is reliably established — Workstream B does not need to stop

The brief required me to stop and report if the test setup cannot reliably distinguish
sandbox from live endpoints. It can, through **four independent layers**:

1. **Name-space isolation.** Credentials must arrive under names carrying an explicit
   `SANDBOX` token, "so a production key sitting in the ambient environment cannot be picked
   up by accident" (harness docstring, item 2).
2. **Active production-key refusal.** `PRODUCTION_CREDENTIAL_NAMES` (harness L66–75)
   enumerates 20 production variable names across all seven venues; the harness "refuses to
   run rather than risk using one."
3. **Measured endpoint departure.** `test_routable_venue_leaves_production_when_sandbox_is_armed`
   asserts the armed client's base URL or headers actually changed versus a pristine client of
   the same class — binance/bybit swap the base URL, okx adds the simulated-trading header.
   This is the stated **precondition** for the live suite: *"If it fails, that suite must not
   run, because an 'order placed in sandbox' would be an order placed with real money."*
4. **Re-measurement of the connected client.** `_assert_not_production` in the lifecycle suite
   re-measures the **CONNECTED** client, not just a constructed one (harness docstring item 3).

Two known-good guardrails reinforce this:

- `SANDBOX_ROUTABLE == frozenset({"binance", "bybit", "okx"})` is asserted by a test, and
  `test_the_sandbox_gate_is_explicit` pins the gate name — so "this directory did not run"
  can never be silent.
- **bitget's sandbox flag is inert and orders would reach production** (EXCH-01). This is an
  upstream ccxt defect, expected to stay red on ccxt 4.3.92 indefinitely. The platform is
  already defended: bitget is withheld from order routing and `connect()` refuses any venue it
  cannot prove left production (`verify_sandbox_endpoint` / `assert_sandbox_supported`,
  fail-closed, in `tests/test_exch01_sandbox_endpoint_verification.py`).
- A missing credential produces `pytest.skip` with the text **"NO SANDBOX CREDENTIAL for
  {venue}. This is MISSING EVIDENCE, not a pass."** (harness L137–140) — the skip cannot be
  misread as a pass.

**Conclusion: the separation is sound, the suite is fail-closed, and the only missing input is
the credential itself.** The blocker is procurement and ownership, not test design.

---

## 4. What this map changes about the launch plan

A24 concluded the real blocker is a missing **owner**, not a missing **approval**. This map
sharpens that into something more actionable:

**Of the nine areas, exactly one decision can be unblocked by a role that is already recorded
and is not the most-contended role.** That is `AP-W1D-1` (`DB owner, read access`). Everything
else routes to `Code owner` (4 areas), an unnamed role (3 areas), or nobody at all (2 areas,
including the top-weighted launch gate).

The two areas with **no role at all** are:

| Area | Weight at stake |
|---|---|
| **A-9 sandbox credential provisioning** | `paper_sandbox_e2e_evidence` — **0.15**, the largest single unmet weight |
| **A-5 `main.py` coordination** | `recon_operational_for_paper` — **0.10** |

Those two gates total **0.25 of the paper track**, which is precisely the
`blocked_or_open_weight_not_counted` figure in the authoritative scorecard. **The entire
blocked weight of the paper launch sits in the two areas with no ownership role recorded.**
That is the finding this map exists to surface.

---

## 5. Confirmations

This phase did **not**: edit any source file, test, migration, workflow or shared
configuration; stage, commit, push, reset, stash, clean or switch branches; mutate the shared
Git index; trigger or dispatch CI; connect to PostgreSQL, Redis or any exchange; install a
package; start a server; make a network call; read, print, copy or commit any credential
value; access a production DSN; or overwrite any existing report.

**Live trading: OFF / NOT CERTIFIED.** Nothing here is a live-trading certification, and a
paper-only launch remains a separate release decision.

No approval is claimed, no owner was contacted, and **no person is named as an owner anywhere
in this document** — because no such record exists.
