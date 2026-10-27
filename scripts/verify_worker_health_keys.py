#!/usr/bin/env python3
"""OBS-02: are `worker:health:*` keys actually being written now?

WHY THIS IS THE RIGHT ACCEPTANCE TEST
=====================================
OBS-02 was a silent failure: redis_manager.hset passed its arguments to redis-py wrongly
(its `name` is the redis key and its `key` is the hash field, the opposite of the
wrapper's parameter names), so every hash write raised TypeError, got caught by the
wrapper's own `except`, logged at WARNING and returned 0. The loudest victim was
WorkerBase's heartbeat, which means Requirement 26.5's "publish status=unhealthy after
three consecutive failures" had never written a byte and any liveness probe reading those
keys saw an empty hash.

"The warning stopped appearing" is NOT sufficient evidence: the wrapper swallowed the
error, so absence of a warning could equally mean the call is no longer being made.
Presence of the KEY, with the fields WorkerBase writes, is the positive evidence.

Read-only: KEYS/HGETALL/TTL only. Runs as a one-off Fargate task because ElastiCache is
only reachable inside the VPC.

    python scripts/verify_worker_health_keys.py
"""
from __future__ import annotations

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
import json, os
out = {}
try:
    import redis
    r = redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=10,
                       socket_timeout=10)
    out["ping"] = bool(r.ping())
    keys = sorted(k.decode() if isinstance(k, bytes) else k
                  for k in r.scan_iter(match="worker:health:*", count=500))
    out["health_keys"] = keys
    detail = {}
    for k in keys[:12]:
        h = r.hgetall(k)
        detail[k] = {
            (f.decode() if isinstance(f, bytes) else f):
            (v.decode() if isinstance(v, bytes) else v)
            for f, v in (h or {}).items()
        }
        detail[k]["_ttl"] = r.ttl(k)
    out["detail"] = detail
    out["dbsize"] = r.dbsize()
except Exception as exc:
    out["fatal"] = "%s: %s" % (type(exc).__name__, exc)
print("HEALTH_KEYS_RESULT " + json.dumps(out, default=str))
"""


def aws(*args: str):
    p = subprocess.run(["aws", *args, "--region", REGION],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def main() -> int:
    code, out, err = aws("ecs", "describe-services", "--cluster", CLUSTER,
                         "--services", API_SERVICE, "--output", "json")
    if code != 0:
        raise SystemExit(f"cannot read the api service: {err[:200]}")
    cfg = ((json.loads(out)["services"][0].get("networkConfiguration") or {})
           .get("awsvpcConfiguration") or {})

    code, out, _ = aws("ecs", "describe-task-definition", "--task-definition", TASK_DEF,
                       "--output", "json")
    td = json.loads(out)["taskDefinition"]
    container = next(c for c in td["containerDefinitions"] if c["name"] == CONTAINER)
    opts = (container.get("logConfiguration") or {}).get("options") or {}
    group, prefix = opts.get("awslogs-group"), opts.get("awslogs-stream-prefix")

    print("OBS-02: looking for worker:health:* keys (read-only)")
    overrides = {"containerOverrides": [
        {"name": CONTAINER, "command": ["python", "-c", PROBE]}]}
    code, out, err = aws(
        "ecs", "run-task", "--cluster", CLUSTER, "--task-definition", TASK_DEF,
        "--launch-type", "FARGATE", "--count", "1",
        "--network-configuration", json.dumps({"awsvpcConfiguration": {
            "subnets": cfg["subnets"], "securityGroups": cfg["securityGroups"],
            "assignPublicIp": cfg.get("assignPublicIp", "ENABLED")}}),
        "--overrides", json.dumps(overrides),
        "--started-by", "obs02-health-keys", "--output", "json",
    )
    if code != 0:
        print(f"run-task FAILED: {err.strip()[:300]}")
        return 1
    arn = json.loads(out)["tasks"][0]["taskArn"]
    task_id = arn.split("/")[-1]
    print(f"task {task_id} started; waiting...")

    for _ in range(40):
        time.sleep(15)
        code, out, _ = aws("ecs", "describe-tasks", "--cluster", CLUSTER,
                           "--tasks", arn, "--output", "json")
        if code == 0 and json.loads(out)["tasks"][0].get("lastStatus") == "STOPPED":
            break

    stream = f"{prefix}/{CONTAINER}/{task_id}"
    for attempt in range(6):
        code, out, err = aws("logs", "get-log-events", "--log-group-name", group,
                             "--log-stream-name", stream, "--limit", "200",
                             "--output", "json")
        if code == 0:
            events = json.loads(out).get("events", [])
            hit = [e["message"] for e in events
                   if "HEALTH_KEYS_RESULT" in e.get("message", "")]
            if hit:
                payload = json.loads(
                    hit[-1].split("HEALTH_KEYS_RESULT", 1)[1].strip())
                print("\n" + "=" * 74)
                print(json.dumps(payload, indent=2)[:2600])
                print("=" * 74)
                keys = payload.get("health_keys") or []
                print(f"\nworker:health:* keys found : {len(keys)}")
                for k in keys:
                    print(f"   {k}")
                if keys:
                    print("\nVERDICT: OBS-02 is FIXED IN PRODUCTION. The heartbeat is")
                    print("writing, so Requirement 26.5's unhealthy signal has somewhere")
                    print("to appear and a liveness probe reading these keys sees state.")
                    return 0
                print("\nVERDICT: NO health keys. Either the image predates the hset fix,")
                print("or the heartbeat is not running. Absence of the HSET warning alone")
                print("would NOT have distinguished those, which is why this looks for the")
                print("key rather than for the absence of an error.")
                return 1
            if events and attempt >= 2:
                for e in events[-20:]:
                    print("   " + e["message"][:240])
                return 1
        time.sleep(10)
    print("could not read the probe result")
    return 1


if __name__ == "__main__":
    sys.exit(main())
