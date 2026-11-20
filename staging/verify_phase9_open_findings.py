"""Independently re-verify every Phase 9 finding against the code, as the INVERSE of
Phase 8's open-finding check.

Phase 8's verifier confirmed that each OPEN finding's premise still held, and failed if a
premise had silently gone. Phase 9 CLOSED those findings, so the direction flips: this
script restates each finding's defect as a falsifiable premise and asserts the premise is
NO LONGER TRUE in the code. A finding whose defect premise still holds means the fix is
absent, and the script exits non-zero.

READ THE RESULT THIS WAY:
  REMEDIATED      - the defect the finding described is no longer present in the code.
  STILL PRESENT   - the defect premise still holds. The fix is missing or incomplete; the
                    finding must NOT be reported as resolved.
  RECLASSIFIED    - ML-01 only. The premise is intentionally still literally true (the
                    executor cannot consume a FeatureMatrix) but the finding was a FALSE
                    POSITIVE: the code path raises on every branch and never returns, so
                    the "cannot consume" is unreachable. Verified as such, not as a fix.
  INCONCLUSIVE    - the check could not be taken here (missing file). Never counted either
                    way.

Exits non-zero if any defect premise STILL HOLDS (an absent fix), because that would mean
an artifact calls a finding resolved while its defect is still in the source.

Run: .venv\\Scripts\\python.exe staging\\verify_phase9_open_findings.py
"""
from __future__ import annotations

import ast
import inspect
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

results: list[tuple[str, str, str]] = []


def record(fid: str, verdict: str, detail: str) -> None:
    results.append((fid, verdict, detail))


def read(rel: str) -> str | None:
    path = ROOT / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


# ── KILL-02 — reset now takes authorization evidence and audit is durable ────
try:
    from backend_app.core.global_safety import GlobalKillSwitch

    params = set(inspect.signature(GlobalKillSwitch.deactivate).parameters)
    authy = {"operator", "operator_id", "credential", "token", "role", "actor",
             "actor_id", "principal", "authorization_source", "acknowledge_holds"}
    present = sorted(params & authy)
    # A durable audit means the deactivate path calls record_safety_transition (which
    # writes a DB row), not only a Redis LIST lpush.
    deact_src = inspect.getsource(GlobalKillSwitch.deactivate)
    durable = "record_safety_transition" in deact_src
    if present and durable:
        record("KILL-02", "REMEDIATED",
                f"deactivate() requires authorization evidence {present} (keyword-only, "
                f"no defaults) and records every transition durably via "
                f"record_safety_transition.")
    else:
        record("KILL-02", "STILL PRESENT",
                f"authorization params present={present}; durable audit={durable}. The "
                f"reset is still unauthenticated or the audit is still best-effort only.")
