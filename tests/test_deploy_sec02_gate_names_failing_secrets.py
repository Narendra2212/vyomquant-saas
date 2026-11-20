"""DEPLOY-SEC-02: the pre-deployment gate names each failing secret and the AWS reason.

2026-11-20: '03 Deploy' runs 581/582 were refused with only "Secrets Manager: SOME MISSING";
nothing in the log or artifacts said which of the ten secrets, or whether it was missing,
scheduled for deletion, or unreadable by the deploy role -- three different fixes.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import aws_validation  # noqa: E402


def test_error_code_is_extracted():
    assert aws_validation._aws_error_code(
        "An error occurred (AccessDeniedException) when calling the DescribeSecret operation"
    ) == "AccessDeniedException"
    assert aws_validation._aws_error_code("") == "no error text"


def test_a_missing_secret_is_named_with_its_reason(monkeypatch):
    def fake(cmd):
        if cmd[:2] == ["secretsmanager", "describe-secret"] and cmd[-1].endswith("jwt_secret"):
            return 254, "", "An error occurred (ResourceNotFoundException) when calling X"
        if cmd[:2] == ["secretsmanager", "list-secret-version-ids"]:
            return 0, '{"Versions": [{"VersionId": "v"}]}', ""
        return 0, "{}", ""

    monkeypatch.setattr(aws_validation, "run_aws_cmd", fake)
    report = aws_validation.validate_aws_live()
    msgs = [e for e in report["errors"] if "jwt_secret" in e]
    assert msgs and "ResourceNotFoundException" in msgs[0], report["errors"]
