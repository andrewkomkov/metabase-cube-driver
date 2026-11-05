.PHONY: package
package:
	sh scripts/package.sh

.PHONY: up down load-check conformance conformance-down grid fuzz fuzz-native production tools lint docs
up: package
	cd dev && docker compose up -d --wait clickhouse cube cube-secure mail
	cd dev && docker compose up -d metabase

down:
	cd dev && docker compose -f docker-compose.yml -f docker-compose.conformance.yml -f docker-compose.grid.yml down -v

load-check:
	python3 .github/scripts/load_check.py

# Runs on stock Cube and with cube-clickhouse. VARIANT=off or VARIANT=on runs only one.
conformance:
	VARIANT="$(VARIANT)" sh conformance/run_isolated.sh $(ARGS)

# Runs dev/review/shapes.py with its truth, on stock Cube and with cube-clickhouse. See dev/review/README.md.
grid:
	sh dev/review/grid.sh $(ARGS)

# Random queries against a truth from ClickHouse. See dev/fuzz/README.md.
fuzz:
	SEED="$(SEED)" COUNT="$(COUNT)" sh dev/fuzz/fuzz.sh $(ARGS)

# The generator of the allow-list of native SQL, on Cube with the dialect, stock Cube and Cube in production mode. See dev/fuzz/README.md.
fuzz-native:
	SEED="$(SEED)" COUNT="$(COUNT)" sh dev/fuzz/fuzz_native.sh $(ARGS)

# A sample of the review shapes on Cube with dev mode off and Cube Store. See dev/review/README.md.
production:
	sh dev/review/production.sh $(ARGS)

conformance-down:
	cd dev && docker compose -f docker-compose.yml -f docker-compose.conformance.yml rm -sfv metabase-conformance
	docker volume rm -f metabase-cube-driver_metabase-conformance-data

tools:
	python3 -m pip install -r .github/tools/requirements.txt
	python3 .github/scripts/install_tools.py $(HOME)/.local/bin

lint:
	clj-kondo --lint src
	cljfmt check src
	actionlint
	zizmor .github
	yamllint --strict .
	gitleaks git --redact --no-banner .
	docker compose -f dev/docker-compose.yml config -q
	docker compose -f dev/docker-compose.yml -f dev/docker-compose.conformance.yml config -q
	docker compose -f dev/docker-compose.yml -f dev/docker-compose.production.yml config -q
	docker compose -f dev/docker-compose.yml -f dev/docker-compose.production.yml -f dev/docker-compose.grid.yml -f dev/fuzz/docker-compose.native.yml config -q

docs:
	python3 -m pip install -r docs/requirements.txt
	.github/scripts/build_docs.sh
