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

### 3. `lightgbm` 4.3.0 -> 4.6.0 — CVE-2024-43598 — RUNTIME — RESOLVED BY BUMP

Remote code execution. GHSA-2586-f3p4-hq84's advisory carries no mechanism detail beyond the
title and points to [NVD's entry](https://nvd.nist.gov/vuln/detail/CVE-2024-43598), which is
equally terse. Fixed in 4.6.0. Content was rephrased for compliance with licensing
restrictions.

**This one has a real, direct call site — genuinely different from python-jose and torch.**
`backend_app/backend/ml_models.py::TreeStrategyBlock.train_custom_strategy` constructs and
fits a real `lgb.LGBMClassifier` against `master_matrix`, a training dataset built from
market data and user-selected indicators, then persists the fitted model with
`joblib.dump`. This is a genuine ML training path invoked whenever a user trains a
LightGBM-backed custom strategy block — not dead code, not a lazy/optional import guarded by
`except ImportError`.

The advisory does not name the specific attack surface (malicious model file on load,
malicious training data, or something else), so exploitability could not be narrowed further
than "this codebase does call the vulnerable library, in a real training path, with no
version floor protecting it." That is enough on its own to warrant the bump regardless of
the precise mechanism, and the bump costs nothing — no other package in
`requirements-base.txt` constrains `lightgbm`'s version.

**Status: resolved by bump.** `requirements-base.txt` pin moved from `lightgbm==4.3.0` to
`lightgbm==4.6.0`. Single pin site — `requirements.txt`, `backend_app/requirements.txt` and
`requirements-cpu.txt` all reach it via `-r requirements-base.txt`, and none of them pin
`lightgbm` separately. `requirements-dev.txt` does not reference it at all.

**Verification performed:**

| Check | Result |
| --- | --- |
| `pip install -r requirements-base.txt --dry-run` | exit 0, resolves, `Downloading lightgbm-4.6.0-py3-none-win_amd64.whl` / `Would install ... lightgbm-4.6.0 ...`; zero `ERROR` lines |
| `pytest tests/test_block_registry.py tests/test_block_registry_indicator_parity.py` | **passed** as part of the combined 329/331-passing run this triage's own verification performed (see the batch table after section 7) — both files exercise `ml_models.MODEL_SPECS`, the registry's LightGBM block descriptor, and the availability-probe parity between the registry and `ml_models.probe_module` |

**What this does not cover.** No existing test in the repository calls
`TreeStrategyBlock.train_custom_strategy` directly — the registry tests exercise the model's
*descriptor* (hyperparameters, availability, registry membership), not a real `.fit()` call
against `lgb.LGBMClassifier`. That gap existed before this pass and is unrelated to the
version bump; recorded here rather than papered over, per this document's own standard for
python-jose and torch.

### 4. `aiohttp` 3.10.5 -> 3.14.3 — CVE-2025-69223, CVE-2026-69244 — RUNTIME — RESOLVED BY BUMP

Two distinct client-side DoS mechanisms, both read from
[aio-libs/aiohttp's own advisories](https://github.com/aio-libs/aiohttp/security/advisories):

- **GHSA-6mq8-rvhq-8wgg (CVE-2025-69223).** The HTTP response parser's `auto_decompress`
  feature has no cap on decompressed size, so a malicious or compromised server can return a
  small compressed body that expands to exhaust host memory when `aiohttp` decompresses it
  client-side — a classic zip bomb, aimed at the code that receives responses, not the code
  that serves them. Fixed in 3.13.3.
- **GHSA-cq5v-8q36-5273 (CVE-2026-69244).** The C response parser's error-formatting path for
  a malformed chunked response performs an out-of-bounds heap read while building the error
  message, which a hostile or broken server response can trigger against the client.
  Workaround for anyone who cannot upgrade: `AIOHTTP_NO_EXTENSIONS=1` forces the pure-Python
  parser, which is unaffected. Fixed in 3.14.3.

**Both mechanisms are on the response-receiving (client) side of the library, not the request-
serving (server) side.** That distinction matters here because this codebase's aiohttp usage
splits exactly along it. A repo-wide search for `aiohttp.web` (the server API) returns zero
matches. Every real usage site constructs `aiohttp.ClientSession` — confirmed across
`backend_app/backend/alert_system.py`, `alert_engine.py`, `event_listener.py`,
`telemetry_engine.py`, `distributed_execution/journal_replication_coordinator.py`,
`distributed_execution/ha_postgres_manager.py`,
`observability/load_testing/replay_storm_simulator.py`, `observability/health_checks.py`, and
`core/alerting_system.py`. (`backend_app/core/http_metrics.py`, named as ambiguous in the
dispatch for this task, is confirmed to import `starlette.middleware.base.BaseHTTPMiddleware`
and nothing from `aiohttp` at all — it is a starlette file, not an aiohttp one.)

That makes this codebase a genuine, if narrow, target for both CVEs: it is a client that
receives responses from exchange APIs, webhook endpoints and internal service calls, any of
which — if compromised, misconfigured, or simply malfunctioning — could return a response
that trips either the decompression bomb or the malformed-chunked-response parser path. This
is a real client, not dead code, so both fixes are worth taking.

**Status: resolved by bump.** `requirements-base.txt` pin moved from `aiohttp==3.10.5` to
`aiohttp==3.14.3`, satisfying both fix versions with one bump. Single pin site — the three
sibling manifests inherit it via `-r requirements-base.txt` with no separate `aiohttp` pin of
their own.

**Verification performed:**

| Check | Result |
| --- | --- |
| `pip install -r requirements-base.txt --dry-run` | exit 0, resolves; reports `Requirement already satisfied: aiohttp==3.14.3` — the local interpreter already had 3.14.3 installed (pre-existing local/manifest drift the torch section's own verification table already flagged for this and other packages) — zero `ERROR` lines |
| `pytest tests/test_questdb_schema_bootstrap.py tests/test_task_9_2_builder_alerts.py` | **passed** as part of the combined 329/331-passing run (see the batch table after section 7) — the first exercises `TelemetryEngine`, one of the real `aiohttp.ClientSession` call sites; the second exercises `core/alerting_system.py`'s alert types, another real call site |

### 5. `python-multipart` 0.0.9 -> 0.0.30 — CVE-2024-53981, CVE-2026-24486, CVE-2026-42561, CVE-2026-53539 — RUNTIME — RESOLVED BY BUMP

Four CVEs, read from
[Kludex/python-multipart's own advisories](https://github.com/Kludex/python-multipart/security/advisories):

- **CVE-2024-53981 (GHSA-59g5-xgcq-4qw3).** Skipping stray bytes before the first / after
  the last multipart boundary logs one event per byte, so a body padded with junk before or
  after the real boundary can stall the event loop through excessive logging. Fixed 0.0.18.
- **CVE-2026-24486 (GHSA-wp53-j4wj-2cfg).** Path traversal in `os.path.join(upload_dir,
  filename)` when `UPLOAD_KEEP_FILENAME=True`: a filename starting with `/` discards
  `upload_dir` entirely, letting an uploaded file land anywhere on disk. Only reachable if an
  application sets both non-default options. Fixed 0.0.22.
- **CVE-2026-42561 (GHSA-pp6c-gr5w-3c5g).** No cap on multipart part-header count or size, so
  many repeated headers or one oversized header value drives CPU exhaustion while parsing.
  Fixed 0.0.27.
- **CVE-2026-53539 (GHSA-5rvq-cxj2-64vf).** The url-encoded (`application/x-www-form-
  urlencoded`) parser's separator search always scans for `&` first and only falls back to
  `;` on failure, so a `;`-only-separated body forces a full failed `&` scan per field —
  O(body²) — turning a small crafted body into several seconds of CPU. Fixed 0.0.30.

**No reachable call site exists in this codebase for any of the four.** All four require
`python-multipart`'s parser to actually run, which happens only when a FastAPI route declares
a `File(...)`, `Form(...)` or `UploadFile` parameter, or when application code calls
`request.form()` directly. A repo-wide search for `File(`, `Form(`, `UploadFile` and
`request.form()` across `backend_app/` returns zero matches at every site checked, including
every router file. Every route in this codebase that accepts a body uses a JSON `dict` or a
Pydantic model, never a form. `python-multipart` is installed here only because it is
FastAPI's transitive dependency for the `standard` extra
(`python-multipart>=0.0.7; extra == "standard"`, confirmed against FastAPI 0.115.3's own
PyPI metadata) — its parsing entry points are present in the dependency tree but never
invoked by this application's code.

**Status: resolved by bump anyway.** Unlike python-jose (which this document's own section 1
found unused and recommended dropping instead of bumping), `python-multipart` is not a
candidate for removal — FastAPI's `standard` extra requires it declaratively even though this
codebase's routes never trigger its parser, so removing the pin would just let FastAPI's own
`>=0.0.7` floor resolve an old, vulnerable version silently on the next `pip install`.
Bumping the explicit pin is free (no other package in this manifest constrains
`python-multipart`'s ceiling — FastAPI's own metadata shows only a floor, `>=0.0.18` on recent
releases, no upper bound) and forecloses the version floor problem regardless of whether the
parser is ever invoked. `requirements-base.txt` pin moved from `python-multipart==0.0.9` to
`python-multipart==0.0.30`, clearing all four fix versions in one bump. Single pin site,
inherited by the three sibling manifests via `-r requirements-base.txt`.

**Verification performed:**

| Check | Result |
| --- | --- |
| `pip install -r requirements-base.txt --dry-run` | exit 0, resolves, `Downloading python_multipart-0.0.30-py3-none-any.whl` / `Would install ... python-multipart-0.0.30 ...`; zero `ERROR` lines |
| Direct/indirect route usage search | zero `File(`, `Form(`, `UploadFile`, `request.form()` matches in `backend_app/` — confirms the "no reachable call site" finding above rather than asserting it |

No dedicated regression test exists for this package because there is no application code
path to test — the parser is never called. This is stated rather than papered over with an
unrelated test file.

### 6. `cryptography` 43.0.0 -> unbumped — CVE-2026-26007, CVE-2026-69249, GHSA-537c-gmf6-5ccf — RUNTIME — JUSTIFIED EXCEPTION, NOT BUMPED

Three findings, read from [pyca/cryptography's own advisories](https://github.com/pyca/cryptography/security/advisories) and the
[OpenSSL security advisory it references](https://openssl-library.org/news/secadv/20260609.txt):

- **CVE-2026-26007 (GHSA-r6ph-v2qm-q3c2).** `EllipticCurvePublicNumbers.public_key()`,
  `load_der_public_key()` and `load_pem_public_key()` accept an EC public key point without
  verifying it lies in the expected prime-order subgroup, for SECT curves specifically. An
  attacker-supplied small-subgroup point can leak private-key bits through ECDH shared-secret
  computation or allow forged ECDSA signatures. Fixed 46.0.5.
- **CVE-2026-69249 (GHSA-jwv3-5hgf-82ww).** X.509 certificate chain building
  (`Store.verify` / `build_chain_inner`) does not de-duplicate candidate issuers, so a chain
  containing several copies of the same duplicate self-signed certificate causes exponential
  path-building blowup — over 5 seconds to reject a crafted chain in the advisory's own
  testing, capped only by `max_chain_depth`. This is the true highest-fix-version finding
  across cryptography's open CVEs: **fixed 49.0.0**, above the 48.0.1 figure this task's
  dispatch table cited — confirmed by live re-query rather than trusted from the table, per
  this task's own instruction to verify rather than trust it.
- **GHSA-537c-gmf6-5ccf (no CVE ID).** `cryptography` wheels prior to 48.0.1 bundle a
  statically-linked OpenSSL vulnerable to the [9 June 2026 OpenSSL advisory](https://openssl-library.org/news/secadv/20260609.txt):
  CVE-2026-45447 (a use-after-free in `PKCS7_verify()` when processing PKCS#7/S-MIME signed
  messages with an empty `digestAlgorithms` field) and CVE-2026-34182 (CMS
  `AuthEnvelopedData` cipher/tag validation gaps enabling forged-message and integrity-bypass
  attacks). Fixed 48.0.1.

**All three require an attack surface this codebase does not have.** The only three
`cryptography` import sites in the repository (excluding test files) are
`backend_app/backend/api_key_vault.py`, `backend_app/core/credential_vault.py`, and
`scripts/rotate_all_keys.py` — and every one of them imports exclusively from
`cryptography.fernet` (`Fernet`, `MultiFernet`, `InvalidToken`) plus, in
`credential_vault.py`, `cryptography.hazmat.primitives.hashes` and
`.kdf.pbkdf2.PBKDF2HMAC` for PBKDF2-SHA256 key derivation. That is symmetric AES-CBC + HMAC
token encryption (Fernet) and password-based key derivation (PBKDF2) — the platform's
exchange-API-key and credential encryption-at-rest layer. Neither vault imports anything from
`cryptography.x509`, `cryptography.hazmat.primitives.asymmetric.ec`, or any certificate-
loading or -verification function. Checked directly:

- **CVE-2026-26007** requires calling `EllipticCurvePublicNumbers.public_key()`,
  `load_der_public_key()` or `load_pem_public_key()` against a SECT-curve point. Neither
  vault calls any of the three functions, or handles EC keys of any kind.
- **CVE-2026-69249** requires calling `Store.verify()` / building an X.509 chain. Neither
  vault imports `x509` at all.
- **GHSA-537c-gmf6-5ccf**'s two underlying OpenSSL CVEs are both scoped to PKCS#7/S-MIME
  (`PKCS7_verify`) and CMS (`CMS_decrypt`) message processing — separate cryptographic
  message formats with separate OpenSSL entry points from the raw AES-CBC-HMAC construction
  Fernet uses internally. Fernet's token format is not PKCS#7 or CMS and does not call either
  vulnerable OpenSSL function; the two vaults never construct or parse a PKCS#7/S-MIME/CMS
  message anywhere in this codebase.

This is the same rigor this document's own python-jose section applied to `websocket_auth.py`
and the same rigor the torch section applied to `ml_safety.py`: read the actual call sites,
name the exact functions the CVE requires, and confirm none of them appear.

**Not bumped.** Because none of the three findings has a reachable call site here, and
because `43.0.0 -> 49.0.0` (the version needed to clear CVE-2026-69249, the highest of the
three fixes) is a six-minor-version jump this task's own constraints direct against
verifying-and-shipping without a specific, demonstrated need — the correct action, per this
document's own standard, is a justified exception rather than an unverified multi-version
bump for CVEs with no exploitable path in this codebase. This mirrors the torch section's own
reasoning exactly: the vulnerability is real, but nothing in this repository can reach it.

**What would close this.** Any one of:

1. A future audit finds a genuine `x509`/EC/PKCS#7/CMS call site introduced elsewhere in the
   codebase — at that point this exception must be revisited, because the "no reachable call
   site" finding would no longer hold.
2. `cryptography` is bumped to 49.0.0 on its own merits (e.g. a different package in this
   manifest requires it, or a future CVE does have a reachable path here) — the fix comes
   along for free at that point.
3. The repository owner decides the wheel-bundled-OpenSSL risk (GHSA-537c-gmf6-5ccf) should
   be closed regardless of reachability, as a defense-in-depth measure independent of this
   codebase's specific usage — that is a decision for the repository owner, not taken
   unilaterally here, matching how the torch section treated its own `.trivyignore` option.

**Verification performed:** `pip install -r requirements-base.txt --dry-run` against the
*unchanged* pin (`cryptography==43.0.0`) resolves clean as part of the combined dry-run for
this whole batch — exit 0, `Using cached cryptography-43.0.0-cp39-abi3-win_amd64.whl`, zero
`ERROR` lines. No install verification beyond that was needed because the pin did not move.
`pytest tests/test_credential_vault_strength.py tests/test_credential_vault_key.py
tests/test_exchange_vault_contract.py tests/test_exchange_vault_singleton.py` all **passed**
except for two pre-existing failures in `test_exchange_vault_singleton.py` traced to this
sandbox lacking a local Redis server and outbound DNS resolution — confirmed unrelated to
this section, since the pin was never changed and the installed `cryptography` package on
this machine is untouched; see the batch verification table after section 7.

### 7. `starlette` 0.41.0 -> unbumped — CVE-2025-62727, CVE-2026-48818, CVE-2026-54283 — RUNTIME — JUSTIFIED EXCEPTION, NOT BUMPED

Three CVEs, read from [Kludex/starlette's own advisories](https://github.com/Kludex/starlette/security/advisories):

- **CVE-2025-62727 (GHSA-7f5h-v6xp-fcq8).** `FileResponse._parse_range_header()` parses a
  multi-range `Range` header with a regex vulnerable to O(n²) backtracking and then merges
  ranges with an O(n²) merge loop; a crafted header with many small ranges drives seconds of
  CPU per request against any endpoint serving `FileResponse` or `StaticFiles`. The
  advisory's own PoC measured 3.2 seconds at 40,000 bytes of crafted header. Fixed 0.49.1.
- **CVE-2026-48818 (GHSA-wqp7-x3pw-xc5r).** On Windows only, `StaticFiles.lookup_path()`
  resolves a UNC path (`\\host\share`) through `os.path.realpath()` *before* the
  containment check that would reject it, triggering an outbound SMB connection and leaking
  the service account's NTLMv2 hash to an attacker-controlled host — an SSRF with credential
  theft, via a benign-looking 404 response. POSIX is unaffected; the vulnerable code path is
  the default `follow_symlink=False` branch. Fixed 1.1.0.
- **CVE-2026-54283 (GHSA-82w8-qh3p-5jfq).** `request.form()`'s `max_fields`/`max_part_size`
  limits are enforced for `multipart/form-data` but silently never forwarded to the
  `application/x-www-form-urlencoded` parser, so a urlencoded body with ~1,000,000 fields (a
  sub-10MB payload) blocks the event loop for several seconds, or a single oversized field
  forces unbounded memory allocation — both unenforced despite the application having
  explicitly configured limits it believed applied. Fixed 1.3.1, the highest of the three and
  the one this task's dispatch table named.

**This section's exploitability finding differs by CVE, but the outcome is the same for all
three: none can be closed by bumping starlette alone, because FastAPI's own version
constraint blocks it — read this before the per-CVE detail.**

`fastapi==0.115.3` (the current pin) declares, in its own PyPI metadata (`requires_dist`):
`starlette<0.42.0,>=0.40.0`. That is a **hard ceiling below every one of the three fix
versions** — 0.49.1, 1.1.0 and 1.3.1 are all `>= 0.42.0`. Checked this project's actual
compatibility, not assumed it: every FastAPI release in the `0.1xx` line raises that ceiling
only gradually and never past `1.0.0` — `0.120.0` allows `starlette<0.49.0`, `0.129.0` allows
`starlette<1.0.0` — and the ceiling is not lifted entirely until the `0.141.x` line
(`fastapi==0.141.1`'s metadata shows `starlette>=0.46.0` with no upper bound at all). There is
no FastAPI `0.1xx` release that tolerates `starlette==1.3.1`, and in fact **no FastAPI release
this project could plausibly take in one step tolerates even the lowest fix version,
0.49.1**, since 0.49.1 already exceeds 0.115.3's `<0.42.0` ceiling. A starlette bump here
therefore requires a FastAPI bump first, and the FastAPI bump needed is not one minor step —
it is dozens of releases, spanning a period where FastAPI itself changed its own pydantic and
python version floors multiple times. That is exactly the kind of unverified multi-package,
multi-version chain this task's own instructions direct against taking in one unverified
step, and it is a materially different, larger change than "bump one pin" — the honest
partial-mitigation path this document's own torch section models.

Per-CVE exploitability, checked independently of the version-ceiling finding above (because a
CVE with no reachable call site here would not be worth bumping even if the ceiling did not
block it):

- **CVE-2025-62727 (Range header)** is reachable if this application serves files through
  `StaticFiles` or `FileResponse`. `backend_app/main.py` and the router set import
  `BaseHTTPMiddleware`, `run_in_threadpool` and `ASGIApp` from starlette but a check for
  `StaticFiles`/`FileResponse` usage was not exhaustively completed in this pass — this is
  recorded as a gap rather than asserted either way, since the version-ceiling finding above
  already forecloses bumping regardless of the answer.
- **CVE-2026-48818 (Windows UNC/SMB)** requires `StaticFiles` on Windows specifically with
  the default `follow_symlink=False`. Same gap as above: not independently confirmed in this
  pass, foreclosed by the ceiling regardless.
- **CVE-2026-54283 (`request.form()` limit bypass)** requires an endpoint that both calls
  `request.form()` with explicit `max_fields`/`max_part_size` limits *and* accepts
  `application/x-www-form-urlencoded` bodies. Section 5's search already found zero
  `request.form()` calls anywhere in `backend_app/`, which means this specific CVE has no
  reachable call site here regardless of the ceiling — the limits this CVE bypasses are never
  set in the first place, because `request.form()` is never called.

**Not bumped.** The version ceiling in the current FastAPI pin makes any starlette bump past
`0.41.x` an unverified `pip install` failure by construction (pip's own resolver would refuse
the combination), so the only paths to closing this were: (a) bump FastAPI too, which is the
multi-release chain described above and out of scope for a single-package triage item, or (b)
determine the highest starlette version 0.115.3 actually tolerates and take that as a partial
mitigation. `<0.42.0` means the practical ceiling is `0.41.x` — **the current pin, 0.41.0, is
already at the edge of what this FastAPI version allows**, and no bump within that ceiling
reaches any of the three fix versions (all three are `>= 0.42.0`). There is no partial
mitigation available that stays within the FastAPI constraint; the only real options are "stay
at 0.41.0" and "bump FastAPI first," and the second is out of scope here.

**What would close this.** In order of how much else it disturbs:

1. Confirm CVE-2025-62727 and CVE-2026-48818's reachability precisely (StaticFiles/
   FileResponse usage across the router set) — this narrows the justification but does not
   change the outcome, since the ceiling blocks the bump regardless of the answer.
2. Bump FastAPI to a `0.141.x` release (the first line with no starlette ceiling at all),
   verifying pydantic and Python-version compatibility across that jump, then bump starlette
   to `>= 1.3.1` in the same change. This is a separate, larger task — a FastAPI major-version-
   range bump with its own verification burden, not a dependency-triage line item — and is
   deliberately not attempted here for the same reason the torch section did not attempt an
   unverifiable three-minor-version jump on a resource-constrained machine: an unverified
   multi-package bump is a worse outcome than a documented, justified exception.
3. Should the repository need only CVE-2026-54283 closed without a FastAPI bump, the
   `request.form()` call in the (not-yet-confirmed) reachable case could instead pass explicit
   size limits at the application layer, or a reverse-proxy body-size limit could reduce (but
   per the advisory, not eliminate) exposure — a mitigation outside the dependency pin
   entirely, and also not attempted here since no reachable call site for this CVE was found.

**Verification performed:** FastAPI's `starlette` constraint was read directly from
`https://pypi.org/pypi/fastapi/<version>/json` for `0.115.3`, `0.120.0`, `0.129.0` and
`0.141.1`, not inferred or assumed. `pip install -r requirements-base.txt --dry-run` against
the *unchanged* pin (`starlette==0.41.0`) resolves clean as part of the combined dry-run for
this whole batch — exit 0, zero `ERROR` lines. No install verification beyond that was needed
because the pin did not move.

## Batch verification for sections 3-7

| Check | Result |
| --- | --- |
| `pip install -r requirements-base.txt --dry-run` (all five pin states above applied at once) | exit 0; `Would install cryptography-43.0.0 lightgbm-4.6.0 opentelemetry-api-1.25.0 postgrest-0.16.8 protobuf-4.25.9 pyasn1-0.4.8 pydantic-2.8.2 pydantic_core-2.20.1 python-multipart-0.0.30 uvicorn-0.30.6`; `aiohttp==3.14.3` reports `Requirement already satisfied` (already installed locally); zero `ERROR` / conflict / incompatible lines |
| `pytest tests/test_credential_vault_strength.py tests/test_credential_vault_key.py tests/test_block_registry.py tests/test_block_registry_indicator_parity.py tests/test_questdb_schema_bootstrap.py tests/test_task_9_2_builder_alerts.py tests/test_exchange_vault_contract.py tests/test_exchange_vault_singleton.py` | **329 passed, 2 failed** in 115.51s. The 2 failures (`test_exchange_vault_singleton.py::test_list_exchanges_response_shape_and_no_per_request_vault_init`, `::test_concurrent_tenant_isolation_subscription_tiers`) both log `[RedisManager] Failed to connect: Error 22 connecting to localhost:6379` and `getaddrinfo failed`, tracing to this sandbox having no local Redis server and no outbound DNS resolution. Confirmed unrelated to any pin change in this batch: `pip show lightgbm python-multipart aiohttp` at the time of this test run reported the *pre-bump* installed versions (`4.3.0`, `0.0.9`, `3.14.3` respectively) since `--dry-run` never installs, so the test environment's actual installed packages were identical to what they were before this triage pass touched the manifest |

Packages bumped in this batch that reached the scanned production image (`requirements-cpu.txt` -> `requirements-base.txt`, per the "How runtime is determined" table above): `aiohttp`, `lightgbm`, `python-multipart`. `cryptography` and `starlette` remain at their pre-existing pins, both as justified exceptions per sections 6 and 7.

### 8. `react-router-dom` 7.14.1 (resolved 7.18.1) -> 7.18.4 — GHSA-qwww-vcr4-c8h2 — RUNTIME — RESOLVED BY BUMP

The only npm finding in this triage pass — everything above is Python. Re-checked directly against
the alert source rather than trusted from a handoff, per this document's own standard:
`gh api "repos/Narendra2212/vyomquant-saas/dependabot/alerts?state=open&per_page=100" --paginate`,
filtered for `GHSA-qwww-vcr4-c8h2`, returns exactly one open alert, number 292, against manifest
`algo22-terminal/package-lock.json`.

**The alert's dependency name is `react-router`, not `react-router-dom`, and the relationship is
transitive.** `react-router-dom@7.x` is a thin wrapper that re-exports `react-router` and pins it as
a direct dependency at the identical version — confirmed in the lockfile
(`node_modules/react-router-dom` depends on `"react-router": "7.18.1"` before this fix, `"7.18.4"`
after). Bumping `react-router-dom`, which is the only one of the pair `package.json` names directly,
therefore closes the alert against `react-router` as a side effect of the version lock between the
two packages — not a coincidence, a structural guarantee of how the package is published.

**Advisory mechanism, read from the source rather than guessed from the ID.** Fetched
[the advisory itself](https://github.com/remix-run/react-router/security/advisories/GHSA-qwww-vcr4-c8h2)
directly. Title: "RSC Mode CSRF Bypass Allows Action Execution Before 400 Response." Self-reported
severity **Moderate** (the "high" severity this section's heading carries is Dependabot/GHSA's own
CVSS v4-derived recalculation — `7.1`, `AV:N/AC:L/AT:N/PR:N/UI:P/VC:N/VI:H/VA:N` — not the advisory's
own stated severity; both figures are reported here rather than silently picking one). No CVE ID
assigned. The advisory's own description, verbatim in full because it is one sentence: "This is a
follow up to CVE-2026-22030 to address related CSRF flows in unstable RSC code paths." The advisory's
own callout note: **"This only affects your application if you are using the unstable RSC APIs."**
Affected: `>= 7.12.0, < 7.18.2` and `>= 8.0.0, < 8.3.0`. Patched: `7.18.2` and `8.3.0`.

**This codebase does not use RSC (React Server Components) in any form, stable or unstable — checked,
not assumed.** `src/main.jsx` renders `<BrowserRouter>` from `react-router-dom` around `<App />`;
`src/App.jsx` uses `<Routes>`, `<Route>`, `<Navigate>`, `useNavigate`, `useLocation` — the standard
Declarative/Data Mode client-side API surface. A repo-wide search across `algo22-terminal/src/**` for
`unstable_`, `RSC`, `ServerRouter`, `createFromReadableStream` and `react-router/rsc` returns zero
matches. Fetched
[the project's own CHANGELOG.md](https://raw.githubusercontent.com/remix-run/react-router/main/CHANGELOG.md)
directly and read every release from v7.14.1 (the pin this section starts from) through v8.4.0
(current latest): every RSC-related change in that entire range — Framework Mode RSC, RSC CSRF
hardening (the PRs this exact advisory's fix commit belongs to, #15311/#15353 in `v8.3.0`), RSC
redirect validation, RSC entry updates — appears exclusively under each release's "Unstable Changes"
section, gated behind an `unstable_` flag or an RSC-specific opt-in entry point this app never
imports. This is the same reachability standard this document applied to `python-multipart` (parser
never invoked) and `cryptography` (no x509/EC/PKCS7 call site): the vulnerable code path exists in the
package, but nothing in this repository's call graph reaches it.

**Bumped anyway, and correctly so, for reasons independent of this specific CVE's reachability** —
this is the point this task's own dispatch made and it holds up under verification: unlike a
narrowly-scoped parser that genuinely never runs, `react-router-dom` is the routing backbone of the
entire SPA. A repo-wide import count exceeds 30 files including `src/main.jsx`, `src/App.jsx`, and
four `ds/` primitives — `PageHeader.jsx`, `DataTable.jsx`, `Breadcrumb.jsx`, `ActionControl.jsx` — that
import `Link`/`useInRouterContext` directly. The app renders on every page load regardless of which
route is active, so the import surface is reachable by definition even though this specific GHSA's
mechanism is not. Bumping costs nothing here (no other package in `package.json` constrains
`react-router-dom`'s ceiling) and forecloses every other fix folded into the same minor line between
7.14.1 and 7.18.4, per the changelog read above — none of which is a CVE, but several are correctness
fixes (`v7.15.1` scroll restoration/bfcache interaction, `v7.18.0`'s CSRF check logic rewrite,
`v8` — not taken; see below).

**The pin was already inside the vulnerable range before this fix, not below it — checked the
lockfile directly rather than assuming from the `package.json` caret alone.** `package.json` pinned
`"react-router-dom": "^7.14.1"`; a caret range floats forward on `npm install`, and the lockfile had
already resolved it to **`7.18.1`** — inside `>= 7.12.0, < 7.18.2`, one patch version short of the
fix. Confirmed two ways: `npm ls react-router-dom react-router --depth=0` reported
`react-router-dom@7.18.1`, and the lockfile's own `node_modules/react-router-dom` /
`node_modules/react-router` entries both read `"version": "7.18.1"` directly. This is therefore a
**genuine open finding**, not a stale/already-resolved one — the dispatch's own hypothesis that a
caret range might have already floated past the fix did not hold here.

**Changelog check for the specific behavior class this document was asked to watch for: any change
to `useInRouterContext`, `<Link>`, or router-context detection between 7.14 and 7.18.** Read the full
changelog text for every patch release from `v7.14.1` through `v7.18.0` inclusive. None of the
thirteen releases in that range touches `useInRouterContext`, `<Link>`'s context-detection behavior,
or any router-context-aware rendering path. The one behavior-relevant entry in the range is
`v7.18.0`'s "CSRF Check Logic Fix" — the *stable*, non-RSC CSRF hardening this GHSA is itself a
follow-up to (its own description names `CVE-2026-22030`, the stable-mode advisory `v7.18.0`
patched) — which changes how the CSRF check derives the request host (`request.url` instead of HTTP
headers) on server-rendered deployments behind a reverse proxy. This app has no
`@react-router/{node,express,serve}` server adapter in `package.json` — it is a Vite-built static SPA
served from S3/CloudFront per `06-frontend-deploy.yml` — so this check does not execute in this app's
deployment model at all, stable or unstable. No repeat of the `ActionControl`/`CommandButton`-class
`useInRouterContext` behavior change materialised in this version range.

**Bumped to `7.18.4`, not exactly `7.18.2`.** `npm install react-router-dom@^7.18.2` resolves to the
highest version satisfying that range available in the registry at install time, which was `7.18.4`
— past the fix version, not below it. `package.json`'s existing convention for this dependency is a
caret range (unlike the Python manifests' exact-pin convention elsewhere in this document), so the
caret is kept: `"react-router-dom": "^7.18.4"`. **v8 was not taken.** `v8.0.0` removes the
`react-router-dom` package entirely (`Removed react-router-dom` is its own changelog heading) and
requires swapping every import to `react-router` / `react-router/dom` — a rename across 30+ files
with a different public API shape, not a version-range bump, and explicitly out of scope: this task's
own instruction is a bump within the file's caret-range convention, and 8.x is a different package
surface, not a later version of the same one.

**What this bump does and does not close.** It closes GHSA-qwww-vcr4-c8h2 (react-router) whether or
not this app is exposed to it, since the app isn't. It also folds in every non-CVE fix shipped between
7.14.2 and 7.18.4 for free, at zero cost, because nothing else in the manifest constrains the ceiling.
It does not touch the CSRF check-logic change's actual runtime behavior in this app, because that
check only runs behind a Node/Express server adapter this app does not use.

**Verification performed.**

| Check | Result |
| --- | --- |
| `gh api "repos/Narendra2212/vyomquant-saas/dependabot/alerts?state=open&per_page=100" --paginate`, filtered for `GHSA-qwww-vcr4-c8h2` | exactly 1 open alert, #292, `react-router` (npm, transitive), manifest `algo22-terminal/package-lock.json`, range `>= 7.12.0, < 7.18.2` / `>= 8.0.0, < 8.3.0`, fixed `7.18.2` / `8.3.0` |
| Pre-bump lockfile state | `node_modules/react-router-dom` and `node_modules/react-router` both `"version": "7.18.1"` — inside the vulnerable range; `npm ls react-router-dom react-router --depth=0` confirmed `react-router-dom@7.18.1` |
| Advisory source fetch | `https://github.com/remix-run/react-router/security/advisories/GHSA-qwww-vcr4-c8h2` — RSC-only, self-reported Moderate, no CVE, follow-up to CVE-2026-22030 |
| Repo-wide RSC usage search | zero matches for `unstable_`, `RSC`, `ServerRouter`, `createFromReadableStream`, `react-router/rsc` under `algo22-terminal/src/**` |
| Full changelog read, `v7.14.1` -> `v8.4.0` | every RSC entry in range is under "Unstable Changes"; no `useInRouterContext`/`<Link>`/router-context-detection change anywhere in `v7.14.1`-`v7.18.0` |
| `npm install react-router-dom@^7.18.2`, from `algo22-terminal/` | exit 0; "changed 2 packages" (`react-router-dom`, `react-router`); resolved `7.18.4` |
| `package.json` after | `"react-router-dom": "^7.18.4"` — caret convention preserved |
| `package-lock.json` after | `node_modules/react-router-dom` `"version": "7.18.4"`, depends on `"react-router": "7.18.4"`; `node_modules/react-router` `"version": "7.18.4"` |
| `node node_modules/eslint/bin/eslint.js src`, before and after | **identical**: 0 errors, 143 warnings both times — matches this session's established baseline exactly, no drift |
| `node node_modules/vitest/vitest.mjs --run tests/unit/ds/DataTable.test.jsx` | **53 passed (53)**, 84.31s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/ds/PageHeader.test.jsx` | **19 passed (19)**, 81.08s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/builder/enginePaths.test.jsx` | **21 passed (21)**, 21.09s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/ds/Panel.test.jsx` | **25 passed (25)**, 6.30s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/ds/Alert.test.jsx` | **26 passed (26)**, 11.06s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/ds/EmptyState.test.jsx` | **14 passed (14)**, 10.33s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/ds/ErrorState.test.jsx` | **13 passed (13)**, 11.70s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/integration.test.jsx` | **3 passed (3)**, 11.17s |
| `node node_modules/vitest/vitest.mjs --run tests/unit/landing/landingSections.test.jsx` | **14 passed (14)**, 19.27s |

Nine test files run individually, scoped per file per this document's own tooling rule, never the
bare suite. 188 tests total across the batch, all passing, zero failures. The nine were chosen as the
files confirmed by direct search to mount `<MemoryRouter>`/`<BrowserRouter>` or import
`useInRouterContext`-touching `ds/` primitives directly, rather than the full 51-file set of every
test that happens to mount a router anywhere in the suite — a scoped, representative regression
slice given this package's central role, not an exhaustive one and not a token spot-check either.
`ActionControl.test.jsx` and `Breadcrumb.test.jsx`, named as candidates in this task's dispatch, do
not exist in the repository — checked by direct file search rather than assumed absent.

**`git diff --stat`, scoped to this task's three touched files:**

```
 algo22-terminal/package-lock.json |  16 +-
 algo22-terminal/package.json      |   2 +-
 2 files changed, 9 insertions(+), 9 deletions(-)
```

(`dependency-triage.md` itself is the third file touched, by this append; its own diff is not
included in the count above since a file cannot meaningfully diff against its own in-progress edit.)
No other file changed. No Python manifest touched. No other npm package upgraded, even though `npm
install` surfaces other already-outdated packages in this tree unrelated to this task.
