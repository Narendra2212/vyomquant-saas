"""Independently re-verify every finding Phase 8 leaves OPEN, against the code.

WHY THIS EXISTS. "Still open" is a claim about the code as it stands, and an audit that
carries a finding forward because an earlier summary said so is exactly the failure this
phase already had to correct once (MONEY-04 was carried as an open P0 for a defect that no
longer existed - see CORR-P8-01). So each finding below is restated as a FALSIFIABLE check
against the source, and the check is run.

READ THE RESULT THIS WAY:
  CONFIRMED OPEN  - the premise still holds in the code. The finding stands.
  PREMISE GONE    - the thing the finding describes is no longer true. The finding must be
                    re-examined and probably reclassified. NOT automatically "fixed".
  INCONCLUSIVE    - the check could not be taken here (missing file, needs a database).
                    Reported as such and never counted as either.

Exits non-zero if any finding's premise has gone, because that means an artifact somewhere
says OPEN about something that is not.
"""
from __future__ import annotations

import ast
import inspect
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


# ── KILL-02 — reset takes no authorization, audit is not durable ──────────────
try:
    from backend_app.core.global_safety import GlobalKillSwitch

    params = set(inspect.signature(GlobalKillSwitch.deactivate).parameters)
    authy = {"operator", "credential", "token", "role", "actor", "actor_id", "principal",
             "user", "user_id", "auth"}
    has_auth = bool(params & authy)
    activate_src = inspect.getsource(GlobalKillSwitch.activate)
    retention = re.search(r"_redis_expire\(\s*self\.HISTORY_KEY\s*,\s*(\d+)", activate_src)
    if has_auth:
        record("KILL-02", "PREMISE GONE",
               f"deactivate() now takes {sorted(params & authy)}")
    else:
        record(
            "KILL-02", "CONFIRMED OPEN",
            f"deactivate() params = {sorted(params)}; no credential/role/operator among "
            f"them. History retention = "
            f"{retention.group(1) + 's' if retention else 'not found'} on a Redis LIST.",
        )
