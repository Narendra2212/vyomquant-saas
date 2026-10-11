"""Structural verification of .github/workflows/07-desktop-build.yml.

Checks the invariants the Phase 10 brief requires, rather than eyeballing the file:

  * valid YAML, three native runners, least-privilege permissions
  * no signing / notarization / publishing / release / deploy step
  * no `continue-on-error` anywhere
  * the upload step is NOT unconditional (`if: always()` would upload a failed build)
  * the bundle-verification script uses no bash-4-only builtin, because `macos-latest`
    runs /bin/bash 3.2
  * `npm ci` and `--locked` are used, so lockfiles are honoured

Exit code 0 when every invariant holds, 1 otherwise. Read-only.
"""

from __future__ import annotations

import re
import sys

import yaml

PATH = ".github/workflows/07-desktop-build.yml"

# Builtins and syntax that exist in bash 4+ but NOT in the bash 3.2 Apple ships.
BASH4_ONLY = [
    (r"\bmapfile\b", "mapfile (bash 4 builtin)"),
    (r"\breadarray\b", "readarray (bash 4 builtin)"),
    (r"declare\s+-A", "associative array (bash 4)"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*\^\^", "${var^^} case conversion (bash 4)"),
    (r"\$\{[A-Za-z_][A-Za-z0-9_]*,,", "${var,,} case conversion (bash 4)"),
    (r"&>>", "&>> append redirect (bash 4)"),
]

# Anything that would make this workflow able to ship or sign an artifact.
FORBIDDEN = [
    (r"codesign", "macOS codesign"),
    # `notarytool`/`altool` are the actual CLIs, and the credential env names are the
    # actual inputs. NOT the bare word "notariz": the report job echoes "Not notarized."
    # into the step summary, which is the disclaimer this workflow exists to make, and a
    # pattern that flagged it would be punishing honesty.
    (r"notarytool|altool|APPLE_ID|APPLE_PASSWORD|APPLE_TEAM_ID", "macOS notarization"),
    (r"signtool|APPLE_CERTIFICATE|CSC_LINK|WINDOWS_CERTIFICATE", "code-signing material"),
    (r"TAURI_SIGNING|TAURI_PRIVATE_KEY", "Tauri updater signing"),
    (r"softprops/action-gh-release|gh\s+release|create-release", "release publishing"),
    (r"aws\s+s3|cloudfront|ecs\s|kubectl|terraform\s+apply", "deployment"),
    (r"npm\s+publish|docker\s+push", "artifact publishing"),
]


def main() -> int:
    with open(PATH, encoding="utf-8") as handle:
        raw = handle.read()
    doc = yaml.safe_load(raw)

    # SCAN WHAT EXECUTES, NOT WHAT IS WRITTEN.
    #
    # This took two corrections, both of which were the checker's fault rather than the
    # workflow's, and both worth recording because they are the standard way a scanner like
    # this produces false confidence in either direction:
    #
    #   1. Grepping the RAW file flagged `continue-on-error`, `mapfile` and "notariz"
    #      because the workflow's YAML comments explain that it has none of them.
    #      `yaml.safe_load` drops comments, so parsing fixed that.
    #   2. Re-dumping the parsed document still flagged `mapfile` and "notariz": the former
    #      appears in a SHELL comment inside a `run:` block (shell comments survive parsing
    #      because they are part of the script string), and the latter in the `report` job's
    #      `echo` of the honest "Not notarized." disclaimer into the step summary.
    #
    # So the scan below is over EXECUTABLE lines only: every `uses:` value, plus the lines
    # of every `run:` block that are neither blank nor shell comments. An `echo` line is
    # still included — it executes — which is why the notarization pattern deliberately
    # does not match the word "notarized" on its own; see FORBIDDEN.
    def executable_lines(node) -> list[str]:
        out: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "uses" and isinstance(value, str):
                    out.append(value)
                elif key == "run" and isinstance(value, str):
                    out.extend(
                        line for line in value.splitlines()
                        if line.strip() and not line.strip().startswith("#")
                    )
                else:
                    out.extend(executable_lines(value))
        elif isinstance(node, list):
            for item in node:
                out.extend(executable_lines(item))
        return out

    # `continue-on-error` is a KEY, so it is checked against the parsed structure.
    structural = yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)
    effective = "\n".join(executable_lines(doc))

    failures: list[str] = []
    notes: list[str] = []

    # ── structure ──────────────────────────────────────────────────────────
    jobs = doc.get("jobs", {})
    if set(jobs) != {"build", "report"}:
        failures.append(f"expected jobs {{build, report}}, got {set(jobs)}")

    build = jobs.get("build", {})
    runners = [m.get("runner") for m in build.get("strategy", {}).get("matrix", {}).get("include", [])]
    expected_runners = {"windows-latest", "macos-latest", "ubuntu-22.04"}
    if set(runners) != expected_runners:
        failures.append(f"expected runners {expected_runners}, got {set(runners)}")
    else:
        notes.append(f"native runners: {', '.join(sorted(runners))}")

    if build.get("strategy", {}).get("fail-fast") is not False:
        failures.append("fail-fast must be false so each OS reports independently")

    perms = doc.get("permissions")
    if perms != {"contents": "read"}:
        failures.append(f"permissions must be exactly {{contents: read}}, got {perms}")
    else:
        notes.append("permissions: contents: read (cannot publish)")

    # ── no hidden failures ─────────────────────────────────────────────────
    # `structural`, NOT `effective`. `continue-on-error` is a YAML KEY, and `effective`
    # holds only `uses:` values and `run:` command lines — so checking it there could never
    # match and the check would be vacuous. This is the third iteration of this scan and
    # the failure mode each time was the checker, not the workflow.
    if "continue-on-error" in structural:
        failures.append("continue-on-error present — a build failure would be hidden")
    else:
        notes.append("no continue-on-error (build failures cannot be hidden)")

    steps = build.get("steps", [])
    upload = [s for s in steps if "upload-artifact" in str(s.get("uses", ""))]
    if len(upload) != 1:
        failures.append(f"expected exactly 1 upload step, found {len(upload)}")
    else:
        if "if" in upload[0]:
            failures.append(
                f"upload step is conditional ({upload[0]['if']!r}); it must inherit the "
                "default success() so a failed build uploads nothing"
            )
        if upload[0].get("with", {}).get("if-no-files-found") != "error":
            failures.append("upload must set if-no-files-found: error")
        else:
            notes.append("upload: gated on success, if-no-files-found=error")

    # ── lockfiles ──────────────────────────────────────────────────────────
    if "npm ci" not in effective:
        failures.append("must install with `npm ci` to honour package-lock.json")
    if "--locked" not in effective:
        failures.append("must pass --locked so Cargo.lock is enforced")
    if "npm ci" in effective and "--locked" in effective:
        notes.append("lockfiles honoured: npm ci + cargo --locked")

    # ── bash 3.2 compatibility ─────────────────────────────────────────────
    for pattern, label in BASH4_ONLY:
        if re.search(pattern, effective):
            failures.append(f"bash-4-only construct present: {label} (macos-latest has bash 3.2)")
    if not any(re.search(p, effective) for p, _ in BASH4_ONLY):
        notes.append("no bash-4-only construct (macOS /bin/bash 3.2 safe)")

    # ── nothing that could ship ────────────────────────────────────────────
    for pattern, label in FORBIDDEN:
        if re.search(pattern, effective, re.IGNORECASE):
            failures.append(f"forbidden capability referenced: {label}")
    if not any(re.search(p, effective, re.IGNORECASE) for p, _ in FORBIDDEN):
        notes.append("no signing, notarization, publishing or deployment step")

    # ── report ─────────────────────────────────────────────────────────────
    for note in notes:
        print(f"OK    {note}")
    for failure in failures:
        print(f"FAIL  {failure}")
    print()
    print(f"{len(notes)} invariant(s) held, {len(failures)} violated.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
