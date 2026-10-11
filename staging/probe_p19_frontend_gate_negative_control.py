"""Phase 19 — executed negative control for the proposed frontend release gate.

WHY THIS EXISTS
---------------
Phase 19 §2 requires that the proposed gate assertions be demonstrated to *detect* a removed
gate or dependency, via "a temporary mutated workflow fixture or another explicit negative
control". No implementation grant exists for `.github/workflows/06-frontend-deploy.yml` or for
`tests/`, so the assertions cannot be committed as a test yet.

This probe runs the exact proposed assertions against:

  1. the REAL current `06-frontend-deploy.yml`      -> must FAIL  (proves pre-fix detection)
  2. a synthetic GATED workflow                     -> must PASS  (proves the fix satisfies them)
  3. four synthetic MUTANTS of the gated workflow   -> must FAIL  (proves each assertion bites)
  4. the REAL current `03-deploy.yml` backend gate   -> must PASS  (regression lock for the
     Phase 18 correction that backend dispatch still routes through the gate)

READ-ONLY. Parses YAML; writes nothing outside this probe's stdout. No governed file is
modified, and no synthetic fixture is written to disk.
"""

from __future__ import annotations

import io
import sys

import yaml

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

GATE_SCRIPT = "require_security_scan.py"
PUBLISH_MARKERS = ("aws s3 sync", "cloudfront create-invalidation", "create-invalidation")


# ── assertion helpers ────────────────────────────────────────────────────────

def _jobs(wf: dict) -> dict:
    return wf.get("jobs") or {}


def _steps_text(job: dict) -> str:
    out = []
    for step in job.get("steps") or []:
        out.append(str(step.get("run") or ""))
        out.append(str(step.get("uses") or ""))
    return "\n".join(out)


def _gate_jobs(wf: dict) -> list:
    return [n for n, j in _jobs(wf).items() if GATE_SCRIPT in _steps_text(j)]


def _publish_jobs(wf: dict) -> list:
    hits = []
    for name, job in _jobs(wf).items():
        text = _steps_text(job)
        if any(m in text for m in PUBLISH_MARKERS):
            hits.append(name)
    return hits


def _needs(job: dict) -> list:
    n = job.get("needs")
    if n is None:
        return []
    return [n] if isinstance(n, str) else list(n)


def _gate_env(wf: dict) -> dict:
    """The env block of the step that runs the gate script."""
    for job in _jobs(wf).values():
        for step in job.get("steps") or []:
            if GATE_SCRIPT in str(step.get("run") or ""):
                return step.get("env") or {}
    return {}


# ── the proposed assertions (what tests/test_frontend_deploy_is_gated.py would hold) ──

def assertions_frontend(wf: dict) -> list:
    """Returns a list of (id, ok, detail)."""
    results = []
    gates = _gate_jobs(wf)
    pubs = _publish_jobs(wf)
    env = _gate_env(wf)

    results.append(("A1 gate script invoked", bool(gates),
                    f"gate jobs={gates or 'NONE'}"))

    ok_a2 = bool(pubs) and all(any(g in _needs(_jobs(wf)[p]) for g in gates) for p in pubs) \
        if gates and pubs else False
    results.append(("A2 publish job needs gate", ok_a2,
                    f"publish jobs={pubs or 'NONE'}; "
                    f"needs={{{', '.join(p + ':' + str(_needs(_jobs(wf)[p])) for p in pubs)}}}"))

    sha = str(env.get("DEPLOY_SHA", ""))
    results.append(("A3 DEPLOY_SHA is github.sha", sha.strip() == "${{ github.sha }}",
                    f"DEPLOY_SHA={sha or 'ABSENT'!r}"))

    unscanned = str(env.get("ALLOW_UNSCANNED", ""))
    ok_a4 = unscanned.strip().strip("'\"").lower() == "false" and "inputs." not in unscanned
    results.append(("A4 ALLOW_UNSCANNED literal false", ok_a4,
                    f"ALLOW_UNSCANNED={unscanned or 'ABSENT'!r}"))

    perms = wf.get("permissions") or {}
    ok_a5 = isinstance(perms, dict) and perms.get("actions") == "read"
    results.append(("A5 permissions actions: read", ok_a5, f"permissions={perms or 'ABSENT'}"))

    ungated = [p for p in pubs if not any(g in _needs(_jobs(wf)[p]) for g in gates)]
    results.append(("A6 no ungated publish path", bool(pubs) and not ungated,
                    f"ungated publishers={ungated or 'none'}"))
    return results


