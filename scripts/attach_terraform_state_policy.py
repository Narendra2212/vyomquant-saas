#!/usr/bin/env python3
"""INF-03 route (A): attach the terraform state-backend permissions, as a MANAGED policy.

WHY MANAGED AND NOT INLINE
==========================
The first attempt used `aws iam put-user-policy` and AWS refused it:

    LimitExceeded: Maximum policy size of 2048 bytes exceeded for user github-actions

That 2048-byte budget is shared across ALL of a user's inline policies, and this user
already carries six (BUDGET500, TerraformRoute53Policy, vyomquant-frontend,
VyomQuantRedisCreate, VyomQuantRedisReadOnly, VyomQuantSecretsAccess). A managed policy
has its own 6144-byte limit, is versioned so a change is revertible to a prior version
rather than by re-pasting a document, and leaves the scarce inline budget for whatever
needs it next.

WHY THE DOCUMENT IS GENERATED RATHER THAN A SECOND FILE
=======================================================
`infra/iam/terraform-state-backend-policy.json` carries `Comment_*` keys explaining each
statement and what is deliberately absent. Those belong in the repository, where a
reviewer reads them. They also cost bytes against the limit and mean nothing to IAM. So
the document sent to AWS is derived from that file by stripping keys beginning with
"Comment", rather than maintained as a separate compact copy that could drift from the
documented one.

SAFETY
======
`--plan` is the default and calls nothing that mutates. The policy grants only what
`scripts/bootstrap_terraform_state.py` needs, scoped to the one bucket and the one table
`terraform/providers.tf` names, and deliberately omits every delete action -- terraform
never destroys its own backend, and state history is what makes a bad apply recoverable.

    python scripts/attach_terraform_state_policy.py --plan
    python scripts/attach_terraform_state_policy.py --apply
    python scripts/attach_terraform_state_policy.py --detach   # revert
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

REGION = "ap-southeast-1"
ACCOUNT = "273709947018"
USER = "github-actions"
POLICY_NAME = "VyomQuantTerraformStateBackend"
POLICY_ARN = f"arn:aws:iam::{ACCOUNT}:policy/{POLICY_NAME}"
SOURCE = pathlib.Path("infra/iam/terraform-state-backend-policy.json")

#: The name terraform/providers.tf's backend block actually uses. Asserted rather than
#: assumed, because the bootstrap script had this wrong (plural) and a policy scoped to
#: the wrong table would grant nothing useful while appearing to succeed.
EXPECTED_LOCK_TABLE = "vyomquant-terraform-lock"
EXPECTED_BUCKET = "vyomquant-terraform-state"


def aws(*args: str) -> tuple:
    p = subprocess.run(["aws", *args], capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def policy_document() -> str:
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    stripped = {k: v for k, v in raw.items() if not k.startswith("Comment")}
    # separators without spaces: every byte counts against the limit.
    return json.dumps(stripped, separators=(",", ":"))


def assert_matches_backend_block() -> None:
    """The policy must name the resources terraform will actually address."""
    backend = pathlib.Path("terraform/providers.tf").read_text(encoding="utf-8")
    if f'"{EXPECTED_LOCK_TABLE}"' not in backend:
        raise SystemExit(
            f"terraform/providers.tf does not name {EXPECTED_LOCK_TABLE!r}; re-read its "
            f"backend block before attaching a policy scoped to that table"
        )
    doc = SOURCE.read_text(encoding="utf-8")
    if f"table/{EXPECTED_LOCK_TABLE}\"" not in doc:
        raise SystemExit(
            f"the policy does not scope DynamoDB to table/{EXPECTED_LOCK_TABLE}. A policy "
            f"scoped to the wrong table attaches cleanly and grants nothing."
        )
    if EXPECTED_BUCKET not in doc:
        raise SystemExit(f"the policy does not name the bucket {EXPECTED_BUCKET!r}")


def existing_version_count() -> int:
    code, out, _ = aws("iam", "list-policy-versions", "--policy-arn", POLICY_ARN,
                       "--output", "json")
    if code != 0:
        return 0
    return len(json.loads(out).get("Versions", []))


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--plan", action="store_true", help="report only (default)")
    g.add_argument("--apply", action="store_true", help="create and attach")
    g.add_argument("--detach", action="store_true",
                   help="detach from the user and delete the policy -- full revert")
    args = p.parse_args()

    assert_matches_backend_block()
    doc = policy_document()
    print(f"source      : {SOURCE}")
    print(f"document    : {len(doc)} bytes after stripping Comment_* keys "
          f"(managed limit 6144; the inline limit this user already exhausts is 2048)")
    print(f"policy      : {POLICY_NAME}")
    print(f"user        : {USER}")
    print(f"bucket      : {EXPECTED_BUCKET}")
    print(f"lock table  : {EXPECTED_LOCK_TABLE}  (matches terraform/providers.tf)")

    if len(doc) > 6144:
        print("\nREFUSED: the document exceeds the managed-policy limit too.")
        return 2

    code, out, _ = aws("iam", "get-policy", "--policy-arn", POLICY_ARN, "--output", "json")
    exists = code == 0
    print(f"exists now  : {exists}"
          + (f" ({existing_version_count()} version(s))" if exists else ""))

    code, out, _ = aws("iam", "list-attached-user-policies", "--user-name", USER,
                       "--output", "json")
    attached = []
    if code == 0:
        attached = [a["PolicyName"]
                    for a in json.loads(out).get("AttachedPolicies", [])]
    print(f"attached now: {POLICY_NAME in attached}")

    if args.detach:
        if POLICY_NAME in attached:
            code, _, err = aws("iam", "detach-user-policy", "--user-name", USER,
                               "--policy-arn", POLICY_ARN)
            print(f"\ndetach: {'ok' if code == 0 else (err or '').strip()[:160]}")
        if exists:
            code, _, err = aws("iam", "delete-policy", "--policy-arn", POLICY_ARN)
            print(f"delete: {'ok' if code == 0 else (err or '').strip()[:160]}")
        return 0

    if not args.apply:
        print("\n--plan only. Nothing was created or attached.")
        print("The document above is what --apply would send.")
        return 0

    if not exists:
        code, out, err = aws("iam", "create-policy", "--policy-name", POLICY_NAME,
                             "--policy-document", doc, "--output", "json",
                             "--description",
                             "INF-03: create and use the terraform S3 state backend and "
                             "DynamoDB lock table. No delete actions by design.")
        if code != 0:
            print(f"\ncreate-policy FAILED: {(err or '').strip()[:260]}")
            return 1
        print(f"\ncreated {json.loads(out)['Policy']['Arn']}")
    else:
        print("\npolicy already exists; not re-creating it")

    if POLICY_NAME not in attached:
        code, _, err = aws("iam", "attach-user-policy", "--user-name", USER,
                           "--policy-arn", POLICY_ARN)
        if code != 0:
            print(f"attach-user-policy FAILED: {(err or '').strip()[:260]}")
            return 1
        print(f"attached to user/{USER}")
    else:
        print("already attached")

    print("\nVERIFY the grant took effect before trusting it:")
    print("   python scripts/bootstrap_terraform_state.py        # probes, writes nothing")
    print("   python scripts/bootstrap_terraform_state.py --apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
