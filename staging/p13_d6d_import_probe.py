"""D6-D: can ``safe_import("APIKeyVault", "api_key_vault")`` actually resolve? Read-only.

``backend_app/core/state.py:67`` is:

    APIKeyVault = safe_import("APIKeyVault", "api_key_vault")

The module name is the BARE top-level name ``api_key_vault``, not
``backend_app.backend.api_key_vault``. A bare name only resolves if the directory holding
that file -- ``backend_app/backend/`` -- is itself on ``sys.path``. So whether the real vault
or a ``MockEngine`` ends up on ``app_state.vault`` depends on the interpreter's path at boot,
which differs between pytest (``pytest.ini`` sets ``pythonpath``) and a deployed process.

This measures the resolution under two paths. It does NOT establish what a deployed
environment does -- that still needs the boot log. It narrows the question and, more
importantly, it corrects the strings the evidence request must ask the platform owner to
grep for.

No credential, key or decrypted value is read or printed. Nothing is imported for its side
effects beyond the module under test, and the probe runs in a subprocess-free, read-only way.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[1]


def can_resolve(modname: str, extra_path: list[str]) -> tuple[bool, str]:
    saved = list(sys.path)
    try:
        for p in extra_path:
            if p not in sys.path:
                sys.path.insert(0, p)
        try:
            spec = importlib.util.find_spec(modname)
        except (ImportError, ValueError, ModuleNotFoundError) as exc:
            return False, f"{type(exc).__name__}: {exc}"
        if spec is None:
            return False, "find_spec returned None"
        return True, str(spec.origin)
    finally:
        sys.path[:] = saved


def main() -> int:
    print("=" * 86)
    print("D6-D - DOES THE VAULT IMPORT RESOLVE?")
    print("=" * 86)

    print("\n### The call under test (backend_app/core/state.py:67)")
    print('    APIKeyVault = safe_import("APIKeyVault", "api_key_vault")')
    print("  -> safe_import does __import__('api_key_vault', fromlist=['APIKeyVault'])")
    print("  -> a BARE top-level module name.")

    vault_file = ROOT / "backend_app" / "backend" / "api_key_vault.py"
    print(f"\n  the real file exists at backend_app/backend/api_key_vault.py : {vault_file.exists()}")

    scenarios = [
        ("repo root only (a plain `python -m backend_app.main` style path)",
         [str(ROOT)]),
        ("repo root + backend_app/backend on sys.path",
         [str(ROOT), str(ROOT / "backend_app" / "backend")]),
        ("pytest.ini pythonpath (. aerora_quant_backend_updated_final1 backend)",
         [str(ROOT), str(ROOT / "aerora_quant_backend_updated_final1"), str(ROOT / "backend")]),
    ]

    print("\n### Resolution of the BARE name 'api_key_vault' under different sys.path")
    for label, paths in scenarios:
        ok, detail = can_resolve("api_key_vault", paths)
        print(f"\n  {label}")
        print(f"    resolves : {ok}")
        print(f"    detail   : {detail}")

    print("\n### For comparison, the fully-qualified name")
    ok, detail = can_resolve("backend_app.backend.api_key_vault", [str(ROOT)])
    print(f"  backend_app.backend.api_key_vault resolves : {ok}")
    print(f"    detail : {detail}")

    # ------------------------------------------------------- the emitter strings
    print("\n" + "=" * 86)
    print("### THE ACTUAL LOG STRINGS A PLATFORM OWNER MUST GREP FOR")
    print("=" * 86)
    state = (ROOT / "backend_app" / "core" / "state.py").read_text(
        encoding="utf-8", errors="replace"
    ).splitlines()
    for i, ln in enumerate(state, 1):
        if "Failed to import" in ln or "initialization failed, using mock" in ln:
            print(f"\n  state.py:{i}")
            print(f"    template : {ln.strip()}")

    print("""
  Rendered for the vault, these are:

    "Failed to import APIKeyVault from api_key_vault: <error>"
    "DEV_MODE: APIKeyVault initialization failed, using mock: <error>"

  CORRECTION TO PHASE 12. That report recorded the emitters as
  `Failed to import APIKeyVault` and `APIKeyVault initialization failed, using mock`.
  Both are paraphrases of the f-string TEMPLATES, not the emitted text: the first omits
  ` from api_key_vault` and the second omits the leading `DEV_MODE: `. A log search for
  the Phase 12 wording would match the first but MISS the second, and the second is the
  one that indicates a mock was substituted after a successful import. The evidence
  request in this report uses the rendered forms above.

  Both are logged at WARNING, via `logging`, so they appear wherever the deployment's
  stdout/stderr or log handler sends WARNING records.
""")

    print("### What this probe does and does not establish")
    print("  ESTABLISHED : whether the bare name resolves under given sys.path values here.")
    print("  NOT ESTABLISHED : what any deployed environment's sys.path is at boot, and")
    print("                    therefore whether app_state.vault is real or a MockEngine")
    print("                    there. That is the evidence still being requested.")
    print("  REASON IT CANNOT BE PROBED REMOTELY : MockEngine.__getattr__ answers ANY")
    print("                    attribute with a no-op, so no introspection distinguishes")
    print("                    it from the real vault. It must be read from the boot log.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
