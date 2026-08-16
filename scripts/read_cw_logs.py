import subprocess
import json
import time

now_ms = int(time.time() * 1000)
start_ms = now_ms - (5 * 60 * 1000)

res = subprocess.run(
    [
        "aws", "logs", "filter-log-events",
        "--log-group-name", "/ecs/vyomquant-api",
        "--region", "ap-southeast-1",
        "--start-time", str(start_ms),
        "--query", "events[*].message",
        "--output", "json"
    ],
    capture_output=True, text=True, encoding="utf-8"
)

try:
    events = json.loads(res.stdout)
    for e in events[-40:]:
        print(e)
except Exception as err:
    print("Error parsing logs:", err, res.stderr)
