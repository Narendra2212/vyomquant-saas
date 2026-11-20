# Phase 11B / A27 — Authorization Reconciliation, Classifier Readiness, Independent Progress

**Date:** 2026-10-11 · **Launch target:** 2026-10-25 · **Scope:** paper/retail only
**Destination verified ABSENT before writing.** No existing report, register or authoritative record was edited or overwritten.
**Continues:** `staging/PHASE11B_A26_DECISION_SHEET.md`

> **LIVE TRADING: OFF / NOT CERTIFIED.** **PostgreSQL integration: UNVERIFIED.**
> **TENANT-01 / QUOTA-01: OPEN.** Paper track **53%**, unchanged and unrevised.
> **No approval was created, implied, or recorded on anyone's behalf.**

## Headline

**D-A26-1 is RESOLVED: `AP-W1D-3` was genuinely GRANTED.** The grant is recorded in an
authoritative decision register A26 did not read —
`staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md`. The implementation is **authorized and
within scope**. What remains unauthorized is *editing the ledger* — a different permission,
still unheld.

**Two prior conclusions are corrected by this phase** (§4.2, §7.2): the AP-W1D-1 target is
**local, not production**, and `OrderWatchdog` **cannot** satisfy an "internal reconciliation"
reading of the paper gate, because its reconciliation loop is venue-keyed.

---

## 1. Baseline — verified entry and exit

Digest serialisations documented inline in `staging/p11b27_baseline.py` (A25 §9.1: a bare
digest is not a portable invariant).

| Measurement | A26 | A27 entry | A27 exit | Verdict |
|---|---|---|---|---|
| branch / `HEAD` | `main` / `074b772a…` | identical | identical | **SAME** |
| `origin/main`, ahead/behind | `074b772a…`, 0/0 | identical | identical | **SAME** |
| digest **A14** | `7379218b…1392d` | identical | identical | **SAME** |
| digest **W1D11** | `98ae5bdf…2704` | identical | identical | **SAME** |
| staged / unstaged | 22 / 102 | 22 / 102 | 22 / 102 | **SAME** |
| untracked / unmerged | 140 / 0 | 140 / 0 | 140 / 0 | **SAME** |
| tracked / worktrees / stash | 2104 / 1 / 2 | identical | identical | **SAME** |

**All five classifier fingerprints are byte-identical to A26** — the W1D-13 implementation has
not moved, and no concurrent session touched it during this phase:

| Path | Blob | State |
|---|---|---|
| `scripts/verify_provisioning.py` | `8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2` | **SAME** |
| `scripts/verify_schema_drift.py` | `f853c1482ca295440c78e4f25a8989438fab041e` | **SAME** |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | **SAME — still the original** |
| `tests/test_snapshot_dimension_invariants.py` | `5106dd5916424c9b795a5b393e36afc21c4ba9b4` | **SAME** |
| `tests/test_drift_classification.py` | `fcffbe8e01367aa7f99fffe2677267821fb8424d` | **SAME** |

Also unchanged: `main.py` `a3849d4a…`, `subscription_engine.py` `256f0399…`,
`order_watchdog.py` `a670923…`, `exchange_reconciliation.py` `d0603bac…`,
`harness.py` `b95c0cbb…` (clean).

### 1.1 Concurrent work — three records new since A26, none conflicting

| Record | Size | Time | Bearing |
|---|---|---|---|
| `PHASE11B_D9_DECISION_PACKET.md` | 12,489 B | 13:39 | adjacent line |
| **`PHASE11B_W1D14_D5_SIGNOFF_GATE_REPORT.md`** | 12,869 B | 13:44 | **D5 — §3** |
| `PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` | 18,143 B | 14:22 | Redis governance; **no new grants** |

Plus `staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` — **pre-existing, and the record
A26 missed.** No file changed during this phase, so nothing here is attributable to A27.

### 1.2 Credential hygiene — status only, no content read

`exchange_keys.json`, `.env`, `.env.corrupt.backup`, **`staging/pg_dsn.txt`** — all exist, all
**untracked**, all **gitignored**. No credential file is tracked. **None was opened.** Zero
`AERORA_*`/`*SANDBOX*` variables set in this shell.

