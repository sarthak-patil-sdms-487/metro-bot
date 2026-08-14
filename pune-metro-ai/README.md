# Pune Metro AI WhatsApp Assistant


Enterprise-grade, provider-agnostic, multilingual AI WhatsApp chatbot for Pune Metro,
built with FastAPI, LangGraph, LangChain, PostgreSQL, Redis, Qdrant, and Langfuse.


## Status
Phase 1: Project scaffolding (folder structure & environment) — in progress.

## Architecture
This project follows Clean Architecture:

- `app/domain` — entities & interfaces (ports). No third-party imports.
- `app/infrastructure` — adapters implementing domain interfaces (providers).
- `app/services` — use cases / application logic.
- `app/agents` — LangGraph orchestration.
- `app/api` — FastAPI routers (delivery mechanism).
- `app/core` — settings, logging, DI wiring.

See `docs/` for detailed design notes as each phase is built.

## Run with Docker

```bash
POSTGRES_PORT=5434 docker compose -f docker/docker-compose.yml up --build
```

Text chat and WhatsApp Calling can run in this same service and share the same
webhook, PostgreSQL history, knowledge, and tools. Calling is optional and is
disabled by default. See [docs/whatsapp_calling_setup.md](docs/whatsapp_calling_setup.md)
for Meta, Sarvam, webhook, and startup configuration.

## Integration test prerequisites

The integration tests for the admin API require the Docker Compose Postgres service to be running first. This workspace uses host port `5434` because `5433` is already occupied. Start it with `POSTGRES_PORT=5434 docker compose -f docker/docker-compose.yml up -d postgres`. The tests target the dedicated database `pune_metro_test`; set both `POSTGRES_PORT=5434` and a matching `DATABASE_URL` when running them from the host.
