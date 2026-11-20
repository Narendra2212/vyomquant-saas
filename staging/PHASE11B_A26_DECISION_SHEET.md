# Phase 11B / A26 — Decision Sheet

**Date:** 2026-10-11 · **Launch target:** 2026-10-25 · **Scope:** paper/retail only
**Destination verified ABSENT before writing.** No existing report, register or artifact was overwritten.
**Reused without rescanning:** `PHASE11B_A25_OWNER_MAP.md`, `PHASE11B_A25_LAUNCH_EXECUTION_DASHBOARD.md`,
`PHASE11B_A24_LAUNCH_CRITICAL_PATH_COORDINATION.md`, `PHASE11B_W1D11_CLASSIFIER_APPROVAL_HANDOFF.md`,
`PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, `PHASE11A_READINESS_SCORECARD.json`.

> **LIVE TRADING: OFF / NOT CERTIFIED.** Unchanged. Paper track **53%**, unchanged and unrevised.
> **No approval is created here on anyone's behalf.**

---

## 1. Fresh baseline, and the differences from A25

Both digest methods reproduce (serialisations documented in `staging/p11b26_baseline.py`):

| Measurement | A25 exit | A26 entry | Delta |
|---|---|---|---|
| branch / `HEAD` | `main` / `074b772a…` | identical | — |
| `origin/main`, ahead/behind | `074b772a…`, 0/0 | identical | — |
| digest **A14** (sorted, no separator) | `7379218b…1392d` | identical | **SAME** |
| digest **W1D11** (raw, LF-joined) | `98ae5bdf…2704` | identical | **SAME** |
| staged / unstaged | 22 / 102 | 22 / 102 | — |
| **untracked** | 139 | **140** | **+1** |
| unmerged / tracked | 0 / 2104 | 0 / 2104 | — |
| worktrees / stash | — | 1 / 2 | — |

### 1.1 The material differences — four, one of them significant

**(a) `AP-W1D-3` has been IMPLEMENTED while the ledger still records it `NOT GRANTED`.** `VER`

Three of the four fingerprints that `W1D11 §1` licensed evidence-reuse on have moved, and
they are exactly the files `AP-W1D-3` steps 1–4 target:

| Path | A25 / W1D11 §1 | A26 entry | Delta |
|---|---|---|---|
| `scripts/verify_provisioning.py` | `51cb6d241de3ef437b662241379f8985f7d5a51e` | `8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2` | **CHANGED** |
| `scripts/verify_schema_drift.py` | `f78b35a52552967d469a7cc7d33de92db0a65e3b` | `f853c1482ca295440c78e4f25a8989438fab041e` | **CHANGED** |
| `tests/test_snapshot_dimension_invariants.py` | `bcb4f479fca2365cca2be61aae8961449e03beb9` | `5106dd5916424c9b795a5b393e36afc21c4ba9b4` | **CHANGED** |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | identical | **SAME** (correctly out of scope) |
| `tests/test_drift_classification.py` | **absent** | **present**, 665 lines | **NEW** — the +1 untracked |

`scripts/verify_provisioning.py` now exports `WHOLE_ROW`, `COMPARISON_KEY` and
`classify_dimension`, and `tests/test_drift_classification.py` imports all three `VER`.
`tests/__pycache__/test_drift_classification.cpython-312-pytest-9.1.1.pyc` exists, so the
file has been executed by pytest.

The implementing session wrote `staging/PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md`
(15,509 B, 13:34), titled *"Authorized Classifier Implementation"*, reporting a 15-check
pre-edit gate that all passed — including the three pre-edit blobs above, which match A25's
recorded values exactly. Its work is careful and its scope discipline is verifiable:
`verify_production_schema.py` byte-identical, snapshot not regenerated, second change
(`diff_snapshots` / `check_drift`) **NOT STARTED** pending D5.

**But the authoritative register has not moved** `VER`:
`staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md` lines 800–804 still read **`NOT GRANTED`**
for `AP-W1D-1` through `AP-W1D-5`, `AP-W1D-3` included at line 802.

**I am not adjudicating this, and I am not recording a grant.** The observable facts are: the
implementation exists and passes; the register says not granted; and the W1D-13 report cites no
repository artifact as its grant. The most likely benign explanation is that the grant was
given in that session's own prompt — which is **not a repository record** and therefore cannot
be verified here, and leaves ledger line 802 factually stale. That inconsistency is itself the
decision item (**D-A26-1**).

**(b) `backend_app/backend/exchange_reconciliation.py` is now ` M`** `VER` — blob
`d0603bac8d2acd7f4aa12d93943430e3db6c5591`. A25 did not record a fingerprint for it. **The
guards still hold regardless** — reconfirmed by execution, §4.

**(c) Two new reports** — `PHASE11B_W1D13_CLASSIFIER_IMPLEMENTATION_REPORT.md` and
`PHASE11B_D8_W1B_EVIDENCE_AND_PARALLEL_TRACKS.md` (27,604 B, 13:14).

**(d) Nothing else moved.** PR #6 head `f5b99e57…`, OPEN, `reviewDecision: ""`, 0 reviews,
0 labels `VER`. `subscription_engine.py` still ` M` `256f0399…`, still uncommitted.
`tests/exchange_sandbox/harness.py` **clean**, `b95c0cbba07a50cb9069d320e8aa669f5cd5afc5` —
so Workstream B's established variable set needs no re-derivation.

**(e) Credential hygiene — checked by status only, no content read** `VER`:
`exchange_keys.json`, `.env` and `.env.corrupt.backup` all exist, all **untracked** and all
**gitignored**. No credential file is tracked. Zero `AERORA_*`/`*SANDBOX*` variables are set in
this shell, so the sandbox suite would skip with its `MISSING EVIDENCE` message.

---

## 2. Decision table

`UNIDENTIFIED` = no authoritative record names a person. Per A25, **0 of 9 areas have an
individual owner**; no `CODEOWNERS` exists; `git log --all` carries one author identity over
464 commits. None of that is authority evidence and none of it is used as such here.

| # | Item | Current state | Recorded owner role | Authorized decision-maker | Exact blocker | Minimum next action |
|---|---|---|---|---|---|---|
| **D-A26-1** | **Reconcile `AP-W1D-3` ledger vs reality** | **Implementation COMPLETE and passing; ledger says `NOT GRANTED`** | `Code owner` (ledger L802) | **UNIDENTIFIED** | The register contradicts the tree. Only the grantor can say which is right | A one-line reply: *"AP-W1D-3 was granted on \<date\> — update L802"*, **or** *"it was not granted"*. Until then L802 must not be edited by anyone else |
| **AP-W1D-1** | `NOT GRANTED` | `DB owner (read access)` | **UNIDENTIFIED** | **Two** blockers, not one — see §3 | Identify the DB owner **and** resolve which database 022–025 were applied to |
| **D-A26-2** | **Sandbox provisioning ownership** | **No owner and no owner role recorded anywhere** | **none exists** | **UNIDENTIFIED** | Nothing in the repo names who procures testnet credentials | Authorize adding one row to an existing register — §5. **I will not invent an owner** |
| **D-A25-C1** | `UNRESOLVED` | Scorecard owner | **UNIDENTIFIED** | A gate definition nobody has been asked to confirm | Answer one question — §6.1. Costs nothing, may release 0.10 |
| **D-A25-C2** | `UNRESOLVED` | "reconciliation owner" + a `main.py` coordinator | **UNIDENTIFIED** | 4 operational prerequisites + a passing guard test | Do **not** wire. Answer C1 first — §6.2 |
| **AP-W1D-2/4/5** | `NOT GRANTED` | `DB owner` / `DB owner + code owner` / `Coordinator + DB owner` | **UNIDENTIFIED** | Same missing role as AP-W1D-1 | Blocked behind AP-W1D-1 |
| **D1–D5** (W1D) | `UNRESOLVED`, **no register entry** | `Code owner` (+ production-audit stakeholder for D5) | **UNIDENTIFIED** | D1/D2 now **implemented** ahead of their own decisions; D5 gates the second change | Record D1–D5 in the ledger, then decide — §7 |
| **Quota verification** | Fix **implemented, never executed by CI** | `UNIDENTIFIED` (A25 A-1/A-4) | **UNIDENTIFIED** | Fix is uncommitted; no real Redis | §8 — do not call it verified |

---

## 3. Workstream A — `AP-W1D-1`, and a prerequisite the ledger does not surface

**Exact evidence and approval required** (ledger §2.7 Phase 1, lines 340–345, verbatim scope):

> **Phase 1 — verify completeness (read-only, no approval beyond DB read access)**
> 1. Build the per-statement predicate set for 022–025 from the files (§2.6).
> 2. Evaluate them under an enforced read-only session — `set_session(readonly=True, autocommit=True)`
>    plus a `SHOW default_transaction_read_only` readback. *"the `autocommit=True` is load-bearing on psycopg2"*

**Why it matters** — `W1D-04` (**P1**): completeness evidence for 022–025 rests on **one marker
per migration**, and a single marker cannot distinguish fully applied from partially applied.
*"No claim that these migrations are fully applied is currently supported — including W1C's."*

**Scale of the predicate set** (ledger §2.2/§2.3): 022 = **2** statements, 023 = **6**,
024 = **11**; all four migrations classified **CONDITIONAL**, every one containing `DO $$`
blocks — which is precisely why a marker-based check is insufficient.

**Can the `DB owner (read access)` role be mapped to an authorized person through an existing
authoritative record? No.** `VER` No `CODEOWNERS`; the ledger names only the role; A25 established
0 of 9 areas have an individual owner. **`UNIDENTIFIED`.**

### 3.1 The unstated prerequisite

The ledger markets AP-W1D-1 as *"Second smallest, read-only… and writes nothing"* (L814–816).
True for effort. But it omits a hard prerequisite: **which database would be read?**

- `W1D-01` (**P2**, OPEN) records that 022's **applier is not identified** — *"No script in
  `staging/` or `scripts/` references `022_…sql` except W1C's own probe."*
- If the target is **production**, there is no DSN: the scorecard records `prod_dsn_verify`
  `NOT_DONE`, *"no production DSN in any phase"*, and production is Supabase via the Supavisor
  pooler connecting as the table-owner role.
- If the target is the **local `pgdata_r1` cluster**, then `AP-W1D-5` (not granted) and
  `W1D-06` (**P1**: `staging/apply_migration.py:61` hardcodes `staging/pg_dsn.txt` with no
  override, and against an isolated cluster *"would apply DDL to the shared `pgdata_r1`"`) both
  bear on it.

