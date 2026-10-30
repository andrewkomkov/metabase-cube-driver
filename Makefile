.PHONY: package
package:
	sh scripts/package.sh

.PHONY: up down load-check conformance conformance-down tools lint docs
up: package
	cd dev && docker compose up -d --wait clickhouse cube cube-secure
	cd dev && docker compose up -d metabase

down:
	cd dev && docker compose -f docker-compose.yml -f docker-compose.conformance.yml down -v

load-check:
	python3 .github/scripts/load_check.py

conformance:
	sh conformance/run_isolated.sh $(ARGS)

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

docs:
	python3 -m pip install -r docs/requirements.txt
	.github/scripts/build_docs.sh
