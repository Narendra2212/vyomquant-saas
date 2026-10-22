"""DEP-01: a pin pip-audit cannot resolve must still be audited, and must not drift.

WHY THIS FILE EXISTS

``.github/workflows/05-security.yml``'s blocking dependency job reported, verbatim, on
main:

    No known vulnerabilities found, 4 ignored
    Name  Skip Reason
    ----- -----------------------------------------------------------------------
    torch Dependency not found on PyPI and could not be audited: torch (2.3.1+cpu)

pip-audit audits the installed environment by resolving each distribution against PyPI.
``torch==2.3.1+cpu`` is installed from ``download.pytorch.org`` and does not exist on
PyPI under that exact version, so pip-audit skipped it -- and a skip is not a failure.
The job stayed green while the largest single source of advisories in the manifest went
unexamined: ``torch==2.3.1`` carried 23 advisories per PyPI's own release data, including
``GHSA-53q9-r3pm-6pq6`` / ``CVE-2025-32434``, CRITICAL, remote code execution via
``torch.load``.

These tests pin the three things that keep that closed:

1. the workflow actually runs ``scripts/audit_local_version_pins.py``;
2. the script still finds the local-version pin (so the detector cannot quietly stop
   matching and pass vacuously); and
3. the torch version is identical in all four places it is written, because a bump that
   lands in one file and not another re-opens the hole in whichever file was missed --
   and the production image reads a DIFFERENT one (the Dockerfile) from the one the audit
   job installs (``requirements-cpu.txt``).

WHAT THESE TESTS DO NOT DO

They do not contact PyPI or run pip-audit. Whether ``torch==2.10.0`` is clean *today* is
the gate's job in CI, where the network and the real advisory database are available;
asserting a live advisory count here would make the suite fail on a newly published
advisory, which is the gate's signal to give, not this file's.
"""

from __future__ import annotations

import pathlib
import re
from typing import Dict

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "05-security.yml"
SCRIPT = REPO_ROOT / "scripts" / "audit_local_version_pins.py"

#: Every place the torch version is written, and whether it carries the +cpu local part.
TORCH_PIN_SITES: Dict[str, bool] = {
    "requirements.txt": False,
    "requirements-cpu.txt": True,
    "backend_app/requirements.txt": False,
    "Dockerfile": True,
}

TORCH_RE = re.compile(r"torch==(?P<version>\d+\.\d+\.\d+)(?P<local>\+[A-Za-z0-9.]+)?")


def _read(rel: str) -> str:
    path = REPO_ROOT / rel
    assert path.is_file(), f"expected {rel} to exist"
    return path.read_text(encoding="utf-8")


def test_the_audit_script_exists_and_is_executable_as_a_module():
    assert SCRIPT.is_file(), f"missing {SCRIPT}"
    source = SCRIPT.read_text(encoding="utf-8")
    assert "def find_local_version_pins" in source
    assert "--no-deps" in source, (
        "the script must audit the pin without resolving or installing it; --no-deps is "
        "what makes that possible"
    )


def test_the_security_workflow_runs_the_local_pin_audit():
    text = _read(".github/workflows/05-security.yml")
    assert "scripts/audit_local_version_pins.py" in text, (
        "05-security.yml must invoke the local-version pin audit, otherwise a pin "
        "pip-audit skips is audited by nothing"
    )
    # It must be a real step, not only mentioned in a comment.
    executable_lines = [
        line for line in text.splitlines()
        if "audit_local_version_pins.py" in line and not line.lstrip().startswith("#")
    ]
    assert executable_lines, (
        "audit_local_version_pins.py appears only inside comments in 05-security.yml; a "
        "mention is not an invocation"
    )


def test_the_stale_starlette_ignores_are_gone():
    """starlette==1.3.1 fixes all five, so listing them is a standing silence.

    Verified before removal against the same service pip-audit uses by default
    (https://pypi.org/pypi/starlette/1.3.1/json -> ``vulnerabilities``: empty).
    """
    text = _read(".github/workflows/05-security.yml")
    stale = [
        "PYSEC-2026-161", "PYSEC-2026-248", "PYSEC-2026-249",
        "PYSEC-2026-2280", "PYSEC-2026-2281",
    ]
    ignore_lines = [
        line for line in text.splitlines()
        if "--ignore-vuln" in line and not line.lstrip().startswith("#")
    ]
    joined = "\n".join(ignore_lines)
    still_present = [vuln_id for vuln_id in stale if vuln_id in joined]
    assert not still_present, (
        "these starlette advisory ids are fixed by the committed starlette==1.3.1 pin but "
        f"are still being ignored by the blocking gate: {still_present}"
    )


