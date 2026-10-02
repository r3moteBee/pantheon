.PHONY: help build up down logs logs-backend logs-frontend restart restart-backend clean dev-backend dev-frontend shell-backend shell-db init-data setup ps test format lint

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: ## Build all Docker images
	docker compose build

up: ## Start all services
	@cp -n .env.example .env 2>/dev/null || true
	@mkdir -p data/db data/chroma data/personality data/projects data/workspace
	docker compose up -d
	@echo "✅ Pantheon running at http://localhost"
	@echo "   Frontend: http://localhost:3000"
	@echo "   Backend API: http://localhost:8000"
	@echo "   API Docs: http://localhost:8000/docs"

down: ## Stop all services
	docker compose down

logs: ## Tail logs from all services
	docker compose logs -f

logs-backend: ## Tail backend logs
	docker compose logs -f backend

logs-frontend: ## Tail frontend logs
	docker compose logs -f frontend

restart: ## Restart all services
	docker compose restart

restart-backend: ## Restart only the backend
	docker compose restart backend

clean: ## Stop and remove containers, volumes, images
	docker compose down -v --rmi local
	docker system prune -f

dev-backend: ## Run backend in development mode (hot reload)
	cd backend && pip install -r requirements.txt && \
	uvicorn main:app --reload --host 0.0.0.0 --port 8000

dev-frontend: ## Run frontend in development mode
	cd frontend && npm ci && npm run dev

shell-backend: ## Open a shell in the backend container
	docker compose exec backend bash

shell-db: ## Open SQLite shell for episodic DB
	docker compose exec backend sqlite3 /app/data/db/episodic.db

init-data: ## Initialize data directories and default personality files
	@mkdir -p data/db data/chroma data/personality data/projects data/workspace
	@if [ ! -f data/personality/soul.md ]; then \
		cp -r backend/data/personality/ data/; \
		echo "✅ Initialized personality files"; \
	fi

setup: init-data ## First-time setup
	@cp -n .env.example .env || true
	@echo "✅ Setup complete. Edit .env with your API keys, then run: make up"

ps: ## Show running services
	docker compose ps

test: ## Run backend tests
	cd backend && python -m pytest tests/ -v

format: ## Format Python code (needs black + isort installed)
	cd backend && black . && isort .

lint: ## Lint Python code (needs flake8 installed)
	cd backend && flake8 . --max-line-length=100

.DEFAULT_GOAL := help
