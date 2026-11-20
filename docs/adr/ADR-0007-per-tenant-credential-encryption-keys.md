# ADR-0007: Per-Tenant Credential Encryption Keys

## Status
Proposed

## Context

Exchange API credentials are the highest-value secret this platform holds. They are not
data about a tenant; they are live authority over that tenant's brokerage account. A
compromise is not a privacy incident, it is somebody else able to trade.

Today they are encrypted correctly but under one shared key set.
`backend_app/backend/api_key_vault.py` reads `MASTER_ENCRYPTION_KEYS`, splits it on
commas, and builds `MultiFernet([Fernet(k) for k in master_keys])`. The first key
encrypts, every key decrypts, which is what makes the zero-downtime rotation in
`docs/KEY_ROTATION_PROCEDURE.md` genuinely executable. The vault raises
`EnvironmentError` rather than falling back to a generated key when the variable is
absent. None of that is the problem.

The problem is that `self._cipher` is one object shared by every tenant.
`_encrypt(raw_secret)` and `_decrypt(encrypted_secret)` take no identity argument, so a
single key protects all rows of `exchange_keys`. Rotation bounds the *window* in which a
leaked key is useful. It does nothing about blast radius: one key plus table access is
every tenant's broker credentials.

Two facts make this cheaper to fix than it looks:

- Both call sites already have identity in scope. `store_exchange_keys(user_id,
  exchange_id, ...)` and `load_decrypted_keys(user_id, exchange_id)` both validate
  `user_id` through `_validate_id` before use. Threading it into `_encrypt`/`_decrypt`
  is a local change, not an architectural one.
- `rotate_key(user_id, exchange_id)` already performs load-then-store, which is exactly
  the re-encryption primitive a migration needs. It exists and is used by
  `scripts/rotate_all_keys.py`.

ADR-0006 established bridge tenancy with per-tenant key material as the target for
financial data, and the Tenant Isolation Matrix records this as the gap with the largest
blast radius on the platform. This ADR decides how to close it.

Tenant population shapes the answer. Tiers are `free`, `starter`, `pro`, `enterprise`
(₹2,499) and `scale` (sales-quoted). The first four are retail individuals in volume;
`scale` is the institutional tier ADR-0006 reserves silo for. A per-tenant design that
costs real money per tenant is affordable for the fifth and ruinous for the first.

## Decision

**Derive a per-tenant key from the existing master key set for all tiers. Add a
per-tenant AWS KMS CMK only for the institutional tier.**

### 1. Per-tenant derivation, retail tiers

Replace the single shared cipher with one derived per tenant, built from *every* master
key so rotation survives:

```python
import base64
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.fernet import Fernet, MultiFernet

#: Bumping this version re-derives every key, so it is a deliberate break, not a tweak.
_DERIVATION_INFO_PREFIX = b"algo22:exchange-keys:v1:"


def _derive_tenant_key(master_key: str, tenant_id: str) -> bytes:
    """A Fernet key belonging to exactly one tenant.

    HKDF rather than PBKDF2: the master is already 32 bytes of generated entropy, so
    the iteration count PBKDF2 exists to provide buys nothing here and would add
    latency to every credential read. PBKDF2 is the right tool for stretching a
    low-entropy password, which is what `core/credential_vault.py` (orphaned) uses it
    for; it is the wrong tool for deriving from a high-entropy key.

    The tenant travels in HKDF's `info` parameter, not concatenated into the salt.
    Concatenation is ambiguous - tenant "ab" with context "c" and tenant "a" with
    context "bc" produce identical input - and HKDF's `info` is the parameter designed
    for domain separation.
    """
    derived = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,  # the master key is already uniformly random; HKDF-Extract needs no salt here
        info=_DERIVATION_INFO_PREFIX + tenant_id.encode("utf-8"),
    ).derive(base64.urlsafe_b64decode(master_key))
    return base64.urlsafe_b64encode(derived)


def _cipher_for(self, tenant_id: str) -> MultiFernet:
    """Per-tenant cipher, derived from the whole master list so rotation still works."""
    return MultiFernet(
        [Fernet(_derive_tenant_key(k, tenant_id)) for k in self._master_keys]
    )
```

`_encrypt` and `_decrypt` take `tenant_id` and use `_cipher_for(tenant_id)`. The derived
cipher is cached per tenant for the process lifetime; HKDF is cheap but not free, and a
credential read is on the order path.

### 2. Per-tenant KMS CMK, institutional tier only

For `scale` tenants, the Fernet data key is wrapped by a dedicated KMS CMK rather than
derived from the platform master. This buys two things derivation cannot:

- **Real cryptographic erasure.** `ScheduleKeyDeletion` makes that tenant's ciphertext
  unrecoverable, including from PITR backups. Derivation cannot do this, because master
  plus `tenant_id` always reconstructs the key.
- **An auditable key-usage trail.** Every decrypt appears in CloudTrail against a key
  named for that tenant. This is the artifact an institutional tenant's auditor asks for,
  and it is not reconstructable after the fact.

