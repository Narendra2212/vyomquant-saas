#!/usr/bin/env python3
"""Prove the market-data chain works end to end, from inside the VPC.

WHAT THIS ANSWERS
=================
"mds is running" and "paper trading can price an order" are different claims, and only
the second one matters for launch. This proves the second by driving the real chain:

    PUBLISH {'action':'subscribe', ...} -> mds:commands
      -> mds/main.broadcast_ohlcv opens the ccxt watch_ohlcv stream
        -> publishes candles to mds:data:{exchange}:{symbol}
        -> AND writes mds:last_price:{exchange}:{symbol} (PAPER-02, 150s TTL)
          -> paper_trading_service.get_fresh_market_price reads that key

If the last key appears, PAPER-02's mechanism is live: a manual paper market order can
fill at a measured price instead of refusing with NO_FRESH_PRICE.

WHY IT RUNS AS A ONE-OFF FARGATE TASK
=====================================
ElastiCache (master.vyomquant-redis-production-cmd...) is reachable only inside the VPC,
so this cannot be driven from a workstation. The task uses the api task definition with a
command override, the same mechanism scripts/probe_worker_queue_depth.py uses.

WHY IT IS SAFE
==============
It publishes a SUBSCRIBE and then an UNSUBSCRIBE for one symbol, and otherwise only
reads. It places no order, touches no credential, and writes nothing to PostgreSQL. A
subscribe that is left open would cost one idle websocket, so the unsubscribe is in a
finally.

    python scripts/verify_mds_end_to_end.py --symbol BTC/USDT
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

REGION = "ap-southeast-1"
CLUSTER = "vyomquant-cluster"
TASK_DEF = "vyomquant-api:237"
CONTAINER = "vyomquant-api"
API_SERVICE = "vyomquant-api-service-cjema2sl"

PROBE = r"""
import asyncio, json, os, time
async def main():
    out = {}
    try:
        import redis.asyncio as aioredis
        url = os.environ.get("REDIS_URL") or ""
        r = aioredis.from_url(url, socket_connect_timeout=10, socket_timeout=10)
        exchange = os.environ.get("PROBE_EXCHANGE", "binance")
        symbol = os.environ.get("PROBE_SYMBOL", "BTC/USDT")
        last_key = "mds:last_price:%s:%s" % (exchange.strip().lower(), symbol.strip())
        data_channel = "mds:data:%s:%s" % (exchange, symbol)
        out["last_price_key"] = last_key
        out["before"] = await r.get(last_key)
        pubsub = r.pubsub()
        await pubsub.subscribe(data_channel)
        cmd = json.dumps({"action": "subscribe", "exchange": exchange, "symbol": symbol})
        out["published"] = await r.publish("mds:commands", cmd)
        candles = 0
        deadline = time.time() + 90
        try:
            while time.time() < deadline and candles < 2:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
                if msg and msg.get("data"):
                    candles += 1
                    if candles == 1:
                        body = msg["data"]
                        if isinstance(body, bytes):
                            body = body.decode()
                        try:
                            out["first_candle"] = json.loads(body)
                        except Exception:
                            out["first_candle"] = str(body)[:300]
            out["candles_received"] = candles
            out["after"] = await r.get(last_key)
            ttl = await r.ttl(last_key)
            out["last_price_ttl"] = ttl
        finally:
            await pubsub.unsubscribe(data_channel)
            await r.publish("mds:commands", json.dumps(
                {"action": "unsubscribe", "exchange": exchange, "symbol": symbol}))
            await r.aclose()
    except Exception as exc:
        out["fatal"] = "%s: %s" % (type(exc).__name__, exc)
    def dec(v):
        return v.decode() if isinstance(v, bytes) else v
    for k in ("before", "after"):
        out[k] = dec(out.get(k))
    print("MDS_E2E_RESULT " + json.dumps(out, default=str))
