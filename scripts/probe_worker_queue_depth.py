#!/usr/bin/env python3
"""INF-03: measure what is waiting in the workers' Redis queues, from inside the VPC.

WHY THIS EXISTS
===============
Starting a worker is not a neutral act, and HOW MUCH it is not depends on what is already
queued -- which cannot be read from a workstation, because ElastiCache
(master.vyomquant-redis-production-cmd...) is only reachable from inside the VPC. So this
runs a ONE-OFF Fargate task on the existing api task definition, with the command
overridden to a read-only Redis inspection, and reads the answer out of CloudWatch.

THE QUESTION IT ANSWERS, AND WHY IT IS NOT THE ONE I FIRST ASKED
================================================================
I told the owner the pre-flight check needed was "whether stale entries are sitting in
command_queue, since the command worker would consume them on startup". Reading the code
makes that the WRONG worry, and inverts which worker is risky:

  * command_worker._ensure_group creates its group with ``id="$"`` -- the stream's CURRENT
    end. A consumer group created at ``$`` is never delivered anything that was added
    before it existed. The group ``command_workers`` is created nowhere else in the
    codebase (grepped), and the worker has never run, so it does not exist yet. Starting
    the command worker therefore CANNOT replay a backlog: whatever is sitting in
    command_queue stays unread. Structural, not a matter of how deep the queue is.

  * backend_app/worker.py (the BACKTEST worker -- the one I called benign) calls
    xgroup_create with ``id="0"``: the stream's BEGINNING. On first start it is delivered
    EVERY entry ``backtest_jobs`` has ever held. And event_bus.publish_backtest_job xadds
    with no MAXLEN, so that stream has never been trimmed -- it holds every backtest job
    POST /api/strategies/backtest has published for the life of the deployment.

    What a replay does is bounded: a backtest is a simulation over historical data. It
    places no order (FEATURE_TRADING_ENABLED is off regardless) and touches no exchange.
    It costs CPU on a 0.25-vCPU task and it rewrites ``backtest:status:{job_id}`` hashes
    for old jobs, so a user could see a months-old backtest flip back to "running". That
    is worth sizing before switching it on, not worth guessing at.

  * dag_worker pops from a Redis list and then checks PostgreSQL: a task that is not in
    the database is logged TASK_NOT_IN_DB and dropped, and one already claimed is logged
    TASK_ALREADY_CLAIMED. So its backlog is self-limiting -- it cannot act on an orphan.

SAFETY
======
Read-only against Redis: XLEN, XINFO GROUPS, LLEN, TYPE and EXISTS only. No XADD, no
XACK, no XGROUP CREATE, no DEL, no FLUSH. It cannot create the consumer groups it reports
on -- XINFO GROUPS does not create a group, which matters, because creating
``backtest_workers`` at the wrong id is precisely the hazard being measured.

    python scripts/probe_worker_queue_depth.py          # run the probe and print results
    python scripts/probe_worker_queue_depth.py --plan   # show what it would run
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

# Read-only inspection. Printed as one JSON object so the log line parses cleanly.
PROBE = r"""
import json, os
out = {}
try:
    import redis
    url = os.environ.get("REDIS_URL") or ""
    out["redis_url_present"] = bool(url)
    r = redis.from_url(url, socket_connect_timeout=10, socket_timeout=10)
    out["ping"] = bool(r.ping())
    for name in ("backtest_jobs", "command_queue", "command_queue:dead_letter"):
        info = {}
        info["exists"] = bool(r.exists(name))
        if info["exists"]:
            try:
                info["type"] = r.type(name).decode()
            except Exception as e:
                info["type"] = "err:%s" % e
            try:
                info["length"] = r.xlen(name)
            except Exception as e:
                info["length"] = "err:%s" % e
            try:
                groups = r.xinfo_groups(name)
                info["groups"] = [
                    {
                        "name": g.get("name", b"").decode()
                            if isinstance(g.get("name"), bytes) else g.get("name"),
                        "consumers": g.get("consumers"),
                        "pending": g.get("pending"),
                        "last_delivered": g.get("last-delivered-id", b"").decode()
                            if isinstance(g.get("last-delivered-id"), bytes)
                            else g.get("last-delivered-id"),
                    }
                    for g in groups
                ]
            except Exception as e:
                info["groups"] = "err:%s" % e
        out[name] = info
    # dag_worker pops from lists; report any that exist.
    lists = {}
    for key in ("dag_tasks", "task_queue", "dag:queue", "celery", "tasks"):
        try:
            if r.exists(key):
                lists[key] = {"type": r.type(key).decode()}
                if lists[key]["type"] == "list":
                    lists[key]["llen"] = r.llen(key)
        except Exception as e:
            lists[key] = "err:%s" % e
    out["candidate_list_keys"] = lists
    try:
        out["dbsize"] = r.dbsize()
    except Exception as e:
        out["dbsize"] = "err:%s" % e
