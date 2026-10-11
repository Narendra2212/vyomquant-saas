"""Would upgrading ccxt close the connectivity shortfall? Measured, not guessed.

WHY THIS IS WORTH RUNNING
-------------------------
The merged tally puts PUBLIC_CONNECTIVITY_VERIFIED at 72 of 103, with a ceiling of 97
even if every ambiguous venue is retried successfully — because 6 venues cannot be
verified AT ALL on the pinned ccxt 4.3.92, and 5 of those 6 fail inside the library while
parsing a venue response (`LIBRARY_DEFECT`). That is a dependency fact, not a product
fact, and it is the single thing standing between the measured ceiling and the 100 floor.

So: run the SAME probe on a newer ccxt and compare. The answer decides whether the launch
target is reachable by a version bump or needs venue substitution.

HOW IT STAYS SAFE
-----------------
It runs inside a THROWAWAY virtualenv created under `staging/.ccxt_upgrade_probe/`. The
project's own `.venv` is never touched and `requirements-base.txt` is not modified — this
measures an upgrade, it does not perform one. Upgrading a trading platform's exchange
library is a change with a blast radius across every venue path, and it belongs in the
repository's dependency-triage process with its own regression run, not in a probe.

Public endpoints only. No credentials. No orders.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import venv

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE_DIR = ROOT / "staging" / ".ccxt_upgrade_probe"
TARGET_VERSION = "4.5.85"

#: The venues that failed on 4.3.92 for a reason a newer library could plausibly fix.
#: `alpaca` is excluded: it needs a credential for market data, which no upgrade changes.
LIBRARY_DEFECT_VENUES = ["okx", "gemini", "hyperliquid", "coinmetro", "timex"]

#: Run inside the throwaway venv. Kept as a string so the probe needs no packaging.
INNER = r'''
import asyncio, json, sys
import ccxt
import ccxt.async_support as ccxta

VENUES = sys.argv[1].split(",")

def universe():
    raw = list(ccxt.exchanges)
    eligible, aliases = [], []
    PUB = ("fetchTicker","fetchTickers","fetchOrderBook","fetchOHLCV","fetchTrades","fetchMarkets")
    for e in raw:
        cls = getattr(ccxt, e, None)
        if cls is None:
            continue
        try:
            i = cls({})
        except Exception:
            continue
        if getattr(i, "alias", False):
            aliases.append(e); continue
        if any(i.has.get(c) is True for c in PUB):
            eligible.append(e)
    return {"raw": len(raw), "aliases": len(aliases), "eligible": len(eligible),
            "eligible_ids": sorted(eligible)}

async def probe(eid):
    cls = getattr(ccxta, eid, None)
    if cls is None:
        return {"id": eid, "ok": False, "error": "absent from this ccxt"}
    c = cls({"enableRateLimit": True, "timeout": 45000})
    try:
        m = await asyncio.wait_for(c.load_markets(), timeout=50)
        return {"id": eid, "ok": bool(m), "markets": len(m or {})}
    except BaseException as ex:
        return {"id": eid, "ok": False,
                "error": f"{type(ex).__name__}: {str(ex)[:160]}"}
    finally:
        try:
            await c.close()
        except Exception:
            pass

async def main():
    res = await asyncio.gather(*(probe(v) for v in VENUES))
    print("___RESULT___" + json.dumps(
        {"ccxt_version": ccxt.__version__, "universe": universe(), "venues": res}))

asyncio.run(main())
'''


def main() -> int:
    print("=" * 80)
    print("CCXT UPGRADE DELTA PROBE — isolated venv, public endpoints only")
    print("=" * 80)
    print(f"installed (project) : 4.3.92  [pinned in requirements-base.txt]")
    print(f"probing             : {TARGET_VERSION}")
    print(f"venues under test   : {LIBRARY_DEFECT_VENUES}")
    print(f"throwaway venv      : {PROBE_DIR}")
    print("The project .venv and requirements-base.txt are NOT modified.")
    print("-" * 80)

    if not PROBE_DIR.exists():
        print("creating throwaway venv...")
        venv.create(PROBE_DIR, with_pip=True)

    py = PROBE_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

    print(f"installing ccxt=={TARGET_VERSION} (quiet)...")
    rc = subprocess.run(
        [str(py), "-m", "pip", "install", "-q", f"ccxt[async]=={TARGET_VERSION}"],
        capture_output=True, text=True,
    )
    if rc.returncode != 0:
        print("pip install FAILED:")
        print(rc.stderr[-2000:])
        return 1

    inner = PROBE_DIR / "inner_probe.py"
    inner.write_text(INNER, encoding="utf-8")

    print("running probe...")
    rc = subprocess.run(
        [str(py), str(inner), ",".join(LIBRARY_DEFECT_VENUES)],
        capture_output=True, text=True, timeout=600,
    )
    marker = "___RESULT___"
    if marker not in rc.stdout:
        print("probe produced no result:")
        print(rc.stdout[-1500:])
        print(rc.stderr[-1500:])
        return 1

    data = json.loads(rc.stdout.split(marker, 1)[1].strip())

    print()
    print("-" * 80)
    print(f"RESULT on ccxt {data['ccxt_version']}")
    print("-" * 80)
    u = data["universe"]
    print(f"raw ids            : {u['raw']}      (4.3.92 had 110)")
    print(f"self-declared alias: {u['aliases']}       (4.3.92 had 7)")
    print(f"ELIGIBLE CANDIDATES: {u['eligible']}      (4.3.92 had 103)")
    print()
    print("The five LIBRARY_DEFECT venues, re-probed:")
    fixed, still = [], []
    for v in data["venues"]:
        if v.get("ok"):
            fixed.append(v["id"])
            print(f"  FIXED      {v['id']:<14} {v.get('markets')} markets")
        else:
            still.append(v["id"])
            print(f"  STILL FAIL {v['id']:<14} {v.get('error','')[:110]}")

    print()
    print("-" * 80)
    print("IMPACT ON THE LAUNCH TARGET")
    print("-" * 80)
    print(f"  venues an upgrade would recover : {len(fixed)}  {fixed}")
    print(f"  venues still unverifiable       : {len(still)}  {still}")
    print(f"  measured ceiling on 4.3.92      : 97 of 103")
    print(f"  projected ceiling on {TARGET_VERSION:<9}  : {97 + len(fixed)} of "
          f"{u['eligible']}")
    reach = 97 + len(fixed) >= 100
    print(f"  100 floor reachable after upgrade: {reach}")

    out = ROOT / "staging" / "ccxt_upgrade_delta.json"
    out.write_text(json.dumps({
        "pinned": "4.3.92",
        "probed": data["ccxt_version"],
        "universe_probed": u,
        "universe_pinned": {"raw": 110, "aliases": 7, "eligible": 103},
        "venues": data["venues"],
        "recovered_by_upgrade": fixed,
        "still_failing": still,
        "ceiling_pinned": 97,
        "ceiling_projected": 97 + len(fixed),
        "hundred_floor_reachable": reach,
    }, indent=2), encoding="utf-8")
    print(f"\nartifact -> {out.relative_to(ROOT)}")
    print("\nNOTE: this measures an upgrade; it does not perform one. A ccxt bump touches")
    print("every venue path and belongs in dependency triage with a full regression run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
