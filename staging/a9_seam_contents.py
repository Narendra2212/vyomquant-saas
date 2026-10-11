"""Phase 11B-A9. READ-ONLY. Symbol presence is necessary but not sufficient.

`tests/conftest.py` is +172/-0 uncommitted and `backend_app/core/cache/redis_manager.py` is
+185/-5. Both symbols the new production-path tests need exist at HEAD, but the two new
tests rely on a specific BEHAVIOUR: `_bypass_dev_mode(True)` must be undone after the test,
or the flipped singleton leaks into every later file in the same session.

The tests' own docstring asserts that safety: "It is listed in tests/conftest.py's
`_REDIS_SINGLETON_SEAMS`, whose autouse `_restore_shared_redis_singleton` fixture restores
it after every test -- so flipping it cannot leak into another file."

If that seam entry exists only in the uncommitted conftest diff, the claim is false in CI.
Print the actual seam list and the restore fixture at HEAD vs the worktree.
"""

from __future__ import annotations

import re
import subprocess

REPO = r"c:\aerora_quant_backend_updated_final1"
HEAD = "074b772ab16cece62cbfca0f2b78497be2ae56d5"


def blob(rev: str, path: str) -> str:
    r = subprocess.run(["git", "cat-file", "blob", f"{rev}:{path}"], cwd=REPO, capture_output=True)
    return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else ""


def show_seams(label: str, text: str) -> None:
    print(f"--- {label} ---")
    m = re.search(r"_REDIS_SINGLETON_SEAMS\s*=\s*\(([^)]*)\)", text, re.S)
    if not m:
        m = re.search(r"_REDIS_SINGLETON_SEAMS\s*=\s*\[([^\]]*)\]", text, re.S)
    if not m:
        print("  <seam list not found in a recognised literal form>")
        idx = text.find("_REDIS_SINGLETON_SEAMS")
        if idx >= 0:
            print("  raw context:")
            for l in text[idx - 200 : idx + 600].splitlines():
                print(f"    {l[:120]}")
        return
    body = m.group(1)
    items = [i.strip() for i in body.split(",") if i.strip()]
    print(f"  seam entries ({len(items)}):")
    for i in items:
        print(f"    {i[:100]}")
    has_bypass = "dev_mode" in body or "bypass" in body
    print(f"  mentions a dev-mode / bypass seam : {has_bypass}")
    print(f"  restore fixture present           : {'_restore_shared_redis_singleton' in text}")
    print(f"  fixture is autouse                : {bool(re.search(r'autouse=True[^#]*\\n\\s*def _restore_shared_redis_singleton|_restore_shared_redis_singleton', text))}")
    print()


def main() -> int:
    head = blob(HEAD, "tests/conftest.py")
    disk = open(REPO + r"\tests\conftest.py", encoding="utf-8", errors="replace").read()
    print(f"conftest.py  HEAD={len(head)} bytes   worktree={len(disk.encode('utf-8'))} bytes")
    print()
    show_seams("HEAD / main (what CI would run)", head)
    show_seams("WORKTREE (uncommitted)", disk)

    print("=== the restore fixture, at HEAD ===")
    idx = head.find("_restore_shared_redis_singleton")
    if idx >= 0:
        start = head.rfind("@pytest", 0, idx)
        print(head[start if start >= 0 else idx : idx + 900])
    else:
        print("  <absent at HEAD>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