except Exception as exc:
    out["fatal"] = "%s: %s" % (type(exc).__name__, exc)
print("QUEUE_PROBE_RESULT " + json.dumps(out, default=str))
"""


def aws(*args: str):
    p = subprocess.run(["aws", *args, "--region", REGION],
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def network_config():
    code, out, err = aws("ecs", "describe-services", "--cluster", CLUSTER,
                         "--services", API_SERVICE, "--output", "json")
    if code != 0:
        raise SystemExit(f"cannot read the api service's network config: {err[:200]}")
    cfg = ((json.loads(out)["services"][0].get("networkConfiguration") or {})
           .get("awsvpcConfiguration") or {})
    if not cfg.get("subnets") or not cfg.get("securityGroups"):
        raise SystemExit("the api service has no usable awsvpcConfiguration")
    return cfg


def log_config():
    code, out, err = aws("ecs", "describe-task-definition", "--task-definition", TASK_DEF,
                         "--output", "json")
    if code != 0:
        raise SystemExit(f"cannot read {TASK_DEF}: {err[:200]}")
    td = json.loads(out)["taskDefinition"]
    container = next(c for c in td["containerDefinitions"] if c["name"] == CONTAINER)
    opts = (container.get("logConfiguration") or {}).get("options") or {}
    return opts.get("awslogs-group"), opts.get("awslogs-stream-prefix")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    cfg = network_config()
    group, prefix = log_config()
    print(f"cluster     : {CLUSTER}")
    print(f"task def    : {TASK_DEF}  (container {CONTAINER})")
    print(f"network     : {len(cfg['subnets'])} subnet(s), "
          f"{len(cfg['securityGroups'])} sg, publicIp={cfg.get('assignPublicIp')}")
    print(f"log group   : {group}  prefix={prefix}")
    print(f"operations  : XLEN / XINFO GROUPS / LLEN / TYPE / EXISTS / DBSIZE only")

    if args.plan:
        print("\n--plan only. No task was run.")
        return 0

    overrides = {"containerOverrides": [
        {"name": CONTAINER, "command": ["python", "-c", PROBE]}]}
    code, out, err = aws(
        "ecs", "run-task", "--cluster", CLUSTER, "--task-definition", TASK_DEF,
        "--launch-type", "FARGATE", "--count", "1",
        "--network-configuration", json.dumps({"awsvpcConfiguration": {
            "subnets": cfg["subnets"], "securityGroups": cfg["securityGroups"],
            "assignPublicIp": cfg.get("assignPublicIp", "ENABLED")}}),
        "--overrides", json.dumps(overrides),
        "--started-by", "inf03-queue-probe", "--output", "json",
    )
    if code != 0:
        print(f"\nrun-task FAILED: {err.strip()[:400]}")
        return 1
    body = json.loads(out)
    if body.get("failures"):
        print(f"\nrun-task reported failures: {body['failures']}")
        return 1
    arn = body["tasks"][0]["taskArn"]
    task_id = arn.split("/")[-1]
    print(f"\nstarted task {task_id}; waiting for it to stop...")

    last = None
    for _ in range(60):
        time.sleep(10)
        code, out, _ = aws("ecs", "describe-tasks", "--cluster", CLUSTER,
                           "--tasks", arn, "--output", "json")
        if code != 0:
            continue
        task = json.loads(out)["tasks"][0]
        status = task.get("lastStatus")
        if status != last:
            print(f"   {status}")
            last = status
        if status == "STOPPED":
            print(f"   stopped reason: {task.get('stoppedReason')}")
            for c in task.get("containers", []):
                print(f"   container {c['name']}: exit={c.get('exitCode')} "
                      f"reason={c.get('reason')}")
            break
    else:
        print("   timed out waiting; check the log stream manually")

    stream = f"{prefix}/{CONTAINER}/{task_id}"
    print(f"\nreading log stream {stream}")
    for attempt in range(6):
        code, out, err = aws("logs", "get-log-events", "--log-group-name", group,
                             "--log-stream-name", stream, "--limit", "200",
                             "--output", "json")
        if code == 0:
            events = json.loads(out).get("events", [])
            hit = [e["message"] for e in events
                   if "QUEUE_PROBE_RESULT" in e.get("message", "")]
            if hit:
                payload = hit[-1].split("QUEUE_PROBE_RESULT", 1)[1].strip()
                print("\n" + "=" * 78)
                print("QUEUE DEPTH")
                print("=" * 78)
                print(json.dumps(json.loads(payload), indent=2))
                return 0
            if events and attempt >= 2:
                print("\n--- last log lines (no probe result found) ---")
                for e in events[-25:]:
                    print("   " + e["message"][:300])
                return 1
        time.sleep(10)
    print(f"could not read the probe result: {err.strip()[:300] if err else 'no events'}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
