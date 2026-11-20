"""List the test files that touch anything Workstream C changed."""
import pathlib

PATTERNS = [
    "connection_engine", "get_or_create_exchange", "release_exchange",
    "routers.exchange", "routers import exchange",
    "subscription_dependencies", "check_exchange_connection_slot",
    "check_strategy_quota", "check_bot_quota", "enforce_quota",
    "hard_quota_enforcer", "HardQuotaEnforcer",
    "dag_task_queue", "entitlement_engine", "entitlement",
    "exchange_connection_slot_guard", "close_all_exchanges",
]

hits = {}
for path in sorted(pathlib.Path("tests").rglob("*.py")):
    if "__pycache__" in path.parts:
        continue
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        continue
    matched = [p for p in PATTERNS if p in text]
    if matched:
        hits[path.as_posix()] = matched

print(f"{len(hits)} affected test file(s)")
for path, matched in hits.items():
    print(f"{path}  <- {', '.join(sorted(set(matched))[:4])}")
