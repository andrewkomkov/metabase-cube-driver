#!/bin/sh
# Builds dev/plugins/cube.metabase-driver.jar: the Clojure source and the plugin manifest, without AOT.
# Metabase loads the source when it initializes the driver, so this needs no Clojure toolchain.
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out="$root/dev/plugins/cube.metabase-driver.jar"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

cp -R "$root/src/." "$stage/"
cp -R "$root/resources/." "$stage/"
mkdir -p "$(dirname "$out")"
rm -f "$out"

if command -v jar >/dev/null 2>&1; then
  (cd "$stage" && jar cf "$out" .)
elif command -v zip >/dev/null 2>&1; then
  (cd "$stage" && zip -qr "$out" .)
else
  python3 -c 'import shutil, sys; shutil.make_archive(sys.argv[1], "zip", sys.argv[2])' "$out" "$stage"
  mv "$out.zip" "$out"
fi

echo "built $out"
