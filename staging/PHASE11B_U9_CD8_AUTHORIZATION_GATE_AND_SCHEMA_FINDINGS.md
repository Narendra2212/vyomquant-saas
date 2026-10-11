# Phase 11B-U9 — CD-8 Authorization Gate, and Four Schema Findings That Change the Plan

**Outcome: STOPPED at the Phase 1 decision gate. Both D8(a) and D8(b) are absent.** No source,
test, register or existing artifact was modified. No database, Redis or provider connection was
made. Nothing staged, committed or pushed.

**This is not an implementation report.** It exists because Phase 2 asks for the missing decision
plus "any new technical findings that materially change the previous plan," and this phase found
four of them — one of which inverts a load-bearing assumption in the U7 design.

---

## 1. Decision gate — both decisions missing

Checked fresh this phase, not carried from U8.

| Record checked | What it says about CD-8 / D8 |
|---|---|
| `staging/PHASE11A_LAUNCH_GATE_REGISTER.md` (mtime 2026-10-10 13:53) | Gates are RECON-01, TENANT-01, FEE-02, MONEY-08, plus live/production sections. **No CD-4, CD-5, CD-7 or CD-8 row exists.** |
| `staging/PHASE11_LAUNCH_BLOCKER_REGISTER.md` | no `CD-` finding, no D8 |
| `staging/*.json` | zero matches for `CD-4\|CD-5\|CD-7\|CD-8` |
| `staging/PHASE11B_A17_APPROVAL_RECONCILIATION__claude_session.md` | the repo's only approval matrix. Covers items **A, B, C, D1–D4, E** — PR selection, commit, push, CI, test removal for the **D5 real-Redis lane**. Headline: "no approval exists." Status line: "A, C, D1–D4, E all **NOT APPROVED**." **D8 and CD-8 appear nowhere in it.** |
| staging files written in the last 3 hours (20 of them, 21:16–21:53) | all belong to other workstreams: `PHASE11B_W1D08_STATIC_DRIFT_GUARDS_REPORT.md`, `PHASE11B_A22V_REDIS_R6_VERIFICATION_AND_DESIGN.md`, `PHASE11B_D5_WATCHDOG_REPAIR_REPORT.md`, `PHASE11B_A20Q_...`, `PHASE11B_W1D07_...`. **No decision record among them.** |
| repository-wide search for `CD-8` | hits only `PHASE11B_U7_CD8_CRASH_RECOVERY_READINESS.md`, `PHASE11B_TENANT01_U4_U6_INVESTIGATION_REPORT.md`, and one false positive in a vendored `ccxt` METADATA file |

### Recorded decision vs. the four things that are not one

* **Recommendation** — U7 §5.5 ranks Option A first. A recommendation by the analysing session.
* **Inferred intent** — the U8 and U9 prompt titles say "Authorized" / "approved." Both briefs
  explicitly forbid reading that as approval.
* **Prior plan** — U7 §4.3 specifies twelve tests. A plan is not a licence to execute it.
* **Code that exists** — `settlement_service.py` is clean and unowned. Availability is not consent.

A17 is the useful control here: it shows this project *does* record decisions, per item, with the
granting party named and "Grant Dx explicitly" as the stated next action. D8 has no such row in any
document. The absence is a real absence, not an unlocated record.

### Exactly what is needed, and from whom

| Decision | Scope | Who | Status |
|---|---|---|---|
| **D8(a)** | Accept CD-8 into the launch-gate register as `marketplace_settlements` partial completion, explicitly distinct from CD-5 (Redis-only event idempotency) and CD-7 (`billing_invoices` schema). **Also state whether the register owner makes that edit or authorizes this workstream to make it** — U9 §Phase 1 asks for this and it is unanswered. | register owner | **NOT OBTAINED** |
| **D8(b)** | Implement Option A in `backend_app/backend/marketplace/settlement_service.py` and `tests/test_settlement_service.py`, with any scope restrictions recorded. | human coordinator | **NOT OBTAINED** |

