# kitchen-docker-stack

A FastAPI kitchen (menu cached in Redis, orders in Postgres) packaged with a multi-stage Dockerfile and Docker Compose, with layered health checks and a regression suite that gates the deploy.

## Setup

1. Install Docker Desktop and make sure it is running.
2. In this folder run:

```bash
docker compose up --build
```

## API docs URL

- Interactive docs: <http://localhost:8000/docs>
- Menu: <http://localhost:8000/menu>
- Liveness: <http://localhost:8000/health> (is the process alive?)
- Deep health: <http://localhost:8000/health/deep> (can it reach Postgres and Redis?)

## Run the tests (against the real stack)

```bash
docker compose --profile test run --rm --build tests
```

## Deploy gate

```bash
bash scripts/deploy.sh
```

Runs the tests first. If any fail, it prints `BLOCKED` and nothing is deployed. The same check runs on GitHub in `.github/workflows/ci.yml`.

## Stop everything

```bash
docker compose --profile test down -v
```

## Without Docker (advanced)

Run your own Postgres and Redis, set `DATABASE_URL` and `REDIS_URL`, then `uvicorn app.main:app --port 8000`. For tests also set `API_URL=http://localhost:8000` and run `pytest`.

See `DOCUMENTATION.md` for the full write-up.
