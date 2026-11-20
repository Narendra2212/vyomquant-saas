"""Phase 33 — resolve release-gate job identity from the LIVE workflow YAML.

Read-only. Answers one question the brief raises: does the verifier's configured set of
mandatory job keys correspond to what the GitHub Actions API would actually report?

The API reports a job's DISPLAY NAME: the `name:` field if present, otherwise the job ID.
A verifier that configures job IDs while the API returns display names will refuse every run,
whatever the real outcome was. `job_spec.get("name", job_id)` is the resolution rule.
"""
from __future__ import annotations

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

WORKFLOWS = pathlib.Path(".github/workflows")
TARGETS = ("05-security.yml", "01-pr-check.yml")


def inventory(path: pathlib.Path) -> list:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    rows = []
    for job_id, spec in (doc.get("jobs") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        display = spec.get("name", job_id)
        rows.append({
            "job_id": job_id,
            "display": display,
            "implicit": "name" not in spec,
            "needs": spec.get("needs"),
            "job_coe": spec.get("continue-on-error"),
            "step_coe": sum(
                1 for s in (spec.get("steps") or [])
                if isinstance(s, dict) and s.get("continue-on-error") in (True, "true")
            ),
        })
    return rows


def main() -> int:
    total = 0
    for name in TARGETS:
        path = WORKFLOWS / name
        if not path.exists():
            print("%s: ABSENT" % name)
            continue
        rows = inventory(path)
        total += len(rows)
        print("=== %s — %d job(s) ===" % (name, len(rows)))
        print("  %-30s %-46s %-9s %s" % ("job_id", "API display name", "implicit", "notes"))
        for r in rows:
            notes = []
            if r["needs"]:
                notes.append("needs=%s" % r["needs"])
            if r["job_coe"] in (True, "true"):
                notes.append("JOB continue-on-error")
            if r["step_coe"]:
                notes.append("%d step(s) continue-on-error" % r["step_coe"])
            print("  %-30s %-46s %-9s %s" % (
                r["job_id"], r["display"], "yes" if r["implicit"] else "no", "; ".join(notes)
            ))
        same = [r["job_id"] for r in rows if r["job_id"] == r["display"]]
        print("  job_id == display for %d/%d job(s): %s" % (len(same), len(rows), same or "none"))
        print("")

    print("TOTAL jobs across both workflows: %d" % total)
    print("")
    print("Compare against the Phase 18 prototype's configured keys:")
    try:
        import phase18_release_gate as gate
        for wf, jobs in gate.REQUIRED_WORKFLOWS.items():
            print("  %-14s -> %s" % (wf, list(jobs)))
        print("  advisory      -> %s" % list(gate.ADVISORY_JOBS))
    except Exception as exc:  # pragma: no cover
        print("  (could not import the prototype: %s)" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