---

## 2. D-A26-1 — AP-W1D-3 authorization: **GRANT FOUND**

### 2.1 The authoritative record

`staging/PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md`, which states of itself: *"**This
document is the decision register entry.**"*

| # | Decision | State | Issued by | Condition |
|---|---|---|---|---|
| **AP-W1D-3** | Authorize the four-file scope in W1D-11 §5.1. **No other files are authorized.** | **GRANTED** | Code owner | none |
| **D1** | Ambiguous key collisions are hard failures with distinct labels | **GRANTED** | Code owner | none |
| **D2** | Reordered identical rows classified **unchanged** | **GRANTED** | Code owner | none |
| **D3** | **Option 1** — one shared classifier, three renderers, two reviewable changes | **GRANTED** | Code owner | none |
| **D4** | Classifier in `verify_provisioning.py`, **not** `verify_schema_drift.py` | **GRANTED** | Code owner | none |
| **D5** | Preserve `check_drift()`'s three-tier severity routing exactly | **CONDITIONALLY GRANTED — condition NOT satisfied** | Code owner | production-audit sign-off, **not given** |

**Tally: 5 GRANTED, 1 CONDITIONALLY GRANTED, 0 REJECTED.**

**Provenance, verbatim (§2):** *"the grant in §1 was issued by the user in this session, acting
in the **Code owner** role named at `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802`."* The
same section states plainly that it **could not** identify the Code owner as a named individual
and **did not invent one**.

### 2.2 Scope conformance — verified, not assumed

| Authorized (W1D-12 §1.1) | Changed by W1D-13 | Verdict |
|---|---|---|
| `scripts/verify_provisioning.py` — add `WHOLE_ROW`, `COMPARISON_KEY`, `classify_dimension()` | changed | **IN SCOPE** |
| `scripts/verify_schema_drift.py` — route `compare()` through the classifier | changed | **IN SCOPE** |
| `tests/test_drift_classification.py` — new | created | **IN SCOPE** |
| `tests/test_snapshot_dimension_invariants.py` — import the production key map | changed | **IN SCOPE** |
| **Explicitly NOT authorized:** `verify_production_schema.py`, `provision_database.py`, `staging/apply_migration.py`, migrations, snapshots, shared config | **untouched** | **RESPECTED** |

`VER` `scripts/verify_production_schema.py` is byte-identical at
`6e194f43a832d1f9cb0f99efd88dd204c5b3c444` — the excluded file was not opened. Exactly four
files changed; the fifth is provably untouched.

### 2.3 The three authorizations, kept distinct

The brief requires these not be conflated. They are not interchangeable and only two are held:

| Authorization | State | Basis |
|---|---|---|
| **To implement** the four-file scope | **GRANTED** | W1D-12 §1, AP-W1D-3, Code owner |
| **To reuse prior evidence** (W1D-09/10 analysis) | **GRANTED, conditions met** | W1D-11 §1 licensed reuse on unchanged fingerprints; all four matched at W1D-13's pre-edit gate |
| **To edit the authoritative ledger** | **NOT GRANTED — NOT ESTABLISHED** | W1D-12 confers no register-editing permission. **No record names a writer for `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`** |

### 2.4 Disposition — and my A26 error, stated plainly

**A26 recorded D-A26-1 as unresolved and hypothesised the grant "lives in that session's
prompt, which is not a repository record." That was half wrong.** The grant did originate in a
session prompt, but it **was** preserved into a repository record — `W1D-12` — which I did not
read. A26 searched for grant *language* and for reports *newer than A26*; W1D-12 is neither,
so it was missed. The correct conclusion is **GRANTED**, and A26's "unresolved" should be read
as superseded by this section.

**The ledger lines 800–804 are therefore stale, not authoritative, for AP-W1D-3.** Two further
facts bound this:

- `W1D-14 §1.2`: *"**D5 is not an AP-W1D ledger item at all**; it exists only in the W1D-12
  decision record."* So the ledger was never the complete decision surface.
- `A23E6 §242-246` independently confirms the only Class-A register holds **role values —
  never a person**, and has **no attested writer**.

