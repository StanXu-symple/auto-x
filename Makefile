SHELL := /bin/sh
PYTHON ?= python3
ENV_FILE ?= $(if $(wildcard .env),.env,.env.example)
COMPOSE := docker compose --env-file $(ENV_FILE) -f docker-compose.yml
COMPOSE_MICROSERVICES := $(COMPOSE) -f docker-compose.microservices.yml
COMPOSE_PROD := $(COMPOSE) -f docker-compose.prod.yml
COMPOSE_EXTERNAL := $(COMPOSE) -f docker-compose.external.yml

.DEFAULT_GOAL := help

.PHONY: help init config prod-config external-config validate-prod-env validate-external-env build up prod-up external-up down prod-down external-down \
	restart logs ps monitor-up prod-monitor-up monitor-down migrate prod-migrate external-migrate backup prod-backup restore prod-restore test test-backend \
	test-frontend shell-backend postgres redis-cli network-groups microservices-init microservices-up microservices-down

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "; printf "X Sentinel commands:\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-16s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: ## Create .env from .env.example without overwriting an existing file
	@test -f .env || install -m 600 .env.example .env
	@chmod 600 .env
	@echo "Environment file ready: .env"

config: ## Validate the base Compose model
	$(COMPOSE) config --quiet

prod-config: ## Validate the production Compose model
	$(COMPOSE_PROD) config --quiet

external-config: ## Validate the external PostgreSQL/Redis Compose model
	$(COMPOSE_EXTERNAL) config --quiet

validate-prod-env: ## Reject missing or placeholder production secrets
	@test -f "$(ENV_FILE)" || { echo "Missing $(ENV_FILE); run 'make init' first."; exit 1; }
	./infra/scripts/validate-prod-env.sh "$(ENV_FILE)"

validate-external-env: ## Validate external PostgreSQL/Redis production settings
	@test -f "$(ENV_FILE)" || { echo "Missing $(ENV_FILE); copy .env.external.example first."; exit 1; }
	./infra/scripts/validate-prod-env.sh "$(ENV_FILE)" external

build: ## Build application images
	$(COMPOSE) build

up: ## Build and start the core stack
	$(COMPOSE) up -d --build

prod-up: validate-prod-env ## Migrate, build and start the production stack
	$(COMPOSE_PROD) config --quiet
	$(COMPOSE_PROD) up -d --build

external-up: validate-external-env ## Migrate and start API, workers and frontend with external PostgreSQL/Redis
	$(COMPOSE_EXTERNAL) up -d --build backend worker ai-worker frontend

down: ## Stop the core stack without deleting persistent data
	$(COMPOSE) down

prod-down: ## Stop the production stack without deleting persistent data
	$(COMPOSE_PROD) down

external-down: ## Stop only API, workers and frontend in external-data mode
	$(COMPOSE_EXTERNAL) stop backend worker ai-worker frontend
	$(COMPOSE_EXTERNAL) rm -f migrate backend worker ai-worker frontend

restart: ## Restart API, workers and frontend
	$(COMPOSE) restart backend worker ai-worker frontend

logs: ## Follow application logs
	$(COMPOSE) logs -f --tail=200 backend worker ai-worker frontend

ps: ## Show container and health status
	$(COMPOSE) ps

monitor-up: ## Start core services plus Prometheus, Grafana and exporters
	$(COMPOSE) --profile monitoring up -d --build

microservices-init: ## Generate service topology and service-auth keys
	./infra/scripts/microservices-init.sh

network-groups: ## Create named Docker network groups from NETWORK_GROUPS
	./infra/scripts/network-groups.sh

microservices-up: microservices-init ## Start the auth center, monitoring center and node agent
	$(COMPOSE_MICROSERVICES) up -d backend auth-center monitor-center monitor-agent

microservices-down: ## Stop the optional microservice control plane
	$(COMPOSE_MICROSERVICES) stop auth-center monitor-center monitor-agent
	$(COMPOSE_MICROSERVICES) rm -f auth-center monitor-center monitor-agent

