#!/bin/sh
# Build dist/setup-stubs.sh: installer/setup-head.sh, a line __PAYLOAD_BELOW__, then the app as a base64 tarball.
#   sh installer/build.sh
# Every top-level *.py ships, found by glob, with README.md, .env.example, ops/ and static/: a new module needs no
# edit here. Tests, data/ and dist/ never ship.
set -eu
cd "$(dirname "$0")/.."
STAGE=$(mktemp -d /tmp/stubs-stage.XXXXXX 2>/dev/null || mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p dist "$STAGE/stubs"

for f in *.py; do
  [ -f "$f" ] || { echo "No top-level *.py files found."; exit 1; }
  cp "$f" "$STAGE/stubs/"
done
for f in README.md .env.example; do
  [ -f "$f" ] || { echo "$f is missing."; exit 1; }
  cp "$f" "$STAGE/stubs/"
done
[ -f ops/stubs-backup ] || { echo "ops/stubs-backup is missing."; exit 1; }
cp -r ops static "$STAGE/stubs/"

# Clean cache directories portably (avoid find -exec + which BusyBox find can reject)
find "$STAGE" \( -name __pycache__ -o -name .pytest_cache \) -exec rm -rf {} \; 2>/dev/null || true

# GNU tar can record root as the owner; BusyBox tar has no such options.
TAR_EXTRA=""
if tar --owner=0 --group=0 --help >/dev/null 2>&1; then
  TAR_EXTRA="--owner=0 --group=0"
fi

OUT=dist/setup-stubs.sh
{
  cat installer/setup-head.sh
  echo "__PAYLOAD_BELOW__"
  # shellcheck disable=SC2086  # TAR_EXTRA is empty or two flags: it must word-split
  ( cd "$STAGE" && tar $TAR_EXTRA -czf - stubs ) | base64
} > "$OUT"
chmod +x "$OUT"
sh -n "$OUT"
echo "Built $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
