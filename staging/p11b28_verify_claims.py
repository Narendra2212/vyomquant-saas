"""A28 Phase 4: independently verify W1D-15's two load-bearing claims against the ledger.

Read-only. Claim 1: the D5 gating role is absent from the authoritative register.
Claim 2: W1C-02's criteria do not include a live-PostgreSQL run.
"""
import re
from pathlib import Path

LEDGER = Path("staging/PHASE11B_W1D_LEDGER_AND_DRIFT_READINESS.md")
W1D11 = Path("staging/PHASE11B_W1D11_CLASSIFIER_APPROVAL_HANDOFF.md")

out = []
text = LEDGER.read_text(encoding="utf-8")
lines = text.splitlines()

out.append("=" * 94)
out.append("CLAIM 1 -- is the D5 gating role defined in the authoritative ledger?")
out.append("=" * 94)
out.append(f"  ledger: {LEDGER}  ({len(lines)} lines)")
for pat in ("stakeholder", "production.audit", "production audit",
            "production-audit", "sign.off", "sign-off", "signoff"):
    hits = [(i + 1, l) for i, l in enumerate(lines) if re.search(pat, l, re.I)]
    out.append(f"  {pat:<20} -> {len(hits)} occurrence(s)")
    for n, l in hits[:4]:
        out.append(f"       L{n}: {l[:150]}")

out.append("")
out.append("  Owner column values in the authorization register (L800-804):")
for n in range(800, 805):
    cells = [c.strip() for c in lines[n - 1].split("|")]
    if len(cells) >= 5:
        out.append(f"       L{n}: owner={cells[3]!r}  status={cells[4]!r}")

out.append("")
out.append("  Origin of the role -- W1D-11 self-described basis:")
if W1D11.exists():
    wl = W1D11.read_text(encoding="utf-8").splitlines()
    for i, l in enumerate(wl):
        if "production-audit stakeholder" in l.lower() and i < 400:
            out.append(f"       W1D11 L{i+1}: {l[:170]}")
            break
    for i, l in enumerate(wl):
        if "Derived from the ledger" in l:
            out.append(f"       W1D11 L{i+1}: {l[:170]}")
            break

out.append("")
out.append("=" * 94)
out.append("CLAIM 2 -- do W1C-02's authoritative criteria require a live PostgreSQL run?")
out.append("=" * 94)
hits = [(i + 1, l) for i, l in enumerate(lines) if "W1C-02" in l]
out.append(f"  'W1C-02' occurrences in ledger: {len(hits)}")
for n, l in hits:
    out.append(f"    L{n}: {l[:260]}")

out.append("")
for pat in ("live PostgreSQL", "live database", "live schema", "synthetic data",
            "pending implementation and regression evidence"):
    hs = [(i + 1, l) for i, l in enumerate(lines) if re.search(re.escape(pat), l, re.I)]
    out.append(f"  {pat!r:<50} -> {len(hs)}")
    for n, l in hs[:3]:
        out.append(f"       L{n}: {l[:220]}")

text_out = "\n".join(out) + "\n"
Path("staging/p11b28_verify_claims.txt").write_text(text_out, encoding="utf-8")
print(text_out)
