"""
tests/test_nightly_audit_workflow.py

Unit tests verifying that:
1. .github/workflows/04-nightly-audit.yml incorporates pip-audit, npm audit, and cargo audit.
2. requirements.txt parses cleanly without encoding errors.
"""

import pathlib


def test_nightly_audit_workflow_contains_audit_steps():
    workflow_path = pathlib.Path(".github/workflows/04-nightly-audit.yml")
    assert workflow_path.exists(), ".github/workflows/04-nightly-audit.yml file missing"

    content = workflow_path.read_text(encoding="utf-8")

    assert "pip-audit" in content, "Nightly workflow missing pip-audit step"
    assert "npm audit" in content, "Nightly workflow missing npm audit step"
    assert "cargo audit" in content, "Nightly workflow missing cargo audit step"
    assert "pip-audit -r requirements.txt" in content, "Nightly workflow missing pip-audit command"
    assert "cd algo22-terminal" in content, "Nightly workflow missing npm audit path"
    assert "cd algo22-terminal/src-tauri" in content, "Nightly workflow missing cargo audit path"


def test_requirements_txt_ascii_comments():
    req_path = pathlib.Path("requirements.txt")
    assert req_path.exists(), "requirements.txt missing"

    content = req_path.read_text(encoding="utf-8")

    # Assert no non-ASCII box drawing characters remain in requirements.txt
    for line in content.splitlines():
        if line.startswith("#"):
            assert line.isascii(), f"Non-ASCII comment line found: {line}"
