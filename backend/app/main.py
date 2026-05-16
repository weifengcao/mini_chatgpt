from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agents, auth, chat
from app.config import get_settings
from app.database import SessionLocal, init_db
from app.seed import seed_demo_data


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db()
        if settings.seed_demo_data:
            with SessionLocal() as db:
                seed_demo_data(db, settings)
        yield

    app = FastAPI(title="Mini ChatGPT API", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(agents.router)
    app.include_router(chat.router)
    return app


app = create_app()