KMS CMKs are billed per key per month plus per request. Verify current figures at
https://aws.amazon.com/kms/pricing/ as they may have changed. The order of magnitude is
what matters: a handful of institutional tenants is a rounding error, and the same
per-key charge across thousands of free-tier retail accounts would exceed what those
accounts generate. That asymmetry, not a security judgement, is why the tiers differ.

### 3. Erasure for retail tiers: row deletion, documented

Retail tenants get `delete_exchange_keys` (already implemented) plus the Supabase backup
retention window, and the retention period is documented as the true erasure horizon. We
do not claim cryptographic erasure for derived keys, because it would not be true.

If a retail-tier erasure guarantee is later required - India's DPDP Act is the plausible
driver and has not been assessed here - the cheapest honest upgrade is a random
per-tenant component stored in a separately deletable location, not in the same Postgres
instance as the ciphertext. A component sitting in the same database comes back with a
PITR restore and so guarantees nothing.

### 4. Migration: the existing primitive does the work

No new code path and no big-bang re-encryption.

During transition, build the cipher as per-tenant keys followed by the platform-wide
keys:

```python
MultiFernet(
    [Fernet(_derive_tenant_key(k, tenant_id)) for k in self._master_keys]
    + [Fernet(k) for k in self._master_keys]          # transition tail, removed at the end
)
```

`MultiFernet.encrypt` uses the first key, so new writes are per-tenant immediately.
`MultiFernet.decrypt` tries each in order, so existing platform-encrypted rows still
decrypt. That is dual-read and single-write from one primitive the vault already uses.

Then:

1. Deploy with the transition tail. Nothing breaks; nothing is re-encrypted yet.
2. Run `scripts/rotate_all_keys.py`, which calls `rotate_key` per row - load under
   whichever key works, store under the per-tenant key. Unchanged in behaviour.
3. Verify zero rows still decrypt only under the tail.
4. Remove the tail. From here a platform-key leak decrypts nothing.

Step 4 is the point at which the gap actually closes. Steps 1-3 are reversible; step 4
is the commit.

### 5. Verification of the above

The snippets in this ADR were executed before it was written, against `cryptography`'s
`HKDF`, `Fernet` and `MultiFernet`, with two generated master keys and two tenant ids.
Nine properties were confirmed:

1. The derived value is a usable Fernet key.
2. Two tenants derive different keys, and tenant A's key raises `InvalidToken` on
   tenant B's ciphertext. This is the blast-radius claim, tested rather than asserted.
3. Derivation is deterministic, so no key material needs storing.
4. Adjacent tenant ids do not collide - the ambiguity that salt concatenation would
   introduce does not arise with `info`.
5. A retired master still decrypts while the primary encrypts, so rotation survives
   derivation.
6. The migration cipher reads rows written before migration.
7. Rows written during migration are per-tenant and readable without the transition tail.
8. After the tail is removed, the platform key alone raises `InvalidToken` on a
   per-tenant row. This is step 4 actually closing the gap.
9. `rotate_key`'s load-then-store round trip migrates a legacy row in place.

These nine assertions should become the test file when this ADR is implemented. They are
not in the suite now, because the code they describe does not exist yet.

## Rationale

Derivation is the right default because it changes the blast radius for free. After
migration, compromising the platform master still requires deriving per tenant - which
an attacker with the master *can* do, so this is not a defence against master
compromise. What it defends against is every weaker and more likely event: a single
tenant's derived key leaking through a log, a crash dump, a debug session, a support
export, or a bug that returns one cipher where it should return another. Those are the
incidents that actually happen, and today each one is platform-wide.

KMS for institutional tenants is justified by what derivation structurally cannot do.
Erasure and a per-tenant audit trail are properties, not degrees - either the key can be
destroyed independently or it cannot. An institutional tenant will ask, and "we derive it
from a master we hold" is a different answer from "we can destroy it and show you every
time it was used."

Splitting by tier rather than picking one is the whole point. ADR-0006 chose bridge
tenancy for the same reason: give the strong answer where it is demanded and funded, keep
the cheap answer where the economics require it. A platform-wide KMS rollout would be the
textbook recommendation and the wrong decision for a business whose entry tier is free.

HKDF over PBKDF2 is not a preference. PBKDF2's cost parameter exists to slow brute force
against guessable inputs. The input here is a generated 32-byte Fernet key, so the
iterations add per-request latency to the order path and no security. Using PBKDF2 here
would be cargo-culting the orphaned vault's choice, which is correct for *its* input and
wrong for this one.

## Alternatives Considered

### Option A: Leave it. Rely on rotation
- **Pros:** Zero work. Rotation genuinely works and the procedure is documented and
  tested.
- **Cons:** Rotation is incident *response*, not containment. It shortens the window in
  which a leaked key is useful and leaves the blast radius at 100% of tenants. The
  response also requires telling every user to rotate their exchange API keys, which is
  a platform-wide customer event triggered by a single leak.