**I did not edit the ledger, and no one should until a writer is established.** Doing so would
be recording a grant on another role's behalf. Nothing was reverted, deleted, disabled or
extended to make the tree match the ledger.

### 2.5 The smallest actionable decision request

> **D-A27-1 — ledger reconciliation authority.** `AP-W1D-3` is **GRANTED** per
> `PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` §1 (Code owner, 2026-10-11, four-file scope,
> no conditions). `PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md:802` still reads `NOT GRANTED`
> and is stale.
>
> **Required:** (a) confirm the W1D-12 grant is the authoritative record; (b) name who is
> authorized to maintain the ledger; (c) authorize updating **line 802 only** to
> `GRANTED (W1D-12)`, leaving 800/801/803/804 untouched.
>
> **Sufficient reply:** *"W1D-12 is authoritative; \<role/person\> maintains the ledger;
> update line 802 to GRANTED citing W1D-12."*

---

## 3. D5 — current sign-off status and scope

**ABSENT.** Established by `W1D-14` and not re-searched here (the brief forbids repeating the
known regex sweep).

| Item | State |
|---|---|
| D5 in the decision register | **CONDITIONALLY GRANTED, condition open** |
| Production-audit stakeholder sign-off | **ABSENT** — no approval record of any kind |
| Second change (`diff_snapshots`, `check_drift`) | **NOT STARTED** |
| Decision-maker | **production-audit stakeholder** — role named in W1D-12; **no individual attested** |

**Why the condition does not block the first change** (W1D-12 §3.2): `check_drift()` lives in
`verify_production_schema.py`, which AP-W1D-3 **explicitly excludes**. Under D3 Option 1 the
severity routing is preserved **by construction** — the authorized task may not open the file.
D5 gates only the second change. `VER` Confirmed: that file is byte-identical.

**The naming collision, preserved from W1D-14 §1.5 because it is a live trap:** `git log
-i --grep=D5` returns five commits (`72bae29b`, `f5b99e57`, `d07014d3`, `a624ce89`, `49e70da2`)
— **all from the D5′ real-Redis workstream**, nothing to do with classifier D5. **A D5′
real-Redis commit is not D5 production-audit approval.** I treated none of them as sign-off.

Also not treated as sign-off: 124 passing tests; the classifier being correct; the real-snapshot
corroboration; the Code owner's *conditional* "D5 — YES, subject to sign-off"; recommendations;
or silence.

**No modification was made to the production-audit integration.**

---

## 4. W1C-02 — acceptance matrix

### 4.1 The eight required invariants

| # | Invariant | Verdict | Evidence |
|---|---|---|---|
| **1** | Three-tier severity routing preserved | **HELD** | `VER` source `verify_production_schema.py:258-271` — `ADVISORY_DIMENSIONS → f.minor`; 10 named dimensions (`tables`, `columns`, `primary_keys`, `foreign_keys`, `row_level_security`, `policies`, `privileges`, `unique_and_check_constraints`, `functions`, `triggers`) → `f.critical`; `else → f.minor`. **And the file is byte-identical** — preserved by construction |
| **2** | Output row cap remains **eight** | **HELD** | `VER` L246 `missing[:8]`, L249 `len(missing) > 8`, L250 `extra[:8]`, L253 `len(extra) > 8` |
| **3** | Exact `MISSING from production` label | **HELD** | `VER` L247 `MISSING from production : {row}`; sibling L251 `NOT IN this repository  : {row}` |
| **4** | Ambiguous changes remain hard failures (**D1**) | **HELD** | `VER` 76/76 pass incl. collision-with-differing-attributes, `AMBIGUOUS KEY` label, `verdict == "drift"`; declaration faults → `inconclusive` rather than a false whole-row fallback |
| **5** | Reordering remains unchanged (**D2**) | **HELD** | `VER` `test_reordered_identical_rows_are_not_drift`, `test_reordered_whole_row_rows_are_not_drift`, plus the control proving reorder-insensitivity does not swallow a real modification |
| **6** | Existing behaviour preserved for every dimension and comparison key | **HELD for the current tree** | `VER` 48/48 guards now checking the **production** key map; `16 dimensions / 16 declared keys`; `set(COMPARISON_KEY) == set(SNAPSHOT_QUERIES)`; `_norm` preserves `None` (L202-204); `ADVISORY_DIMENSIONS` = `frozenset({"recorded_migrations","alembic_version"})` unchanged. **Limitation in §4.3** |
| **7** | No false equivalence, no suppressed drift | **HELD** | `VER` **mutation probe run by me this phase: 12 applied / 12 bit / 0 silent**, real dicts restored. Plus "no nonzero drift without a subject" and header/detail consistency cases |
| **8** | Affected entry points and regression tests cover the same semantics | **HELD, with one gap** | `VER` five-file group **103 passed / 12 skipped / 1 xfailed**. `compare()` routed and covered; `diff_snapshots()` and `check_drift()` **still carry the whole-tuple diff** — the second change, D5-gated |

