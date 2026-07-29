# Rollback Guide

In the event of a catastrophic failure during or after a deployment, follow these procedures to rollback.

## 1. Rolling back ECS Deployments via AWS Console
AWS ECS maintains previous Task Definition revisions. If a new deployment fails or introduces critical bugs:
1. Navigate to the **Amazon ECS** console.
2. Select the **vyomquant-cluster-prod**.
3. Select the failing service (e.g., `vyomquant-api-service-prod`) and click **Update**.
4. In the **Task Definition** dropdown, select the previous known good revision (e.g., `vyomquant-api-prod:5`).
5. Complete the update. ECS will automatically drain the failing tasks and spin up tasks with the old definition.

## 2. Rolling back via CI/CD (GitHub Actions)
If you need to redeploy an old commit:
1. Revert the commit in Git: `git revert <bad_commit_hash>`
2. Push the code: `git push origin main`
3. The GitHub Actions pipeline will automatically build the reverted code and deploy it to ECS.

## 3. Database Rollbacks (Supabase)
If a database migration caused the issue:
1. Ensure your migration scripts have a `down` procedure.
2. Execute the down migration against the Supabase database URL.
3. If data corruption occurred, utilize Supabase Point-in-Time Recovery (PITR) via the Supabase Dashboard.

## 4. Terraform Infrastructure Rollback
If a Terraform apply broke the infrastructure:
1. Check the local or remote state.
2. Revert the Terraform code changes in git.
3. Run `terraform apply` to converge back to the desired prior state.
