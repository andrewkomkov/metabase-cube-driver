# Demo image: Metabase with the Cube driver in its plugins directory.
# The release job gives the pinned version and puts the released jar next to this file.
ARG METABASE_VERSION
FROM metabase/metabase:${METABASE_VERSION}

ENV MB_PLUGINS_DIR=/plugins
COPY --chown=2000:2000 --chmod=644 cube.metabase-driver.jar /plugins/cube.metabase-driver.jar
