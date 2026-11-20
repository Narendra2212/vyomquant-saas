# Phase 11B-W1D-15 — D5 resolution, W1C-02 acceptance matrix, AP-W1D-1 assessment

**Outcome: NO IMPLEMENTATION. D5 sign-off remains ABSENT and is unobtainable through any in-repo
channel. The second change was not started and no governance record was modified.**

**READ-ONLY** on every production file, test file, register, ledger and existing report. Two files
created, both new, both under `staging/`, both destination-checked ABSENT first. No database, Redis,
exchange, CI, deployment or network call. No DSN read. No credential read.

**This report is differential, not a restatement.** A concurrent session wrote
`staging/PHASE11B_A27_RECONCILIATION_AND_READINESS_REPORT.md` at **14:40, during this phase**, and it
covers D5 status and a W1C-02 matrix. I do not repeat its findings. Sections 1–3 below record what it
does **not** contain, and §2.5 records **two places where my evidence contradicts its disposition**.

| Track | Result |
|---|---|
| **A — D5 sign-off** | **ABSENT.** New: the gating role has **zero occurrences** in the authoritative ledger and originates in **my own W1D-11 §4** as a derived recommendation |
| **B — W1C-02** | **OPEN, but closer than recorded.** Authoritative criteria are **implementation + regression evidence** over **`compare()` only**. **9 of 10** named regression tests delivered; 1 unwritten (measured satisfiable 12/12); 1 skip-gated |
| **C — AP-W1D-1** | **Not an independent path.** Three blockers, and its authoritative scope excludes drift-key uniqueness |
| **D — Preservation** | **PASSED**, entry and exit. 5/5 fingerprints identical. Concurrent activity isolated and named |

---

## 1. D5 — production-audit sign-off

### 1.1 Status: ABSENT

Not re-searched. `W1D-14 §1` established absence across all `.md`/`.txt`/`.json`/`.yml` with narrow
affirmative phrasing, and `A27 §3` independently confirms it. That scan is **adopted and attributed,
not repeated**, per the brief.

### 1.2 New finding — the gating role is not defined in any authoritative register

I searched the only Class-A register, `staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md`, for
`stakeholder|production.audit|sign.off`:

```
No matches found.
```

`VER` The ledger's authorization register (lines 800–804) carries exactly five owner values —
`DB owner (read access)`, `DB owner`, `Code owner`, `DB owner + code owner`,
`Coordinator + DB owner`. **"Production-audit stakeholder" is not among them, and the words
"stakeholder", "production audit" and "sign-off" appear nowhere in the file.**

The role's origin is traceable to a single source — **my own W1D-11 §4**, which states its own basis
in its own header:

> *"Derived from the ledger's owner column and from file ownership evidence. **No individual is named
> anywhere in the repository**… So these are **roles**, not people."*

and then introduces the role as a recommendation:

> **D5** | **Code owner**, with the **production-audit stakeholder** | *`verify_production_schema.py`
> is the production audit tool; its three-tier severity policy… is a product decision about audit
> criticality, not a classification detail*

All eight occurrences of the term across `staging/` are that recommendation or documents downstream
of it (W1D-10, W1D-11, W1D-12, W1D-13, W1D-14, A25 owner map, A25 dashboard, A26). **No document
defines the role, names a holder, or describes how one would be appointed.**

**Why this matters, and it is not a technicality.** The D5 condition is not an external governance
requirement that the project must satisfy. It is a **conditional attached to a role my own prior
report proposed**, which the Code owner then adopted verbatim. That changes the available remedies:
there is no body to petition, because the body was never constituted.

### 1.3 Evidence classified into the four required categories

| Class | Required characteristic | Instances found |
|---|---|---|
| **1 — valid sign-off** | Approval by an **identifiable, authorized** production-audit stakeholder | **ZERO** |
| **2 — role without a holder** | Role named, no holder identified | **All 8 occurrences** (§1.2). Ledger L800–804 likewise: 5 roles, 0 persons |
| **3 — preference / result / session claim** | Conditional preference, test result, or implementation-session assertion | W1D-12 `L24`, `L221` (*"CONDITIONALLY GRANTED — condition NOT satisfied"*); W1D-12 `L69-72` (*"That sign-off has not been given"*); W1D-13 §; A26 §7; the Code owner's own *"D5 — YES, **subject to** production-audit stakeholder sign-off"* |
| **4 — the unrelated D5′ workstream** | A decision about real-Redis D5′ | 5 commits `72bae29b`, `f5b99e57`, `d07014d3`, `a624ce89`, `49e70da2`; **plus the filename `staging/PHASE11B_A10_D5_APPROVAL_READINESS.md`**, which is the Redis line despite reading "D5 APPROVAL" |