**So AP-W1D-1 has two blockers, not one: an unidentified `DB owner`, and an unresolved target
database identity.** Granting the role alone would not make it executable. Recording this so
the next session does not discover it mid-flight.

**Not done, per the brief:** no database operation, no PostgreSQL install, no role creation, no
DSN access, no approval claimed.

---

## 4. Workstream C — blockers reconfirmed by execution

`backend_app/main.py` is **unchanged** `VER` (`a3849d4acd7afcf4e53054fc85b16c786776eada`). It was
not edited. `exchange_reconciliation` is **not** wired.

Ran this phase `VER` — `pytest tests/test_p10d_exchange_reconciliation_fails_safely.py
tests/test_exchange_reconciliation_startup_assessment.py -q -p no:cacheprovider` →
**12 passed, 7 warnings in 82.25s**.

| Blocker | State | Basis |
|---|---|---|
| `test_the_service_is_not_registered_in_the_application_lifespan` | **PASSING — still a hard guard** | `VER` executed. Wiring `main.py` would turn it red |
| `test_the_service_is_not_started_anywhere` | **PASSING** | `VER` executed |
| Duplicate-worker / leader election | **UNRESOLVED** | `--workers ${WORKERS:-2}`, no leader election. `main.py` has no leader-election code (the only `elect` hit is `"select"` in a comment at L265) |
| Tenant enumeration | **UNRESOLVED** | loop is per-tenant; nothing enumerates tenants |
| Ghost-order idempotency | **UNRESOLVED** | named by the guard docstring |
| `_reconciliation_task` lifecycle | **DEFECTIVE** | set to `None` at L111, **never assigned**; `stop_reconciliation_loop`'s cancel branch (L639-640) is dead code; `start_reconciliation_loop` awaits `while self._running` (L609) so it would hang boot |
| Shared lifespan conflict | **ACTIVE** | three workstreams contend: WS-C pool drain, RECON-01, OBS-01 |

