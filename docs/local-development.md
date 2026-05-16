# Local Development

## Backend

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
DATABASE_URL=postgresql+psycopg://mini_chatgpt:mini_chatgpt@localhost:5432/mini_chatgpt \
uvicorn app.main:app --app-dir backend --reload
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

## Model Provider

The default provider is `fake`, which keeps the app runnable without external credentials.

For NVIDIA-hosted Llama 3 compatible APIs, configure:

```text
AI_PROVIDER=nvidia
NVIDIA_API_KEY=...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
AI_MODEL=...
```