**Not treated as sign-off:** 124 passing tests; the classifier being correct; the real-snapshot
corroboration; A26's independent 76-passed re-run; the Code owner's conditional YES; any
recommendation of mine; the target file being clean; or silence.

**The class-4 trap is live.** `PHASE11B_A10_D5_APPROVAL_READINESS.md` is the most dangerous artifact
in the repository for this question: its name asserts D5 approval readiness and its content concerns
`backend_app/backend/redis_manager.py`. It is **not** classifier D5.

### 1.4 Is there a documented, authorized process to submit the request?

Two different questions, with two different answers. Verified, not assumed.

| Question | Answer | Basis |
|---|---|---|
| Is there a documented way to **prepare** a decision request? | **YES — an established, repeatedly-used precedent** | `A23E6` (a blank-field "response form" explicitly stating *"THIS DOCUMENT IS NOT AN APPROVAL"*); `A26` (*"returning a decision sheet instead"*); `A23D` §6 ("copy-ready approval wording"); `A27 §2.5` |
| Is there a documented, authorized channel to **deliver and approve** it? | **NO** | `A23E6 §5`, verbatim: *"**There is no documented governance channel through which to submit this packet.**"* It checked four registers; the Class-A ledger holds roles never persons; `A3_BLOCKER_REGISTER` §3's Owner column holds roles; `PHASE11A_LAUNCH_GATE_REGISTER` §A has no owner column |

Three independent sessions corroborate that no individual owner exists anywhere: **A25 owner map —
0 of 9 areas have an individual owner**; **A26 §2 — every decision-maker `UNIDENTIFIED`**; **A23E6
§2 row 1 — "None exists. No `CODEOWNERS`/`OWNERS`/`MAINTAINERS`… 464 commits under one author
identity, which is *not* authority evidence."**

Consistent with the brief, I did **not** contact anyone external and did **not** use an unapproved
channel. The established in-repo route is a blank-field request in `staging/` surfaced to the session
operator, which is what §1.5 is.

### 1.5 The smallest actionable decision request

Three admissible resolutions. **Option C is the smallest and is newly available because of §1.2.**

> **D-W1D15-1 — resolve the D5 condition.** `D5` is `CONDITIONALLY GRANTED` per
> `PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md:24`, conditioned on *"production-audit stakeholder
> sign-off"*. Per §1.2 that role has **zero occurrences** in the authoritative ledger and originates
> in W1D-11 §4 as a derived recommendation. Select exactly one:
>
> ☐ **A — Appoint.** *"The production-audit stakeholder is `<name/role>`, recorded in `<register>`."*
> Then that holder rules on D5 separately.
>
> ☐ **B — Assume.** *"As Code owner I also hold production-audit authority for
> `verify_production_schema.py`; D5 sign-off is GIVEN."*
>
> ☐ **C — Re-scope, no sign-off needed.** *"D5's substance is **preservation**, not change. A
> semantics-preserving refactor requires no production-audit sign-off; the condition is withdrawn.
> Any change to the severity policy itself remains a separate production-audit decision."*
>
> ☐ **D — Defer.** The second change stays NOT STARTED; `diff_snapshots()` and `check_drift()`
> keep the whole-tuple defect.

**Why Option C is defensible and not self-approval.** D5 as written is *"Preserve `check_drift()`'s
existing three-tier severity routing **exactly**"*. My own W1D-10 §, which proposed the condition,
scoped it to the opposite case: *"Revisiting it is a **production-audit policy change**, not
W1C-02."* A sign-off is the right gate for **altering** audit criticality. It is not obviously the
right gate for **leaving it byte-identical**. Option C recognises that asymmetry. **I am not
exercising it — only the Code owner can, and it is listed beside three alternatives.**

### 1.6 Policy terms that any D5 grant must bind

Carried forward unchanged and stated explicitly, per the brief:

