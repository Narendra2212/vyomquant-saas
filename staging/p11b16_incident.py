"""Phase 1: characterise the overwritten-report incident. Read-only; writes nothing but its log."""
import re
from pathlib import Path

P = Path("staging/PHASE11B_A14_D5_CONSOLIDATION_REPORT.md")
out = []

out.append("=" * 94)
out.append("OVERWRITTEN DESTINATION")
out.append("=" * 94)
out.append("  path   : %s" % P)
out.append("  size   : %d bytes   (this session wrote 19367 bytes at the end of A14)" % P.stat().st_size)
out.append("  mtime  : %s" % __import__("datetime").datetime.fromtimestamp(P.stat().st_mtime))
text = P.read_text(encoding="utf-8", errors="replace")
out.append("  lines  : %d" % len(text.splitlines()))

out.append("")
out.append("=" * 94)
out.append("CURRENT OCCUPANT -- first 40 lines")
out.append("=" * 94)
for l in text.splitlines()[:40]:
    out.append("  " + l[:118])

out.append("")
out.append("=" * 94)
out.append("AUTHORSHIP / TARGET SIGNALS IN THE CURRENT OCCUPANT")
out.append("=" * 94)
sigs = {
    "names PR #7 as canonical/target": r"(canonical|target\w*)[^\n]{0,80}#\s*7|#\s*7[^\n]{0,80}(canonical|target)",
    "names PR #6 as canonical/target": r"(canonical|target\w*)[^\n]{0,80}#\s*6|#\s*6[^\n]{0,80}(canonical|target)",
    "mentions my content digest":      r"7379218b",
    "mentions A14's 39/23 split":      r"\b23 introduced\b|\b39 pre-existing\b",
    "mentions the A14 baseline run":   r"37346853502",
    "mentions the webhook keyspace":   r"_prove_one_keyspace|keyspace split",
    "mentions production quota tests":  r"test_production_reserve_quota",
}
for label, pat in sigs.items():
    hits = [(text[:m.start()].count("\n") + 1, text.splitlines()[text[:m.start()].count("\n")].strip()[:100])
            for m in re.finditer(pat, text, re.I)]
    out.append("")
    out.append("  %-34s %d hit(s)" % (label, len(hits)))
    for ln, s in hits[:4]:
        out.append("      L%-5d %s" % (ln, s))

out.append("")
out.append("=" * 94)
out.append("WHAT SURVIVES OF THIS SESSION'S A14 WORK")
out.append("=" * 94)
survivors = [
    "staging/p11b14_phase0.py", "staging/p11b14_baseline.py", "staging/p11b14_baseline.txt",
    "staging/p11b14_index_delta.py", "staging/p11b14_index_delta.txt",
    "staging/p11b14_index_content_digest.py", "staging/p11b14_index_digest.txt",
    "staging/p11b14_files.py", "staging/p11b14_files.txt",
    "staging/p11b14_metered_delta.py", "staging/p11b14_metered_delta.txt",
    "staging/p11b14_blocking_lane.py", "staging/p11b14_blocking_lane.txt",
    "staging/p11b14_preexisting.py", "staging/p11b14_preexisting.txt",
    "staging/p11b14_local.txt", "staging/p11b14_pr6_unit.log",
]
for s in survivors:
    p = Path(s)
    out.append("  %-9s %-52s %s"
               % ("PRESENT" if p.exists() else "ABSENT", s,
                  ("%d bytes" % p.stat().st_size) if p.exists() else ""))

out.append("")
out.append("  A15 report quotes the A14 conclusions verbatim and is intact:")
a15 = Path("staging/PHASE11B_A15_D5_EXECUTION_REPORT.md").read_text(encoding="utf-8", errors="replace")
for pat, label in [(r"37346853502", "baseline run id"),
                   (r"39 failed, 12017 passed", "baseline tally"),
                   (r"7379218b", "content digest"),
                   (r"13 / 9 / 7 / 4 / 1 / 1 / 1 / 1", "per-file baseline counts")]:
    out.append("    %-24s %s" % (label, "yes" if re.search(pat, a15) else "NO"))

text_out = "\n".join(out)
open("staging/p11b16_incident.txt", "w", encoding="utf-8").write(text_out)
print(text_out)
