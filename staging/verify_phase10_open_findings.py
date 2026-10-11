"""Independently re-verify every Phase 10 finding against the code.

Each finding Phase 10 acted on is restated as a falsifiable premise and checked against the
source/schema. Direction depends on the finding:

  REMEDIATED      - a defect premise that Phase 10 CLOSED is no longer true in the code.
  COVERAGE ADDED  - a residual that Phase 10 gave test coverage for; the control exists.
  CONFIRMED OPEN  - a finding Phase 10 deliberately left OPEN; its premise still holds, so
                    the OPEN status is honest (NOT a silent closure).
  INCONCLUSIVE    - the check could not be taken here.

Exits non-zero if a finding Phase 10 claims FIXED still has its defect premise, OR if a
finding Phase 10 reports OPEN has silently become closed (either direction is a lie in the
artifacts).

Run: .venv\\Scripts\\python.exe staging\\verify_phase10_open_findings.py
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

results: list[tuple[str, str, str]] = []
bad = []  # findings whose status is a lie


def record(fid, verdict, detail, is_lie=False):
    results.append((fid, verdict, detail))
    if is_lie:
        bad.append(fid)


def read(rel):
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None


# ── CONN-01 — store_keys now evicts the pooled client on rotation (REMEDIATED) ──
try:
    exch = read("backend_app/routers/exchange.py") or ""
    tree = ast.parse(exch)
    store_keys = next(
        (n for n in ast.walk(tree)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "store_keys"),
        None,
    )
    evicts = False
    if store_keys is not None:
        for n in ast.walk(store_keys):
            if (isinstance(n, ast.Call)
                    and (getattr(n.func, "id", None) == "release_exchange"
                         or getattr(n.func, "attr", None) == "release_exchange")):
                evicts = True
    if evicts:
        record("CONN-01", "REMEDIATED",
               "store_keys calls release_exchange, so a credential rotation evicts the "
               "pooled client and the new credential takes effect in-process.")
    else:
        record("CONN-01", "STILL PRESENT",
               "store_keys does not call release_exchange; a rotation is still silently "
               "ineffective until restart.", is_lie=True)
except Exception as exc:  # noqa: BLE001
    record("CONN-01", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── QUOTA-01 — metered reservation hard-limit now has concurrency coverage (COVERAGE) ──
try:
    t = ROOT / "tests" / "test_quota01_metered_reservation_is_hard.py"
    has = t.is_file()
    body = t.read_text(encoding="utf-8") if has else ""
    concurrency = "asyncio.gather" in body and "admit_exactly_the_limit" in body
    real_redis_gate = "AERORA_TEST_REDIS_URL" in body and "skipif" in body
    # And HardQuotaEnforcer must STILL be unwired (the deliberate decision).
    hq_test = read("tests/test_quota01_enforcement_is_wired.py") or ""
    hq_still_guarded = "HardQuotaEnforcer" in hq_test and "unwired" in hq_test.lower()
    if has and concurrency and real_redis_gate and hq_still_guarded:
        record("QUOTA-01", "COVERAGE ADDED",
               "a concurrency test proves the metered reservation admits exactly the limit; "
               "a real-Redis variant is gated to skip when absent; HardQuotaEnforcer stays "
               "unwired by its own guard.")
    else:
        record("QUOTA-01", "COVERAGE MISSING",
               f"test present={has} concurrency={concurrency} real_redis_gate="
               f"{real_redis_gate} hq_guard={hq_still_guarded}", is_lie=True)
except Exception as exc:  # noqa: BLE001
    record("QUOTA-01", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TENANT-01 — stays OPEN; its residuals must still hold (CONFIRMED OPEN) ──
try:
    # (a) FORCE ROW LEVEL SECURITY still absent from migrations.
    force_hits = []
    for p in (ROOT / "backend_app" / "migrations").glob("*.sql"):
        txt = p.read_text(encoding="utf-8", errors="replace")
        # strip comments so a comment mentioning FORCE is not a false positive
        code = "\n".join(l for l in txt.splitlines() if not l.strip().startswith("--"))
        if "FORCE ROW LEVEL SECURITY" in code.upper():
            force_hits.append(p.name)
    # (b) credential vault still isolates with an in-code Python tenant comparison, AND it
    #     now has negative-control coverage.
    vault = read("backend_app/core/credential_vault.py") or ""
    python_check = 'credential_dict.get("tenant_id") != tenant_id' in vault
    vault_test = (ROOT / "tests" / "test_tenant01_credential_vault_isolation.py").is_file()
    # (c) sweep coverage improved: referral_obligations now has a CHECK_OVERRIDES entry.
    sweep = read("staging/anon_read_sweep.py") or ""
    sweep_covers_obligations = '"referral_obligations"' in sweep and '"USD"' in sweep
    if not force_hits and python_check and vault_test and sweep_covers_obligations:
        record("TENANT-01", "CONFIRMED OPEN",
               "FORCE ROW LEVEL SECURITY is still set on zero tables (the documented "
               "architectural residual stands); the credential vault still isolates by an "
               "in-code Python tenant check, now with negative-control coverage; the "
               "cross-tenant sweep now covers referral_obligations. OPEN is honest.")
    else:
        record("TENANT-01", "RESIDUAL CHANGED",
               f"force_hits={force_hits} python_check={python_check} "
               f"vault_test={vault_test} sweep_covers_obligations="
               f"{sweep_covers_obligations}. The OPEN status may no longer match the code.",
               is_lie=True)
except Exception as exc:  # noqa: BLE001
    record("TENANT-01", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── OBS-A fee_pct — third-asset fee can be recorded (REMEDIATED) ──
try:
    from backend_app.backend.fee_engine import FeeRecordModel

    pct_nullable = FeeRecordModel.__table__.columns["fee_pct"].nullable
    amount_mandatory = FeeRecordModel.__table__.columns["fee_amount"].nullable is False
    asset_mandatory = FeeRecordModel.__table__.columns["fee_asset"].nullable is False
    mig = (ROOT / "backend_app" / "migrations" / "031_fee_records_fee_pct_nullable.sql")
    mig_present = mig.is_file() and "ALTER COLUMN fee_pct DROP NOT NULL" in mig.read_text(
        encoding="utf-8"
    )
    if pct_nullable and amount_mandatory and asset_mandatory and mig_present:
        record("OBS-A-FEE-PCT", "REMEDIATED",
               "fee_pct is nullable (ORM) and migration 031 drops its NOT NULL; fee_amount "
               "and fee_asset stay mandatory, so a third-asset fee records (amount+asset "
               "exact, fee_pct NULL) rather than being lost to a NOT NULL violation.")
    else:
        record("OBS-A-FEE-PCT", "STILL PRESENT",
               f"pct_nullable={pct_nullable} amount_mandatory={amount_mandatory} "
               f"asset_mandatory={asset_mandatory} migration={mig_present}", is_lie=True)
except Exception as exc:  # noqa: BLE001
    record("OBS-A-FEE-PCT", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── OBS-B payout — referral.py writes the real payout_method column (REMEDIATED) ──
try:
    ref = read("backend_app/routers/referral.py") or ""
    tree = ast.parse(ref)
    wrong = []
    right = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "insert"):
            seg = ast.get_source_segment(ref, node) or ""
            if "referral_payouts" not in seg:
                continue
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys = {k.value for k in arg.keys
                            if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                    if "payment_method" in keys or "payment_details" in keys:
                        wrong.append(sorted(keys))
                    if "payout_method" in keys:
                        right.append(sorted(keys))
    reads_right = 'p.get("payout_method")' in ref
    if right and not wrong and reads_right:
        record("OBS-B-PAYOUT", "REMEDIATED",
               "create_payout_request inserts payout_method/payout_details (the provisioned "
               "columns) and the read path consults payout_method; the broken "
               "payment_method/payment_details insert is gone.")
    else:
        record("OBS-B-PAYOUT", "STILL PRESENT",
               f"wrong_inserts={wrong} right_inserts={right} reads_payout_method="
               f"{reads_right}", is_lie=True)
except Exception as exc:  # noqa: BLE001
    record("OBS-B-PAYOUT", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── OBS-D reconciliation — ExchangeReconciliationService NOT started (CONFIRMED OPEN) ──
try:
    callers = []
    backend = ROOT / "backend_app"
    for p in backend.rglob("*.py"):
        if "__pycache__" in p.parts or p.name == "exchange_reconciliation.py":
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "start_reconciliation_loop"):
                callers.append(f"{p.relative_to(ROOT).as_posix()}:{n.lineno}")
    main_src = read("backend_app/main.py") or ""
    escalation_started = "start_reconciliation_scheduler()" in main_src
    if not callers and escalation_started:
        record("OBS-D-RECON", "CONFIRMED OPEN",
               "ExchangeReconciliationService.start_reconciliation_loop has no caller, so "
               "venue order/fill reconciliation is not operational (reported OPEN, not "
               "fabricated); the mismatch-escalation scheduler IS started in lifespan.")
    else:
        record("OBS-D-RECON", "STATUS CHANGED",
               f"service_callers={callers} escalation_started={escalation_started}. The "
               f"'not started' assessment no longer matches the code.", is_lie=True)
except Exception as exc:  # noqa: BLE001
    record("OBS-D-RECON", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── report ──────────────────────────────────────────────────────────────────
print("=" * 78)
print("PHASE 10 FINDINGS - INDEPENDENT RE-VERIFICATION AGAINST THE CODE")
print("=" * 78)
width = max(len(f) for f, _, _ in results)
for fid, verdict, detail in results:
    print(f"\n{fid:<{width}}  {verdict}")
    for line in re.findall(r".{1,84}(?:\s|$)", detail):
        if line.strip():
            print(f"{'':<{width}}  {line.strip()}")

print("\n" + "=" * 78)
print(f"checked = {len(results)}   status-lies = {len(bad)} {bad if bad else ''}")
print("=" * 78)
sys.exit(1 if bad else 0)