**Note:** `exchange_reconciliation.py` changed (§1.1b) and the guards still pass, which confirms
the edit did not wire it — but it also means the fail-safe behaviour the guards assert is now
being asserted against **modified** code. Worth a glance from whoever owns that change; it is
not a blocker.

---

## 5. Workstream B — where the provisioning owner should be recorded

Established variable set **reused unchanged** (harness clean, `b95c0cbb…`). Binance and Bybit
need **two** variables each; **only OKX needs a passphrase**. The stale
`staging/p11b_prerequisites.json` listing `AERORA_SANDBOX_BINANCE_PASSPHRASE` was **not** used.
**No credential value was read, printed, copied, committed or transmitted.**

**An authoritative place already exists.** `staging/PHASE11B_A3_BLOCKER_REGISTER.md` §3 has the
schema and precedent:

```
| ID | Sev | Component | Finding | Evidence | Acceptance test | Owner | Status |
```

Its existing rows carry **role** values in that column — `SRE + coordinator (shared workflow)`
for `A3-01`, `Billing owner` for `C-1`. It is designed to hold exactly this. By contrast
`PHASE11A_LAUNCH_GATE_REGISTER.md` §A (`| Gate | Sev | State | Evidence | Remediation |`) has
**no owner column**, though it does already carry the `PAPER-SANDBOX-E2E` gate with its closure
criterion.

