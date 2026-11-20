# Exchange sandbox certification suite

Phase 10 · Workstream E. Opt-in. Nothing here runs in the default `pytest tests/` lane.

Results and verdict: [`staging/EXCHANGE_SANDBOX_CERTIFICATION.md`](../../staging/EXCHANGE_SANDBOX_CERTIFICATION.md)
Inventory and capability matrix: [`staging/EXCHANGE_ADAPTER_INVENTORY.md`](../../staging/EXCHANGE_ADAPTER_INVENTORY.md)

---

## What is here

| File | Contents | Needs a credential? |
|---|---|---|
| `test_sandbox_routing_contract.py` | 11 offline tests. Measures where an order actually goes when the platform says "sandbox" | **no** |
| `test_sandbox_order_lifecycle.py` | 16 tests covering the brief's 12 scenarios against a real exchange sandbox | **yes** |
| `harness.py` | Constants, routing helpers, credential lookup | — |
| `conftest.py` | Collection gate, production-credential refusal | — |

## Running it

```powershell
# Offline only. Produces real evidence, no credential needed.
$env:AERORA_EXCHANGE_SANDBOX_CERT='1'
python -m pytest tests/exchange_sandbox/test_sandbox_routing_contract.py -q

# Everything (the lifecycle suite will skip, naming what it needs)
python -m pytest tests/exchange_sandbox/ -q -rs
```

Expected offline result: **3 failed, 8 passed**. The three failures are deliberate — see
"Red by design" below.

## Running the live lifecycle

Only `binance`, `bybit` and `okx` can be certified. The other four are rejected by the
harness, with measured reasons in `harness.SANDBOX_ROUTABLE`.

1. Create a **testnet / demo** account and API key:
   - Binance → <https://testnet.binance.vision/>
   - Bybit → Testnet, then API Management
   - OKX → Demo Trading, then an API key with the demo flag (passphrase required)
2. Fund it with testnet assets. Scenarios 2, 5a and 11 need a non-zero balance.
3. Start a local Redis. The kill switch is re-read at the venue boundary and
   `is_active()` fails **closed** when Redis is unreachable, so without it every order is
   correctly refused and no scenario can run.
4. Export, substituting your venue:

```powershell
$env:AERORA_EXCHANGE_SANDBOX_CERT='1'
$env:AERORA_SANDBOX_VENUE='binance'          # binance | bybit | okx
$env:AERORA_SANDBOX_SYMBOL='BTC/USDT'        # optional
$env:AERORA_SANDBOX_BINANCE_API_KEY='...'    # testnet key
$env:AERORA_SANDBOX_BINANCE_SECRET='...'     # testnet secret
# OKX also needs AERORA_SANDBOX_OKX_PASSPHRASE
python -m pytest tests/exchange_sandbox/test_sandbox_order_lifecycle.py -q
```

One venue per run — each needs its own credential and its own funded account.

### Run scenario 8 first

`test_08_duplicate_client_order_id_is_refused_by_the_venue` is the highest-value test in
the suite. The platform's only protection against a retry becoming a second real order is
that `execution_id` is forwarded as `clientOrderId` and that the **venue** enforces
uniqueness on it. ccxt does not normalise this field across venues and nothing in this
repository verifies the mapping or that the venue echoed the id back. If a venue silently
ignores it, every retry path above this layer doubles a position.

## Safety

Four independent mechanisms, because the first three are each one editing mistake away
from failing silently — and EXCH-01 is proof that this class of mistake is real.

1. **Collection gate.** No `AERORA_EXCHANGE_SANDBOX_CERT=1`, no collection.
2. **Credential naming.** Keys must arrive as `AERORA_SANDBOX_<VENUE>_*`. The mandatory
   `SANDBOX` token means you cannot satisfy this harness with production keys you already
   hold.
3. **Production-credential refusal.** An autouse fixture fails the run if any of 19
   production-shaped names (`BINANCE_API_KEY`, `EXCHANGE_SECRET`, …) is set. Presence only
   — no value is read.
4. **Pre-order routing proof.** `_assert_not_production` builds a pristine reference client
   and compares the **connected** client's base URL and headers against it, after
   `connect()` and before any order. No demonstrable move off production, no order.

Live trading is never enabled: nothing here touches `VYOMQUANT_MODE` or
`VYOMQUANT_ENABLE_LIVE_TRADING`. Orders are sized at the venue's own published minimum and
priced far from market wherever the scenario allows, so nothing fills by accident.

The platform's three order-path controls are **satisfied, not bypassed**: the anti-bypass
token is minted with `global_safety.generate_validation_token` and the authorisation with
the public `live_execution_gate.authorize_live_order`, which refuses unless every member of
`REQUIRED_CHECKS` is named. `enforce_kill_switch()` runs untouched. See
`EXCHANGE_SANDBOX_CERTIFICATION.md` §2.4 for the honest accounting of which declared checks
are substantive here and which are vacuous.

No secret is ever logged, asserted on, or written to a report — only env var **names**.

## Red by design

Three tests in `test_sandbox_routing_contract.py` reproduce measured defects and fail while
those defects are open:

| Test | Finding |
|---|---|
| `test_bitget_sandbox_flag_is_inert_and_orders_reach_production` | **EXCH-01** (P0-class). Bitget accepts "sandbox" and sends orders to production |
| `test_certification_registry_sandbox_claims_match_measured_reality` | **EXCH-04**. The registry claims sandboxes for coinbase and kucoin that raise `NotSupported`, and gates live deployment on those claims |
| `test_customer_facing_faq_sandbox_claims_match_measured_reality` | **EXCH-05**. The published FAQ advertises the same two |

Each asserts the **required** behaviour, so fixing the defect turns the test green with no
edit here. A reproduction that passes is not a reproduction — which is the other reason
this directory is gated out of the shared lane.
