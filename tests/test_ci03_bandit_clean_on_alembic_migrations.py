"""CI-03: the Bandit gate that blocked HEAD (9 x B608 in alembic migrations) stays green.

`05-security.yml` runs `bandit -r backend_app -ll` with no baseline; any MEDIUM finding turns
05 red and 03 Deploy refuses. The audit measured 9 findings, all B608 in three alembic
revisions that interpolate IDENTIFIERS (validated by `_validate_table_name`, or module
constants) into DDL/SELECT text -- identifiers cannot be bound parameters. They are
suppressed with a reasoned `# nosec B608`; the AST guard in
test_bandit_sast_gate_is_green.py continues to hold the premise (nothing unvalidated reaches
the statement). This test runs the gate's own command on the directory that failed.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ALEMBIC = REPO / "backend_app" / "alembic"


def test_bandit_ll_reports_nothing_under_alembic():
    pytest.importorskip("bandit", reason="bandit is installed by 05-security.yml")
    done = subprocess.run(
        [sys.executable, "-m", "bandit", "-r", str(ALEMBIC), "-ll", "-f", "json", "-q"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    report = json.loads(done.stdout)
    findings = report["results"]
    assert not findings, "bandit -ll findings under alembic:\n" + "\n".join(
        f"  {f['filename']}:{f['line_number']} {f['test_id']} {f['issue_text']}" for f in findings
    )


def test_every_alembic_nosec_carries_a_reason():
    for path in ALEMBIC.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r"(?<![`'\"])# nosec B\d+(.*)$", line)
            if m:
                assert m.group(1).startswith(" - "), f"{path}:{n} nosec without a reason"