### 5.1 The proposed change — smallest auditable form, NOT APPLIED

One row appended to `PHASE11B_A3_BLOCKER_REGISTER.md` §3:

| ID | Sev | Component | Finding | Evidence | Acceptance test | Owner | Status |
|---|---|---|---|---|---|---|---|
| `A26-01` | **P1** | exchange / launch gate | Sandbox credential provisioning has **no owner and no owner role** recorded anywhere, while `paper_sandbox_e2e_evidence` carries **0.15** — the largest unmet paper-track weight. Harness, gate and fail-closed endpoint checks are complete; only the credential is missing | `PHASE11B_A25_OWNER_MAP.md` A-9 §3; harness `GATE_ENV` L30, `CREDENTIAL_TEMPLATE` L58-62 | `test_sandbox_order_lifecycle.py` reaches `SANDBOX_ORDER_FLOW_VERIFIED` for ≥1 venue | **`<TO BE NAMED>`** | OPEN |

**I did not apply it, for two reasons:**

1. **I have no owner to record.** The brief forbids inventing one, and the `Owner` cell is the
   entire point of the row. Writing `UNIDENTIFIED` there adds a row that says nothing the A25
   owner map does not already say more precisely.
2. **`PHASE11B_A3_BLOCKER_REGISTER.md` is another session's report and its authorized writer is
   not established.** Appending to it would be rewriting a shared register without permission.

**Who must authorize:** the **coordinator** — the role A3 §3 already uses for shared-surface
items (`SRE + coordinator`). The decision request is one line: *"A26-01 is owned by \<role or
person\>; add the row."*

**Not done:** no live exchange test, no credential read. Any future run must preserve the
harness's four fail-closed layers (`SANDBOX`-token names, `PRODUCTION_CREDENTIAL_NAMES`
refusal, measured endpoint departure, `_assert_not_production` on the connected client) and use
explicitly authorized testnet/demo credentials only.

---

## 6. `D-A25-C1` and `D-A25-C2`, presented separately

### 6.1 `D-A25-C1` — what does `recon_operational_for_paper` (0.10) actually require?