### 4.2 The four claims, kept separate

| Claim | Status |
|---|---|
| **Implementation exists** | **YES** — four files, classifier in `verify_provisioning.py` per D4 |
| **Local regression tests pass** | **YES** — `VER` 76 + 48 focused; group 103/12/1; mutation 12/12/0 |
| **Snapshot/file-based behaviour correct** | **YES, narrowly** — `REC` W1D-13 §3.5 self-compared the real captured snapshot; one capture, not a constraint check |
| **Verified against a live PostgreSQL schema** | **NO.** **Zero evidence.** The 12 skips are PostgreSQL-gated and are **not passes** — each names the reason (`default_transaction_read_only` and SQLSTATE 25006 do not exist in SQLite) |

### 4.3 Limitation on independent verification — disclosed

`VER` The pre-change blobs `51cb6d24…`, `f78b35a5…`, `bcb4f479…` are **absent from the object
database** (`git cat-file -e` fails on all three). These files are untracked, so their prior
content was never hashed into the ODB. **A true before/after diff is therefore impossible**,
and W1D-13's claim that `_norm()`, `_as_tuples()`, `diff_snapshots()`, the snapshot format and
the 16 SQL queries are unmodified **cannot be independently verified from git**. I verified the
*current* state satisfies the required properties (invariant 6) and that the behavioural guards
bite (invariant 7); that is the strongest available evidence, and it is not the same thing.

This matters because `verify_production_schema.py` **imports from both modified modules** —
`SNAPSHOT_QUERIES, _norm` from `verify_provisioning` (L101) and `ADVISORY_DIMENSIONS,
_as_tuples, _refuse_transaction_pooler, assert_read_only, redact` from `verify_schema_drift`
(L102-107). So the production audit tool's behaviour depends on the edited files even though
the file itself is untouched. Current values check out; the historical diff does not exist.

### 4.4 W1C-02 disposition

**OPEN.** Remaining scope is exactly two items, and I am not recording a closure:

1. The identical whole-tuple defect persists in `diff_snapshots()`
   (`verify_provisioning.py:507`) and `check_drift()` (`verify_production_schema.py:217`) —
   including the **production audit** tool. That is the second change, D5-gated.
2. No drift run has exercised the new `compare()` against a live schema.

---

## 5. AP-W1D-1 — owner, target, prerequisites

### 5.1 Exact requirement (ledger §2.7 Phase 1)

> 1. Build the per-statement predicate set for 022–025 from the files (§2.6).
> 2. Evaluate them under an enforced read-only session — `set_session(readonly=True,
>    autocommit=True)` plus a `SHOW default_transaction_read_only` readback. *"the
>    `autocommit=True` is load-bearing on psycopg2."*

Scale: 022 = **2** statements, 023 = **6**, 024 = **11**; all four **CONDITIONAL**, every one
containing `DO $$` blocks — which is why `W1D-04` (**P1**) holds that one marker per migration
cannot distinguish fully from partially applied, and that *"no claim that these migrations are
fully applied is currently supported — including W1C's."*

### 5.2 Blocker 1 — authorized database owner: **UNIDENTIFIED**

Who may grant read-only access is recorded only as the role `DB owner (read access)`. No
`CODEOWNERS`/`OWNERS`/`MAINTAINERS`; A25: 0 of 9 areas have an individual owner; 464 commits
under one author identity, which is not authority evidence. `A23E6` reaches the same answer
independently. **Unchanged from A26.**

