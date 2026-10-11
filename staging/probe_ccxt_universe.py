"""Phase 1/2 — enumerate the exchange universe in the INSTALLED ccxt. Offline, read-only.

No count is hardcoded. Everything below is read out of the installed dependency, because
the launch target is defined against "the installed CCXT version" and that version is
pinned (`requirements-base.txt:95` -> ccxt[async]==4.3.92).

WHAT COUNTS AS A DISTINCT ELIGIBLE VENUE
----------------------------------------
`ccxt.exchanges` is not a list of distinct venues. It contains, mixed together:

  * genuine distinct venues;
  * ALIASES — several ids whose class is literally the same Python object, or whose
    `describe()['id']` differs from the attribute name (e.g. a rebrand kept for backward
    compatibility). Counting those twice would inflate the total;
  * entries ccxt itself marks `alias: True` in `describe()`;
  * derivative/sub-market siblings that are separate classes but the same company
    (recorded, not silently merged — that is a product decision, not a technical one);
  * classes that fail to even construct on this Python/ccxt combination.

So this probe reports FOUR different numbers and keeps them apart:

  raw_ids              every name in ccxt.exchanges
  constructible        the class instantiates without raising
  distinct_venues      after collapsing aliases by (class identity, describe id)
  eligible             distinct, constructible, not flagged alias, and exposing at least
                       one public market-data method

`eligible` is the only one that may be compared against the 100-110 target, and even then
it is a CANDIDATE count: it means "worth attempting verification", state DISCOVERED. It is
not connectivity. Connectivity requires a response from the venue, which is what
`scripts/verify_exchange_connectivity.py` does.
"""
from __future__ import annotations

import json
import pathlib
import sys
from collections import defaultdict

import ccxt

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: Public market-data capabilities. A venue exposing none of these cannot be verified
#: without credentials and is therefore not an eligible candidate for a credential-free
#: connectivity check.
PUBLIC_CAPS = ("fetchTicker", "fetchTickers", "fetchOrderBook", "fetchOHLCV",
               "fetchTrades", "fetchMarkets", "fetchStatus", "fetchCurrencies")

#: Private capabilities, recorded so execution capability stays separate from
#: connectivity rather than being inferred from it.
PRIVATE_CAPS = ("fetchBalance", "createOrder", "cancelOrder", "fetchOrder",
                "fetchOpenOrders", "fetchClosedOrders", "fetchMyTrades",
                "createStopOrder", "createStopLimitOrder", "createPostOnlyOrder",
                "createReduceOnlyOrder", "fetchPositions", "fetchTradingFees")


def describe_safe(eid: str) -> dict:
    """Everything decidable about one exchange id without a network call."""
    row: dict = {"id": eid}

    cls = getattr(ccxt, eid, None)
    if cls is None:
        row["constructible"] = False
        row["error"] = "attribute missing from ccxt module"
        return row

    row["class_qualname"] = getattr(cls, "__name__", "?")
    row["class_object_id"] = id(cls)

    try:
        inst = cls({})
    except Exception as exc:  # noqa: BLE001
        row["constructible"] = False
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    row["constructible"] = True
    try:
        row["describe_id"] = inst.id
        row["name"] = inst.name
        row["countries"] = inst.countries
        row["certified"] = bool(getattr(inst, "certified", False))
        row["pro"] = bool(getattr(inst, "pro", False))
        row["alias"] = bool(getattr(inst, "alias", False))
        row["has_public"] = {c: inst.has.get(c) for c in PUBLIC_CAPS}
        row["has_private"] = {c: inst.has.get(c) for c in PRIVATE_CAPS}
        row["public_cap_count"] = sum(
            1 for c in PUBLIC_CAPS if inst.has.get(c) is True
        )
        row["requires"] = sorted(
            k for k, v in (inst.requiredCredentials or {}).items() if v
        )
        urls = inst.urls or {}
        row["has_test_url"] = urls.get("test") is not None
        row["www"] = urls.get("www")
        row["api_is_str"] = isinstance(urls.get("api"), str)
        # Unresolved `{hostname}` templates mean the base URL is parameterised; recorded
        # because it affects how a connectivity probe must build its request.
        api_blob = json.dumps(urls.get("api"), default=str)
        row["api_templated"] = "{" in api_blob
        row["timeout_default_ms"] = getattr(inst, "timeout", None)
        row["rateLimit_ms"] = getattr(inst, "rateLimit", None)
    except Exception as exc:  # noqa: BLE001
        row["describe_error"] = f"{type(exc).__name__}: {exc}"
    return row