| | |
|---|---|
| **Decision-maker role** | **Scorecard owner** — author of `PHASE11A_READINESS_SCORECARD.json` |
| **Identified person** | **UNIDENTIFIED** |
| **Minimum evidence needed** | None. This is a definition question, answerable from the gate's intent |
| **Cost** | Zero. No code, no CI, no database |

The recorded evidence string: *"ExchangeReconciliationService not started; dangerous paths
disarmed and watchdog fail-safe, but **venue** reconciliation is not operational."*

| Option | Consequence |
|---|---|
| **1 — venue reconciliation** | Then the gate is **not satisfiable on a paper-only launch**: the service fetches from a venue, paper has no venue order flow, and with no exchange client it correctly **raises** every cycle (L227-232: *"'I cannot see the exchange' and 'the exchange is empty' must not be the same value"*). Honest outcome: move the gate to the live track — which changes the paper denominator and is a scorecard-owner action |
| **2 — internal execution-record reconciliation** | `OrderWatchdog` is already wired (`main.py:432`, interval 30 s / stale 60 s / alert 300 s) and *"already runs a reconciliation loop over the same `execution_records` rows"*. The 0.10 could close on **evidence**, not new code. Prerequisite: close watchdog defect **W-1b** |
| **3 — leave unresolved** | 0.10 stays blocked; `D-A25-C2` stays blocked behind it |

**Recommended: answer this before anything else in Workstream C.** `PROJ` Option 2 is the only
path to the 0.10 that does not require defeating a passing guard test. I am not asserting
which reading is correct.

### 6.2 `D-A25-C2` — authorize reconciliation wiring?

| | |
|---|---|
| **Decision-maker roles** | **"reconciliation owner"** (role recorded at `EXCHANGE_SANDBOX_CERTIFICATION.md:431`) **+ a `main.py` coordinator** |
| **Identified person** | **UNIDENTIFIED** for both |
| **Minimum evidence needed** | Leader-election design; a tenant-enumeration source; a ghost-order idempotency key; a working stop path; and an explicit decision on overlapping `OrderWatchdog` |
| **Options** | **(a)** Defer pending C1 — **recommended**. **(b)** Authorize the full prerequisite set as its own scoped task, accepting the `main.py` three-way conflict. **(c)** Wire it now — **rejected**: turns a passing guard red, runs N duplicate reconcilers, and inherits a non-functional stop path |

**Do not wire while the guard and the four operational prerequisites are unresolved.** Nothing
was wired.

---

## 7. Workstream D — classifier

| Item | State | Evidence class |
|---|---|---|
| Classifier implementation exists | **YES** — 4 files, 665-line new test file | `VER` |
| `tests/test_drift_classification.py` | **76 passed in 4.50s** | **`VER` — run by me this phase, independently** |
| `AP-W1D-3` in the ledger | **`NOT GRANTED`** (L802, unchanged) | `VER` |
| `D1` (ambiguous = hard failure) / `D2` (reorder = unchanged) | reported implemented | `REC` W1D-13 §8 |
| Second change (`diff_snapshots`, `check_drift`) | **NOT STARTED** — D5 outstanding | `REC` |
| 48 existing guard cases / mutation probe 12-12-0 / five-file group 103-12-1 | reported met | **`REC` — I did not re-run these** |
| Key uniqueness | **unverified for all 16 dimensions** | `REC` — needs AP-W1D-1/5, both NOT GRANTED |
| `W1C-02` | **OPEN** | `REC` |

I verified the one claim that was cheapest to verify and most load-bearing (the new test file
passes). I did **not** re-run the mutation probe or the five-file group; those remain inherited
claims. Note W1D-13 reported its group run as `99 passed / 17 skipped` first and
`103 / 12 / 1` on a re-run, attributing the difference to a transient concurrent PostgreSQL
lifecycle — it reported both runs rather than only the clean one, which is the right call.

