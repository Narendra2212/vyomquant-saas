# Dependency vulnerability triage

Requirement 1.33 / task 10.5. This is the triage record distinguishing **runtime** from
**development** dependencies, which is the deliverable clause 1.33 names ("no triage record
distinguishing runtime from development dependencies").

## Scope of this pass — read this first

This document covers **only the two CRITICAL findings that `05 Security` reports on `main`**, both
of which have a fix available and both of which therefore fail the Trivy gate. It is **not** a
triage of the full 295-alert Dependabot backlog (8 critical, 60 high, 127 medium, 100 low).

Nothing below is asserted about an alert that was not actually inspected. The remaining 293 alerts
are **untriaged** and are listed as such in [Not yet triaged](#not-yet-triaged). No justification,
exception, or runtime/development classification is claimed for them.

## How "runtime" is determined

`05-security.yml`'s `container-security` job builds `Dockerfile` and scans the resulting image, so
"runtime" means **present in the scanned production image**. The image installs exactly:

| Installed by | Manifest | In image |
| --- | --- | --- |
| `Dockerfile` line 23, a hardcoded pre-install layer | `torch==2.3.1+cpu` | yes |
| `Dockerfile` line 27 | `requirements-cpu.txt` -> `-r requirements-base.txt` + `torch==2.3.1+cpu` | yes |

Everything reachable from `requirements-base.txt` is therefore a **runtime** dependency.

These manifests exist but do **not** reach the scanned image:

- `requirements.txt` — `-r requirements-base.txt` + `torch==2.3.1` (local/GPU developer install)
- `backend_app/requirements.txt` — `-r ../requirements-base.txt` + `torch==2.3.1` (same, nested copy)
- `requirements-dev.txt` — `hypothesis==6.165.10`, `flake8==7.3.0`. Its own header states it is not
  installed in the production image. **Development only.**

Trivy is configured `severity: CRITICAL`, `ignore-unfixed: true`, `exit-code: 1`, so only
fixable criticals fail the gate.

## Findings

### 1. `python-jose` 3.3.0 -> 3.4.0 — CVE-2024-33663 — RUNTIME — RESOLVED

Algorithm confusion with OpenSSH ECDSA keys and other key formats.

**Status: resolved by bump.** `requirements-base.txt` now pins
`python-jose[cryptography]==3.4.0`, matching the file's existing exact-pin convention. Patch-level
bump, single pin site, reaches the image via `requirements-cpu.txt`.

**Correction to the assumption this bump was raised under.** The bump was prioritised on the
premise that `backend_app/core/websocket_auth.py::_decode_hs256_token` — the function all nine
WebSocket routes and the `verify_ws_ticket` credential path depend on — verifies JWTs through
python-jose. It does not. The auth path uses **PyJWT**:

- `websocket_auth.py::_decode_hs256_token` delegates to
  `backend_app/core/auth_middleware.py::decode_token_local` and catches `jwt.exceptions.*`.
- `auth_middleware.py` imports `jwt`, `PyJWKClient`, `ExpiredSignatureError`,
  `InvalidAudienceError`, `InvalidTokenError` — all PyJWT (`PyJWT==2.13.0`).
- `decode_token_local` pins `algorithms=["ES256"]` against a JWKS-resolved signing key and never
  selects a key from the token's own unverified `alg` header, which is the CWE-347 shape
  CVE-2024-33663 exploits.

**`python-jose` has zero import sites in this repository.** A repo-wide search for `jose`
(excluding virtualenvs, `node_modules`, caches) matches only the pin in `requirements-base.txt`. No
installed distribution declares it as a dependency either, so it is a direct pin that nothing
currently imports.

So the honest exploitability statement is: CVE-2024-33663 had **no reachable call site** in this
image even at 3.3.0. The bump is still the correct action — it is free, it clears the Trivy gate,
and it means the fixed version is what a future `from jose import jwt` would pick up — but it did
not close an open hole on the WebSocket auth path, because that path was never on python-jose.

**Follow-up, not done here (out of scope):** the stronger fix is to drop the
`python-jose[cryptography]` pin entirely, since it is unused and only adds attack surface and image
weight. That is a manifest removal with an import-audit check attached and is deliberately left for
a separate change.

### 2. `torch` 2.3.1+cpu -> 2.6.0 — CVE-2025-32434 — RUNTIME — JUSTIFIED EXCEPTION, NOT BUMPED

Remote code execution via `torch.load`, including with `weights_only=True` (CWE-502, deserialization
of untrusted data). Fixed in 2.6.0.
Refs: [Ubuntu CVE-2025-32434](https://ubuntu.com/security/CVE-2025-32434),
[Armis CVE-2025-32434](https://cve.armis.com/CVE-2025-32434). Content was rephrased for compliance
with licensing restrictions.

**Pin left at `2.3.1+cpu`.** Two reasons, in order.

**a. The vulnerability has no reachable call site.** CVE-2025-32434 is reached only through
`torch.load`. A repo-wide search for `torch.load`, `load_state_dict`, `torch.jit`, and `.pt` / `.pth`
model artifacts returns **zero matches**. There is no code path that deserialises a torch
checkpoint, and no checkpoint in the repository to deserialise.

torch is imported at exactly three sites, all in `backend_app/core/ml_safety.py`, all lazy and all
optional — each is `try: import torch` wrapped with `except ImportError: pass` (or
`except ImportError: return "cpu"`), so the module functions with torch absent:

| Line | Call |
| --- | --- |
| 124-130 | `torch.manual_seed`, `torch.cuda.manual_seed_all`, `torch.backends.cudnn.deterministic`, `torch.backends.cudnn.benchmark` |
| 382-386 | `torch.cuda.is_available`, `torch.cuda.device_count` |
| 432-436 | `torch.cuda.is_available`, `torch.device` |

That is the entire torch API surface this codebase touches: seeding, CUDA availability probing, and
device selection. `TORCH` also appears as a `serialization` enum member in
`backend_app/backend/strategy_dag/block_specs.py` and `backend_app/backend/ml_models.py`, but it is
a string label with no torch implementation behind it.

**b. The bump is not verifiable in this environment, and an unverified three-minor-version bump was
not an acceptable trade.** 2.3.1 -> 2.6.0 spans three minor releases. Verification was attempted in
an isolated virtualenv and failed on a hard resource limit, not on a torch problem:

```
Downloading torch-2.6.0%2Bcpu-cp312-cp312-win_amd64.whl (206.5 MB)
ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device
```

Free space on the volume is **0.19 GB**; the wheel alone is 206.5 MB and unpacks to roughly 1.5 GB.
`torch==2.6.0+cpu` is confirmed available on `https://download.pytorch.org/whl/cpu`, so the blocker
is disk, not availability. For the same reason a full-graph `pip install -r requirements-cpu.txt
--dry-run` resolution against `torch==2.6.0+cpu` was not run: that index serves no PEP 658 metadata
for torch, so pip must download the whole wheel to resolve it.

The scratch virtualenv created for this attempt was removed.

**What this exception does not do.** It does not make `05 Security` pass. Trivy runs with
`ignore-unfixed: true` and CVE-2025-32434 has a fix, so the torch finding still fails the gate at
`exit-code: 1` while the pin stays at 2.3.1+cpu. Closing the gate needs one of:

1. Perform the bump on a machine with ~3 GB free, verifying the three `ml_safety.py` paths import
   and run and that `requirements-cpu.txt` still resolves. Three pin sites must move together —
   `requirements-cpu.txt`, `requirements.txt`, `backend_app/requirements.txt` — **and**
   `Dockerfile` line 23, which pins `torch==2.3.1+cpu` separately from any manifest.
2. Or check in a `.trivyignore` entry for CVE-2025-32434 carrying the justification in (a).

Option 2 suppresses a real CRITICAL finding from the scanner and is a decision for the repository
owner, so it was not taken unilaterally here. Option 1 is the recommendation.

## Verification performed

| Check | Result |
| --- | --- |
| `pip install -r requirements-base.txt --dry-run` | exit 0, resolves, `Would install ... python-jose-3.4.0`; zero `ERROR` / conflict / incompatible lines |
| `python-jose==3.4.0` installed into the test interpreter | confirmed `3.4.0`; `jose.jwt` / `jws` / `jwk` import, HS256 encode-decode roundtrip passes |
| `pytest tests/test_ws_ticket_redemption.py tests/test_phase7b_auth_remediation.py` | **80 passed**, 35 warnings, 75.57s — matches the 64 + 16 baseline, run against 3.4.0 |
| `pytest tests/test_no_undefined_names.py` | **3 passed**, 67.45s |
| python-jose 3.4.0 API compatibility | no call sites to break — python-jose is not imported anywhere in the repo |
| torch 2.6.0 isolated-venv verification | **blocked**: `OSError [Errno 28] No space left on device`, 0.19 GB free vs a 206.5 MB wheel |

The dry-run reports downgrades of `cryptography`, `protobuf`, `pyasn1`, `pydantic`, `uvicorn`,
`aiohttp`, `postgrest` and `opentelemetry-api` relative to what is installed locally. That is
pre-existing drift between this workstation and the pinned manifest, unrelated to this change.

## Not yet triaged

295 Dependabot alerts are open on the default branch — 8 critical, 60 high, 127 medium, 100 low.
This pass resolved or excepted **2 criticals**, both runtime, both surfaced by Trivy on the built
image. That leaves:

- **6 criticals** and **60 highs** with no runtime/development classification and no exploitability
  assessment in this document.
- 127 medium and 100 low, which clause 1.33 does not gate on.

Per task 10.5, every remaining critical and high on a **runtime** dependency must be resolved or
carry a checked-in justified exception here before the requirement is satisfied;
development-only alerts defer to P2. `gh` is authenticated as `Narendra2212`, so the counts are
directly re-checkable. Task 10.5 stays open.