### 5.3 Blocker 2 — target identity: **substantially narrowed this phase**

A26 recorded this as unresolved and raised production as a possibility. **Source evidence now
shows the target is LOCAL, and production was never it** — established without reading any DSN:

| Evidence | Finding |
|---|---|
| `apply_025.py:13` | hardcoded loopback DSN, host `127.0.0.1`, port `62674`, db `postgres` (no password in the string) |
| `apply_migration.py:61-66` | reads `staging/pg_dsn.txt`, then **refuses** any host not in `127.0.0.1`/`localhost`/`::1`/`""` |
| `apply_remediation.py:13,27` | `from dsn import resolve_dsn`; `psycopg2.connect(resolve_dsn())` |
| `verify_provisioning.py:540-548` | exits on port `6543`; **refuses non-local hosts** without `--i-understand-this-is-remote`; **creates and drops its own** `f"{DB_PREFIX}_{os.getpid()}"` database |
| `verify_production_schema.py:996-997` | production is reachable **only** via an explicit `--dsn`, "Direct connection (5432), not the pooler (6543)" |

**Conclusion:** every identified applier of 022–025 targeted a **local loopback** database; two
of three refuse non-local hosts by construction. The baseline snapshot likewise came from a
local, ephemeral, repo-provisioned database — `verify_provisioning.py` is structurally
incapable of snapshotting production. **AP-W1D-1 is a question about a local database, and
answering it establishes nothing about production.** `prod_dsn_verify` stays `NOT_DONE`
regardless.

### 5.4 The residual ambiguity, and its root cause

`VER` `staging/provisioning_snapshot.json`: **16 top-level keys, all dimensions, zero
underscore-prefixed keys — `_provenance` is ABSENT.** Captured 2026-10-06 20:14:20, 396,325 B.

**The snapshot does not record which database produced it.** That is precisely the gap
`AP-W1D-4` ("Add the `_provenance` envelope and recapture the snapshot") would close — and
AP-W1D-4 is **NOT GRANTED**. So blocker 2 and AP-W1D-4 are **the same problem**, which no prior
report connected.

Worse for executability: `apply_025.py` used port **62674** while A3 recorded the reachable
bootstrap cluster at **51805**. Different ephemeral ports mean different clusters. **The
specific local database that received 022–025 was ephemeral and is probably gone** — so
AP-W1D-1 may be **unexecutable even with a grant and even locally**, because its subject no
longer exists. That is a materially different prerequisite than "get DB read access".

### 5.5 Hazards bearing on a proposed read-only verification

- `W1D-06` (**P1**): `staging/apply_migration.py:61` hardcodes `staging/pg_dsn.txt` with no
  override; pointed at an isolated cluster it would apply DDL to the shared `pgdata_r1`.
- Mitigation already present in the read-only tools: `_refuse_transaction_pooler` exits on port
  6543 because session settings do not survive the pooler, and `assert_read_only` reads back
  `default_transaction_read_only` rather than assuming it.

**Not done:** no DSN read, no PostgreSQL connection, no role change, no migration execution, no
production access.

### 5.6 Decision request

> **AP-W1D-1** needs **three** things, not one: (a) name the `DB owner (read access)`;
> (b) identify **which** database 022–025 were applied to, given every applier was loopback and
> the ports were ephemeral; (c) if that cluster is gone, decide whether re-provisioning plus
> re-application is in scope — which is a **different and larger** request than read-only
> verification, and would need `AP-W1D-5`.

---

## 6. Independent progress

### 6.1 Track A — sandbox provisioning ownership

No change in authority. `A23E6 §242-246` independently corroborates A26's register analysis:
`PHASE11B_A3_BLOCKER_REGISTER.md` §3 has an `Owner` column designed for this (values are roles:
`SRE + coordinator`, `Billing owner`); `PHASE11A_LAUNCH_GATE_REGISTER.md` §A has **no owner
column**; the Class-A ledger holds roles only.

`VER` `tests/exchange_sandbox/harness.py` **clean**, `b95c0cbba07a50cb9069d320e8aa669f5cd5afc5`
— the established variable set needs no re-derivation. Binance and Bybit need **two** variables
each; **only OKX needs a passphrase**. The stale `p11b_prerequisites.json` passphrase entry was
not used. **No credential value read, printed, copied, committed or transmitted. No venue
connection.**