Granting (a) without (b) is coherent: it records the defect and sequences the fix behind D7. Given
Finding 1 below, recording it is now the more urgent of the two.

---

## 2. Baseline (entry, this phase)

| Invariant | Value |
|---|---|
| HEAD / branch | `074b772ab16cece62cbfca0f2b78497be2ae56d5` / `main` |
| upstream | 0 ahead / 0 behind |
| `git write-tree` | `6f40856cee2bc7ea4d741f69f4b1f68b463d70a4` |
| normalized staged digest | `65dfc45367ced6f67573f3ae485a1c92d63c803b4d881b6a186afd3d54a5bd0c` |
| staged / unmerged | 22 / 0 |
| unstaged tracked | 102 |
| untracked | 3510 (was 3503 at end of U8 — concurrent drift) |

All five candidate targets verified **clean and unowned**: `settlement_service.py`,
`tests/test_settlement_service.py`, `tests/test_marketplace_concurrency.py`,
`tests/property/test_settlement_idempotence.py`, `tests/property/test_settlement_ledger.py`.

**Path correction:** U9's brief cites `staging/PHASE11B_U7_CD8_RECOVERY_READINESS.md`. That path
does **not** exist. The actual U7 artifact is
`staging/PHASE11B_U7_CD8_CRASH_RECOVERY_READINESS.md` (mtime 2026-10-10 20:48) — note `CRASH_`.
Only one U7 file exists; there is no second report under the briefed name, so nothing was missed.

---

## 3. The new evidence source: `staging/provisioning_snapshot.json`

Not previously examined in U4–U8. A full PostgreSQL catalogue dump, found while re-verifying the
three design risks.

| Property | Value |
|---|---|
| size / mtime | 396,325 bytes / **2026-10-06 20:14** (4 days stale) |
| tracked by git? | **No** — untracked |
| contents | 85 tables, 1135 columns, 400 indexes, 188 policies, 122 unique/check constraints, 61 triggers, 991 privileges, 50 recorded migrations |
| `alembic_version` | `d4e5f6a7b8c9` |
| extensions | `plpgsql` only |

**Evidentiary weight, stated precisely.** This is a committed-to-disk observation of *some*
provisioned database, not a live query. It is untracked, four days old, and carries no record of
which host or environment it came from. It therefore **does not discharge D7** and this report
claims no real-PostgreSQL verification. But it is materially stronger than U7's "unverified," and
it contradicts U7 on one point, which is why it is reported now rather than deferred.

### 3.1 It answers several of U7's §6 questions as evidence

| U7 query | Snapshot answer |
|---|---|
| **Q2** — `uq_settlement_reference_reversal` present? | **YES.** Both as unique index (`CREATE UNIQUE INDEX … USING btree (provider_reference, is_reversal)`) and as constraint (`'u', 'UNIQUE (provider_reference, is_reversal)'`). CD-8's premise holds; it is not superseded by the worse defect U7 flagged. |
| **Q1** — `marketplace_settlements` columns | all 16 present, correct types: `amount_minor`/`owner_share_minor`/`platform_fee_minor` **bigint**, `settled_at`/`created_at`/`updated_at` **timestamptz**, `is_reversal` **boolean**, ids **uuid**. `_settlement_payload`'s 13 keys are all satisfiable. |
| **Q6/Q7** — append-only really enforced? | **YES.** `trg_settlements_append_only` → `marketplace_settlement_append_only_guard` attached. Privileges: `authenticated` SELECT; `service_role` INSERT, SELECT, REFERENCES, TRIGGER, TRUNCATE — **no UPDATE, no DELETE for any role.** U7 §5.1's exclusion of a progress column is confirmed against a real catalogue. |
| **Q8** — which guard body? | `trg_subscription_transition_guard` → `marketplace_subscription_guard`; `migrations/014_subscription_admin_reinstatement.sql` recorded applied, so the reinstatement arm is likely present. |
| **Q9** — RLS state | `marketplace_settlements`: `relrowsecurity = true`, `relforcerowsecurity = **false**`; owner **`postgres`**. All 5 CHECKs and 3 policies present. **Independently corroborates CD-1** (ENABLE-not-FORCE plus superuser owner ⇒ RLS bypassed on the owner connection). |
| **Q4** — `uq_deployment_permissions` present? | **NO — a differently named constraint is.** See Finding 2. |

