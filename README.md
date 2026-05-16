# mini_chatgpt

`mini_chatgpt` is a proof-of-concept multi-tenant chat platform with AI agent integration. The project demonstrates how to connect a chat UI, tenant authentication, company-specific agents, and a model gateway into a single system.

## What it is

- Multi-tenant architecture: each company has its own account and users.
- Agent management: companies can register agents and assign access to their employees.
- Chat sessions: users join chat sessions backed by a single agent.
- Model gateway support: the app can run with a `fake` provider for local development and can be configured for NVIDIA-hosted model APIs.

## Key goals

- Build an end-to-end chat experience with backend and frontend.
- Keep tenant isolation explicit in authentication and data access.
- Support agent-driven replies and tool-like task execution.
- Provide a local development workflow with SQLite or PostgreSQL + Redis.

## Features

- FastAPI backend with authentication and session management
- React + TypeScript frontend
- Docker Compose for optional PostgreSQL and Redis
- Seeded demo account for quick local startup
- Configurable AI provider via environment variables

## Local development

### Backend

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e "backend[dev]"
uvicorn app.main:app --app-dir backend --reload
```

The backend defaults to SQLite at `./mini_chatgpt.db` and seeds a demo account:

```text
admin@mini.local
password
```

To use PostgreSQL and Redis locally:

```bash
docker compose up -d
DATABASE_URL=postgresql+psycopg://mini_chatgpt:mini_chatgpt@localhost:5432/mini_chatgpt 
uvicorn app.main:app --app-dir backend --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Model provider configuration

The default provider is `fake`, so the app can run without external credentials.

For NVIDIA-hosted Llama 3 compatible APIs, configure:

```text
AI_PROVIDER=nvidia
NVIDIA_API_KEY=...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
AI_MODEL=...
```

## Project structure

- `backend/` - FastAPI application, database models, APIs, and agent/service logic
- `frontend/` - Vite-powered React UI
- `docker-compose.yml` - optional PostgreSQL and Redis services
- `docs/` - design and development notes

## Notes

This repository is intended as a learning project for building an AI-first chat service with agent support and multi-tenant isolation.
