"""List scripts in staging/ that NO launch document cites.

The reverse of `staging/check_doc_citations.py`. Scaffolding that earned its keep is
cited and should stay; scaffolding that is not cited is a one-off probe and should not
accumulate. This makes the distinction explicit so the clean-up is a decision rather
than a guess about what is still needed.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
STAGING = ROOT / "staging"

DOCS = sorted(ROOT.glob("*.md"))

#: Infrastructure and shared helpers. Not cited by name in prose, but every cited probe
#: depends on them, so "uncited" does not mean "unused".
INFRASTRUCTURE = {
    "dsn.py",
    "bootstrap_pg.py",
    "q.py",
    "reprovision.py",
    "run_pg_suite.py",
    "run_all_probes.py",
}


def main() -> int:
    corpus = "\n".join(
        d.read_text(encoding="utf-8", errors="replace") for d in DOCS
    )

    cited: list[str] = []
    uncited: list[str] = []
    infra: list[str] = []

    for script in sorted(STAGING.glob("*.py")):
        name = script.name
        if name in INFRASTRUCTURE:
            infra.append(name)
        elif name in corpus:
            cited.append(name)
        else:
            uncited.append(name)

    print("=" * 78)
    print("STAGING SCRIPTS BY WHETHER A LAUNCH DOCUMENT CITES THEM")
    print("=" * 78)
    print(f"CITED ({len(cited)}) -- keep, a document points a reader at these")
    for name in cited:
        print(f"  {name}")
    print()
    print(f"INFRASTRUCTURE ({len(infra)}) -- keep, the cited ones depend on these")
    for name in infra:
        print(f"  {name}")
    print()
    print(f"UNCITED ({len(uncited)}) -- candidates for removal")
    for name in uncited:
        print(f"  {name}")
    print("-" * 78)
    print(f"SUMMARY: {len(cited)} cited, {len(infra)} infrastructure, {len(uncited)} uncited")
    return 0


if __name__ == "__main__":
    sys.exit(main())