Q10–Q13 (the historical population counts) remain unanswered; the snapshot carries catalogue
metadata only, no row counts.

---

## 4. Four findings that change the plan

### Finding 1 — BUG-056 appears UNFIXED on this snapshot, which makes CD-8 the steady state, not a rare window

* `library_subscriptions` has **24 columns and no `updated_at`**: `cancelled_at, currency,
  expires_at, failed_at, failure_cause, id, library_id, owner_id, owner_share_minor, payment_id,
  payment_provider, period_expiry, period_start, platform_fee_minor, price_minor, price_paid,
  provider, provider_reference, provider_session_at, renewal_enabled, started_at, status,
  subscription_tier, user_id`.
* `trg_library_subscriptions_updated_at` → `update_library_subscriptions_updated_at` **is
  attached**, `BEFORE UPDATE … FOR EACH ROW`
  (`archived_migrations/root_migrations/007_create_library_subscriptions.sql:30-39`), and that
  function's body is `BEGIN NEW.updated_at = NOW(); RETURN NEW; END;` — verified by direct read at
  `007:32`.
* **`022_fix_library_subscriptions_updated_at.sql` is NOT in `recorded_migrations`.** Searched the
  50 recorded entries: no `022`. `006_reconcile`, `008_marketplace`, `012_subscription` and
  `014_subscription` are all recorded; 022 is not.

If this snapshot reflects the deployment target, then **every UPDATE to `library_subscriptions`
raises `42703`** — which is step 3a of `settle()`. Consequence: every marketplace payment inserts
its ledger row, fails to activate, exhausts the 5-attempt budget, answers 500, and then answers
**200 with no entitlement** on the provider's redelivery (the U7 §3.2 mechanism). CD-8 would not be
an occasional crash window on that database; it would be the **deterministic steady state of every
marketplace payment.**

**This raises the priority of D8(a) specifically.** Recording CD-8 does not depend on D8(b), and on
this evidence the finding is not latent.

Caveats held firmly: the snapshot is untracked, 4 days old, of unidentified provenance, and 022 may
have been applied since. The snapshot does not carry function bodies (the `functions` entries for
both triggers are `["<name>", "", false]`), so the body is inferred from the archived migration
rather than read from the live catalogue. Independent corroboration exists in `022`'s own header,
which states the `42703` was reproduced against a provisioned database. **Confirming or refuting
this is now the highest-value item in the D7 query list** and I have added it as Q14.

### Finding 2 — the entitlement constraint has a different name live than in the tracked migration. This breaks U7's design as written.

| Source | Constraint name | Columns |
|---|---|---|
| `migrations/006_reconcile_production_database.sql:112` (tracked) | `uq_deployment_permissions` | `(user_id, library_id, is_active)` |
| `archived_migrations/root_migrations/010_create_deployment_permissions.sql:18` (untracked) | `deployment_permissions_user_library_idx` | `(user_id, library_id, is_active)` |
| **snapshot (observed)** | **`deployment_permissions_user_library_idx`** | `(user_id, library_id, is_active)` |

The mechanism is visible in `recorded_migrations`: `root_migrations/010` ran at `step0b` and created
the table, so `006`'s later `CREATE TABLE IF NOT EXISTS public.deployment_permissions (…)` was a
**no-op** and its constraint name never took effect. This is precisely the silent-`IF NOT EXISTS`
class that `008`'s shape-assertion blocks were written to catch — `006` has no such assertion.