except Exception as exc:  # noqa: BLE001
    record("KILL-02", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── KILL-03 — no operator-facing path engages the global kill switch ─────────
try:
    call_sites: list[str] = []
    scanned = 0
    for sub in ("backend_app", "backend", "scripts", "core"):
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
    kill_all = ""
    if admin:
        tree = ast.parse(admin)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seg = ast.get_source_segment(admin, node) or ""
                if "HALT" in seg and ("kill" in node.name.lower() or "kill-all" in seg):
                    kill_all = seg
                    break
    engages = bool(kill_all) and (
        "get_global_kill_switch" in kill_all or "GlobalKillSwitch" in kill_all
    )

    if scanned < 100:
        record("KILL-03", "INCONCLUSIVE", f"only {scanned} source files scanned")
    elif call_sites or engages:
        record("KILL-03", "PREMISE GONE",
               f"call_sites={call_sites}; kill_all_engages_switch={engages}")
    else:
        record(
            "KILL-03", "CONFIRMED OPEN",
            f"enforce_kill_switch() has 0 call sites across {scanned} source files; the "
            f"/api/admin/kill-all handler ({'found' if kill_all else 'NOT FOUND'}) does "
            f"not reference GlobalKillSwitch.",
        )
except Exception as exc:  # noqa: BLE001
    record("KILL-03", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── KILL-04 — the venue gate records "kill_switch" as a claim, not a re-check ─
try:
    engine_src = read("backend_app/core/execution_engine.py") or ""
    engine_tree = ast.parse(engine_src)

    # AST, NOT A TEXT SEARCH. The first version of this check counted `.is_active()`
    # textually, found two, and reported the finding's premise gone. The second occurrence
    # is inside the COMMENT that annotates the asserted string:
    #     "kill_switch",   # get_global_kill_switch().is_active()
    # There is exactly one real call. A check that cannot tell a call from a comment about
    # a call is not evidence either way.
    real_is_active = [
        n.lineno
        for n in ast.walk(engine_tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "is_active"
    ]
    claim_strings = [
        n.lineno
        for n in ast.walk(engine_tree)
        if isinstance(n, ast.Constant) and n.value == "kill_switch"
    ]
    if not claim_strings:
        record("KILL-04", "PREMISE GONE",
               "execution_engine.py no longer passes a 'kill_switch' check string")
    elif len(real_is_active) > 1:
        record("KILL-04", "PREMISE GONE",
               f"is_active() is now genuinely called at lines {real_is_active}, so the "
               f"boundary may re-check rather than assert")
    else:
        record(
            "KILL-04", "CONFIRMED OPEN",
            f"'kill_switch' is passed as a literal check string at line(s) "
            f"{claim_strings}, while is_active() is genuinely called only at line(s) "
            f"{real_is_active} - so the venue boundary asserts the check rather than "
            f"re-taking it.",
        )
except Exception as exc:  # noqa: BLE001
    record("KILL-04", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── MONEY-06 — Razorpay referral commissions are never credited ──────────────
try:
    billing = read("backend_app/routers/billing.py") or ""
    tree = ast.parse(billing)
    razorpay_body = ""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "razorpay" in node.name:
            seg = ast.get_source_segment(billing, node) or ""
            if len(seg) > len(razorpay_body):
                razorpay_body = seg
    # REAL CALL NODES, NOT A SUBSTRING. The first version of this check asked whether the
    # text "process_referral_commission" appeared anywhere in the handler. It does - in the
    # comment that records that this branch USED to call it:
    #     # This branch used to call `process_referral_commission` with the INR
    # so the check reported the finding's premise gone when the opposite is true.
    credit_calls: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "razorpay" not in node.name:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            if "process_referral_commission" in ast.dump(inner):
                credit_calls.append(inner.lineno)

    refuses = "referral commission SKIPPED for Razorpay" in razorpay_body
    writes_row = re.search(r"INSERT\s+INTO\s+public\.referral_commissions", razorpay_body,
                           re.IGNORECASE) is not None
    if credit_calls or writes_row:
        record("MONEY-06", "PREMISE GONE",
               f"the Razorpay path now credits (call nodes at {credit_calls}, "
               f"insert={writes_row})")
    elif not razorpay_body:
        record("MONEY-06", "INCONCLUSIVE", "no razorpay handler found by AST")
    else:
        record(
            "MONEY-06", "CONFIRMED OPEN",
            f"across every razorpay-named handler there are ZERO real "
            f"process_referral_commission call nodes and no referral_commissions INSERT "
            f"(largest handler {len(razorpay_body)} chars); the only artefact of an owed "
            f"commission is the refusal log (present={refuses}).",
        )
except Exception as exc:  # noqa: BLE001
    record("MONEY-06", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── MONEY-07 — the capture path never reads the payment's own currency ───────
try:
    billing = read("backend_app/routers/billing.py") or ""
    billing_tree = ast.parse(billing)

    # AST AGAIN. The first version used `_provider_major_amount\([^)]*?["']INR["']`, and the
    # real call is `_provider_major_amount(payment.get("amount"), "INR")` - the `[^)]*?`
    # stops at the `)` of the NESTED `.get(...)` call and never reaches the literal. It
    # reported "no hardcoded INR remains" about a line that hardcodes INR.
    #
    # The finding is also SHARPENED here. "never reads payment.currency" was too broad:
    # `_razorpay_settlement_metadata` does read it for metadata. The defect is narrower and
    # worse-stated-loosely - the CONVERSION at the capture site pairs a payload amount with
    # a literal currency.
    conversions: list[tuple[int, str]] = []
    for node in ast.walk(billing_tree):
        if not isinstance(node, ast.Call):
            continue
        if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) != \
                "_provider_major_amount":
            continue
        literals = [
            a.value for a in node.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        if literals:
            conversions.append((node.lineno, literals[0]))

    inr_sites = [ln for ln, cur in conversions if cur == "INR"]

    # Does the capture handler itself consult the payload's currency?
    capture_reads_currency: list[int] = []
    for node in ast.walk(billing_tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and \
                node.name == "razorpay_webhook":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and \
                        getattr(inner.func, "attr", None) == "get":
                    args = [a.value for a in inner.args
                            if isinstance(a, ast.Constant)]
                    target = getattr(getattr(inner.func, "value", None), "id", "")
                    if "currency" in args and target in ("payment", "entity"):
                        capture_reads_currency.append(inner.lineno)

    if not inr_sites:
        record("MONEY-07", "PREMISE GONE",
               f"no _provider_major_amount(..., 'INR') call remains; conversions = "
               f"{conversions}")
    elif capture_reads_currency:
        record("MONEY-07", "PREMISE GONE",
               f"razorpay_webhook now reads the payment's own currency at lines "
               f"{capture_reads_currency}")
    else:
        record(
            "MONEY-07", "CONFIRMED OPEN",
            f"_provider_major_amount is called with a LITERAL currency at lines "
            f"{conversions}; the INR site(s) {inr_sites} pair a payload amount with a "
            f"hardcoded denomination, and razorpay_webhook never reads the captured "
            f"payment's own currency field.",
        )
except Exception as exc:  # noqa: BLE001
    record("MONEY-07", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── FEE-01 — an unattributable fee row is owned by the all-zero uuid ─────────
try:
    fee = read("backend_app/backend/fee_engine.py") or ""
    zero_fallbacks = len(re.findall(r"UUID\(\s*int\s*=\s*0\s*\)", fee))
    if zero_fallbacks == 0:
        record("FEE-01", "PREMISE GONE", "no UUID(int=0) fallback remains in fee_engine.py")
    else:
        record(
            "FEE-01", "CONFIRMED OPEN",
            f"{zero_fallbacks} UUID(int=0) tenant fallback(s) in fee_engine.py, so a fee "
            f"whose parent row is missing is written against a tenant that does not exist.",
        )
except Exception as exc:  # noqa: BLE001
    record("FEE-01", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── ML-01 — MLExecutor cannot consume a FeatureMatrix ───────────────────────
try:
    dag = read("backend_app/backend/dag_engine.py") or ""
    start = dag.find("class MLExecutor")
    body = ""
    if start >= 0:
        nxt = dag.find("\nclass ", start + 10)
        body = dag[start: nxt if nxt > 0 else len(dag)]
    fm = body.count("FeatureMatrix")
    series = body.count("pd.Series")
    if not body:
        record("ML-01", "INCONCLUSIVE", "MLExecutor class not found")
    elif fm > 0:
        record("ML-01", "PREMISE GONE",
               f"MLExecutor now references FeatureMatrix {fm} time(s)")
    else:
        record(
            "ML-01", "CONFIRMED OPEN",
            f"MLExecutor body ({len(body)} chars): FeatureMatrix references = {fm}, "
            f"pd.Series references = {series}.",
        )
except Exception as exc:  # noqa: BLE001
    record("ML-01", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TRADE-07 — no reaper for stale EXECUTING execution records ───────────────
try:
    reapers: list[str] = []
    for sub in ("backend_app", "backend", "scripts"):
        base = ROOT / sub
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "execution_records" not in text:
                continue
            # A reaper would move rows OFF 'EXECUTING' based on age.
            for m in re.finditer(r"UPDATE\s+execution_records[\s\S]{0,400}", text,
                                 re.IGNORECASE):
                seg = m.group(0)
                if re.search(r"EXECUTING", seg) and re.search(
                    r"(interval|now\(\)\s*-|age|stale|older)", seg, re.IGNORECASE
                ):
                    if "claim_execution_within_capacity" in text and "SUM(" in seg:
                        continue
                    reapers.append(f"{path.relative_to(ROOT)}")
    reapers = sorted(set(reapers))
    if reapers:
        record("TRADE-07", "PREMISE GONE",
               f"an age-based EXECUTING reaper now appears in {reapers}")
    else:
        record(
            "TRADE-07", "CONFIRMED OPEN (partially mitigated)",
            "no age-based UPDATE moves execution_records off EXECUTING anywhere in the "
            "application source. The Phase 8 capacity reservation's 300s stale cutoff stops "
            "such a row holding CAPACITY, but the row itself stays unclaimable.",
        )
except Exception as exc:  # noqa: BLE001
    record("TRADE-07", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TRADE-11 — a market order contributes nothing to measured exposure ──────
try:
    repo = read("backend_app/core/models/execution_record.py") or ""
    nullif = "NULLIF(price, '')::numeric" in repo or "NULLIF(price,'')::numeric" in repo
    engine_src = read("backend_app/core/execution_engine.py") or ""
    price_or_zero = "abs(Decimal(str(price or 0)))" in engine_src
    if not nullif:
        record("TRADE-11", "PREMISE GONE",
               "the capacity SUM no longer uses the NULLIF(price,'') form")
    else:
        record(
            "TRADE-11", "CONFIRMED OPEN",
            f"the capacity SUM multiplies by NULLIF(price,'')::numeric, so a NULL/empty "
            f"price yields NULL and SUM skips the row; the per-order check uses "
            f"`price or 0` (present={price_or_zero}), i.e. the same blind spot.",
        )
except Exception as exc:  # noqa: BLE001
    record("TRADE-11", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── TEST-06 — a protected-logic token too short to discriminate ──────────────
try:
    # Importing that test module initialises the rate limiter, which REFUSES to run with an
    # unreachable REDIS_URL outside a declared local environment - correctly, and that
    # refusal is itself a Phase 2 fix. This is a static check on a token set, so the
    # environment is declared local rather than the refusal being worked around.
    import os

    os.environ.setdefault("DEV_MODE", "true")
    os.environ.setdefault("ENV", "development")

    from tests.test_subscriber_restricted_operations import protected_tokens

    tokens = sorted(protected_tokens())
    short_numeric = [t for t in tokens if len(t) <= 4 and t.isdigit()]
    short_any = [t for t in tokens if len(t) <= 4]
    if not short_numeric:
        record("TEST-06", "PREMISE GONE",
               f"no short all-digit token remains; short tokens = {short_any}")
    else:
        record(
            "TEST-06", "CONFIRMED OPEN",
            f"{len(tokens)} protected tokens, of which {short_any} are <= 4 chars and "
            f"{short_numeric} are all digits - so a substring match against a timestamp is "
            f"possible and the verdict depends on the clock.",
        )
except Exception as exc:  # noqa: BLE001
    record("TEST-06", "INCONCLUSIVE", f"{type(exc).__name__}: {exc}")


# ── report ──────────────────────────────────────────────────────────────────
print("=" * 78)
print("PHASE 8 OPEN FINDINGS - INDEPENDENT RE-VERIFICATION AGAINST THE CODE")
print("=" * 78)
width = max(len(f) for f, _, _ in results)
gone = [f for f, v, _ in results if v == "PREMISE GONE"]
unclear = [f for f, v, _ in results if v == "INCONCLUSIVE"]
for fid, verdict, detail in results:
    print(f"\n{fid:<{width}}  {verdict}")
    for line in re.findall(r".{1,86}(?:\s|$)", detail):
        if line.strip():
            print(f"{'':<{width}}  {line.strip()}")

print("\n" + "=" * 78)
print(f"checked        = {len(results)}")
print(f"confirmed open = {len(results) - len(gone) - len(unclear)}")
print(f"premise gone   = {len(gone)} {gone if gone else ''}")
print(f"inconclusive   = {len(unclear)} {unclear if unclear else ''}")
print("=" * 78)
sys.exit(1 if gone else 0)