- **Rejected because** the response cost scales with tenant count while the fix does not.

### Option B: Per-tenant KMS CMK for every tenant
- **Pros:** Strongest option. Real erasure and a full audit trail for everyone. No
  derivation logic to get wrong.
- **Cons:** A per-key monthly charge against a free tier is an unbounded cost on
  unmonetised accounts. Also adds a KMS call to the credential read path and a key
  provisioning step to onboarding, with its own rollback requirements.
- **Rejected on economics,** not on security. This is the right answer for the
  institutional tier and is adopted there.

### Option C: Envelope encryption with one CMK wrapping per-tenant data keys
- **Pros:** One CMK to pay for. Per-tenant data keys stored encrypted alongside the
  ciphertext.
- **Cons:** If the wrapped data keys live in the same database as the ciphertext, a
  database compromise yields both, and a PITR restore resurrects both - so it delivers
  neither erasure nor meaningful separation. It adds real machinery for a property it
  does not actually provide here.
- **Rejected because** it looks like the KMS option and behaves like the derivation
  option, at higher complexity than either.

### Option D: Per-tenant key in Secrets Manager
- **Pros:** Separately deletable, so genuine erasure. Cheaper per secret than a CMK.
- **Cons:** Per-secret monthly cost is still per-tenant, so the retail economics problem
  returns in smaller form. No per-decrypt audit trail, which is half of what the
  institutional tier needs.
- **Rejected** as strictly worse than the CMK where cost is acceptable, and still too
  expensive where it is not.

## Consequences

### Positive
- A leaked single-tenant key exposes one tenant. Today it exposes all of them.
- Rotation keeps working unchanged, because derivation is applied across the whole master
  list rather than to one key.
- Migration needs no new mechanism: `MultiFernet` ordering gives dual-read, and
  `rotate_key` already does the re-encryption.
- The institutional tier gains the two things its buyers' auditors ask for - key
  destruction and per-decrypt attribution - without imposing their cost on retail.
- Closes the largest-blast-radius gap in the Tenant Isolation Matrix, and gives the
  `scale` tier its first isolation property beyond quota.

### Negative (accepted trade-offs)
- Two key management paths instead of one. The institutional path is a different code
  route and will be exercised far less, so it needs its own tests rather than inheriting
  confidence from the retail path.
- Derived keys give no erasure guarantee, and we are choosing to document that rather
  than engineer around it.
- A per-tenant cipher cache is new state in the vault, with the ordinary invalidation
  question attached to it.
- Bumping `_DERIVATION_INFO_PREFIX` re-derives everything, so the version string is now
  a migration trigger that looks like a constant. Worth a comment at the definition,
  which the snippet above has.

### Risks
- **Migration step 4 is irreversible by design.** Removing the transition tail is what
  closes the gap, and any row not re-encrypted by then becomes unreadable. Step 3's
  verification is the gate, and it must assert zero rows decrypt only under the tail
  rather than sampling.
- **Key derivation is now on the credential read path.** Uncached HKDF per decrypt would
  add latency to order placement. The cache is a correctness requirement for latency, not
  an optimisation.
- **`tenant_id` becomes key material.** Today `tenant_id` and `user_id` hold the same
  value - `routers/orders.py` and `routers/portfolio.py` both derive
  `tenant_id = UUID(user["id"])` - but `exchange_keys` is keyed on `user_id` while the
  nine execution tables are keyed on `tenant_id`, and ADR-0006 flags that divergence as a
  silo-migration hazard. If those two identities ever diverge, keys derived from one will
  not open ciphertext written under the other. Settle which identity is the derivation
  input and state it in code, not in a convention.
- **The orphaned vault is a live hazard to this work.**
  `backend_app/core/credential_vault.py` is a complete parallel implementation imported
  by nothing. It takes `MASTER_ENCRYPTION_KEYS.split(",")[0]` and discards the rest,
  so wiring it up would break rotation and this design at once. Delete it before starting,
  not after.
- **KMS is a new availability dependency** for institutional tenants. A KMS outage or a
  throttle becomes a failure to read credentials, which becomes a failure to trade.
  Needs an explicit decision on whether that fails closed, and almost certainly it should.
- **DPDP Act obligations are unassessed.** Whether India's data protection regime
  requires an erasure guarantee stronger than row deletion plus backup expiry has not
  been evaluated, and it is the regime that governs this platform. Verify with counsel
  before relying on section 3.

## Related
- [ADR-0006](./ADR-0006-bridge-tenancy-with-institutional-silo-tier.md) - bridge tenancy
  and the institutional silo tier this ADR's split mirrors
- [Tenant Isolation Matrix](../saas-architecture/tenant-isolation-matrix.md) - gap 3,
  which this closes, and gap 3b, the orphaned vault to delete first
- `docs/KEY_ROTATION_PROCEDURE.md` - the rotation procedure this design preserves
- `backend_app/backend/api_key_vault.py` - the live vault being changed
- `scripts/rotate_all_keys.py` - the migration driver, used unchanged
