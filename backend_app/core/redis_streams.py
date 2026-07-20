import redis
import json

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

# Stream names
MARKET_STREAM = "market_data"
STRATEGY_STREAM = "strategy_signal"
RISK_STREAM = "risk_signal"
EXECUTION_STREAM = "execution_signal"


def publish(stream, data):
    r.xadd(stream, {"data": json.dumps(data)})


def consume(stream, group, consumer):
    try:
        return r.xreadgroup(group, consumer, {stream: ">"}, count=1, block=5000)
    except Exception:
        return []