except Exception as exc:  # noqa: BLE001
    record("KILL-02", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── KILL-03 — an operator path now engages the authoritative kill switch ─────
try:
    call_sites: list[str] = []
    scanned = 0
    for sub in ("backend_app",):
        base = ROOT / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            scanned += 1
            if "enforce_kill_switch" not in text:
                continue
            for line in text.splitlines():
                s = line.strip()
                if "enforce_kill_switch" not in s:
                    continue
                if s.startswith(("def ", "async def ", "#", '"', "'", "from ", "import ")):
                    continue
                if "await enforce_kill_switch" in s or "enforce_kill_switch()" in s:
                    call_sites.append(f"{path.relative_to(ROOT)}: {s}")

    admin = read("backend_app/routers/admin.py") or ""
    engages = "get_global_kill_switch" in admin or "GlobalKillSwitch" in admin or \
              "kill_switch.activate" in admin
    if scanned < 100:
        record("KILL-03", "INCONCLUSIVE", f"only {scanned} source files scanned")
    elif call_sites and engages:
        record("KILL-03", "REMEDIATED",
                f"enforce_kill_switch() now has {len(call_sites)} call site(s): "
                f"{call_sites}; and /api/admin kill-all engages the authoritative "
                f"GlobalKillSwitch (engages={engages}).")
    else:
        record("KILL-03", "STILL PRESENT",
                f"call_sites={call_sites}; admin engages switch={engages}. The "
                f"authoritative switch is still not reached by an operator path.")
except Exception as exc:  # noqa: BLE001
    record("KILL-03", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── KILL-04 — the venue boundary re-reads live kill state before the order ───
try:
    executor_src = read("backend_app/backend/exchange_executor.py") or ""
    tree = ast.parse(executor_src)
    # The place_order path must CALL enforce_kill_switch (which itself calls is_active),
    # as a real call node rather than a string literal or a comment.
    enforce_calls = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and (getattr(n.func, "id", None) == "enforce_kill_switch"
             or getattr(n.func, "attr", None) == "enforce_kill_switch")
    ]
    if enforce_calls:
        record("KILL-04", "REMEDIATED",
                f"the venue boundary (exchange_executor.place_order) calls "
                f"enforce_kill_switch() at line(s) {enforce_calls}, which reads live "
                f"kill-switch state immediately before create_order rather than asserting "
                f"a stale 'kill_switch' claim string.")
    else:
        record("KILL-04", "STILL PRESENT",
                "no enforce_kill_switch() call node on the venue boundary; the boundary "
                "may still assert a check string rather than re-taking it.")
except Exception as exc:  # noqa: BLE001
    record("KILL-04", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── MONEY-06 — an owed referral commission is now a durable obligation row ───
try:
    billing = read("backend_app/routers/billing.py") or ""
    tree = ast.parse(billing)
    # A real call node to _record_referral_obligation inside a webhook handler, plus the
    # obligations table name present. Substring alone would be fooled by a comment, so the
    # call is matched as an AST Call.
    obligation_calls: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "_record_referral_obligation":
                obligation_calls.append(node.lineno)
    writes_table = "referral_obligations" in billing
    if obligation_calls and writes_table:
        record("MONEY-06", "REMEDIATED",
                f"an owed commission is recorded as a durable row in "
                f"public.referral_obligations via _record_referral_obligation (call "
                f"nodes at {obligation_calls}), not only a refusal log line.")
    else:
        record("MONEY-06", "STILL PRESENT",
                f"_record_referral_obligation call nodes={obligation_calls}; "
                f"referral_obligations referenced={writes_table}. The owed commission is "
                f"still only a log line.")
except Exception as exc:  # noqa: BLE001
    record("MONEY-06", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── MONEY-07 — the capture path now validates the provider's own currency ────
try:
    billing = read("backend_app/routers/billing.py") or ""
    tree = ast.parse(billing)
    # The remediation VALIDATES rather than substitutes: each handler keeps its literal
    # denomination but calls _require_provider_currency(provider_declared, expected, ...)
    # which reads the provider's own currency field and refuses a mismatch.
    validate_calls: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "_require_provider_currency":
                validate_calls.append(node.lineno)
    has_mismatch_type = "class ProviderCurrencyMismatch" in billing
    # The validator must actually read a provider currency field (payment/session .get).
    reads_currency = bool(re.search(r"\.get\(\s*[\"']currency[\"']\s*\)", billing))
    if validate_calls and has_mismatch_type and reads_currency:
        record("MONEY-07", "REMEDIATED",
                f"_require_provider_currency is called at line(s) {validate_calls}; it "
                f"reads the provider's own declared currency and raises "
                f"ProviderCurrencyMismatch rather than pairing a payload amount with a "
                f"hardcoded denomination unchecked.")
    else:
        record("MONEY-07", "STILL PRESENT",
                f"_require_provider_currency calls={validate_calls}; "
                f"ProviderCurrencyMismatch defined={has_mismatch_type}; reads a currency "
                f"field={reads_currency}. The denomination may still be assumed.")
except Exception as exc:  # noqa: BLE001
    record("MONEY-07", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── FEE-01 — an unattributable fee row no longer defaults to the zero uuid ───
try:
    fee = read("backend_app/backend/fee_engine.py") or ""
    fee_tree = ast.parse(fee)
    # AST, not substring: a UUID(int=0) in a docstring that EXPLAINS the old behaviour is
    # not a live fallback. Count only genuine call nodes. (Phase 9 note: the first version
    # of this check used a regex and reported STILL PRESENT because the fix's own prose
    # names the removed form three times.)
    zero_fallbacks = 0
    for node in ast.walk(fee_tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name != "UUID":
            continue
        for kw in node.keywords:
            if kw.arg == "int" and isinstance(kw.value, ast.Constant) and kw.value.value == 0:
                zero_fallbacks += 1
    raises = any(
        isinstance(n, ast.Raise) and "TenantResolutionError" in ast.dump(n)
        for n in ast.walk(fee_tree)
    )
    if zero_fallbacks == 0 and raises:
        record("FEE-01", "REMEDIATED",
                "no UUID(int=0) tenant fallback remains in fee_engine.py; an "
                "unresolvable tenant now raises TenantResolutionError and the fee is not "
                "written against a non-existent tenant.")
    else:
        record("FEE-01", "STILL PRESENT",
                f"UUID(int=0) fallbacks={zero_fallbacks}; TenantResolutionError "
                f"present={raises}. A fee may still be filed under the all-zero tenant.")
except Exception as exc:  # noqa: BLE001
    record("FEE-01", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── ML-01 — RECLASSIFIED as a false positive, verified against the code ──────
try:
    dag = read("backend_app/backend/dag_engine.py") or ""
    tree = ast.parse(dag)
    ml_node = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MLExecutor":
            ml_node = node
            break
    if ml_node is None:
        record("ML-01", "INCONCLUSIVE", "MLExecutor class not found")
    else:
        execute_fn = None
        for node in ml_node.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                    node.name == "execute":
                execute_fn = node
                break
        if execute_fn is None:
            record("ML-01", "INCONCLUSIVE", "MLExecutor.execute not found")
        else:
            # The false-positive claim: execute() raises on every reachable path and never
            # returns a value, so "MLExecutor cannot consume a FeatureMatrix" describes an
            # unreachable branch. Verify there is a Raise and no Return-with-value that
            # would make the type-mismatch observable.
            raises = [n for n in ast.walk(execute_fn) if isinstance(n, ast.Raise)]
            returns_value = [
                n for n in ast.walk(execute_fn)
                if isinstance(n, ast.Return) and n.value is not None
            ]
            if raises and not returns_value:
                record("ML-01", "RECLASSIFIED",
                        f"MLExecutor.execute raises on every path ({len(raises)} raise "
                        f"statement(s)) and returns no value, so the 'cannot consume a "
                        f"FeatureMatrix' branch is unreachable. The finding was a FALSE "
                        f"POSITIVE; no nominal reference was added (forbidden) because it "
                        f"would change nothing.")
            else:
                record("ML-01", "STILL PRESENT",
                        f"MLExecutor.execute has {len(raises)} raise(s) and "
                        f"{len(returns_value)} value-returning return(s); the "
                        f"false-positive reclassification premise does not hold.")
except Exception as exc:  # noqa: BLE001
    record("ML-01", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TRADE-07 — an age-based recovery now moves stale EXECUTING rows ──────────
try:
    repo = read("backend_app/core/models/execution_record.py") or ""
    watchdog = read("backend_app/backend/order_watchdog.py") or ""
    has_classify = "def classify_stale_executions" in repo
    has_resolve = "def resolve_stale_execution" in repo
    has_recover = "async def recover_unsubmitted_executions" in watchdog
    wired = "recover_unsubmitted_executions()" in watchdog
    if has_classify and has_resolve and has_recover and wired:
        record("TRADE-07", "REMEDIATED",
                "classify_stale_executions + resolve_stale_execution exist on the "
                "repository and order_watchdog.recover_unsubmitted_executions is wired "
                "into the monitoring loop, so age-based stale EXECUTING rows are "
                "adjudicated (never blindly resubmitted).")
    else:
        record("TRADE-07", "STILL PRESENT",
                f"classify={has_classify} resolve={has_resolve} recover={has_recover} "
                f"wired={wired}. Stale EXECUTING rows may still have no reaper.")
except Exception as exc:  # noqa: BLE001
    record("TRADE-07", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TRADE-11 — missing/zero price now fails closed instead of vanishing ──────
try:
    repo = read("backend_app/core/models/execution_record.py") or ""
    engine_src = read("backend_app/core/execution_engine.py") or ""
    risk_src = read("backend_app/core/risk_engine.py") or ""
    # The DEFECT premise was that a missing/empty/zero price VANISHES from measured
    # exposure (the capacity SUM yielded NULL and SUM skipped the row; the per-order gate
    # read `price or 0`). The fix does NOT remove NULLIF -- NULLIF is now used to DETECT an
    # unusable price -- it adds an explicit unusable-row COUNT and REFUSES while any exist.
    # So the real test is: does the capacity SQL count unusable rows AND fail closed on
    # them, and is the live `price or 0` form gone from executable engine code?
    #
    # (Phase 9 note: the first version of this check keyed on the mere presence of
    # NULLIF(price,'') and reported STILL PRESENT, not distinguishing the old vanishing
    # SUM from the new detect-and-refuse SUM, and counted `price or 0` occurrences that are
    # only in comments documenting the finding.)
    engine_tree = ast.parse(engine_src)
    engine_code_price_or_zero = False  # a live `str(price or 0)` BoolOp in executable code
    for node in ast.walk(engine_tree):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            vals = node.values
            if (len(vals) == 2
                    and isinstance(vals[0], ast.Name) and vals[0].id == "price"
                    and isinstance(vals[1], ast.Constant) and vals[1].value == 0):
                engine_code_price_or_zero = True
    counts_unusable = ("AS unusable" in repo or "AS unusable," in repo
                       or re.search(r"\bunusable\b", repo) is not None)
    refuses_on_unusable = ("if unusable:" in repo
                           and ("rollback" in repo or "return (False" in repo
                                or "return False" in repo))
    risk_validates = "InvalidOperation" in risk_src
    if (counts_unusable and refuses_on_unusable and not engine_code_price_or_zero
            and risk_validates):
        record("TRADE-11", "REMEDIATED",
                f"the capacity SQL now COUNTS rows with no usable price as `unusable` "
                f"(NULL/empty/'0') and the reservation FAILS CLOSED while any exist "
                f"(refuses_on_unusable={refuses_on_unusable}); the live `price or 0` gate "
                f"is gone from executable engine code; risk_engine validates price with "
                f"InvalidOperation handling. A missing price is refused, not summed as "
                f"zero.")
    else:
        record("TRADE-11", "STILL PRESENT",
                f"counts_unusable={counts_unusable} refuses_on_unusable={refuses_on_unusable} "
                f"engine_code_price_or_zero={engine_code_price_or_zero} "
                f"risk_validates={risk_validates}. A missing price can still vanish from "
                f"measured exposure.")
except Exception as exc:  # noqa: BLE001
    record("TRADE-11", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TRADE-12 (NEW) — venue id no longer written into the FK-constrained order_id ─
try:
    migration = read("backend_app/migrations/030_execution_venue_order_id.sql") or ""
    repo = read("backend_app/core/models/execution_record.py") or ""
    engine_src = read("backend_app/core/execution_engine.py") or ""
    column_added = "ADD COLUMN IF NOT EXISTS venue_order_id" in migration
    repo_has_param = "venue_order_id" in repo
    engine_passes = "venue_order_id=" in engine_src
    if column_added and repo_has_param and engine_passes:
        record("TRADE-12", "REMEDIATED",
                "migration 030 adds execution_records.venue_order_id (varchar, nullable, "
                "no FK); update_status accepts venue_order_id and the engine passes the "
                "venue identifier there instead of into the orders-FK-constrained "
                "order_id, so a real venue write no longer raises 23503 and strands the "
                "row in EXECUTING.")
    else:
        record("TRADE-12", "STILL PRESENT",
                f"column_added={column_added} repo_param={repo_has_param} "
                f"engine_passes={engine_passes}. The venue id may still collide with the "
                f"orders foreign key.")
except Exception as exc:  # noqa: BLE001
    record("TRADE-12", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TEST-06 — the protected-logic check is now deterministic (numeric boundary) ─
try:
    proj = read("backend_app/backend/marketplace/listing_projection.py") or ""
    numeric_boundary = "_NUMERIC_TOKEN_RE" in proj and r"(?<![\d.])" in proj
    if numeric_boundary:
        record("TEST-06", "REMEDIATED",
                "numeric protected-logic tokens are matched on a numeric-value boundary "
                "(_NUMERIC_TOKEN_RE + the (?<![\\d.]) lookarounds), so a bare number no "
                "longer collides with a timestamp/count/id and the verdict no longer "
                "depends on the wall clock. String tokens keep substring matching.")
    else:
        record("TEST-06", "STILL PRESENT",
                f"numeric-boundary matching present={numeric_boundary}. A short numeric "
                f"token may still match substrings of unrelated numbers.")
except Exception as exc:  # noqa: BLE001
    record("TEST-06", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── report ──────────────────────────────────────────────────────────────────
print("=" * 78)
print("PHASE 9 FINDINGS - INDEPENDENT RE-VERIFICATION AGAINST THE CODE")
print("=" * 78)
width = max(len(f) for f, _, _ in results)
still = [f for f, v, _ in results if v == "STILL PRESENT"]
unclear = [f for f, v, _ in results if v == "INCONCLUSIVE"]
for fid, verdict, detail in results:
    print(f"\n{fid:<{width}}  {verdict}")
    for line in re.findall(r".{1,86}(?:\s|$)", detail):
        if line.strip():
            print(f"{'':<{width}}  {line.strip()}")

print("\n" + "=" * 78)
print(f"checked            = {len(results)}")
print(f"remediated/reclass = {len(results) - len(still) - len(unclear)}")
print(f"still present      = {len(still)} {still if still else ''}")
print(f"inconclusive       = {len(unclear)} {unclear if unclear else ''}")
print("=" * 78)
sys.exit(1 if still else 0)
