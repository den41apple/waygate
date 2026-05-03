# Waygate — командные обёртки для частых задач.
# Запусти `make help` чтобы увидеть список целей.

.DEFAULT_GOAL := help
SHELL := /bin/bash
BUMP := python3 scripts/bump_version.py

.PHONY: help \
        release-info release-agent release-agent-push \
        release-server release-server-push \
        build-awg-client-image \
        test test-backend test-frontend test-e2e \
        compose-up compose-down

help:
	@echo "Waygate — make-цели:"
	@echo ""
	@echo "  Релизы (тег → GitHub Actions собирает wheel/image):"
	@echo "    release-info          Показать текущие версии и последние теги"
	@echo "    release-agent         Bump backend/agent/pyproject.toml + commit + tag (локально)"
	@echo "    release-agent-push    То же + git push origin master <tag>"
	@echo "    release-server        Bump backend/server/pyproject.toml + commit + tag (локально)"
	@echo "    release-server-push   То же + git push"
	@echo "    build-awg-client-image  Собрать docker-образ awg-клиента локально"
	@echo ""
	@echo "  Тесты:"
	@echo "    test                  Backend + frontend + e2e"
	@echo "    test-backend          ruff + mypy + pytest"
	@echo "    test-frontend         typecheck + build"
	@echo "    test-e2e              playwright"
	@echo ""
	@echo "  Compose:"
	@echo "    compose-up            cd deploy && docker compose up -d --build"
	@echo "    compose-down          cd deploy && docker compose down"

# ---------- Релизы ----------

release-info:
	@printf 'agent:  %s\n' "$$(awk -F'\"' '/^version/ {print $$2; exit}' backend/agent/pyproject.toml)"
	@printf 'server: %s\n' "$$(awk -F'\"' '/^version/ {print $$2; exit}' backend/server/pyproject.toml)"
	@echo ""
	@echo "Последние теги:"
	@git tag -l 'agent-v*' | sort -V | tail -3 | sed 's/^/  /' || true
	@git tag -l 'server-v*' | sort -V | tail -3 | sed 's/^/  /' || true

# Внутренняя цель: bump + commit + tag. Ничего не пушит сама.
# Параметры:
#   PYPROJECT — путь к pyproject.toml
#   TAG_PREFIX — префикс тега (agent или server)
_release-local:
	@if ! git diff --quiet || ! git diff --cached --quiet; then \
		echo "✗ working tree не чист — закоммить или сбрось перед релизом"; exit 1; \
	fi
	@git fetch --tags --quiet
	@new_version=$$($(BUMP) $(PYPROJECT) --tag-prefix $(TAG_PREFIX)-v); \
	tag="$(TAG_PREFIX)-v$$new_version"; \
	if git rev-parse "$$tag" >/dev/null 2>&1; then \
		echo "✗ тег $$tag уже существует — bump'нул pyproject но не коммичу"; \
		git checkout -- $(PYPROJECT); \
		exit 1; \
	fi; \
	git add $(PYPROJECT); \
	git commit --quiet -m "$(TAG_PREFIX): bump version → $$new_version"; \
	git tag "$$tag"; \
	echo "✓ локально: коммит + тег $$tag"; \
	echo "  pushed?: нет — выполни 'git push origin HEAD $$tag' или используй *-push цель"

release-agent:
	@$(MAKE) _release-local PYPROJECT=backend/agent/pyproject.toml TAG_PREFIX=agent

release-server:
	@$(MAKE) _release-local PYPROJECT=backend/server/pyproject.toml TAG_PREFIX=server

# Internal: тот же flow + push на origin
_release-push:
	@$(MAKE) _release-local PYPROJECT=$(PYPROJECT) TAG_PREFIX=$(TAG_PREFIX)
	@last_tag=$$(git describe --tags --abbrev=0 --match '$(TAG_PREFIX)-v*'); \
	branch=$$(git rev-parse --abbrev-ref HEAD); \
	echo ""; \
	echo "→ git push origin $$branch $$last_tag"; \
	git push origin "$$branch" "$$last_tag"

release-agent-push:
	@$(MAKE) _release-push PYPROJECT=backend/agent/pyproject.toml TAG_PREFIX=agent

release-server-push:
	@$(MAKE) _release-push PYPROJECT=backend/server/pyproject.toml TAG_PREFIX=server

# ---------- Docker-образы ----------

build-awg-client-image:
	docker build -f backend/agent/awg-client.Dockerfile -t waygate-awg-client:dev backend/agent/

# ---------- Тесты ----------

test: test-backend test-frontend test-e2e

test-backend:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest -q

test-frontend:
	cd frontend && npm run typecheck && npm run build

test-e2e:
	cd frontend && npm run test:e2e

# ---------- Compose ----------

compose-up:
	cd deploy && docker compose up -d --build

compose-down:
	cd deploy && docker compose down