**Why this breaks the U7 plan.** U7 §3.5 and §5.2 specify recognising
`uq_deployment_permissions`' `23505` as "already granted." A helper keyed on that name would
**never match** on this database: the duplicate would fall through to
`SettlementPersistenceError`, be retried five times, and surface as `SettlementPersistFailed` —
failing in exactly the W3 case the recovery exists to fix. U7 §3.5 cited the tracked name in good
faith; the snapshot contradicts it.

**The correct shape is already in the codebase.** `_is_duplicate_reference`
(`settlement_service.py:1999-2025`) matches the constraint name first and falls back to
`_UNIQUE_VIOLATION_SQLSTATE in text and SETTLEMENT_TABLE in text` — table-scoped, so a violation
from another statement "cannot borrow it," as its comment at `:2023-2024` puts it. A sibling
`_is_duplicate_permission` must therefore match **both** candidate names *and* carry the
`23505 + PERMISSION_TABLE` fallback. Matching one tracked name would be wrong; matching bare
`23505` would swallow unrelated violations, which that docstring explicitly warns against.

`settlement_service.py` already names the table once as `PERMISSION_TABLE = "deployment_permissions"`
(`:274`) and exports `SETTLEMENT_REFERENCE_UNIQUE_CONSTRAINT` (`:335`); there is no permission
constraint constant yet.

### Finding 3 — `deployment_permissions` carries the BUG-056 shape too, currently unfired. Recovery must never UPDATE it.

* Snapshot columns: `expires_at, granted_at, granted_via, id, is_active, library_id, revoked_at,
  subscription_id, user_id` — **no `created_at`, no `updated_at`.**
* `trg_deployment_permissions_updated_at` → `update_deployment_permissions_updated_at` **is
  attached**, and that function's body is `BEGIN NEW.updated_at = NOW(); RETURN NEW; END;`
  (`archived_migrations/root_migrations/010:22-25`, direct read), `BEFORE UPDATE` (`010:31-33`).
* It never fires today because `grant_deployment_permission` (`:564-631`) only ever **INSERTs**
  (`:620`).
* The shape has been written once before: `backend_app/routers/library.py:3687` preserves, inside a
  docstring describing **deleted** code, an
  `.update({"is_active": False, "revoked_at": until}).eq("subscription_id", …)` against
  `deployment_permissions`. Had it survived, it would raise `42703`.

**Design constraint for Option A:** recovery must be strictly *insert-or-recognise-duplicate* on
`deployment_permissions`. It must not UPDATE a permission row — not to reactivate one, not to extend
`expires_at`. Note the unique key includes `is_active`, so an existing `is_active = TRUE` row does
block a second active row for the same `(user, library)` pair, which is what makes
recognise-duplicate sufficient. A design that wanted to revive an `is_active = FALSE` row would need
an UPDATE and would hit `42703` on this database. That is a second reason to keep recovery
insert-only, and it is new information relative to U7.

This is a candidate finding in its own right — a latent `42703` on a money-adjacent table, armed and
waiting for the first code path that updates a permission. It is **not** proposed as CD-9 here;
proposing findings is D8's business and D8 is unobtained. Recorded for the register owner.

### Finding 4 — the three U7 design risks, re-verified from source

| Risk | Status after re-verification |
|---|---|
| duplicate handling for the entitlement constraint | **CONFIRMED and WORSENED.** `grant_deployment_permission:625-631` still converts every exception uniformly to `SettlementPersistenceError`; no duplicate classification exists. Finding 2 adds that the constraint name U7 named is not the live one. |
| `FakeSupabase` fidelity | **CONFIRMED.** `tests/test_settlement_service.py:349-359` — the `PERMISSION_TABLE` insert branch appends unconditionally with a generated `perm-N` id and models **no** uniqueness. `_MoneyPathSupabase:841-853` enforces only the settlement key. Neither models `trg_*_updated_at`, so **no existing double can see Findings 1 or 3 at all.** |
| retry behaviour under concurrent deliveries | **CONFIRMED as designed-for, unchanged.** The optimistic `.eq("status", from_status)` at `:1550-1553` with the zero-rows check at `:1568-1572` is the arbiter; `_Attempt.status_moved` (`:675`, rationale at `:668-674`) keeps a landed UPDATE from being re-issued within one call. Under Option A a losing concurrent delivery would newly observe "transition missing," attempt it, lose the guard, and retry — the behaviour change U7 flagged, still the main residual risk, now additionally interacting with Finding 1 (on a 022-unapplied database the UPDATE fails for a different reason entirely, and the probe must not misread `42703` as "another writer won"). |

