"""
tests/test_terraform_iam_ssm_scoping.py

Unit tests verifying that terraform/iam.tf scopes ssm:GetParameters to the application parameter path
arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/vyomquant/*
instead of an account-wide wildcard Resource "*".
"""

import re
import pathlib


def test_ecs_task_policy_ssm_resource_is_scoped():
    iam_tf_path = pathlib.Path("terraform/iam.tf")
    assert iam_tf_path.exists(), "terraform/iam.tf file missing"

    content = iam_tf_path.read_text(encoding="utf-8")

    # Isolate the ssm:GetParameters statement's own Resource value. CI-02's OIDC deploy
    # role (added later, same file) legitimately uses `Resource = "*"` on actions that do
    # not support resource-level scoping at all (ecr:GetAuthorizationToken, the six
    # ecs:Describe*/Update*/List* calls -- each with its own comment explaining why), so a
    # whole-file substring ban on `Resource = "*"` is too broad: it would fail the moment
    # any OTHER, correctly-unscoped statement is added anywhere in this file, regardless of
    # whether ssm:GetParameters itself stayed scoped. Scope the check to the one statement
    # this test is actually about.
    ssm_statement_match = re.search(
        r'"ssm:GetParameters"\s*\]\s*Resource\s*=\s*(\[[^\]]*\]|"[^"]*")',
        content,
    )
    assert ssm_statement_match, "could not find the ssm:GetParameters statement's Resource value in terraform/iam.tf"
    ssm_resource_value = ssm_statement_match.group(1)

    # Confirm that statement's own Resource is no longer wildcard "*"
    assert '"*"' != ssm_resource_value.strip(), (
        "ssm:GetParameters still contains un-scoped wildcard Resource '*'"
    )

    # Confirm Resource is scoped to parameter/vyomquant/*
    expected_arn_pattern = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/vyomquant/*"
    assert expected_arn_pattern in ssm_resource_value, (
        f"ssm:GetParameters expected resource pattern '{expected_arn_pattern}' missing "
        f"in terraform/iam.tf (found Resource = {ssm_resource_value!r} instead)"
    )
