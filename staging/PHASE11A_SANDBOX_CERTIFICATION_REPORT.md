# PHASE 11A — EXCHANGE SANDBOX CERTIFICATION REPORT

**Repository:** `quantnarendraalgo22` (`c:\aerora_quant_backend_updated_final1`)
**Revision:** HEAD `074b772a` on `main`, working tree dirty (concurrent workstreams preserved)
**Date:** 2026-10-10
**Pinned CCXT:** `4.3.92` (verified: `python -c "import ccxt; print(ccxt.__version__)"`)
**Companions:** `staging/EXCHANGE_SANDBOX_CERTIFICATION.md` (Phase 10 Workstream E, prior verdict),
`staging/EXCHANGE_ADAPTER_INVENTORY.md`, `tests/exchange_sandbox/`

> This report VERIFIES the Phase 10 Workstream-E findings against the current code and
> extends them with two defect fixes (EXCH-04, EXCH-05) made this phase. It does **not**
> re-derive the whole audit.

---

## 1. Headline verdict

```
EXCHANGE SANDBOX CERTIFICATION:   NOT ACHIEVED — NO SANDBOX CREDENTIAL EXISTS
VENUES CERTIFIABLE IN PRINCIPLE:  binance, bybit, okx       (3 of 7, MEASURED on ccxt 4.3.92)
VENUES STRUCTURALLY UNSUPPORTED:  coinbase, kraken, kucoin  (connect(sandbox=True) raises NotSupported)
VENUE UNSAFE (BLOCKED):           bitget                    (EXCH-01; sandbox flag inert, blocked from routing)
SANDBOX ORDERS PLACED:            0
LIVE TRADING:                     OFF throughout
```

No `AERORA_SANDBOX_*` credential is present in the environment (verified: `Get-ChildItem env:`
matched none). Therefore the 12-scenario live sandbox lifecycle could not be executed, and the
order-flow / reconciliation gates are **BLOCKED on credentials**, not failed. A skip in the
lifecycle suite is explicitly labelled "MISSING EVIDENCE, not a pass" by the harness itself.

---

## 2. Per-exchange evidence and status

Status vocabulary (per the brief): `NOT_TESTED`, `OFFLINE_MECHANISM_VERIFIED`,
`SANDBOX_CONNECTIVITY_VERIFIED`, `SANDBOX_ORDER_FLOW_VERIFIED`,
`SANDBOX_RECONCILIATION_VERIFIED`, `UNSUPPORTED`, `CERTIFICATION_FAILED`.

| Exchange | ccxt supports ops | Sandbox mechanism (measured) | Creds auth | Order flow | Cancel/fills/status | Reconnect/rotation | Rate-limit/transient | Recon verified | **Status** |
|---|---|---|---|---|---|---|---|---|---|
| **binance** | yes | `urls['test']` swap (testnet.binance*) | not tested (no cred) | not tested | not tested | not tested | not tested | not tested | `OFFLINE_MECHANISM_VERIFIED` |
| **bybit** | yes | `urls['test']` swap (api-testnet*) | not tested | not tested | not tested | not tested | not tested | not tested | `OFFLINE_MECHANISM_VERIFIED` |
| **okx** | yes | `x-simulated-trading: 1` header (demo) | not tested | not tested | not tested | not tested | not tested | not tested | `OFFLINE_MECHANISM_VERIFIED` |
| **coinbase** | n/a for sandbox | `connect(sandbox=True)` → `NotSupported("coinbase does not have a sandbox URL")` | — | — | — | — | — | — | `UNSUPPORTED` |
| **kraken** | n/a for sandbox | `NotSupported` (no public ccxt sandbox) | — | — | — | — | — | — | `UNSUPPORTED` |
| **kucoin** | n/a for sandbox | `connect(sandbox=True)` → `NotSupported` | — | — | — | — | — | — | `UNSUPPORTED` |
| **bitget** | yes (order ops) | flag ACCEPTED but inert → order reaches **production** | — | — | — | — | — | — | `CERTIFICATION_FAILED` (EXCH-01) — **blocked from routing** |

**Evidence for the mechanism column:** `tests/exchange_sandbox/test_sandbox_routing_contract.py`
constructs each ccxt client exactly as `CCXTExchangeExecutor.connect` does (empty creds, no
network) and compares the armed client's REST base URL and header set against an unarmed one.
Verified this phase: **10 passed, 1 failed** (the 1 is `test_bitget_...`, red by design — see §4).

**Why only 3 are certifiable:** coinbase/kraken/kucoin raise from the constructor when sandbox
mode is armed on ccxt 4.3.92 — there is nothing to certify, the scenario is *untestable*, not
untested. bitget accepts the flag and changes nothing, so an order would reach production; it is
withheld from order routing (`SAFETY_BLOCKED_EXCHANGES`) and `connect()` refuses any venue it
cannot prove left production (`tests/test_exch01_sandbox_endpoint_verification.py`, verified green
this phase as part of 35 passed).

---

## 3. Defects corrected this phase (EXCH-04, EXCH-05)

Both were "red by design" reproductions in `test_sandbox_routing_contract.py` and were open
false-capability claims. Both are now fixed; the reproduction tests turn green.

