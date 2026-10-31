#!/bin/sh
# Builds the native module with cubesql and the Tesseract planner from a patched Cube tree, in the Debian release and with the Python
# version of the Cube image, so the result links to the same glibc and libpython. The toolchain comes from the tree.
# Usage: build-native.sh CUBE_DIR CACHE_DIR OUT_FILE
set -eu

cube=$(CDPATH= cd -- "$1" && pwd)
mkdir -p "$2" "$(dirname "$3")"
cache=$(CDPATH= cd -- "$2" && pwd)
channel=$(sed -n 's/^channel = "\(.*\)"$/\1/p' "$cube/packages/cubejs-backend-native/rust-toolchain.toml")
[ -n "$channel" ] || { echo "error: no channel in rust-toolchain.toml" >&2; exit 1; }

docker run --rm \
  -v "$cube:/src" -v "$cache:/cache" \
  -e CARGO_HOME=/cache/cargo -e CARGO_TARGET_DIR=/cache/target -e CARGO_INCREMENTAL=0 -e PYO3_PYTHON=python3.13 \
  "rust:$channel-trixie" sh -euc '
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends python3.13 libpython3.13-dev cmake libssl-dev pkg-config >/dev/null
    cd /src/packages/cubejs-backend-native
    cargo build --release --locked --features python
    cp /cache/target/release/libcubejs_native.so /cache/index.node
  '
sudo chown -R "$(id -u):$(id -g)" "$cache"
mv "$cache/index.node" "$3"
