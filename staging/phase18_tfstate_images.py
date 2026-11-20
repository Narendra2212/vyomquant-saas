"""Phase 18 Lane D probe: which container images does the CURRENT Terraform state reference?

Read-only. Prints ONLY resource type counts and container image strings. No credential,
secret, output value, variable or connection string is read or printed: the regex extracts
`"image": "<value>"` occurrences and nothing else, and only from the two state files that
are tracked in git.
"""
from __future__ import annotations

import io
import pathlib
import re

IMAGE_RE = re.compile(r'image\\?"\s*:\s*\\?"([A-Za-z0-9_./:@-]+)')

for name in ("terraform/terraform.tfstate", "terraform/terraform.tfstate.backup"):
    path = pathlib.Path(name)
    if not path.exists():
        print("%s: ABSENT" % name)
        continue
    text = io.open(path, encoding="utf-8", errors="replace").read()
    print("=== %s (%d bytes) ===" % (name, len(text)))
    print("  aws_ecs_task_definition occurrences : %d" % text.count("aws_ecs_task_definition"))
    print("  aws_ecs_service occurrences         : %d" % text.count("aws_ecs_service"))
    images = sorted(set(IMAGE_RE.findall(text)))
    print("  container images referenced         : %d" % len(images))
    for image in images:
        mutable = image.endswith(":latest") or ":" not in image.rsplit("/", 1)[-1]
        print("    %-52s %s" % (image, "MUTABLE TAG" if mutable else ""))
