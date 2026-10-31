#!/bin/sh
# Runs make conformance on an image of ghcr.io/andrewkomkov/cube, with the dialect. VARIANT=off runs it with no dialect.
# Usage: conformance.sh IMAGE [ARGS for conformance/run.py]
# The compose file reads cubejs/cube:$CUBE_VERSION, so this gives the image a local name in that repository.
set -eu

image=$1
shift
docker pull "$image"
docker tag "$image" cubejs/cube:mcd-under-test
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CUBE_VERSION=mcd-under-test VARIANT="${VARIANT:-on}" ARGS="$*" make -C "$root" conformance