**The A26-01 row remains a handoff only** — not inserted. I have no owner to record, inventing
one is forbidden, and the register has no attested writer. One new systemic note from
`A23E6 §5`: **"Delivery route for this packet: NONE documented."** There is no established
channel for delivering any of these decision requests — which is plausibly why eight
workstreams are all parked on unmade decisions.

### 6.2 Track B — D-A25-C1, and a correction to my own projection

**The scorecard does not unambiguously establish the definition.** Its evidence string
describes the *gap* (*"venue reconciliation is not operational"*), not the *requirement*. So the
one-question decision request stands.

**But option 2 is weaker than A25 and A26 projected, and I am correcting that.** A25/A26
suggested that if the gate meant internal execution-record reconciliation, the already-wired
`OrderWatchdog` might satisfy it. Source says otherwise `VER`:

| `order_watchdog.py` | Finding |
|---|---|
| `_find_open_orders()` L369-371 | requires `ExecutionRecordModel.order_id.isnot(None)` — *"Must have **exchange order ID**"* |
| `_reconcile_open_orders()` L296 | `await executor.get_order_status(exchange_order_id=order.order_id, symbol=...)` — **a venue call** |
| L313-316 | logs `Exchange status=`, `Exchange filled=` |

**`OrderWatchdog`'s reconciliation loop is itself a venue reconciler.** With live trading OFF,
paper rows carry no exchange order id, so `_find_open_orders()` returns nothing and the loop is
a **no-op for paper**. The only genuinely internal path is
`recover_unsubmitted_executions()` / `classify_stale_executions` (TRADE-07) — *"EXECUTING rows
with NO venue order id, which `_check_stale_orders` filters out by design"*, 900 s threshold.
That is **crash recovery of unsubmitted rows, not reconciliation of fills.**

**So D-A25-C1 cannot be resolved cheaply by pointing at `OrderWatchdog`.** Either reading leaves
real work. The decision is still free to make and still the highest-leverage item, but the
projected shortcut should not be relied on.

One favourable contrast, relevant to A26 §4: `OrderWatchdog.start()` spawns its loop with
`asyncio.create_task(self._reconciliation_loop())` (L191) and **cancels it properly** on
`CancelledError` (L219-224). `ExchangeReconciliationService` never assigns
`_reconciliation_task` at all. The wired service has the correct lifecycle; the unwired one does
not — reinforcing A26's "do not wire" finding.

**Nothing was wired. No fail-safe test weakened. No duplicate reconciler started.**

### 6.3 Track C — quota readiness

`VER` `subscription_engine.py` still ` M`, blob `256f03994b395d383fc49dd5b87a456ba68d99cb`,
**uncommitted**; `HEAD` unchanged; PR #6 head still `f5b99e57…`, OPEN, `reviewDecision: ""`,
0 reviews, 0 labels (A26, re-confirmed by unchanged HEAD).

| Item | Status |
|---|---|
| Quota clamp `settled = min(max(reverted, 0), effective)` | **IMPLEMENTED, NOT VERIFIED** |
| Fail-open guard `if amount > 0 and new_usage < amount` | **IMPLEMENTED, NOT VERIFIED** |
| **F1** slot guard | **IMPLEMENTED, NOT VERIFIED** |

A25 proved the engine blob inside the CI commit (`edc0c9e9…`) is identical to HEAD and that the
commit touched only three files, none of them the engine. **No CI run has ever executed the
fixed quota implementation, and nothing this phase changed that.**

**Minimum authorized route to test the actual implementation:** none exists today. It requires
either committing the fix (no owner, no authorization) or a real Redis (6379 closed, no docker).
The next step is a **decision** — identify an owner for `subscription_engine.py` able to
authorize committing to PR #6 — not a verification. **No commit, no CI dispatch, no Redis
install or start, no workflow change.**

### 6.4 Track D — regression readiness

