.PHONY: up down build restart logs shell test migrate makemigrations superuser tunnel collectstatic sync

# Docker
up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose up -d --build

restart:
	docker compose down && docker compose up -d

logs:
	docker compose logs -f

shell:
	docker compose exec vrc-ta-hub bash

# Django
test:
	docker compose exec vrc-ta-hub python manage.py test

migrate:
	docker compose exec vrc-ta-hub python manage.py migrate

makemigrations:
	docker exec -it vrc-ta-hub bash -c "python manage.py makemigrations && python manage.py migrate"

superuser:
	docker compose exec vrc-ta-hub python manage.py createsuperuser

collectstatic:
	docker compose exec -e DEBUG=False vrc-ta-hub python manage.py collectstatic --noinput

# カレンダー同期
sync:
	docker compose exec vrc-ta-hub python manage.py generate_recurring_events

# Cloudflare Tunnel
tunnel:
	cloudflared tunnel run vrc-ta-hub-local
