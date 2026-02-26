SHELL := /bin/bash
COMPOSE := docker compose -f docker-compose.yml
API_INNER := http://127.0.0.1:18080
WEB_BASE ?= http://127.0.0.1:18181

.PHONY: up down restart ps logs db-bootstrap test-backend openapi openapi-check eval-prepare eval-m1 eval-planner eval-mapreduce eval-search-perf eval-crosslang eval-agent eval-agent-dual eval-agent-trend eval-all e2e-ui governance-snapshot governance-trend governance-gate check-all

up:
	$(COMPOSE) up -d

down:
	$(COMPOSE) down

restart:
	$(COMPOSE) up -d --force-recreate fkv-api fkv-worker fkv-frontend

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --tail=120

db-bootstrap:
	docker exec -i fkv-api python -c "from app import models; from app.db import Base, engine; Base.metadata.create_all(bind=engine); print('db_bootstrap_ok')"

test-backend:
	docker exec -i fkv-api pytest -q

openapi:
	docker exec -i fkv-api python /app/scripts/export_openapi.py

openapi-check:
	docker exec -i fkv-api python /app/scripts/check_openapi_freeze.py

eval-prepare:
	docker exec -i fkv-api python /app/evaluation/prepare_eval_samples.py --source /app/ingest_samples --target /app/ingest_eval/current --run-id $$(date +%s)

eval-m1: db-bootstrap eval-prepare
	docker exec -i fkv-api python /app/evaluation/run_m1_regression.py --api $(API_INNER) --sample-dir /app/ingest_eval/current --container-prefix /app/ingest_eval/current --out /app/evaluation/m1_regression_report.json

eval-planner:
	docker exec -i fkv-api python /app/evaluation/run_planner_eval.py --api $(API_INNER) --out /app/evaluation/planner_eval_report.json

eval-mapreduce:
	docker exec -i fkv-api python /app/evaluation/run_map_reduce_eval.py --api $(API_INNER) --out /app/evaluation/map_reduce_eval_report.json

eval-search-perf:
	docker exec -i fkv-api python /app/evaluation/run_search_perf.py --api $(API_INNER) --out /app/evaluation/search_perf_report.json

eval-crosslang:
	docker exec -i fkv-api python /app/evaluation/run_crosslang_eval.py --api $(API_INNER) --out /app/evaluation/crosslang_eval_report.json

eval-agent:
	docker exec -i fkv-api python /app/evaluation/run_agent_eval.py --api $(API_INNER) --cases /app/evaluation/agent_eval_cases_v1.json --sample-size 20 --out /app/evaluation/agent_eval_report.json --md-out /app/evaluation/agent_eval_report.md

eval-agent-dual:
	docker exec -i fkv-api python /app/evaluation/run_agent_eval.py --api $(API_INNER) --cases /app/evaluation/agent_eval_cases_v1.json --sample-size 20 --boundary-cases /app/evaluation/agent_eval_boundary_suite_v1.json --boundary-sample-size 10 --out /app/evaluation/agent_eval_report.json --md-out /app/evaluation/agent_eval_report.md

eval-agent-trend:
	docker exec -i fkv-api python /app/evaluation/run_agent_eval_trend.py --glob "/app/evaluation/agent_eval_report*.json" --out /app/evaluation/agent_eval_trend.json

eval-all: eval-m1 eval-planner eval-mapreduce eval-search-perf eval-crosslang eval-agent

governance-snapshot:
	docker exec -i fkv-api python /app/scripts/category_debt_snapshot.py --output-dir /app/data

governance-trend:
	docker exec -i fkv-api python /app/scripts/category_debt_trend.py --data-dir /app/data --output /app/data/category_debt_trend_latest.json

governance-gate:
	docker exec -i fkv-api python /app/scripts/check_category_debt_gate.py

# Uses Playwright official image so host npm/node is not required.
e2e-ui:
	docker run --rm --network host -e FKV_WEB_BASE=$(WEB_BASE) -v $$(pwd)/frontend:/work -w /work mcr.microsoft.com/playwright:v1.50.0-jammy bash -lc "npm install && npx playwright test --config=playwright.config.js"

check-all: openapi-check test-backend governance-snapshot governance-trend governance-gate eval-all e2e-ui
