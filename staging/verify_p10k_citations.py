"""Verify every file:line citation in the Workstream K report still resolves."""
import pathlib

CITATIONS = [
    ("backend_app/core/subscription_dependencies.py", 907, "_acquire_slot_lock"),
    ("backend_app/core/subscription_dependencies.py", 946, "_release_slot_lock"),
    ("backend_app/core/subscription_dependencies.py", 1123, "_release_slot_lock"),
    ("tests/conftest.py", 13, "REDIS_URL"),
    ("backend_app/core/cache/redis_manager.py", 448, "_GETDEL_LUA"),
    ("backend_app/core/cache/redis_manager.py", 847, "getdel"),
    ("backend_app/core/cache/redis_manager.py", 883, "_GETDEL_LUA"),
    ("tests/test_quota01_exchange_slot_is_atomic.py", 646, "real_redis"),
    ("tests/test_quota01_exchange_slot_is_atomic.py", 702, "real_redis"),
    ("tests/test_quota01_exchange_slot_is_atomic.py", 761, "successors_lock"),
    ("tests/test_quota01_exchange_slot_is_atomic.py", 782, "normal_exit_releases"),
    ("tests/test_quota01_exchange_slot_is_atomic.py", 800, "ownership_outcome"),
    ("tests/test_quota01_exchange_slot_is_atomic.py", 840, "bare_delete_on_release"),
    # :224 is the `@pytest.mark.skipif(` line -- which is the location pytest itself reports
    # in "SKIPPED [1] ...:224". The test function it guards is on :233.
    ("tests/test_quota01_metered_reservation_is_hard.py", 224, "skipif"),
    ("tests/test_quota01_metered_reservation_is_hard.py", 233, "real_redis"),
    ("backend_app/core/subscription_engine.py", 1150, "PLAN_LIMITS"),
]

bad = 0
for path, line, expect in CITATIONS:
    lines = pathlib.Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    actual = lines[line - 1] if 0 < line <= len(lines) else "<out of range>"
    ok = expect in actual
    if not ok:
        bad += 1
    print(f"{'OK  ' if ok else 'MISS'} {path}:{line}  expect {expect!r}")
    if not ok:
        print(f"       actual: {actual.strip()[:90]}")

print(f"\n{len(CITATIONS) - bad}/{len(CITATIONS)} citations resolve")
