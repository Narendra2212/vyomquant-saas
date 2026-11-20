"""INF-02/INF-06: no workflow may run `terraform apply` (or `plan` that feeds one).

WHY THIS GUARD EXISTS, AND WHY IT IS A TEST RATHER THAN A COMMENT.

`terraform/` does not describe the running production estate. A measured plan
(2026-11-01) says `101 to add, 1 to change, 1 to destroy` against infrastructure
that is already running: the 100 creates are duplicates of live resources --
including a second load balancer, a second CloudFront distribution, a second ECS
cluster, nine ECS services and three NAT gateways -- and the one destroy is
`aws_security_group.alb`, which is in state and attached to the live ALB.

So an `apply` from this configuration does not converge production onto the
config. It builds a parallel one and replaces a live security group. It would
also fail partway, because eight of the creates are Secrets Manager secrets
whose names already exist.

There are exactly two paths to that happening: a human in the directory, and a
CI workflow. The first is addressed by `terraform/DO-NOT-APPLY.md`, which is the
most a file can do. The second is enforceable, and this test enforces it --
because a workflow that runs `terraform apply` would do it unattended, with
production credentials, on every push, which is categorically worse than someone
typing it once and reading the plan.

WHEN THE RECONCILIATION PROJECT IS DONE, this test is the thing to delete, and
deleting it should be a deliberate act in the same change that makes `apply`
safe -- not a quiet edit to get a pipeline green.
"""

from __future__ import annotations

import pathlib
import re

WORKFLOWS = pathlib.Path(".github/workflows")

#: `terraform apply`, `terraform -chdir=... apply`, and the `-auto-approve` form.
#: Deliberately matches the subcommand rather than the word "terraform", because
#: 05-security.yml legitimately points Trivy at this directory as static IaC and
#: a word-level match would forbid that too.
APPLY = re.compile(r"\bterraform\b[^\n|;&]*\bapply\b")
DESTROY = re.compile(r"\bterraform\b[^\n|;&]*\bdestroy\b")
AUTO_APPROVE = re.compile(r"-auto-approve")

#: The official Terraform GitHub Action, which can apply without the word
#: `terraform` appearing as a command at all.
TF_ACTIONS = ("hashicorp/setup-terraform", "dflook/terraform-apply",
              "hashicorp/terraform-github-actions")


def _workflow_files() -> list[pathlib.Path]:
    assert WORKFLOWS.is_dir(), (
        f"{WORKFLOWS} does not exist, so this guard would pass by finding nothing. "
        f"Run pytest from the repository root."
    )
    files = sorted(list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))
    assert files, (
        "no workflow files found, so this guard cannot fail. A check that cannot "
        "fail is worse than no check."
    )
    return files


def _non_comment_lines(text: str):
    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        yield i, line


def test_no_workflow_runs_terraform_apply():
    offenders = []
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in _non_comment_lines(text):
            if APPLY.search(line) or (AUTO_APPROVE.search(line) and "terraform" in text):
                offenders.append(f"{path.name}:{i}: {line.strip()[:110]}")
    assert not offenders, (
        "A workflow runs `terraform apply`. The committed configuration does not "
        "describe the running production estate -- a measured plan says 101 to add, "
        "1 to change, 1 to destroy, where the adds duplicate live infrastructure and "
        "the destroy is the live ALB's security group. Applying it unattended on every "
        "push would build a parallel production and replace that group.\n\n"
        "See terraform/DO-NOT-APPLY.md and INF-02/INF-06 in the remediation ledger. If "
        "the reconciliation project is genuinely finished, delete this test in the same "
        "change that makes apply safe.\n\nOffending lines:\n  " + "\n  ".join(offenders)
    )


def test_no_workflow_runs_terraform_destroy():
    offenders = []
    for path in _workflow_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for i, line in _non_comment_lines(text):
            if DESTROY.search(line):
                offenders.append(f"{path.name}:{i}: {line.strip()[:110]}")
    assert not offenders, (
        "A workflow runs `terraform destroy`. State currently tracks the live VPC and "
        "two live security groups, so this would delete real production "
        "infrastructure.\n\nOffending lines:\n  " + "\n  ".join(offenders)
    )


def test_the_do_not_apply_warning_is_present_and_says_why():
    """The human path is guarded by documentation, so the documentation has to exist.

    Asserting on content rather than mere existence: an empty or renamed file would
    satisfy a path check while telling the next person nothing.
    """
    doc = pathlib.Path("terraform/DO-NOT-APPLY.md")
    assert doc.is_file(), (
        "terraform/DO-NOT-APPLY.md is gone. It is the only thing standing between "
        "someone who has just typed `cd terraform` and an apply that builds a second "
        "production. If the reconciliation project is done, remove it deliberately "
        "together with the tests above."
    )
    text = doc.read_text(encoding="utf-8", errors="replace")
    for needed in ("terraform apply", "aws_security_group.alb", "INF-06"):
        assert needed in text, (
            f"terraform/DO-NOT-APPLY.md no longer mentions {needed!r}, so it has lost "
            f"either the instruction, the specific destructive resource, or the "
            f"pointer to the finding that tracks the project."
        )