| Scope | Trigger | Status |
|---|---|---|
| `test_drift_classification.py` (76) | any classifier change | `VER` green |
| `test_snapshot_dimension_invariants.py` (48) | key map / SQL change | `VER` green |
| Mutation probe | guard change | `VER` 12/12/0 — **runs alone**, perturbs module dicts |
| Five-file group (103/12/1) | any of the four files | `VER` green |
| **PostgreSQL-gated (12 skips)** | the second change, or any live claim | **CANNOT RUN** — no cluster |
| Real-Redis quota | quota commit | **CANNOT RUN** — no Redis |
| Venue sandbox | credential arrival | **CANNOT RUN** — no credential |

The bottom three are deliberately kept separate from the local pure-data lane and **no skip is
counted as a pass.**

---

## 7. Implementation and verification

### 7.1 Files changed: **NONE**

No source, test, migration, workflow, register, ledger, scorecard or existing report was
modified. **Phase 5 was not entered.** The found grant (`AP-W1D-3`) was already fully executed
by W1D-13 within its authorized scope, so it licensed no further edit: per the brief, approval
for the first classifier change is **not** permission for the second change, a ledger edit,
database access, quota commits or exchange wiring.

Artifacts created — all new, all untracked, all under `staging/`:

```
PHASE11B_A27_RECONCILIATION_AND_READINESS_REPORT.md   this report
p11b27_baseline.py / .txt                             entry+exit baseline, digest methods inline
```

### 7.2 Tests actually executed — three commands, all passing

| Command | Result |
|---|---|
| `python staging/probe_p11b_w1d05_guard_mutation.py` | **12 applied / 12 bit / 0 silent**; real dicts restored; 16 dimensions / 16 declared keys |
| `pytest` five-file group `-q --no-header -rs -p no:cacheprovider` | **103 passed, 12 skipped, 1 xfailed** in 48.18s |
| *(A26, same tree)* `pytest test_drift_classification.py` | **76 passed** in 4.50s |

The mutation probe is **new evidence**: W1D-14 deliberately skipped it as "re-measuring
unchanged files", so no session had run it under the current tree. It is the direct test of
invariant 7. The group run likewise had not been re-run since W1D-13.

**Deliberately not re-run:** the 124 focused cases (W1D-14 ran them at these exact
fingerprints). **Unverified claims, explicitly:** the historical no-change diff of `_norm`,
`_as_tuples`, `diff_snapshots`, snapshot format and the 16 queries (§4.3 — the blobs do not
exist); any live-PostgreSQL behaviour; any key-uniqueness claim (**0 of 16**).

### 7.3 Preservation

Entry equals exit on every measurement in §1. No `reset`, `clean`, `stash`, `checkout`, branch
switch, stage, commit, push, index mutation, shared-object write, CI dispatch, database
connection, Redis connection, network call, package install or server start. **Concurrent
drift during this phase: none** — all five classifier fingerprints and all other tracked
fingerprints identical at entry and exit.

---

## 8. Next action

> **Authorize ledger reconciliation (D-A27-1, §2.5).** It is the only item where the
> substantive decision is **already made** and merely unrecorded. One reply naming the ledger's
> maintainer and authorizing a single-line edit converts a documented contradiction into a
> correct register — and it unblocks nothing less than the credibility of every other
> `NOT GRANTED` row, since the register is now demonstrably stale in at least one place.

Runner-up, unchanged and still free: **D-A25-C1** (§6.2) — one question to the scorecard owner,
now with the honest caveat that neither answer is cheap.

| Item | Status |
|---|---|
| **D-A26-1 / AP-W1D-3** | **RESOLVED — GRANTED** (W1D-12 §1); ledger stale; ledger edit **unauthorized** |
| **D5** | **sign-off ABSENT**; second change NOT STARTED |
| **W1C-02** | **OPEN** — two items (§4.4) |
| **AP-W1D-1** | **NOT GRANTED**; owner UNIDENTIFIED; target **local**, likely **gone** |
| **TENANT-01 / QUOTA-01** | **OPEN** |
| **PostgreSQL integration** | **UNVERIFIED** |
| **Live trading** | **OFF / NOT CERTIFIED** |
| Registers edited / approvals created | **0 / 0** |

No approval is claimed from implementation, silence, role labels or test results.
