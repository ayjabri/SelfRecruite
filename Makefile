# Shortcuts for the Docker setup. Native installs use the `jobagent` CLI directly (see README.md).
COMPOSE ?= docker compose

.PHONY: up down build logs ps test crawl score shell migrate secret-key users

up:            ## build and start everything (first start pulls the models)
	$(COMPOSE) up -d --build
down:          ## stop the containers; data stays in the volumes
	$(COMPOSE) down
build:
	$(COMPOSE) build
logs:
	$(COMPOSE) logs -f app
ps:
	$(COMPOSE) ps
crawl:         ## pull new postings from every configured source, then score them for every user
	$(COMPOSE) exec app jobagent pipeline "Daily pull: at most 200 new postings per target from every configured source, posted in the last 48 hours."
score:         ## re-score for user 1 (learn from marks, scope, fast score)
	$(COMPOSE) exec app jobagent score
users:
	$(COMPOSE) exec app jobagent users
secret-key:
	$(COMPOSE) run --rm --no-deps --entrypoint jobagent app secret-key
migrate:
	$(COMPOSE) exec app alembic upgrade head
shell:
	$(COMPOSE) exec app bash
test:          ## the offline test suite inside the image
	$(COMPOSE) run --rm --no-deps --entrypoint sh app -c "pip install -q pytest py-mini-racer && pytest -q"