asyncio.run(main())
"""


def aws(*args: str):
    p = subprocess.run(["aws", *args, "--region", REGION],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--exchange", default="binance")
    args = ap.parse_args()

    code, out, err = aws("ecs", "describe-services", "--cluster", CLUSTER,
                         "--services", API_SERVICE, "--output", "json")
    if code != 0:
        raise SystemExit(f"cannot read the api service network config: {err[:200]}")
    cfg = ((json.loads(out)["services"][0].get("networkConfiguration") or {})
           .get("awsvpcConfiguration") or {})

    code, out, err = aws("ecs", "describe-task-definition", "--task-definition", TASK_DEF,
                         "--output", "json")
    td = json.loads(out)["taskDefinition"]
    container = next(c for c in td["containerDefinitions"] if c["name"] == CONTAINER)
    opts = (container.get("logConfiguration") or {}).get("options") or {}
    group, prefix = opts.get("awslogs-group"), opts.get("awslogs-stream-prefix")

    print(f"symbol   : {args.exchange} {args.symbol}")
    print(f"publishes: subscribe -> mds:commands, then unsubscribe in a finally")
    print(f"reads    : mds:data:{args.exchange}:{args.symbol} and "
          f"mds:last_price:{args.exchange.lower()}:{args.symbol}")

    overrides = {"containerOverrides": [{
        "name": CONTAINER,
        "command": ["python", "-c", PROBE],
        "environment": [
            {"name": "PROBE_SYMBOL", "value": args.symbol},
            {"name": "PROBE_EXCHANGE", "value": args.exchange},
        ],
    }]}
    code, out, err = aws(
        "ecs", "run-task", "--cluster", CLUSTER, "--task-definition", TASK_DEF,
        "--launch-type", "FARGATE", "--count", "1",
        "--network-configuration", json.dumps({"awsvpcConfiguration": {
            "subnets": cfg["subnets"], "securityGroups": cfg["securityGroups"],
            "assignPublicIp": cfg.get("assignPublicIp", "ENABLED")}}),
        "--overrides", json.dumps(overrides),
        "--started-by", "mds-e2e-verify", "--output", "json",
    )
    if code != 0:
        print(f"run-task FAILED: {err.strip()[:300]}")
        return 1
    body = json.loads(out)
    if body.get("failures"):
        print(f"run-task failures: {body['failures']}")
        return 1
    arn = body["tasks"][0]["taskArn"]
    task_id = arn.split("/")[-1]
    print(f"\ntask {task_id} started; waiting...")

    for _ in range(40):
        time.sleep(15)
        code, out, _ = aws("ecs", "describe-tasks", "--cluster", CLUSTER,
                           "--tasks", arn, "--output", "json")
        if code != 0:
            continue
        if json.loads(out)["tasks"][0].get("lastStatus") == "STOPPED":
            break

    stream = f"{prefix}/{CONTAINER}/{task_id}"
    for attempt in range(6):
        code, out, err = aws("logs", "get-log-events", "--log-group-name", group,
                             "--log-stream-name", stream, "--limit", "200",
                             "--output", "json")
        if code == 0:
            events = json.loads(out).get("events", [])
            hit = [e["message"] for e in events if "MDS_E2E_RESULT" in e.get("message", "")]
            if hit:
                payload = json.loads(hit[-1].split("MDS_E2E_RESULT", 1)[1].strip())
                print("\n" + "=" * 74)
                print("RESULT")
                print("=" * 74)
                print(json.dumps(payload, indent=2)[:2500])
                print()
                got_candle = int(payload.get("candles_received") or 0) > 0
                got_price = payload.get("after") is not None
                print(f"candles on mds:data:*      : {payload.get('candles_received')}")
                print(f"mds:last_price before      : {payload.get('before')}")
                print(f"mds:last_price after       : {payload.get('after')}")
                print(f"its TTL (expect <= 150)    : {payload.get('last_price_ttl')}")
                print()
                if got_candle and got_price:
                    print("VERDICT: the chain works. mds streams candles AND writes the")
                    print("last-price key, so paper_trading_service.get_fresh_market_price")
                    print("has a measured price to fill a manual market order at.")
                    return 0
                if got_candle:
                    print("VERDICT: candles stream, but the last-price key was NOT written.")
                    print("PAPER-02's cache is inert -- a manual market order falls through")
                    print("to fetch_venue_quote's REST read instead of the free cache.")
                    return 1
                print("VERDICT: no candle arrived within the window. mds may not have")
                print("picked up the subscribe; check the mds service log.")
                return 1
            if events and attempt >= 2:
                print("\n--- last log lines (no result marker) ---")
                for e in events[-20:]:
                    print("   " + e["message"][:260])
                return 1
        time.sleep(10)
    print("could not read the probe result")
    return 1


if __name__ == "__main__":
    sys.exit(main())