def main() -> None:
    raw_ids = list(ccxt.exchanges)
    rows = [describe_safe(e) for e in raw_ids]

    constructible = [r for r in rows if r.get("constructible")]
    broken = [r for r in rows if not r.get("constructible")]

    # ── Alias collapsing ────────────────────────────────────────────────────────────
    # Two independent signals, because neither alone catches every case:
    #   (a) the same class OBJECT reachable under two attribute names;
    #   (b) describe()['id'] differing from the attribute name, which is how ccxt
    #       expresses a rebrand that keeps the old name importable.
    by_class: dict[int, list[str]] = defaultdict(list)
    for r in constructible:
        by_class[r["class_object_id"]].append(r["id"])

    shared_class_groups = {k: v for k, v in by_class.items() if len(v) > 1}

    id_mismatch = [
        (r["id"], r.get("describe_id"))
        for r in constructible
        if r.get("describe_id") and r["describe_id"] != r["id"]
    ]
    self_declared_alias = [r["id"] for r in constructible if r.get("alias")]

    # Canonical representative per class group: prefer the id that equals describe_id,
    # else the shortest name (rebrands are usually shorter than the legacy alias).
    aliases_dropped: list[str] = []
    canonical: list[dict] = []
    for _cls_id, ids in by_class.items():
        group = [r for r in constructible if r["id"] in ids]
        preferred = next(
            (r for r in group if r.get("describe_id") == r["id"]),
            sorted(group, key=lambda r: (len(r["id"]), r["id"]))[0],
        )
        canonical.append(preferred)
        aliases_dropped.extend(sorted(r["id"] for r in group if r is not preferred))

    # Drop ccxt's own self-declared aliases too, even if they have a unique class.
    pre = len(canonical)
    canonical = [r for r in canonical if not r.get("alias")]
    alias_flag_dropped = pre - len(canonical)

    eligible = [r for r in canonical if r.get("public_cap_count", 0) > 0]
    no_public = [r["id"] for r in canonical if r.get("public_cap_count", 0) == 0]

    # Execution capability, kept strictly separate from connectivity.
    can_create_order = [
        r["id"] for r in eligible if r["has_private"].get("createOrder") is True
    ]
    with_sandbox = [r["id"] for r in eligible if r.get("has_test_url")]
    ws_capable = [r["id"] for r in eligible if r.get("pro")]

    print("=" * 78)
    print("CCXT EXCHANGE UNIVERSE — INSTALLED DEPENDENCY, OFFLINE ENUMERATION")
    print("=" * 78)
    print(f"ccxt version (pinned)          : {ccxt.__version__}")
    print(f"raw ids in ccxt.exchanges      : {len(raw_ids)}")
    print(f"constructible                  : {len(constructible)}")
    print(f"failed to construct            : {len(broken)}")
    print(f"class-sharing alias groups     : {len(shared_class_groups)}")
    print(f"aliases dropped (shared class) : {len(aliases_dropped)}")
    print(f"aliases dropped (alias=True)   : {alias_flag_dropped}")
    print(f"describe_id != attribute name  : {len(id_mismatch)}")
    print(f"DISTINCT VENUES                : {len(canonical) + alias_flag_dropped}")
    print(f"  of which no public market data: {len(no_public)}")
    print()
    print(f">>> ELIGIBLE CANDIDATES (state DISCOVERED) : {len(eligible)}")
    print(f"    target band 100-110 -> "
          f"{'MET as candidates' if 100 <= len(eligible) else 'SHORTFALL'}"
          f" ({len(eligible)} eligible)")
    print()
    print("CAPABILITY SPLIT ACROSS THE ELIGIBLE SET (declared by ccxt, NOT verified):")
    print(f"  createOrder declared          : {len(can_create_order)}")
    print(f"  urls['test'] (sandbox) present: {len(with_sandbox)}")
    print(f"  ccxt.pro / WebSocket capable  : {len(ws_capable)}")
    print()

    if broken:
        print("-" * 78)
        print("FAILED TO CONSTRUCT")
        print("-" * 78)
        for r in broken:
            print(f"  {r['id']:<22} {r.get('error', '?')[:90]}")
        print()

    if shared_class_groups:
        print("-" * 78)
        print("ALIAS GROUPS (same class object under multiple ids)")
        print("-" * 78)
        for ids in sorted(shared_class_groups.values()):
            print(f"  {sorted(ids)}")
        print()

    if id_mismatch:
        print("-" * 78)
        print("describe()['id'] DIFFERS FROM ATTRIBUTE NAME")
        print("-" * 78)
        for attr, did in sorted(id_mismatch):
            print(f"  attribute {attr:<22} -> describe id {did}")
        print()

    if no_public:
        print("-" * 78)
        print("DISTINCT BUT NO PUBLIC MARKET-DATA METHOD (not eligible)")
        print("-" * 78)
        print(f"  {sorted(no_public)}")
        print()

    out = ROOT / "staging" / "ccxt_universe.json"
    out.write_text(
        json.dumps(
            {
                "ccxt_version": ccxt.__version__,
                "python": sys.version,
                "counts": {
                    "raw_ids": len(raw_ids),
                    "constructible": len(constructible),
                    "failed_to_construct": len(broken),
                    "alias_groups_shared_class": len(shared_class_groups),
                    "aliases_dropped_shared_class": len(aliases_dropped),
                    "aliases_dropped_alias_flag": alias_flag_dropped,
                    "describe_id_mismatch": len(id_mismatch),
                    "distinct_venues": len(canonical) + alias_flag_dropped,
                    "eligible_candidates": len(eligible),
                    "declares_create_order": len(can_create_order),
                    "declares_sandbox_url": len(with_sandbox),
                    "websocket_capable": len(ws_capable),
                },
                "eligible_ids": sorted(r["id"] for r in eligible),
                "aliases_dropped": sorted(aliases_dropped),
                "self_declared_aliases": sorted(self_declared_alias),
                "describe_id_mismatch": sorted(id_mismatch),
                "no_public_market_data": sorted(no_public),
                "failed_to_construct": [
                    {"id": r["id"], "error": r.get("error")} for r in broken
                ],
                "declares_create_order": sorted(can_create_order),
                "declares_sandbox_url": sorted(with_sandbox),
                "per_exchange": {r["id"]: r for r in rows},
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"machine-readable inventory -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
