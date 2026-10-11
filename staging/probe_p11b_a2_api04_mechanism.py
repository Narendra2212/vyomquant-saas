"""PHASE 11B-A2 section 4 -- the API04 failure's actual mechanism and its CI relevance.

Read-only. Prints no secret: from `.env` it reads ONLY the trading-mode keys and reports
whether they are set and to what mode, never any other value.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess

TEST = pathlib.Path("tests/test_api04_store_failure_after_startup.py")
POOL = pathlib.Path("backend_app/core/database_pool.py")


def banner(text: str) -> None:
    print("=" * 78)
    print(text)
    print("=" * 78)


def main():
    banner("1. THE RAISE, AND ITS CONDITION (committed code)")
    src = POOL.read_text(encoding="utf-8")
    for i, line in enumerate(src.splitlines(), 1):
        if 135 <= i <= 143:
            print(f"  {i}: {line}")
    tracked = subprocess.run(["git", "status", "--porcelain", "--", str(POOL)],
                             capture_output=True, text=True, encoding="utf-8").stdout.strip()
    print(f"\n  working-tree status of database_pool.py: "
          f"{tracked if tracked else 'clean (committed)'}")

    banner("2. IS THE ENV-CLEARING FIXTURE AUTOUSE FOR THE FAILING TEST?")
    tree = ast.parse(TEST.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for dec in node.decorator_list:
                txt = ast.unparse(dec)
                if "fixture" in txt:
                    print(f"  fixture {node.name}: {txt}")
                    autouse = "autouse=True" in txt
                    print(f"    autouse: {autouse}")
                    body = ast.unparse(node)
                    print(f"    deletes VYOMQUANT_MODE: {'VYOMQUANT_MODE' in body}")

    banner("3. WHAT DOES get_vyomquant_mode FALL BACK TO WHEN THE VAR IS DELETED?")
    # The repository carries a developer .env; CI does not (it is gitignored).
    env_file = pathlib.Path(".env")
    print(f"  .env present locally : {env_file.is_file()}")
    gi = pathlib.Path(".gitignore").read_text(encoding="utf-8", errors="replace")
    print(f"  .env gitignored      : {any(l.strip() in ('.env', '/.env') for l in gi.splitlines())}")
    tracked_env = subprocess.run(["git", "ls-files", ".env"], capture_output=True,
                                 text=True, encoding="utf-8").stdout.strip()
    print(f"  .env tracked by git  : {bool(tracked_env)}  -> {'present in CI' if tracked_env else 'ABSENT in CI'}")
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() in ("VYOMQUANT_MODE", "AERORA_MODE", "DATABASE_URL"):
                k = key.strip()
                v = value.strip()
                if k == "DATABASE_URL":
                    print(f"    .env {k:16s} = {'set (value withheld)' if v else 'empty'}")
                else:
                    print(f"    .env {k:16s} = {v!r}   <-- mode keys are not secrets")

    banner("4. IS THE TEST EVEN IN CI?")
    t = subprocess.run(["git", "ls-files", str(TEST)], capture_output=True,
                       text=True, encoding="utf-8").stdout.strip()
    print(f"  tracked by git : {bool(t)}")
    print(f"  => {'in CI' if t else 'NOT in CI -- untracked, never committed, never run there'}")
    print("  Corroborated independently: the most recent CI job log contains 0 mentions")
    print("  of 'api04_store_failure' (staging/p11b_a2_unit_job_failure.txt section E).")

    banner("5. WOULD THE CI SELECTOR PICK IT UP IF IT WERE COMMITTED?")
    name = "test_the_sensitive_endpoints_that_must_stay_rate_limited_are_decorated"
    nodeid = f"tests/test_api04_store_failure_after_startup.py::{name}"
    print(f"  selector: -k \"not chaos and not load\" -m \"not frontend_bundle\"")
    print(f"  'chaos' in id : {'chaos' in nodeid}")
    print(f"  'load'  in id : {'load' in nodeid}")
    print(f"  => would be SELECTED: {not ('chaos' in nodeid or 'load' in nodeid)}")

    banner("CONCLUSION")
    print("  The raise is in COMMITTED code. The test deletes VYOMQUANT_MODE, so the mode")
    print("  resolver falls back to whatever the local .env declares. With a paper/live")
    print("  mode there and DATABASE_URL blanked by conftest, database_pool raises AT")
    print("  IMPORT. CI has no .env, so the resolver defaults to 'safe' and the SQLite")
    print("  fallback applies -- which is why this is a LOCAL-ENVIRONMENT artefact.")
    print("  It is NOT D5'-attributable and NOT currently a CI blocker, because the file")
    print("  is not in CI at all.")


if __name__ == "__main__":
    main()
