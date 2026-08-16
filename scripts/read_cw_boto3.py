import boto3
import time
import sys

sys.stdout.reconfigure(encoding="utf-8")

client = boto3.client("logs", region_name="ap-southeast-1")
now_ms = int(time.time() * 1000)
start_ms = now_ms - (10 * 60 * 1000)

resp = client.filter_log_events(
    logGroupName="/ecs/vyomquant-api",
    startTime=start_ms,
    limit=50
)

for event in resp.get("events", []):
    print(event.get("message", "").strip())