def test_the_live_ignores_are_still_declared():
    """Negative control: the two genuinely-live ignores must not be removed by accident."""
    text = _read(".github/workflows/05-security.yml")
    for vuln_id, why in (
        ("PYSEC-2026-3447", "setuptools; fix removes pkg_resources and breaks razorpay"),
        ("PYSEC-2026-1805", "protobuf; capped by opentelemetry-proto/tensorflow pins"),
    ):
        assert vuln_id in text, f"{vuln_id} must stay ignored ({why})"


def test_a_local_version_pin_still_exists_for_the_script_to_find():
    """Guards against this suite passing vacuously.

    If every local-version pin disappears the script becomes a no-op, which is fine -- but
    then these tests are measuring nothing, and that should be a deliberate edit rather
    than a silent state.
    """
    found = []
    for rel, has_local in TORCH_PIN_SITES.items():
        if not has_local:
            continue
        for match in TORCH_RE.finditer(_read(rel)):
            if match.group("local"):
                found.append((rel, match.group(0)))
    assert found, (
        "no +local torch pin found in any of "
        f"{[r for r, l in TORCH_PIN_SITES.items() if l]}; if that is intentional, update "
        "this test and scripts/audit_local_version_pins.py together"
    )


def test_the_torch_version_is_identical_in_every_place_it_is_written():
    versions: Dict[str, str] = {}
    for rel, expect_local in TORCH_PIN_SITES.items():
        text = _read(rel)
        matches = TORCH_RE.findall(text)
        assert matches, f"no torch=={{version}} pin found in {rel}"
        # findall with groups returns tuples of (version, local)
        found_versions = {m[0] for m in matches}
        assert len(found_versions) == 1, (
            f"{rel} pins more than one torch version: {sorted(found_versions)}"
        )
        version = found_versions.pop()
        versions[rel] = version

        has_local = any(m[1] for m in matches)
        assert has_local == expect_local, (
            f"{rel}: expected local version segment={expect_local}, found={has_local}. "
            "requirements-cpu.txt and the Dockerfile install the +cpu build from "
            "download.pytorch.org; the other two resolve from PyPI."
        )

    distinct = set(versions.values())
    assert len(distinct) == 1, (
        "the torch version has drifted between the files that pin it: "
        f"{versions}. The production image installs the Dockerfile's version while the "
        "blocking audit job installs requirements-cpu.txt's, so a mismatch means the "
        "audited version is not the shipped one."
    )


def test_the_pinned_torch_clears_the_critical_rce_advisory():
    """CVE-2025-32434 (torch.load RCE) is fixed in 2.6.0; the pin must be at least that.

    A version comparison, not a live advisory lookup: this asserts the floor the finding
    was about, and stays true without network access.
    """
    version = TORCH_PIN_SITES and TORCH_RE.search(_read("requirements-cpu.txt"))
    assert version, "could not read the torch pin from requirements-cpu.txt"
    parts = tuple(int(p) for p in version.group("version").split("."))
    assert parts >= (2, 6, 0), (
        f"torch is pinned at {version.group('version')}, below the 2.6.0 that fixes "
        "GHSA-53q9-r3pm-6pq6 / CVE-2025-32434 (CRITICAL, remote code execution via "
        "torch.load)"
    )


def test_every_accepted_advisory_in_the_script_carries_a_reason():
    """An ignore without a stated reason is indistinguishable from suppression."""
    source = SCRIPT.read_text(encoding="utf-8")
    block = source.split("IGNORED: Dict[str, str] = {", 1)
    assert len(block) == 2, "could not find the IGNORED map in the audit script"
    body = block[1].split("\n}", 1)[0]
    # Must match BOTH id shapes. An earlier version of this test matched only
    # PYSEC-YYYY-N, which silently skipped every GHSA- entry -- so a GHSA ignore with no
    # reason would have passed this guard. Caught while negative-controlling the script.
    ids = re.findall(r'"((?:PYSEC|CVE|BIT)-\d{4}-[0-9A-Za-z-]+|GHSA-[0-9a-z]{4,}(?:-[0-9a-z]{4,})+)"\s*:', body)
    assert ids, "the IGNORED map declares no advisory ids; the parser may have drifted"
    ghsa = [i for i in ids if i.startswith("GHSA-")]
    pysec = [i for i in ids if i.startswith("PYSEC-")]
    assert ghsa and pysec, (
        "expected the IGNORED map to contain both GHSA- and PYSEC- ids (the same advisory "
        f"is often listed under both); parsed {ids}. If the map genuinely no longer has "
        "one of the two shapes, relax this assertion deliberately rather than leaving a "
        "parser that cannot see it."
    )
    for vuln_id in ids:
        # The reason is the mapped value; assert it is a non-trivial string.
        m = re.search(rf'"{re.escape(vuln_id)}"\s*:\s*\(?\s*"([^"]+)"', body)
        assert m and len(m.group(1).strip()) > 12, (
            f"{vuln_id} is accepted without a substantive reason"
        )
