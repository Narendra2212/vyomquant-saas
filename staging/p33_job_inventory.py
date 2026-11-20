"""Complete release-gate job inventory: every real job vs the configured classification.

Phase 32 measured the configured-to-live direction and found 9 of 9 mandatory identifiers
unmatched. This measures the INVERSE: for every job that really exists in the two required
workflows, is it classified as mandatory, advisory, or not classified at all?

An unclassified job is the dangerous case. A release gate that enumerates only some of a
workflow's jobs can be fully green while an unlisted job failed, because nothing asks about it.

Read-only. Parses the workflow YAML and the Phase 18 prototype's own configuration. Writes
nothing, contacts nothing.
"""

from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]
WF_DIR = ROOT / ".github" / "workflows"

# Resolved from the prototype rather than retyped, so this cannot drift from what it configures.
sys.path.insert(0, str(ROOT / "staging"))
import phase18_release_gate as gate  # noqa: E402

WORKFLOW_FILES = {
    "05 Security": WF_DIR / "05-security.yml",
    "01 PR Check": WF_DIR / "01-pr-check.yml",
}


def real_jobs(path: pathlib.Path) -> dict[str, str]:
    """{job_id: api_name} from the YAML. api_name is the `name:` field, else the id."""
    import yaml

    doc = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    out: dict[str, str] = {}
    for jid, body in (doc.get("jobs") or {}).items():
        name = None
        if isinstance(body, dict):
            name = body.get("name")
        out[jid] = str(name) if name else jid
    return out


def main() -> int:
    print("=" * 100)
    print("COMPLETE JOB INVENTORY vs CONFIGURED CLASSIFICATION")
    print("=" * 100)
    print(f"advisory list in prototype: {gate.ADVISORY_JOBS}\n")

    grand_unclassified = []
    grand_total = 0

    for wf, path in WORKFLOW_FILES.items():
        mandatory = gate.REQUIRED_WORKFLOWS.get(wf, ())
        jobs = real_jobs(path)
        grand_total += len(jobs)
        print(f"### {wf}  ({path.name}) — {len(jobs)} real jobs")
        print(f"  {'job id':<30} {'API name':<52} {'classification'}")
        print(f"  {'-'*30} {'-'*52} {'-'*22}")
        for jid, api in sorted(jobs.items()):
            if jid in mandatory:
                cls = "MANDATORY (by id)"
            elif api in mandatory:
                cls = "MANDATORY (by name)"
            elif jid in gate.ADVISORY_JOBS or api in gate.ADVISORY_JOBS:
                cls = "advisory"
            else:
                cls = "** UNCLASSIFIED **"
                grand_unclassified.append((wf, jid, api))
            print(f"  {jid:<30} {api:<52} {cls}")
        print()

        # Which configured identifiers point at nothing real?
        dangling = [m for m in mandatory if m not in jobs and m not in jobs.values()]
        print(f"  configured mandatory: {len(mandatory)}")
        print(f"  dangling (match no real job id OR name): {len(dangling)} {dangling}")
        print()

    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print(f"  real jobs across both workflows : {grand_total}")
    print(f"  UNCLASSIFIED real jobs          : {len(grand_unclassified)}")
    for wf, jid, api in grand_unclassified:
        print(f"      {wf:<14} id={jid:<28} name={api}")
    print()
    print("  Why unclassified jobs matter: the gate only asks about jobs it enumerates.")
    print("  A job that is neither mandatory nor advisory can fail without the gate noticing,")
    print("  because no assertion references it. That is a silent coverage hole, distinct from")
    print("  the identity defect measured in Phase 32.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
