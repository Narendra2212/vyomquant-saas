# Release Sign-off

| | |
|---|---|
| Release candidate | `claude/vyomquant-launch-blockers-wv92gv` (commits 0851b9c6 … 16b3140d, on top of main f3323dbd) |
| Audit dates | 2026-11-19 → 2026-11-20 UTC |
| Auditor | Claude Code, as Principal QA Architect, under the master certification prompt |
| **Verdict** | **NO-GO** today → **CONDITIONAL GO** when C1–C3 are met |

## Conditions

| # | Condition | Status |
|---|---|---|
| C1 | Email signup works: Supabase Auth SMTP fixed, signup and confirmation re-tested | ☐ Owner |
| C2 | PR merged with CI green; migration 080 applied; backend then frontend deployed; post-deploy smoke tests in `MIGRATION_AND_DEPLOYMENT_CHECKLIST.md` §6 pass | ☐ Owner |
| C3 | BLOCKED rows re-run to PASS: EX-04, TN-05 (Testnet with the QA identity), MP-04 / PR-05 (second identity), ML-03 (Pro account), BL-03 (staging test-mode checkout), OBS-02 (CloudWatch) | ☐ QA + owner |

## Evidence summary

**Defects fixed: 13.** One P0, seven P1, four P2, one P3 (CI lane). Each has a regression test proven to fail without its fix.

**Defects open: 1 P0 (owner action), 5 P2, 7 P3.** None of the open P2s lets money move or crosses a tenant boundary:
- QUOTA-422-01 over-charges quota.
- STRAT-QUOTA-01 lets a user exceed their bot count, but every order still passes the full live gate.
- BT-504-01 and BT-RISK-01 are disclosed backtester limitations.
- MARGIN-01 is a display inconsistency.

**Suites:**
- Frontend vitest: 4,056 / 4,056 (fresh build).
- Backend pytest, full: 16,624 passed, 1 failed in the first run. The failure was the CI-lane listing guard catching the new PG test (CI-LANE-01), fixed in 16b3140d. The second full run is recorded below.
- PostgreSQL lane on a fresh 078–080 provision: 1,399 passed. The 5 failures fail identically on the pre-078 schema.

## Safety attestation

- Binance Testnet only (`VYOMQUANT_LIVE_SANDBOX_ONLY=true`). No production exchange credentials were used or read.
- No real-money order, charge, subscription, payout or email.
- MFA was never bypassed or forged.
- No secret, key or token was printed, logged or committed.
- QA records are labelled `[QA-CERT]`; QA-account state changes were reverted (see `BUG_REMEDIATION_LOG.md`).
- Schema changes in production were limited to 078 and 079, each applied after explicit owner approval and carrying a rollback.
- Nothing was merged to main or deployed.

## Sign-off

| Role | Name | Decision | Date |
|---|---|---|---|
| QA (audit) | Claude Code | NO-GO → CONDITIONAL GO on C1–C3 | 2026-11-20 |
| Platform owner | | | |
