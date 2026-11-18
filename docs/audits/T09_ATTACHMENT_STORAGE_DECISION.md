# T09 support attachments — the storage decision, made on what is already here

**Status:** recommendation, ready to act on. Needs one yes/no from the owner.
**Blocks:** support attachments (upload, download, retention). Nothing else in T09 —
the ticket write, replies and status transitions landed in #309.

## What exists today, measured

Attachments are **metadata only**, and the product says so rather than pretending
otherwise. `AttachmentMeta` carries a filename, a content type and a size;
`routers/support.py` stores that dict on the ticket and no endpoint accepts file bytes.
The UI labels the field a "Safe Attachment Simulation", and the FAQ answer reads *"Not as
uploaded files — there is no file upload yet."* The extension blocklist in
`_check_attachment` is a real check on a real field; it just guards a name, not a file.

That is a defensible interim state, which is why this was deferred rather than bodged.

## The decision: Supabase Storage, not S3

Four measurements decide it, and none of them is a preference.

**1. The abstraction is already written, for Supabase.**
`backend_app/backend/model_versioning.py` defines an `ArtifactStore` interface (`put`,
`get`) with two implementations — `LocalArtifactStore` and
`SupabaseStorageArtifactStore` — and typed failures that are exactly the ones an
attachment path needs: `ArtifactExists`, `ArtifactTooLarge`,
`ArtifactChecksumMismatch`. The contract is append-only, which is the right default for
an attachment on an audit-relevant ticket. Choosing S3 means writing a third
implementation; choosing Supabase means reusing a tested one.

**2. The AWS SDK is not a dependency.**
`boto3`/`botocore` appear in none of `requirements.txt`, `requirements-base.txt`,
`requirements-cpu.txt` or `requirements-dev.txt`. S3 adds a runtime dependency to every
API image.

**3. The API tasks have no S3 credential, and giving them one widens the blast radius.**
The OIDC role `VyomQuantGithubActionsDeploy-production` holds S3 and CloudFront
permissions, but that is a **GitHub Actions deploy** role, not something the ECS tasks
assume. S3 attachments would mean a new runtime IAM principal with write access to a
bucket, on the same tasks that already hold `SUPABASE_SERVICE_ROLE_KEY`. Supabase
Storage needs no new secret: the service-role key the tasks already carry is the same
credential.

**4. Access control stays in one model instead of two.**
Supabase Storage bucket policies are expressed against `auth.uid()` — the same identity
`backtests_owner_access` and every other policy in this database uses, and the same one
the MKT-10 work just finished reasoning about. S3 would move attachment authorization to
API-issued presigned URLs: a second authorization model, with its own expiry semantics,
sitting beside RLS. Two models is how a gap opens between them.

Production already runs one bucket (`strategy-images`), so this is a second bucket on a
path that exists, not a new subsystem.

### What would change the answer

Attachment volume or size large enough that Supabase Storage egress pricing beats S3 +
CloudFront, or a compliance requirement for object-lock/retention that Supabase Storage
cannot express. Neither applies to support-ticket attachments at current scale. If
either becomes true, the `ArtifactStore` interface is the seam to add S3 behind — which
is the main reason to reuse it rather than bypass it.

## What implementing it then requires

Scoped so it can be reviewed in one pass. **Not done in this change** — this document is
the decision, and the decision was the blocker.

1. **Bucket `support-attachments`, private, with owner-scoped policies.** `anon` gets
   nothing. A customer reads and writes only under a `{user_id}/{ticket_id}/` prefix.
   The support operator reads via the service role, which bypasses the policy.
2. **`POST /api/support/tickets/{id}/attachments`, accepting bytes.** Validate before
   storing, never after: size ceiling, content-type allow-list (not the current
   blocklist — a blocklist of `.exe/.sh/.bat` is the wrong shape for real files), and
   the existing secret-scanning the ticket body already gets. Reject, don't sanitise.
3. **Write through the service-role client**, for the reason MKT-10 established: a
   customer-writable evidence surface is forgeable. Keep the `user_id` predicate on the
   statement, because it becomes the whole ownership scope.
4. **Short-lived signed URLs for download**, issued per request and never stored on the
   ticket row.
5. **Retention and deletion.** A closed ticket's attachments need a stated lifetime, and
   account deletion has to remove them — otherwise this becomes the one place customer
   data outlives the account.
6. **Replace the metadata field rather than keep both.** Two sources of truth for "what
   is attached" is worse than either alone. The FAQ answer and the "Safe Attachment
   Simulation" label both have to change in the same PR, or the product keeps saying
   uploads do not exist after they do.

## Why this is not built yet

Items 1–6 are one PR each at this repo's bar, and item 1 is a production storage
configuration change. The decision above is what was actually blocking, and it is now
made and justified. The sequencing recommendation is 1, then 2+3 together (the write
path is not reviewable without its authorization), then 4, then 5, then 6.
