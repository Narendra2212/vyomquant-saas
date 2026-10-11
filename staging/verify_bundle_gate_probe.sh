#!/usr/bin/env bash
# Probe for the `Verify bundle artifacts` step in .github/workflows/07-desktop-build.yml.
#
# Runs the step's script verbatim against `algo22-terminal/releases/`, which contains one
# genuine 112 MiB Windows installer and three 64-69 byte text placeholders. A correct gate
# reports 1 OK and 3 FAIL and exits non-zero.
#
# This is a probe, not a test fixture: it proves the gate rejects the exact class of file
# that was sitting in the repository claiming to be an installer.
set -euo pipefail

BUNDLE_DIR="${1:-algo22-terminal/releases}"
MIN_BYTES=$((1024 * 1024))

if [ ! -d "$BUNDLE_DIR" ]; then
  echo "FAIL: no bundle directory at $BUNDLE_DIR"
  exit 1
fi

echo "Bundle directory: $BUNDLE_DIR"

LIST="$(mktemp)"
find "$BUNDLE_DIR" -type f \
  \( -name '*.exe' -o -name '*.msi' -o -name '*.dmg' \
     -o -name '*.AppImage' -o -name '*.deb' -o -name '*.rpm' \) \
  | sort > "$LIST"

COUNT=$(wc -l < "$LIST" | tr -d '[:space:]')
if [ "$COUNT" -eq 0 ]; then
  echo "FAIL: the bundler produced no installer artifact under $BUNDLE_DIR"
  find "$BUNDLE_DIR" -maxdepth 3 -type f | sed 's/^/  /' || true
  exit 1
fi

STATUS=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  SIZE=$(wc -c < "$f" | tr -d '[:space:]')
  if [ "$SIZE" -lt "$MIN_BYTES" ]; then
    echo "FAIL: $f is $SIZE bytes, below the ${MIN_BYTES}-byte floor."
    echo "      A file this small is a placeholder, not an installer."
    STATUS=1
  else
    echo "OK:   $f ($SIZE bytes)"
  fi
done < "$LIST"

echo "Verified $COUNT artifact(s)."
exit "$STATUS"
