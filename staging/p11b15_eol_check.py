"""Why did the patch fail? Compare line endings and byte identity, worktree vs blob."""
import subprocess

PR6 = "d07014d36d32c63db44f8fb9659933c0e84e82f2"
PATHS = [
    ".github/workflows/01-pr-check.yml",
    "tests/test_webhook_duplicate_delivery_real_redis.py",
    "tests/test_quota01_metered_reservation_is_hard.py",
]

out = []


def sh(args):
    return subprocess.run(args, capture_output=True).stdout


def eol(data):
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    return "CRLF=%d LF=%d" % (crlf, lf)


out.append("%-54s %-22s %-22s" % ("path", "worktree bytes/eol", "PR#6 blob bytes/eol"))
for p in PATHS:
    disk = open(p, "rb").read()
    blob = sh(["git", "show", f"{PR6}:{p}"])
    out.append("")
    out.append("  %s" % p)
    out.append("    worktree : %7d bytes   %s" % (len(disk), eol(disk)))
    out.append("    PR#6 blob: %7d bytes   %s" % (len(blob), eol(blob)))
    out.append("    trailing-whitespace lines on disk: %d"
               % sum(1 for l in disk.split(b"\n") if l.rstrip(b"\r") != l.rstrip(b"\r \t")))

out.append("")
out.append("DIAGNOSIS")
out.append("  `git show` emits blob bytes (LF, as stored). The worktree copies are CRLF on")
out.append("  Windows because core.autocrlf rewrites on checkout. A diff built between a")
out.append("  LF 'old' file and a CRLF 'new' file describes every line as changed and its")
out.append("  context cannot match the blob, so `git apply` rejects it.")
out.append("")
out.append("  Fix for the patch builder: normalise BOTH sides to LF before diffing, so the")
out.append("  patch is expressed in the same line endings git stores. This is a defect in")
out.append("  my patch-generation script, not in the proposed change.")

text = "\n".join(out)
open("staging/p11b15_eol.txt", "w", encoding="utf-8").write(text)
print(text)