def assertions_backend(wf: dict) -> list:
    """Regression lock: backend dispatch must still route through the gate."""
    results = []
    gates = _gate_jobs(wf)
    results.append(("B1 backend gate job exists", bool(gates), f"gate jobs={gates or 'NONE'}"))

    deployers = [n for n, j in _jobs(wf).items()
                 if "ecs" in n.lower() or "update-service" in _steps_text(j)]
    ok_b2 = bool(deployers) and all(
        any(g in _needs(_jobs(wf)[d]) for g in gates) for d in deployers)
    results.append(("B2 ECS deploy needs the gate", ok_b2,
                    f"deploy jobs={deployers or 'NONE'}"))

    # The gate job must be reachable on workflow_dispatch, not excluded by its own `if:`.
    reachable = False
    for g in gates:
        cond = str(_jobs(wf)[g].get("if") or "")
        if not cond or "workflow_dispatch" in cond:
            reachable = True
    results.append(("B3 gate reachable on dispatch", reachable,
                    "gate job `if:` admits workflow_dispatch"))
    return results


# ── synthetic fixtures (never written to disk) ───────────────────────────────

GATED = """
name: 06 Frontend Deploy
on:
  push:
    branches: [main]
    paths: ['algo22-terminal/**']
  workflow_dispatch:
permissions:
  contents: read
  actions: read
jobs:
  security-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Require the security scan to have passed for this commit
        env:
          GITHUB_TOKEN: TOKEN_PLACEHOLDER
          DEPLOY_SHA: ${{ github.sha }}
          ALLOW_UNSCANNED: 'false'
        run: python scripts/require_security_scan.py
  deploy-frontend:
    needs: security-gate
    runs-on: ubuntu-latest
    steps:
      - name: Sync build artifacts to S3
        run: aws s3 sync dist/ s3://bucket/
      - name: Invalidate CloudFront cache
        run: aws cloudfront create-invalidation --distribution-id X --paths '/*'
"""

MUTANTS = {
    "M1 needs removed": GATED.replace("    needs: security-gate\n", ""),
    "M2 ALLOW_UNSCANNED wired to input":
        GATED.replace("ALLOW_UNSCANNED: 'false'",
                      "ALLOW_UNSCANNED: ${{ inputs.allow_unscanned }}"),
    "M3 SHA from branch name":
        GATED.replace("DEPLOY_SHA: ${{ github.sha }}", "DEPLOY_SHA: main"),
    "M4 actions permission dropped":
        GATED.replace("  actions: read\n", ""),
}


def report(label: str, results: list, expect_pass: bool) -> bool:
    passed = all(ok for _, ok, _ in results)
    verdict = "PASS" if passed else "FAIL"
    expected = "PASS" if expect_pass else "FAIL"
    correct = passed == expect_pass
    print(f"\n{label}")
    print(f"  outcome={verdict}  expected={expected}  "
          f"{'OK' if correct else '*** UNEXPECTED ***'}")
    for aid, ok, detail in results:
        print(f"    [{'x' if ok else ' '}] {aid:34} {detail}")
    return correct


def main() -> int:
    print("=" * 100)
    print("Phase 19 negative control — proposed frontend release-gate assertions")
    print("=" * 100)

    correct = []

    with open(".github/workflows/06-frontend-deploy.yml", encoding="utf-8") as fh:
        real_fe = yaml.safe_load(fh)
    correct.append(report("[1] REAL current 06-frontend-deploy.yml",
                          assertions_frontend(real_fe), expect_pass=False))

    correct.append(report("[2] SYNTHETIC gated workflow",
                          assertions_frontend(yaml.safe_load(GATED)), expect_pass=True))

    for name, text in MUTANTS.items():
        correct.append(report(f"[3] MUTANT {name}",
                              assertions_frontend(yaml.safe_load(text)), expect_pass=False))

    with open(".github/workflows/03-deploy.yml", encoding="utf-8") as fh:
        real_be = yaml.safe_load(fh)
    correct.append(report("[4] REAL current 03-deploy.yml (backend regression lock)",
                          assertions_backend(real_be), expect_pass=True))

    print("\n" + "=" * 100)
    ok = all(correct)
    print(f"negative control: {sum(correct)}/{len(correct)} fixtures behaved as expected")
    print("INTERPRETATION:")
    print("  [1] failing proves the assertions DETECT the current ungated workflow.")
    print("  [2] passing proves the proposed gate SATISFIES them.")
    print("  [3] each mutant failing proves the corresponding assertion BITES.")
    print("  [4] passing is the regression lock for Phase 18's correction: backend")
    print("      dispatch still routes through its security gate.")
    print(f"\nRESULT: {'negative control VALID' if ok else 'NEGATIVE CONTROL INVALID'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