**`D1`–`D5` are now implemented ahead of their own unresolved decisions.** W1D11 §2.2 recorded
that D1–D5 *"have never been put to an owner in a tracked register."* They are now embodied in
code. Folding them into the ledger is part of **D-A26-1**.

---

## 8. Workstream D — quota, and the distinction preserved

| Claim | Status |
|---|---|
| Quota **clamp** (`settled = min(max(reverted, 0), effective)`) | **IMPLEMENTED, NOT VERIFIED** |
| Quota **fail-open guard** (`if amount > 0 and new_usage < amount`) | **IMPLEMENTED, NOT VERIFIED** |
| **F1 slot guard** | **IMPLEMENTED, NOT VERIFIED** |

Reconfirmed this phase `VER`: `subscription_engine.py` is still ` M`, blob
`256f03994b395d383fc49dd5b87a456ba68d99cb`, **uncommitted**; `HEAD` is unchanged; PR #6's head
is still `f5b99e57…`. A25 proved the engine blob inside that commit
(`edc0c9e9c4dac9f8b662a508cfba9fc405cc9dc0`) is **identical to HEAD** and that the commit
touched only three files, none of them the engine.

> **Therefore no CI run has ever executed the fixed quota implementation, and nothing in this
> phase changed that.** None of the three items above may be described as verified.

**Smallest next step that advances quota readiness safely:** none is available without either a
commit (no owner, no authorization) or a real Redis (absent: 6379 closed, no docker). The
honest next step is a **decision**, not a verification — identify an owner for
`subscription_engine.py` able to authorize committing the fix to PR #6. Recorded, not acted on.

Not done: no CI rerun, no workflow dispatch, no workflow edit, no quota-code edit.

---

## 9. Files changed, tests run, preservation

**Source, test, migration, workflow, register and scorecard files changed: NONE.**
No register was edited, because for every candidate the authorized writer is **not established**
— which is the §6 condition for returning a decision sheet instead.

**Artifacts created — all new, all untracked, all under `staging/`:**

```
PHASE11B_A26_DECISION_SHEET.md    this file
p11b26_baseline.py / .txt         entry baseline, both digest methods documented inline
```

**Tests executed this phase — three files, 88 tests, all passing:**

| Command | Result |
|---|---|
| `pytest tests/test_drift_classification.py -q -p no:cacheprovider` | **76 passed in 4.50s** |
| `pytest tests/test_p10d_exchange_reconciliation_fails_safely.py tests/test_exchange_reconciliation_startup_assessment.py -q -p no:cacheprovider` | **12 passed, 7 warnings in 82.25s** |

All three files are pure-data or static source assertions — no database, no Redis, no network,
no venue. `-p no:cacheprovider` on every run, so no cache was written. These characterize
**existing** code (another session's, in the classifier's case); they verify nothing I wrote,
because I wrote no code.

**Preservation:** `HEAD` `074b772a…` unchanged; both index digests **SAME**; 22 staged / 102
unstaged / 0 unmerged / 2104 tracked unchanged. Untracked moved 139 → 140, accounted for
exactly by another session's `tests/test_drift_classification.py`. No reset, clean, stash,
branch switch, index mutation, commit, push or CI dispatch.

---

## 10. Confirmations

This phase did **not**: edit any source, test, migration, workflow, register, scorecard or
existing report; wire `exchange_reconciliation` into `main.py`; create or modify an approval
record; stage, commit, push, reset, clean, stash or switch branches; mutate the shared Git
index; trigger, dispatch or re-run CI; connect to PostgreSQL, Redis, any exchange or any
payment endpoint; install PostgreSQL or create a database role; access a DSN; read, print, copy
or transmit any credential value; or run a live or sandbox exchange test.

| Item | Status |
|---|---|
| **Live trading** | **OFF / NOT CERTIFIED** — unchanged |
| Paper track | **53%** — unchanged, unrevised |
| Live / Production tracks | **0% / 0%** — unchanged |
| Approvals granted or recorded this phase | **0** |
| Owners newly identified as individuals | **0** |
| Registers edited | **0** |

Nothing in this document constitutes an approval, and no owner was contacted.
