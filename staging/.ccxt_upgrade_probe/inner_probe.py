
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
