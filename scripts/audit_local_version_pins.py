#!/usr/bin/env python3
"""Audit requirement pins that ``pip-audit`` silently skips.

WHY THIS EXISTS
---------------
``pip-audit`` audits the INSTALLED environment by resolving each distribution against
PyPI. A pin carrying a PEP 440 *local version* segment -- ``torch==2.3.1+cpu``, installed
from ``https://download.pytorch.org/whl/cpu`` -- does not exist on PyPI under that exact
version, so pip-audit cannot resolve it. It does not fail. It prints a *skip* and exits 0:

    No known vulnerabilities found, 4 ignored
    Name  Skip Reason
    ----- -----------------------------------------------------------------------
    torch Dependency not found on PyPI and could not be audited: torch (2.3.1+cpu)

That is the real output of the blocking gate in ``.github/workflows/05-security.yml`` on
main. The headline said "No known vulnerabilities found" while the single largest source
of advisories in the manifest went unexamined. Queried directly against the same service
pip-audit uses by default (PyPI's own per-release ``vulnerabilities`` array),
``torch==2.3.1`` carried **23 advisories**, including ``GHSA-53q9-r3pm-6pq6`` /
``CVE-2025-32434`` -- rated CRITICAL, remote code execution via ``torch.load``.

So the gate was not wrong about what it checked. It was silent about what it skipped, and
a skip in a blocking security job is indistinguishable from a pass to anyone reading the
summary line.

WHAT THIS DOES
--------------
Finds every pin with a local version segment in the requirement files, strips the local
part, and audits the base version with ``pip-audit --no-deps``, which audits a pinned
requirement without resolving or installing anything. Any advisory that is not in
:data:`IGNORED` fails the build.

It also fails when a local-version pin exists that this script did not audit, so adding a
second such pin cannot quietly inherit the same blind spot.

WHY NOT JUST ``pip-audit --strict``
-----------------------------------
``--strict`` fails on *any* skipped dependency, which would fail permanently for as long
as a ``+cpu`` pin exists -- it turns an invisible gap into an unfixable red build rather
than auditing the thing. This script audits the pin instead, so the gap closes.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Requirement files to scan. ``requirements-cpu.txt`` is the one the production image and
#: the blocking audit job both install; the other two carry the same package for local and
#: GPU use and are scanned so a bump cannot land in one and be forgotten in another.
REQUIREMENT_FILES: Tuple[str, ...] = (
    "requirements-cpu.txt",
    "requirements.txt",
    "backend_app/requirements.txt",
)

#: Advisories accepted for a local-version pin, each with the reason it is accepted.
#: Same ratchet-from-a-known-baseline convention as 05-security.yml's own
#: ``--ignore-vuln`` list: an advisory outside this map fails the job, and an entry is
#: removed in the same commit that bumps the pin which fixes it.
#:
#: THIS MAP IS NOW EMPTY, and DEP-01 is suppression-free across BOTH dependency gates --
#: this one and 05-security.yml's pip-audit step, which no longer passes --ignore-vuln
#: either.
#:
#: The three entries removed were all torch, all LOW, and both of their stated reasons
#: had expired:
#:
#:   GHSA-rrmf-rvhw-rf47 / CVE-2025-3000 (memory corruption via torch.jit)
#:   PYSEC-2025-194 (its alias)
#:       Accepted as "needs 2.13.0; tracked in DEP-01 for a later pass", on the
#:       reasoning that stretching from 2.10.0 to 2.13.0 for one LOW would widen the
#:       blast radius of a dependency used only for optional seeding and GPU detection.
#:       That reasoning was sound at the time. It is now cheap to take: 2.13.0+ exists,
#:       `torch==2.14.1+cpu` is on download.pytorch.org for cp311, and the API surface
#:       actually used is seven symbols in one module, all stable since torch 1.x.
#:
#:   PYSEC-2026-139 / CVE-2026-4538
#:       Accepted as "no fixed version published in any release yet", which was true
#:       when written. OSV now reports torch 2.13.0, 2.14.0 and 2.14.1 as UNAFFECTED.
#:       An accepted advisory whose justification has silently expired is the failure
#:       mode this whole convention exists to prevent, and it happened here.
#:
#: BEFORE ADDING AN ENTRY BACK: re-query OSV for the pin rather than trusting a previous
#: pass's note. Both entries above, and the razorpay premise that kept setuptools pinned
#: in requirements-base.txt, were inherited conclusions that had stopped being true.
IGNORED: Dict[str, str] = {}

LOCAL_PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*==\s*(?P<version>[^\s;#]+)"
)


def find_local_version_pins() -> Dict[str, List[Tuple[str, str]]]:
    """Map base-pin -> list of (file, original spec) for every local-version pin."""
    found: Dict[str, List[Tuple[str, str]]] = {}
    for rel in REQUIREMENT_FILES:
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith(("#", "-")):
                continue
            m = LOCAL_PIN_RE.match(line)
            if not m:
                continue
            version = m.group("version")
            if "+" not in version:
                continue
            base = f"{m.group('name')}=={version.split('+')[0]}"
            found.setdefault(base, []).append((rel, f"{m.group('name')}=={version}"))
    return found


def audit(base_pins: List[str]) -> int:
    """Run ``pip-audit --no-deps`` over the base pins. Returns pip-audit's exit code."""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", delete=False, encoding="utf-8"
    ) as fh:
        fh.write("\n".join(base_pins) + "\n")
        req_path = fh.name

    cmd = [
        sys.executable, "-m", "pip_audit",
        "--no-deps",
        "--requirement", req_path,
        "--progress-spinner", "off",
    ]
    for vuln_id in IGNORED:
        cmd += ["--ignore-vuln", vuln_id]

    print("$ " + " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, cwd=REPO_ROOT)
    try:
        pathlib.Path(req_path).unlink()
    except OSError:
        pass
    return completed.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="print the pins that would be audited and exit 0 (used by the test suite)",
    )
    args = parser.parse_args()

    pins = find_local_version_pins()
    if not pins:
        # Not an error: it means no pin carries a local version any more, so pip-audit's
        # own environment audit already covers everything and this gate is a no-op.
        print(
            "No local-version pins found in "
            f"{', '.join(REQUIREMENT_FILES)} -- nothing pip-audit would skip."
        )
        return 0

    print("Local-version pins pip-audit cannot resolve against PyPI:")
    for base, occurrences in sorted(pins.items()):
        for rel, spec in occurrences:
            print(f"  {spec:<24} ({rel})   -> auditing {base}")

    if args.list_only:
        return 0

    print()
    print(f"{len(IGNORED)} advisory id(s) accepted with a documented reason:")
    for vuln_id, reason in sorted(IGNORED.items()):
        print(f"  {vuln_id}: {reason}")
    print()

    code = audit(sorted(pins))
    if code != 0:
        print(
            "\nFAILED: a local-version pin carries an advisory that is not in this "
            "script's IGNORED map. Bump the pin, or add the id with the reason it is "
            "accepted -- in every file listed above, so the manifests cannot drift.",
            file=sys.stderr,
        )
    else:
        print("\nOK: every local-version pin audited clean (modulo the ids above).")
    return code


if __name__ == "__main__":
    sys.exit(main())
