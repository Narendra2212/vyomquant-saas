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

    # Confirm Resource is no longer wildcard "*" for ssm:GetParameters
    assert '"Resource" = "*"' not in content, "ssm:GetParameters still contains un-scoped wildcard Resource '*'"
    assert 'Resource = "*"' not in content, "ssm:GetParameters still contains un-scoped wildcard Resource '*'"

    # Confirm Resource is scoped to parameter/vyomquant/*
    expected_arn_pattern = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/vyomquant/*"
    assert expected_arn_pattern in content, f"ssm:GetParameters expected resource pattern '{expected_arn_pattern}' missing in terraform/iam.tf"
