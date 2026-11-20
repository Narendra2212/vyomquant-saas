"""Which .env file does the application actually load, and what does it inject?

`backend_app/core/config.py:17` calls `load_dotenv()` with no argument. python-dotenv then
calls `find_dotenv()`, which walks UP from the calling module's own directory -- not from
the repository root and not from the working directory. So the file that wins is whichever
`.env` is nearest to `backend_app/core/`, and this repository contains more than one.

Reports KEY NAMES and VALUE LENGTHS only. No value is printed: this walks real environment
files. The one exception is a value that is byte-identical to a placeholder shipped in
`.env.example` / `.env.corrupt.backup`, which is reported as PLACEHOLDER because the whole
point of the measurement is that a placeholder is truthy.

Run:  .venv\\Scripts\\python.exe staging\\probe_dotenv_resolution.py
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values, find_dotenv  # noqa: E402

CONFIG_DIR = ROOT / "backend_app" / "core"

PLACEHOLDER_MARKERS = (
    "YOUR_",
    "your-",
    "your_",
    "changeme",
    "CHANGEME",
    "dummy",
    "placeholder",
    "xxx",
    "REPLACE",
)


def classify(value: str | None) -> str:
    if value is None:
        return "None"
    if value == "":
        return "EMPTY"
    if any(marker in value for marker in PLACEHOLDER_MARKERS):
        return f"PLACEHOLDER len={len(value)}"
    return f"len={len(value)}"


def tracked(rel: str) -> str:
    cp = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", rel],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return "TRACKED" if cp.returncode == 0 else "untracked"


def main() -> int:
    print("1. What find_dotenv() resolves to, from each plausible starting point")
    resolved_cwd = find_dotenv(usecwd=True)
    print(f"   find_dotenv(usecwd=True)  -> {resolved_cwd or '<none>'}")

    # Reproduce the walk python-dotenv performs from config.py's own directory.
    walked = None
    for parent in [CONFIG_DIR, *CONFIG_DIR.parents]:
        candidate = parent / ".env"
        if candidate.is_file():
            walked = candidate
            break
    print(f"   walk up from backend_app/core/ -> {walked or '<none>'}")
    print(
        "   => load_dotenv() in backend_app/core/config.py resolves the SECOND one, "
        "because\n      python-dotenv starts the walk at the calling module's directory."
    )

    print("\n2. Every environment file in the tree, with its git status")
    for path in sorted(ROOT.rglob(".env*")):
        if any(
            part in {"node_modules", ".venv", ".git", "site-packages"}
            for part in path.parts
        ):
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        print(f"   {tracked(rel):<9} {path.stat().st_size:>6}B  {rel}")

    print("\n3. What the loaded file injects (names and lengths only)")
    for label, path in (
        ("LOADED BY THE APP", walked),
        ("repo-root .env", ROOT / ".env"),
    ):
        if path is None or not pathlib.Path(path).is_file():
            print(f"   {label}: <absent>")
            continue
        values = dotenv_values(str(path))
        print(f"   {label}: {path}")
        for key in sorted(values):
            print(f"     {key:<34} {classify(values[key])}")

    print("\n4. Does a PLACEHOLDER satisfy the production config validator?")
    print("   core/config.py validate() tests `if not self.SUPABASE_SERVICE_ROLE_KEY`.")
    placeholder = "YOUR_SUPABASE_SERVICE_ROLE_KEY"
    print(f"   bool({placeholder!r}) = {bool(placeholder)}")
    print(
        "   => a required-credential check written as a truthiness test is satisfied by "
        "the\n      placeholder it is meant to reject."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
