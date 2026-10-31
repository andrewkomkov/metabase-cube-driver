#!/bin/sh
# Checks that the image runs the binaries of this build. Without this check, a wrong path falls back in silence: Cube downloads
# the released Cube Store, and a post-install step can restore a released native module.
# Usage: smoke.sh IMAGE INDEX_NODE CUBESTORED
set -eu

want_native=$(sha256sum "$2" | cut -d' ' -f1)
want_store=$(sha256sum "$3" | cut -d' ' -f1)
docker run --rm --entrypoint sh "$1" -c '
  set -eu
  cd /cube/conf
  native=$(node -p "require.resolve(\"@cubejs-backend/native/index.node\")")
  store=$(node -p "require(\"@cubejs-backend/cubestore/dist/src/download\").getBinaryPath()")
  sha256sum "$native" | cut -d" " -f1
  sha256sum "$store" | cut -d" " -f1
  node -e "require(\"@cubejs-backend/native\"); require(\"@cubejs-backend/schema-compiler/dist/src/adapter/ClickHouseQuery\")"
' > smoke.out
got_native=$(sed -n 1p smoke.out)
got_store=$(sed -n 2p smoke.out)
rm -f smoke.out
[ "$got_native" = "$want_native" ] || { echo "error: the native module in the image is $got_native, the build made $want_native" >&2; exit 1; }
[ "$got_store" = "$want_store" ] || { echo "error: cubestored in the image is $got_store, the build made $want_store" >&2; exit 1; }
echo "native module $got_native and cubestored $got_store are the ones of this build"