prod-monitor-up: validate-prod-env ## Start production services plus monitoring without changing deployment mode
	$(COMPOSE_PROD) --profile monitoring up -d --build

monitor-down: ## Remove only optional monitoring containers (preserves metrics volumes)
	$(COMPOSE) --profile monitoring stop grafana prometheus node-exporter redis-exporter postgres-exporter
	$(COMPOSE) --profile monitoring rm -f grafana prometheus node-exporter redis-exporter postgres-exporter

migrate: ## Apply Alembic database migrations
	$(COMPOSE) build migrate
	$(COMPOSE) run --rm migrate

prod-migrate: validate-prod-env ## Apply migrations with the production Compose model
	$(COMPOSE_PROD) build migrate
	$(COMPOSE_PROD) run --rm migrate

external-migrate: validate-external-env ## Apply migrations to configured external PostgreSQL without local data containers
	$(COMPOSE_EXTERNAL) build migrate
	$(COMPOSE_EXTERNAL) run --rm --no-deps migrate

backup: ## Back up PostgreSQL and Redis into BACKUP_DIR
	COMPOSE_ENV_FILE="$(abspath $(ENV_FILE))" COMPOSE_FILES="$(abspath docker-compose.yml)" ./infra/scripts/backup.sh

prod-backup: validate-prod-env ## Back up production PostgreSQL and Redis
	COMPOSE_ENV_FILE="$(abspath $(ENV_FILE))" COMPOSE_FILES="$(abspath docker-compose.yml):$(abspath docker-compose.prod.yml)" ./infra/scripts/backup.sh

restore: ## Restore BACKUP=<directory>; requires CONFIRM_RESTORE=yes
	@test -n "$(BACKUP)" || { echo "Usage: make restore BACKUP=backups/<timestamp> CONFIRM_RESTORE=yes"; exit 1; }
	@test "$(CONFIRM_RESTORE)" = "yes" || { echo "Set CONFIRM_RESTORE=yes to acknowledge destructive restore."; exit 1; }
	COMPOSE_ENV_FILE="$(abspath $(ENV_FILE))" COMPOSE_FILES="$(abspath docker-compose.yml)" ./infra/scripts/restore.sh "$(abspath $(BACKUP))" --yes

prod-restore: validate-prod-env ## Restore production BACKUP=<directory>; requires CONFIRM_RESTORE=yes
	@test -n "$(BACKUP)" || { echo "Usage: make prod-restore BACKUP=backups/<timestamp> CONFIRM_RESTORE=yes"; exit 1; }
	@test "$(CONFIRM_RESTORE)" = "yes" || { echo "Set CONFIRM_RESTORE=yes to acknowledge destructive restore."; exit 1; }
	COMPOSE_ENV_FILE="$(abspath $(ENV_FILE))" COMPOSE_FILES="$(abspath docker-compose.yml):$(abspath docker-compose.prod.yml)" ./infra/scripts/restore.sh "$(abspath $(BACKUP))" --yes

test: test-backend test-frontend ## Run backend and frontend checks

test-backend: ## Run Ruff and pytest (requires backend dev dependencies)
	cd backend && $(PYTHON) -m ruff check . && $(PYTHON) -m pytest

test-frontend: ## Install locked frontend dependencies, type-check and build
	cd frontend && npm ci && npm run type-check && npm run build

shell-backend: ## Open a shell in the API container
	$(COMPOSE) exec backend /bin/sh

postgres: ## Open a PostgreSQL client as the application user
	$(COMPOSE) exec postgres sh -c 'PGPASSWORD="$$POSTGRES_PASSWORD" exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

redis-cli: ## Open an authenticated Redis CLI
	$(COMPOSE) exec redis sh -c 'exec redis-cli --no-auth-warning -a "$$REDIS_PASSWORD"'