---

## 5. Revised plan impact

If D8(b) is granted, three changes to the U7 §5.2 design are now mandatory:

1. **`_is_duplicate_permission` must match both candidate constraint names plus a table-scoped
   `23505` fallback**, mirroring `_is_duplicate_reference:1999-2025`. Keying on
   `uq_deployment_permissions` alone is now known to be wrong.
2. **Recovery must be insert-only on `deployment_permissions`.** No UPDATE, for the Finding 3
   reason.
3. **The completion probe must distinguish `42703` from a lost optimistic race.** On a
   022-unapplied database the transition UPDATE fails structurally, not because a competitor moved
   the row; treating the two alike would make recovery retry a step that can never succeed and
   report a misleading cause.

And one addition to the test plan: `FakeSupabase` must model the entitlement uniqueness under its
**actual** name (Finding 2), and the suite should carry at least one test proving the probe does not
misclassify a non-duplicate `23505` or a `42703`. Modelling the broken `updated_at` triggers in the
double is **not** proposed — that is schema reality, belongs in a real-PostgreSQL test, and U7
acceptance criterion 10 already requires one.

### Added to the D7 query list

| ID | Question | Why it is now first |
|---|---|---|
| **Q14** | Is `022_fix_library_subscriptions_updated_at.sql` applied, and does `library_subscriptions` have an `updated_at` column, on the **current** target? | Finding 1. If not: every marketplace payment is already failing to activate, and that is a P0 independent of CD-8's recovery design. |
| **Q15** | What is the actual unique-constraint name on `deployment_permissions`, and does the table have `updated_at` while `trg_deployment_permissions_updated_at` is attached? | Findings 2 and 3. Determines whether the recovery helper can match, and whether any permission UPDATE is safe. |

Q14 supersedes Q10–Q13 in priority: knowing whether activation works at all precedes counting how
many settlements failed to activate.

---

## 6. Preservation

Entry and exit measured identically: HEAD `074b772a`, write-tree `6f40856c`, staged digest
`65dfc453…`, 22 staged, 0 unmerged. Unstaged tracked 102 at both ends. Untracked 3503 → 3510 across
U8/U9, all concurrent-session drift (`p11bd5_*`, `p11b22_*`, `w1d08_*` artifacts timestamped
21:16–21:53), plus this report.

The five candidate targets are byte-unchanged and clean. `staging/provisioning_snapshot.json` was
**read only** — it is another session's untracked artifact and was not modified, moved or staged.
No existing report was overwritten; this destination was verified absent immediately before writing.

No `git add`, `reset`, `checkout`, `clean`, `stash`, `commit`, `push`, or CI dispatch. No database,
Redis, exchange or provider connection. `git write-tree` was used for the staged-tree invariant; a
concurrent session (`A17` §1b) reads its own constraints as forbidding that call because it writes
unreferenced objects to the shared object database. The alternative digest is available on request.

---

## 7. Status

**CD-8: OPEN / PROPOSED.** Not accepted, not implemented, not closed. Unit-test evidence does not
exist yet and would not suffice; U7 acceptance criteria 1–12 stand, with criterion 2 now partly
satisfied by §3.1 as *evidence* and criterion 10's real-PostgreSQL requirement untouched.

TENANT-01 OPEN · QUOTA-01 OPEN · CD-4 / CD-5 / CD-7 / CD-8 PROPOSED · PostgreSQL enforcement
**UNEXECUTED** · live trading **OFF / NOT CERTIFIED**. No completion percentage revised.

Awaiting **D8(a)** from the register owner and **D8(b)** from the coordinator, separately recorded.