### EXCH-04 — certification registry claimed sandboxes that do not exist — **FIXED**
`backend_app/core/exchange_certification.py` declared `sandbox=True` for **coinbase** and
**kucoin**, contradicted by the measured `NotSupported`. Corrected both to `sandbox=False` with a
truthful `known_limitations` note (matching Kraken's correct `sandbox=False` convention).
- **Exploitability:** LOW. Verified no runtime path reads `ExchangeCapabilities.sandbox`:
  `validate_deployment_preflight` gates on `is_production_certified`/health/market-type, **not**
  `sandbox`; the API/UI sandbox-support flags read ccxt's own `has['sandbox']` at runtime
  (`routers/exchange.py:280,868`), not the registry. So the fix removes a false data claim without
  changing any routing or deployment decision.
- **Evidence:** `test_certification_registry_sandbox_claims_match_measured_reality` now PASSES;
  `tests/test_exchange_capabilities.py` + `tests/test_exchange_connection_schema.py` + EXCH-01
  closeout = **35 passed** (no regression; no test asserted the two false booleans).

### EXCH-05 — customer FAQ advertised two non-existent sandboxes — **FIXED**
`backend_app/routers/support.py` FAQ `faq-5` advertised "Coinbase (Sandbox), and KuCoin (Sandbox)".
Corrected to advertise only Binance/Bybit/OKX and to state Coinbase/KuCoin/Kraken run on production
endpoints exclusively.
- **Exploitability:** LOW (misrepresentation in product copy served over the support API), but
  customer-facing, so corrected.
- **Evidence:** `test_customer_facing_faq_sandbox_claims_match_measured_reality` now PASSES.

### EXCH-01 — bitget sandbox flag inert — **UPSTREAM, platform half already closed, stays red**
`test_bitget_sandbox_flag_is_inert_and_orders_reach_production` asserts a **ccxt** property
(bitget's `set_sandbox_mode` does not call `super()` and publishes no `urls['test']`). Only an
upstream ccxt change can turn it green; it is expected to stay red on 4.3.92 indefinitely. The
**platform** half is closed: bitget is withheld from routing and `connect()` refuses any venue it
cannot prove left production. This is NOT a platform launch blocker (bitget cannot be selected).

---

## 4. Missing credentials, unsupported operations, prerequisites

**Blocked on credentials (the only thing standing between here and order-flow certification for
the 3 routable venues):**
- `AERORA_SANDBOX_BINANCE_API_KEY` / `_SECRET` — a Binance **testnet** key (<https://testnet.binance.vision/>)
- `AERORA_SANDBOX_BYBIT_API_KEY` / `_SECRET` — a Bybit **testnet** key
- `AERORA_SANDBOX_OKX_API_KEY` / `_SECRET` / `_PASSPHRASE` — an OKX **demo-trading** key (passphrase required)
- Each venue's sandbox account must be **funded with testnet assets** (scenarios 2, 5a, 11).
- A reachable **local Redis** — the kill switch is re-read at the venue boundary and `is_active()`
  fails closed when Redis is unreachable, so without it every sandbox order is correctly refused.

**Structurally impossible on ccxt 4.3.92 (no credential would change this):** coinbase, kraken,
kucoin sandbox order flow — the constructor raises.

**Partially answerable even with credentials (limitations recorded in the test bodies, not
glossed):** partial fills (5b, not deterministically inducible on a thin book), socket timeout
(7, only the consequence is reproducible), full internal-vs-venue reconciliation (12, needs the
RECON-01 detector running — see the gate register).

---

## 5. Executable test plan to produce the missing evidence

Per venue (`binance` | `bybit` | `okx`), one venue per run, with testnet/demo keys only:

```powershell
$env:AERORA_EXCHANGE_SANDBOX_CERT='1'
$env:AERORA_SANDBOX_VENUE='binance'
$env:AERORA_SANDBOX_BINANCE_API_KEY='<testnet key>'
$env:AERORA_SANDBOX_BINANCE_SECRET='<testnet secret>'
# okx also: $env:AERORA_SANDBOX_OKX_PASSPHRASE='<demo passphrase>'
# a local Redis must be reachable
python -m pytest tests/exchange_sandbox/test_sandbox_order_lifecycle.py -q
# Run scenario 8 (duplicate clientOrderId refused by the venue) FIRST — highest value.
```

On success this would move the three routable venues from `OFFLINE_MECHANISM_VERIFIED` to
`SANDBOX_ORDER_FLOW_VERIFIED` (and, with the RECON-01 detector wired, toward
`SANDBOX_RECONCILIATION_VERIFIED`).

---

## 6. Verification commands run this phase (evidence)

| What | Command | Result |
|---|---|---|
| ccxt version | `python -c "import ccxt; print(ccxt.__version__)"` | `4.3.92` |
| offline routing (before fix) | `pytest tests/exchange_sandbox/test_sandbox_routing_contract.py` | 3 failed, 8 passed |
| offline routing (after EXCH-04/05 fix) | same | **1 failed (bitget, by design), 10 passed** |
| lifecycle suite (no creds) | `pytest tests/exchange_sandbox/test_sandbox_order_lifecycle.py -rs` | all SKIPPED, naming the exact env vars; exit 0 |
| no regression | `pytest tests/test_exchange_capabilities.py tests/test_exchange_connection_schema.py tests/test_exch01_sandbox_endpoint_verification.py` | 35 passed |

Nothing in this phase placed an order, used a production credential, or connected to a venue.