| # | Invariant the second change must preserve |
|---|---|
| 1 | **Three-tier severity routing** — `f.critical` for `tables, columns, primary_keys, foreign_keys, row_level_security, policies, privileges, unique_and_check_constraints, functions, triggers`; `f.minor` for the advisory set plus `indexes, sequences, extensions, enum_types` |
| 2 | **The eight-row cap** in `check_drift()` (distinct from `compare()`'s 12 and `diff_snapshots()`'s 4) |
| 3 | **The exact label `MISSING from production`** (distinct from `compare()`'s `MISSING from the database`) |
| 4 | The classifier may replace the whole-tuple diff **only if 1–3 remain intact** |
| 5 | **Any change to 1–3 is a separate production-audit decision**, not part of this one |

---

## 2. W1C-02 — acceptance matrix

### 2.1 The authoritative definition, and what it does **not** require

| Source | Statement |
|---|---|
| `PHASE11B_W1C_RECOVERY_REPORT.md` §7 (origin) | *"**`verify_schema_drift.py`** compares whole tuples, so a **modified** object reports as one missing plus one extra… Any reading of its 'missing' counts as deletions will be wrong."* `OPEN` |
| W1D ledger `L837` | *"**Scoped: 12 of 16 dimensions**, not one… Still **OPEN pending implementation and regression evidence**."* |
| W1D ledger `L932` | *"**OPEN pending implementation and regression evidence** — mechanism scoped to 12/16 dimensions; classification demonstrated only on synthetic data"* |
| W1D ledger `L802` (AP-W1D-3) | *"Implement the **4-way drift classification + the 10 regression tests** (§3.5–3.6)"* |
| W1D ledger §3.6 closing note | *"the proposed change touches **only `compare()`**, a pure function, and must not alter `live_snapshot`'s session setup."* |

**The authoritative closure criteria are exactly two: implementation, and regression evidence.**

**There is no live-PostgreSQL requirement in any authoritative statement of W1C-02.** I checked
specifically because the brief warned against inventing one. The ledger's phrase *"demonstrated only
on synthetic data"* is a **description of the state at the time of writing**, in a sentence whose
operative clause is *"pending implementation and regression evidence"*. It is not a stated criterion.

**The authoritative remediation subject is `compare()`.** Both the origin finding and the ledger's
own scope note name `verify_schema_drift.py` / `compare()` alone.

### 2.2 Matrix — the 10 named regression tests against delivered evidence

Evidence classes: **U** = executed unit test, pure data · **S** = file-snapshot corroboration ·
**L** = live PostgreSQL · **G** = gated, not executed · **ST** = static source check.

| # | Ledger §3.6 test | Delivered test(s) | Class | Verdict |
|---|---|---|---|---|
| 1 | `test_identical_rows_are_unchanged_not_drift` | `test_identical_snapshots_report_nothing`, `test_a_whole_identical_snapshot_produces_no_drift`, `test_empty_dimensions_report_nothing`, `test_equal_duplicate_multiplicity_is_unchanged` | **U** | **COVERED** |
| 2 | `test_a_changed_attribute_is_reported_as_modified_once` | `test_a_modified_attribute_is_one_modification_not_a_removal_and_addition`, `test_the_changed_attribute_is_identified`, `test_a_modified_object_is_named_exactly_once`, `test_multiple_changed_attributes_are_all_listed` | **U** | **COVERED** |
| 3 | `test_a_removed_object_is_missing_not_modified` | `test_a_genuine_removal_is_missing`, `test_removal_and_addition_keep_their_original_labels` | **U** | **COVERED** |
| 4 | `test_an_added_object_is_added_not_modified` | `test_a_genuine_addition_is_added` | **U** | **COVERED** |
| 5 | `test_whole_tuple_key_dimensions_never_report_modified` | `test_a_whole_row_dimension_never_reports_modified` + `test_the_whole_row_set_matches_the_declaration` (binds the real 4-member `WHOLE_ROW` set) | **U** | **COVERED** |
| 6 | `test_counts_reduce_to_the_legacy_output` — `missing+modified == legacy_missing`, `added+modified == legacy_extra` | **NONE.** The nearest are label compatibility (`test_removal_and_addition_keep_their_original_labels`), type compatibility (`test_compare_still_returns_two_lists_of_strings`) and internal header/detail consistency (`test_the_header_counts_match_the_detail`) — **none asserts the arithmetic** | — | **GAP — see §2.3** |
| 7 | `test_every_snapshot_query_has_a_total_order_by` | `test_every_dimension_has_a_result_set_order_by`, `test_order_by_covers_the_declared_key` | **U** | **COVERED** (pre-existing W1D-05) |
| 8 | `test_every_dimension_declares_a_key_width` | `test_every_dimension_declares_a_comparison_key`, `test_a_declared_key_is_whole_row_or_a_valid_width`, `test_every_dimension_in_the_snapshot_has_a_declared_key` — now binding **production** `COMPARISON_KEY` | **U** | **COVERED, strengthened** |
| 9 | `test_a_mutated_migration_checksum_is_reported_as_modified` | `NONZERO_CASES` entry `("advisory change", "recorded_migrations", [MIGRATION], [MIGRATION_CHANGED])` — `abc123`→`def456`, exactly a mutated checksum — flowing through 3 parametrised tests, plus `test_advisory_dimensions_route_to_advisories_not_failures` asserting `any("MODIFIED" in l)` | **U** | **COVERED** |
| 10 | `test_read_only_enforcement_is_unchanged` | `tests/test_schema_drift_checker_is_read_only_postgres.py` — 10 tests incl. `test_port_6543_is_refused`, `test_the_checkers_own_session_cannot_write` | **G** | **NOT EXECUTED — see §2.4** |

**9 of 10 covered by executed unit evidence. 1 unwritten. 1 gated.**

### 2.3 Test 6 — measured this phase, and it is a smaller gap than it looks

The arithmetic is asserted nowhere, so I measured whether it actually holds against the **delivered**
classifier. Probe: `staging/probe_p11b_w1d15_compat_invariant.py` → `p11b_w1d15_compat_invariant.txt`.

The pre-W1D-13 source is **untracked with no git history** (§4.2), so "compares whole tuples" cannot
be recovered from git. I therefore reconstructed **both** readings and reported both:

```
SET reading:      12 hold, 0 break
MULTISET reading: 10 hold, 2 break
```

**The SET reading is the correct reconstruction**, and `tests/test_drift_classification.py`'s own
header says why: the original used *"`r not in actual` membership"*, which is *"order- and
count-INsensitive"* — set semantics. The two MULTISET breaks are the duplicate-multiplicity cases,
which the original code could not detect at all.

**So ledger test 6 is satisfiable as literally written, 12/12, including the ambiguous-key and
whole-row cases.** This corrects my own expectation: I anticipated the new `duplicated`/`ambiguous`
categories would break a two-category identity and that a scope qualification would be needed (the
probe's closing note still records that anticipation). Under the real legacy semantics no
qualification is required. **Test 6 is a pure coverage gap — one test, no decision, no approval
beyond the existing AP-W1D-3 four-file scope.**

### 2.4 Test 10 — the evidence is entirely skip-gated

`tests/test_schema_drift_checker_is_read_only_postgres.py:40` carries a **module-level** gate:

```python
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"), ...)
```

`VER` **All 10 tests in that file are gated on `AERORA_TEST_DATABASE_URL`** — including
`test_port_6543_is_refused` and the three `redact()` tests, which need no server. These are part of
the **12 PG-gated skips**, and skips are not passes.

**A correction to my own check.** I first ran `git diff` filtered for `assert_read_only`,
`live_snapshot`, `default_transaction_read_only` and `6543` and got no matches. That was a **false
negative** — `git diff` is empty for these files because they are **untracked** (§4.2), not because
the functions were untouched. I withdraw it as evidence.

What I can establish, and its limit:

- `ST` **The guarantee is present and intact in the current source** — `verify_schema_drift.py:103`
  `assert_read_only` executes `SHOW default_transaction_read_only` and exits unless `'on'`; `:129`
  `_refuse_transaction_pooler` exits on port 6543; `:157` and `:183`
  `conn.set_session(readonly=True, autocommit=True)`; `live_snapshot` calls `assert_read_only` at
  `:159`.
- `REC` That it is *unchanged relative to pre-W1D-13* rests on the W1D-13 session's own record.
  **It cannot be proven from git, because no pre-change version exists in any ref.**
- This matters beyond `verify_schema_drift.py`: `verify_production_schema.py` **imports**
  `assert_read_only`, `_refuse_transaction_pooler` and `redact` from it, so the production audit
  tool's read-only behaviour depends on the edited file even though that file is byte-identical.
  `A27 §4.3` reaches the same conclusion independently.

### 2.5 Where my evidence contradicts A27's disposition

`A27 §4.4` records W1C-02's remaining scope as exactly two items. **I disagree with both**, and the
disagreement is load-bearing for whether D5 blocks W1C-02 at all.

| A27 §4.4 | My finding | Basis |
|---|---|---|
| *"The identical whole-tuple defect persists in `diff_snapshots()` and `check_drift()`… That is the second change, D5-gated."* — treated as W1C-02 scope | **Not W1C-02's authoritative scope.** W1C-02's origin names `verify_schema_drift.py`; ledger §3.6 says the change *"touches only `compare()`"*; AP-W1D-3 is scoped to *"the 4-way classification + the 10 regression tests (§3.5–3.6)"*. The other two functions enter via **D3 Option 1** — scope the project **added** in W1D-10/W1D-12, correctly tracked as open defects, but **not part of W1C-02's definition** | §2.1 |
| *"No drift run has exercised the new `compare()` against a live schema."* — listed as remaining scope | **Not a closure criterion.** The authoritative criteria are *"implementation and regression evidence"*. A live run is **desirable additional confidence**, not a requirement. Treating it as one is the universal-live-PostgreSQL requirement the brief warned against inventing | §2.1, §3.3 |

**Consequence, stated carefully.** If the Code owner confirms W1C-02's subject is `compare()`, then
its two authoritative criteria are met apart from **one unwritten test (§2.3)**, and **W1C-02 does
not depend on D5 at all**. If the Code owner confirms the subject is all three call paths, W1C-02
stays blocked behind D5. **That is a scope question only the Code owner can answer, and it is the
single highest-value question in this report.** I am not answering it and **not recording a
closure**.

### 2.6 Disposition

**W1C-02 remains OPEN.** `12 of 16` dimensions still carry the defect in `diff_snapshots()` and
`check_drift()`; test 6 is unwritten; test 10's evidence is gated; and the scope question in §2.5 is
unresolved.

### 2.7 The seven substantive questions

The brief required seven numbered answers. I did not have their verbatim text in context, so these
are the seven substantive questions the acceptance matrix requires; each is answered from source or
test evidence above.

| # | Question | Answer |
|---|---|---|
| 1 | What are W1C-02's authoritative closure criteria? | **Implementation + regression evidence.** `L837`, `L932` |
| 2 | What is the authoritative remediation subject? | **`compare()` only.** Ledger §3.6 note; W1C §7 origin |
| 3 | Are the 10 named regression tests delivered? | **9 covered, 1 unwritten, 1 gated** (9+1+1 counts test 10 as both named and gated) — §2.2 |
| 4 | Does closure require a live PostgreSQL run? | **No.** No authoritative statement requires one — §2.1 |
| 5 | Is the D5-gated second change required for closure? | **Not by W1C-02's definition.** It is D3 Option 1 scope — §2.5 |
| 6 | What evidence class supports each criterion? | **U** for 9 tests; **S** for the real-snapshot self-comparison; **G** for test 10; **L** for nothing — §2.2 |
| 7 | What would add confidence beyond the criteria? | Live key-uniqueness verification (**0 of 16** dimensions verified), a live drift run, and snapshot provenance (**AP-W1D-4**, NOT GRANTED) — all desirable, none required — §3.3 |

---

## 3. AP-W1D-1 — assessed as an independent path

### 3.1 Authoritative definition

Ledger §6: *"**AP-W1D-1** | Evaluate per-statement completeness predicates for 022–025 under an
enforced read-only session (§2.7 Phase 1) | **DB owner (read access)** | **NOT GRANTED**"*, and
*"**Second smallest, read-only:** AP-W1D-1. It settles §2.6… and **writes nothing**."*

Scope per §2.7 Phase 1: build the per-statement predicate set for 022–025 from the files, then
evaluate under `set_session(readonly=True, autocommit=True)` with a `SHOW
default_transaction_read_only` readback. Scale: 022 = 2 statements, 023 = 6, 024 = 11; all four
`CONDITIONAL`, all containing `DO $$` blocks.

### 3.2 Three blockers, not one

| # | Blocker | Evidence |
|---|---|---|
| 1 | **No named role holder.** *"Can the `DB owner (read access)` role be mapped to an authorized person through an existing authoritative record? **No.**"* | `A26 §3` `VER`; corroborated by A25 (0 of 9), A23E6 §2 |
| 2 | **Target database identity unresolved.** Production has no DSN — scorecard `prod_dsn_verify` = `NOT_DONE`, *"no production DSN in any phase"*. The local `pgdata_r1` route implicates `AP-W1D-5` (NOT GRANTED) and `W1D-06` (`staging/apply_migration.py:61` hardcodes the shared `staging/pg_dsn.txt`) | `A26 §3.1` |
| 3 | **NEW — its scope excludes what W1C-02 would want.** AP-W1D-1 evaluates **migration-completeness predicates for 022–025**. That is **W1C-01** evidence. W1C-02 would want **drift-key uniqueness** across the 16 snapshot dimensions — a different query set entirely | §3.3 |

### 3.3 Why it is not an independent path to removing the W1C-02 blocker

Three reasons, each sufficient on its own:

1. **Scope.** Running uniqueness queries under an AP-W1D-1 grant would be **scope expansion beyond
   the grant**, requiring separate authorization. W1D-11 §4 records uniqueness as an *adjacent*
   benefit of AP-W1D-1/AP-W1D-5 and states plainly: *"**Not a blocker for AP-W1D-3.**"*
2. **Necessity.** W1C-02's authoritative criteria require no live evidence (§2.1), so there is no
   blocker here for a live run to remove.
3. **Blocker type.** The actual W1C-02 blocker is the **scope question in §2.5** — a `Code owner`
   question. AP-W1D-1 is a `DB owner` access question. **Different role, different gate.** Granting
   AP-W1D-1 in full would not advance W1C-02 closure by one step.

**Live checks that would add confidence if ever authorized** (listed, not requested, not performed):
per-dimension `GROUP BY <declared key> HAVING count(*) > 1` across all 16 dimensions to settle
uniqueness (**0 of 16** verified); one `compare()` run against a live catalogue; snapshot provenance
via AP-W1D-4. All require authorization that does not exist.

**Not done:** no connection, no DSN read, no credential access, no `pg_dsn.txt` content read, no
PostgreSQL install, no role creation, no network operation. **I did not infer permission to access
production from any reference to "read access."**

---

## 4. Implementation status

### 4.1 First change — delivered, authorized, independently corroborated

`AP-W1D-3` is **GRANTED** (`PHASE11B_W1D12_CLASSIFIER_DECISION_RECORD.md` §1; Code owner; four-file
scope; no conditions). Ledger `L802` still reads `NOT GRANTED` and is **stale**; `A27 §2` resolves
that as `D-A26-1` and notes a ledger edit is **unauthorized**. I made no ledger edit.

| Path | Fingerprint (`git hash-object`) | Lines | State |
|---|---|---|---|
| `scripts/verify_provisioning.py` | `8f432d994d9d0a8b9f707b7f18fbb4e27c0a5db2` | 660 | classifier added |
| `scripts/verify_schema_drift.py` | `f853c1482ca295440c78e4f25a8989438fab041e` | 406 | `compare()` routed through it |
| `tests/test_drift_classification.py` | `fcffbe8e01367aa7f99fffe2677267821fb8424d` | 665 | new, 76 cases |
| `tests/test_snapshot_dimension_invariants.py` | `5106dd5916424c9b795a5b393e36afc21c4ba9b4` | 521 | 48 guards, now on production keys |
| `scripts/verify_production_schema.py` | `6e194f43a832d1f9cb0f99efd88dd204c5b3c444` | 1092 | **untouched, correctly out of scope** |

All five identical to W1D-13's recorded values. **No concurrent edits.** Independent corroboration:
`A26 §7` re-ran `tests/test_drift_classification.py` itself → **76 passed in 4.50s**; `A27 §4.1`
re-ran the mutation probe → **12 applied / 12 bit / 0 silent**.

### 4.2 Material correction — the entire drift toolchain is untracked, with no git copy anywhere

I had been treating these files as tracked-and-modified. **They are not.** Measured:

```
?? scripts/verify_provisioning.py
?? scripts/verify_schema_drift.py
?? scripts/verify_production_schema.py
?? tests/test_drift_classification.py
?? tests/test_snapshot_dimension_invariants.py
```

- `git ls-tree HEAD -- <all five>` → **empty**
- `git log --all --oneline -- scripts/verify_schema_drift.py` → **empty** (no history on any ref)
- `git check-ignore -v` → **no output** (not ignored; genuinely untracked yet trackable)
- `git diff HEAD --stat` → **empty**; `git ls-files -s` → **empty** for all five

**Consequences, and they are more serious than my earlier framing.** I previously described this work
as *"one `git checkout` from loss"*. That was wrong in both directions: `git checkout` would **not**
touch untracked files, but **`git clean -fd` would delete all five outright, with no git recovery
path whatsoever** — not from HEAD, not from the index, not from any ref or stash. This is W1C's
finding `W1B-04` (P2, *"toolchain untracked"*), which records that the loss **already happened once**
and *"was only recoverable because the editor kept local history — not because the repository did."*

**The whole of W1C-02's implementation evidence — the classifier and all 124 tests — exists only as
untracked working-tree files.** Nothing in §2's matrix survives a `git clean`.

The fingerprints in §4.1 and in every prior W1D report are therefore `git hash-object` digests of
working-tree content, not index or HEAD blobs. They remain valid as change-detection, which is how
they were used.

### 4.3 Second change — NOT STARTED

`diff_snapshots()` (`verify_provisioning.py`) and `check_drift()`
(`verify_production_schema.py:217`, defect at `:242-243`) still carry the whole-tuple diff. Blocked
on D5 (§1). Caps and labels remain distinct: `compare()` 12 / `MISSING from the database`;
`diff_snapshots()` 4 / `only in run 1/2`; `check_drift()` 8 / `MISSING from production`.

---

## 5. Verification

### 5.1 Executed this phase

| Command | Result |
|---|---|
| `python staging/probe_p11b_w1d15_compat_invariant.py` | **SET 12 hold / 0 break; MULTISET 10 hold / 2 break**, exit 0 |
| Preservation and fingerprint measurements (§6) | **PASSED** |

### 5.2 Reused, not re-run — with the reason

Per the brief's instruction not to rerun the 124-test suite to create activity:

| Evidence | Result | Why reuse is sound |
|---|---|---|
| `tests/test_drift_classification.py` + `tests/test_snapshot_dimension_invariants.py` | **124 passed** (13.58s) | Re-confirmed at W1D-15 Track D against the **exact five fingerprints re-verified in §4.1**. Inputs unchanged ⇒ the result is unchanged |
| Mutation probe | 12 applied / 12 bit / 0 silent | Re-run independently by **A27 §4.1** this hour |
| `test_drift_classification.py` alone | 76 passed | Re-run independently by **A26 §7** |
| Five-file group | 103 passed / 12 skipped / 1 xfailed | W1D-13 `REC`. The 12 skips are **PG-gated and are not passes** |

### 5.3 What the test evidence does not establish

Quoting the delivered suite's own header, which states its limits correctly:

> *"**Nothing about PostgreSQL.** The declared key widths rest on documented catalogue properties and
> are **UNVERIFIED against any live catalogue for all 16 dimensions.**"*

Also not established: that any declared key is unique (**0 of 16**); that `compare()` behaves
correctly against a live schema; snapshot provenance; and that the read-only path is *unchanged*
relative to pre-W1D-13 (§2.4).

---

## 6. Preservation

| Invariant | Entry | Exit | Result |
|---|---|---|---|
| `HEAD` | `074b772ab16cece62cbfca0f2b78497be2ae56d5` | identical | **SAME** |
| branch / `origin/main` / divergence | `main` / `074b772a…` / `0 0` | identical | **SAME** |
| Digest A (sha256, raw `git ls-files -s`) | `98ae5bdf2af9a0ae74d27ae88c028d4a60166e098fffeb6b0c3d0d6651cc2704` | identical | **SAME** |
| staged / unstaged / unmerged | 22 / 102 / 0 | 22 / 102 / 0 | **SAME** |
| tracked / stash / worktrees | 2104 / 2 / 1 | 2104 / 2 / 1 | **SAME** |
| `.git/objects` file count | 198 | 198 | **SAME — no object written** |
| 5 toolchain fingerprints | §4.1 | identical | **SAME** |
| untracked (collapsed `--porcelain`) | 140 | 140 | **SAME** |
| untracked (expanded `-uall`) | 3581 | 3586 | **+5 — accounted below** |

**Untracked-count reconciliation.** `staging/` contains **0 tracked files** (`git ls-files staging/`
→ empty), so the collapsed view reports the whole directory as **one** `?? staging/` entry and is
insensitive to new reports. Prior phases mixed the two methods: A26 cited 139→140 (collapsed), A23E6
cited 3580 (expanded). Both are correct under their own flag. **This is a measurement-method
difference, not drift.**

**The +5, named rather than summarised.** Mine: exactly **2** —
`staging/probe_p11b_w1d15_compat_invariant.py` (14:38:30) and
`staging/p11b_w1d15_compat_invariant.txt` (14:38:54). **Concurrent sessions, not this task:**
`staging/PHASE11B_A23E6_GOVERNANCE_DECISION_PACKET.md` (14:22), `staging/p11b27_baseline.py`
(14:31), `staging/p11b27_baseline.txt` (14:37), `staging/PHASE11B_A27_RECONCILIATION_AND_READINESS_REPORT.md`
(14:40 — written **during** this phase). No overlapping file was touched by both.

**Files created by this task: exactly 3** — the probe, its output, and this report. All new, all
untracked, all under `staging/`, all destination-checked ABSENT immediately before writing.

**Confirmed NOT done:** production edit; test edit; governance record, ledger or register edit;
report overwrite; dependency change; staging; commit; push; reset; `--hard`; clean; stash; rebase;
branch switch; index mutation; CI trigger or dispatch; Redis start; database, exchange or payment
call; DSN or credential read; deployment; approval created, recorded or inferred.

---

## 7. Next action

**Single highest-value action — costs nothing, needs no database, no code and no new authorization:**

> **Answer the §2.5 scope question.** *"W1C-02's authoritative subject is **\<`compare()` only` /
> all three call paths\>**."*

It is the pivot for everything else. If `compare()`, then W1C-02's two authoritative criteria are met
but for **one unwritten test** (§2.3, measured satisfiable 12/12, inside the existing AP-W1D-3
four-file scope), and **W1C-02 does not depend on D5**. If all three paths, W1C-02 stays blocked
behind D5 and §1.5 becomes the critical path.

**Then, in order:**

1. **`D-W1D15-1`** (§1.5) — resolve the D5 condition: Appoint / Assume / **Re-scope** / Defer.
2. **Authorize tracking the drift toolchain** (§4.2). Five untracked files hold the entire W1C-02
   implementation and its 124 tests, with **no git copy on any ref**. `W1B-04` records this loss
   already occurring once. This is the largest preventable risk in this workstream and it is
   unrelated to D5.
3. **Write ledger test 6** (§2.3) — one test, within existing authorization, closes the last
   unwritten criterion.
4. Leave `D-A27-1` (ledger line 802) to A27; I made no ledger edit.

**Deliberately not proposed:** any live database access (§3.3), any AP-W1D-1 pursuit (§3 — it
unblocks nothing here), any governance-record edit, and any second-change implementation.

---

## 8. Status

| Item | State |
|---|---|
| **D5 production-audit sign-off** | **ABSENT** — role undefined in the authoritative register (§1.2); unobtainable in-repo |
| **Second change** (`diff_snapshots`, `check_drift`) | **NOT STARTED** |
| **W1C-02** | **OPEN** — 9/10 named tests covered; 1 unwritten; 1 gated; scope question open |
| **AP-W1D-1 / -2 / -4 / -5** | **NOT GRANTED** — AP-W1D-1 has three blockers and is not a path here |
| **AP-W1D-3** | **GRANTED** (W1D-12 §1); ledger `L802` **stale**; ledger edit **unauthorized** |
| **Key uniqueness** | **UNVERIFIED — 0 of 16 dimensions** |
| **Drift toolchain** | **UNTRACKED — no git copy on any ref** (§4.2) |
| **PostgreSQL integration** | **UNVERIFIED** |
| **TENANT-01 / QUOTA-01** | **OPEN / OPEN** |
| Approvals granted, recorded or inferred this phase | **0** |
| Governance records edited | **0** |
| Owners newly identified as individuals | **0** |
| **Live trading** | **OFF / NOT CERTIFIED** |

No approval was inferred from this brief's framing, from the 124 passing tests, from A26's or A27's
independent re-runs, from the classifier being correct, from the Code owner's conditional YES, from
any recommendation of mine, from role names, from commit authorship, or from silence.
