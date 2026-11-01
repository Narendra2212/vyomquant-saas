# Do not run `terraform apply` from this directory

**This configuration does not describe the running production estate.** Running
`apply` against it would not converge production onto this config — it would
build a second one alongside, and replace one security group the live load
balancer depends on.

This is recorded under **INF-02** and **INF-06** in
`docs/launch_readiness/REMEDIATION_LEDGER.json`. This file exists because those
entries are not in front of someone who has just typed `cd terraform`.

## What a plan actually says

Measured 2026-11-01 with `terraform plan -refresh=false -lock=false`, saved with
`-out` and read back via `terraform show -json` (the text output was truncated by
the shell at 162 lines, which is worth knowing if you try to read it the quick
way):

```
Plan: 101 to add, 1 to change, 1 to destroy.
```

Broken down from the plan's own JSON, which is the form that cannot be silently
cut off:

| Action | Count | What it means here |
|---|---:|---|
| `create` | 100 | Resources that **already exist in AWS**. Terraform does not know them, so it would build duplicates. |
| `delete` + `create` | 1 | `aws_security_group.alb` — **the destructive one.** It is in state and attached to the live ALB. |
| `update` | 1 | `aws_vpc.main` |
| `no-op` | 1 | `aws_security_group.redis` — the one resource that was imported properly and agrees with live. |

The 100 creates include a second load balancer, a second CloudFront
distribution, a second ECS cluster, nine ECS services, nine task definitions,
three NAT gateways and three Elastic IPs. The NAT gateways alone are roughly
$100/month of duplicate infrastructure.

It would also fail partway: eight of the creates are
`aws_secretsmanager_secret` resources whose names match secrets that already
exist, so those would error with `ResourceExistsException` — after other
resources had already been created. A failed `apply` is not a no-op.

## Why `terraform import` is not the quick fix

The obvious reading of "state is empty" is "so import everything". That does not
work here, because **the configuration's names do not match the live ones**, and
for most AWS resource types a name change forces replacement. Importing a live
resource into a mismatched address is therefore *worse* than leaving state
empty: an empty state duplicates production on apply, a half-imported one
mutates it.

| Configuration wants | Actually running |
|---|---|
| `vyomquant-alb-production` | `vyomquant-alb` |
| `vyomquant-cluster-production` | `vyomquant-cluster` |
| `vyomquant-api-production` | `vyomquant-api` |
| `vyomquant-alb-sg-production` | `vyomquant-alb-sg` |
| `vyomquant-frontend-static-production` | `vyomquant-frontend` |

**And there is no single convention to rename to.** Measured across 19 live
resources: 16 carry no environment segment at all (ALB, ECS cluster, target
groups, every task family), while 3 carry `-production` (both ElastiCache
replication groups and their security group). Nothing is named
`vyomquant-prod-*`.

That last point matters because INF-06's recorded decision said to "rename the
configuration to the live `vyomquant-prod-*` convention". No such convention
exists, so following that instruction literally would produce a *second*
mismatch. The rename is per-resource reconciliation, not one find-and-replace.

`aws_security_group.alb` is the known hard case regardless of naming: both
`name` and `description` are ForceNew, and its live description is the
hand-typed `Allow ssh acces to developer`.

## What deploys production, if not this

Production is deployed imperatively by CI: `02-build` pushes an image to ECR and
`03-deploy` runs `scripts/ecs_deploy_and_diagnose.py`, which registers a new
task definition and calls `ecs:UpdateService`. **No workflow in this repository
runs `terraform` at all** — the only references are Trivy scanning this
directory as static IaC. A test (`tests/test_terraform_is_not_applied_by_ci.py`)
asserts that stays true, so the automated path to an accidental apply is closed
and the remaining path is a human in this directory. Hence this file.

## If you are doing the reconciliation project

The ordered plan lives in INF-06 and is still right in substance. Two
corrections from the 2026-11-01 measurement:

1. **The environment blocker is gone.** INF-02 said this needed "a machine that
   can run `terraform plan`". It runs. The failure was
   `timeout while waiting for plugin to start`, and the cause was a cold-start
   race, not a broken provider: the 685 MB AWS provider binary takes **52.8 s**
   to start cold against terraform's ~60 s plugin timeout, and **7.4 s** warm.
   Free some disk, run the provider binary once directly to warm the page cache,
   then plan.
2. **The rename target is wrong** — see above. Decide it per resource, and decide
   it together with SEC-23's residual, because the live ECS services use the VPC
   default security group, all four subnets and `assignPublicIp=ENABLED`. Making
   terraform ratify that posture would quietly close SEC-23's residual by
   writing it down as intended.

Deliberately **not** added as a guard: `prevent_destroy` on
`aws_security_group.alb`. It would block `apply`, but it makes `terraform plan`
itself error whenever the plan contains that destroy — which is exactly the
diagnostic the reconciliation project needs at every import step. A guard that
blocks the fix is the wrong guard.
