# The workflow appends this to packages/cubejs-docker/dev.Dockerfile of the Cube tag, so these lines continue its last stage.
# The build context "mcd" has the binaries from the patched tree. A named context does not read the .dockerignore of Cube.
COPY --from=mcd --chmod=755 index.node /cubejs/packages/cubejs-backend-native/index.node
COPY --from=mcd --chmod=755 cubestored /cubejs/rust/cubestore/downloaded/latest/bin/cubestored
# dev.Dockerfile is for development, and these two values are the ones of the released image.
ENV NODE_ENV=production
ENV CUBEJS_DOCKER_IMAGE_TAG=latest
# /cube is a link to a directory with no node_modules, so a file in /cube/conf finds the packages only through this path.
ENV NODE_PATH=/cube/conf/node_modules:/cube/node_modules:/cubejs/node_modules
ARG MCD_FINGERPRINT=unknown
ARG MCD_CUBE_TAG=unknown
ARG MCD_PATCHES=unknown
LABEL org.opencontainers.image.source="https://github.com/andrewkomkov/metabase-cube-driver"
LABEL org.opencontainers.image.description="Cube with the fixes that wait in cube-js/cube pull requests. Temporary. See docs/cube-image.md."
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.version="${MCD_CUBE_TAG}"
LABEL mcd.fingerprint="${MCD_FINGERPRINT}"
LABEL mcd.cube-tag="${MCD_CUBE_TAG}"
LABEL mcd.patches="${MCD_PATCHES}"
