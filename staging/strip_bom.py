"""Find and remove UTF-8 BOMs from source files in this repository.

PowerShell 5.1's `Set-Content -Encoding utf8` writes UTF-8 **with a byte-order mark**.
Several files were edited that way during this work and acquired a leading U+FEFF.

WHY THIS IS NOT COSMETIC
------------------------
CPython's import machinery tolerates a BOM, so a module with one still imports and runs
-- which is exactly why it went unnoticed. But anything that reads the source as TEXT and
parses it sees the BOM as a character:

    >>> ast.parse(open(path, encoding="utf-8").read())
    SyntaxError: invalid non-printable character U+FEFF

That broke the static safety checks in
`tests/test_production_security_probe_rolls_back_postgres.py`, whose entire job is to
prove the production probe contains no reachable `commit()`. A check that cannot parse
its target is a check that proves nothing, and it would have failed closed rather than
open -- but only because it asserts on the parse. A laxer check would have passed.

It also affects `tests/test_bandit_sast_gate_is_green.py`, which walks every module's AST
to find unconstrained SQL interpolation.

    python staging/strip_bom.py [--apply] [paths...]

Without `--apply` it only reports.
"""

from __future__ import annotations

import pathlib
import sys

BOM = b"\xef\xbb\xbf"

DEFAULT_ROOTS = ("scripts", "staging", "tests", "backend_app/alembic/versions",
                 "backend_app/core/models", "migrations")


def main() -> int:
    args = [a for a in sys.argv[1:] if a != "--apply"]
    apply = "--apply" in sys.argv

    root = pathlib.Path(__file__).resolve().parent.parent
    targets: list[pathlib.Path] = []
    if args:
        targets = [pathlib.Path(a) for a in args]
    else:
        for rel in DEFAULT_ROOTS:
            base = root / rel
            if base.exists():
                targets.extend(sorted(base.rglob("*.py")))
                targets.extend(sorted(base.rglob("*.sql")))

    affected: list[pathlib.Path] = []
    for path in targets:
        if not path.is_file():
            continue
        data = path.read_bytes()
        if not data.startswith(BOM):
            continue
        affected.append(path)
        if apply:
            path.write_bytes(data[len(BOM):])

    print("=" * 78)
    print("UTF-8 BOM SCAN")
    print("=" * 78)
    print(f"{len(targets)} file(s) scanned")
    if not affected:
        print("no BOM found")
        return 0

    print(f"{len(affected)} file(s) carry a BOM:")
    for path in affected:
        try:
            shown = path.relative_to(root)
        except ValueError:
            shown = path
        print(f"  {shown}")
    if apply:
        print()
        print(f"BOM removed from {len(affected)} file(s)")
        return 0
    print()
    print("re-run with --apply to remove them")
    return 1


if __name__ == "__main__":
    sys.exit(main())
