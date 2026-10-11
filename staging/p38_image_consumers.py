#!/usr/bin/env python3
"""
PHASE 38 / PROTOTYPE ONLY -- inventory of every place an image identity is
produced or consumed, per Phase 38 section 4.2.

Classification is deliberately conservative. Phase 38 states that a bounded
search cannot prove a manually operated script is unused, so "no workflow
reference" is recorded as NEEDS OWNER CONFIRMATION rather than as dead code.

Reads only. Writes nothing but stdout.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git", ".venv", "node_modules", ".hypothesis", "__pycache__",
    ".codex_verify_venv", "python_portable", "Git", "archive",
    "node-v22.14.0-win-x64", "node-v20.18.0-win-x64", ".terraform",
    "staging", "site-packages", "dist", "build", ".pytest_cache",
}

INCLUDE_SUFFIX = {".yml", ".yaml", ".py", ".sh", ".tf", ".json", ".Dockerfile"}
INCLUDE_NAMES = {"Dockerfile"}

PATTERNS = [
    ("mutable-tag-literal", re.compile(r":latest\b")),
    ("IMAGE_TAG",           re.compile(r"\bIMAGE_TAG\b")),
    ("IMAGE_DIGEST",        re.compile(r"\bIMAGE_DIGEST\b", re.I)),
    ("image_digest-output", re.compile(r"image_digest")),
    ("docker-build",        re.compile(r"\bdocker\s+build\b")),
    ("docker-push",         re.compile(r"\bdocker\s+push\b")),
    ("kubectl-set-image",   re.compile(r"kubectl\s+set\s+image")),
    ("ecs-task-def-image",  re.compile(r"[\"']image[\"']\s*[:=]|containerDefinitions")),
    ("tf-image-ref",        re.compile(r"\bimage\s*=")),
    ("ecr-describe",        re.compile(r"ecr\s+describe-images|describe_images")),
    ("register-task-def",   re.compile(r"register_task_definition|register-task-definition")),
    ("sha256-digest-form",  re.compile(r"sha256:")),
]


def interesting(path: pathlib.Path) -> bool:
    if path.name in INCLUDE_NAMES:
        return True
    return path.suffix in INCLUDE_SUFFIX


def walk():
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        parts = set(path.relative_to(REPO).parts)
        if parts & SKIP_DIRS:
            continue
        if not interesting(path):
            continue
        yield path


def main() -> int:
    hits: dict[pathlib.Path, dict[str, list[int]]] = {}
    for path in walk():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found: dict[str, list[int]] = {}
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pat in PATTERNS:
                if pat.search(line):
                    found.setdefault(label, []).append(lineno)
        if found:
            hits[path] = found

    # Which workflows reference which scripts, for the reachability column.
    wf_dir = REPO / ".github" / "workflows"
    wf_text = {}
    for wf in sorted(wf_dir.glob("*.y*ml")):
        wf_text[wf.name] = wf.read_text(encoding="utf-8", errors="replace")
    all_wf = "\n".join(wf_text.values())

    print("=" * 100)
    print("IMAGE-IDENTITY SURFACES  (%d files)" % len(hits))
    print("=" * 100)
    print("")
    for path in sorted(hits):
        rel = path.relative_to(REPO).as_posix()
        labels = hits[path]

        # Reachability
        name = path.name
        if rel.startswith(".github/workflows/"):
            referenced = "n/a (is a workflow)"
        elif re.search(re.escape(name), all_wf):
            which = [w for w, t in wf_text.items() if name in t]
            referenced = "referenced by: " + ", ".join(which)
        else:
            referenced = "NO workflow reference -> NEEDS OWNER CONFIRMATION"

        print("  %s" % rel)
        print("      %s" % referenced)
        for label in sorted(labels):
            lines = labels[label]
            shown = ",".join(str(n) for n in lines[:10])
            more = "" if len(lines) <= 10 else " (+%d more)" % (len(lines) - 10)
            print("      %-22s %s%s" % (label, shown, more))
        print("")

    print("=" * 100)
    print("DIGEST VS TAG SUMMARY")
    print("=" * 100)
    digest_producers = [p for p, l in hits.items()
                        if "IMAGE_DIGEST" in l or "image_digest-output" in l]
    tag_consumers = [p for p, l in hits.items()
                     if "kubectl-set-image" in l or "IMAGE_TAG" in l]
    print("")
    print("  files mentioning a DIGEST : %d" % len(digest_producers))
    for p in sorted(digest_producers):
        print("      %s" % p.relative_to(REPO).as_posix())
    print("")
    print("  files mentioning a TAG    : %d" % len(tag_consumers))
    for p in sorted(tag_consumers):
        print("      %s" % p.relative_to(REPO).as_posix())

    # Does any workflow READ a digest output across the needs boundary?
    consumed = re.findall(r"needs\.[\w-]+\.outputs\.image_digest", all_wf)
    print("")
    print("  workflow expressions reading `needs.<job>.outputs.image_digest`: %d"
          % len(consumed))
    print("  `uses:` references to 02-build.yml                            : %d"
          % len(re.findall(r"uses:\s*\./\.github/workflows/02-build\.yml", all_wf)))

    # ECR immutability declared anywhere in Terraform?
    tf_repo = []
    for path in hits:
        if path.suffix == ".tf":
            tf_repo.append(path)
    ecr_decl = 0
    for path in REPO.rglob("*.tf"):
        if set(path.relative_to(REPO).parts) & SKIP_DIRS:
            continue
        t = path.read_text(encoding="utf-8", errors="replace")
        ecr_decl += len(re.findall(r'resource\s+"aws_ecr_repository"', t))
    print("  `aws_ecr_repository` resources declared in Terraform          : %d" % ecr_decl)
    if ecr_decl == 0:
        print("      => tag immutability is NOT declared as code anywhere;")
        print("         its live value is UNKNOWN from this repository.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
