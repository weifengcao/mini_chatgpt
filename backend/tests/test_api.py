import asyncio
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    os.environ["APP_ENV"] = "test"
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path / 'test.db'}"
    os.environ["AI_PROVIDER"] = "fake"
    os.environ["APP_SECRET_KEY"] = "test-secret-test-secret-test-secret"
    os.environ["SEED_DEMO_DATA"] = "true"
    get_settings.cache_clear()
    from app.database import SessionLocal, init_db
    app = create_app()
    init_db()
    from app.seed import seed_demo_data

    with SessionLocal() as db:
        seed_demo_data(db, get_settings())
    return TestClient(app)


def auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": "admin@mini.local", "password": "password"})
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def auth_headers_for(client: TestClient, email: str, password: str = "password") -> dict[str, str]:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_login_and_seeded_agent(tmp_path: Path):
    client = make_client(tmp_path)
    headers = auth_headers(client)
    response = client.get("/api/agents", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Demo Agent"


def test_user_email_is_globally_unique(tmp_path: Path):
    make_client(tmp_path)
    from app.database import SessionLocal
    from app.models import Company, User
    from app.security import hash_password

    with SessionLocal() as db:
        other_company = Company(name="Other Company")
        db.add(other_company)
        db.flush()
        db.add(
            User(
                company_id=other_company.id,
                email="admin@mini.local",
                password_hash=hash_password("password"),
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_create_session_and_calculator_stream(tmp_path: Path):
    client = make_client(tmp_path)
    headers = auth_headers(client)
    agent = client.get("/api/agents", headers=headers).json()[0]
    session_response = client.post("/api/chat-sessions", headers=headers, json={"agent_id": agent["id"]})
    assert session_response.status_code == 201, session_response.text
    session_id = session_response.json()["id"]

    with client.stream(
        "POST",
        f"/api/chat-sessions/{session_id}/messages/stream",
        headers={**headers, "Idempotency-Key": "calc-1"},
        json={"content": "/calc 2 + 2"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "message_completed" in body
    assert "4" in body


def test_idempotent_message_send_replays_existing_response(tmp_path: Path):
    client = make_client(tmp_path)
    headers = auth_headers(client)
    agent = client.get("/api/agents", headers=headers).json()[0]
    session_id = client.post("/api/chat-sessions", headers=headers, json={"agent_id": agent["id"]}).json()["id"]

    for _ in range(2):
        with client.stream(
            "POST",
            f"/api/chat-sessions/{session_id}/messages/stream",
            headers={**headers, "Idempotency-Key": "same-key"},
            json={"content": "hello"},
        ) as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())
            assert "message_" in body

    messages = client.get(f"/api/chat-sessions/{session_id}/messages", headers=headers).json()
    assert len(messages) == 2


def test_employee_without_agent_access_cannot_read_agent_run_diagnostics(tmp_path: Path):
    client = make_client(tmp_path)
    admin_headers = auth_headers(client)
    agent = client.get("/api/agents", headers=admin_headers).json()[0]
    session_id = client.post("/api/chat-sessions", headers=admin_headers, json={"agent_id": agent["id"]}).json()["id"]

    with client.stream(
        "POST",
        f"/api/chat-sessions/{session_id}/messages/stream",
        headers={**admin_headers, "Idempotency-Key": "diag-1"},
        json={"content": "/calc 3 + 4"},
    ) as response:
        assert response.status_code == 200
        assert "message_completed" in "".join(response.iter_text())

    run_id = client.get("/api/agent-runs", headers=admin_headers).json()[0]["id"]

    from app.database import SessionLocal
    from app.models import User
    from app.security import hash_password

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "admin@mini.local"))
        db.add(User(company_id=admin.company_id, email="employee@mini.local", password_hash=hash_password("password")))
        db.commit()

    employee_headers = auth_headers_for(client, "employee@mini.local")
    assert client.get("/api/agent-runs", headers=employee_headers).json() == []
    assert client.get(f"/api/agent-runs/{run_id}", headers=employee_headers).status_code == 403
    assert client.get(f"/api/agent-runs/{run_id}/diagnostics", headers=employee_headers).status_code == 403


def test_stale_running_agent_run_is_recovered_before_next_send(tmp_path: Path):
    client = make_client(tmp_path)
    headers = auth_headers(client)
    agent = client.get("/api/agents", headers=headers).json()[0]
    session_id = client.post("/api/chat-sessions", headers=headers, json={"agent_id": agent["id"]}).json()["id"]

    from app.database import SessionLocal
    from app.models import AgentRun, User, utcnow
    from app.services import prepare_chat_run

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "admin@mini.local"))
        _, _, _, _, stale_run, _ = prepare_chat_run(db, get_settings(), user, session_id, "stale", "stale-key")
        stale_run_id = stale_run.id
        stale_run.started_at = utcnow() - timedelta(seconds=120)
        stale_run.loop_limits_json = {**stale_run.loop_limits_json, "max_run_duration_seconds": 1}
        db.commit()

    with client.stream(
        "POST",
        f"/api/chat-sessions/{session_id}/messages/stream",
        headers={**headers, "Idempotency-Key": "after-stale"},
        json={"content": "/calc 5 + 6"},
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "SESSION_BUSY" not in body
    assert "message_completed" in body

    with SessionLocal() as db:
        stale_run = db.get(AgentRun, stale_run_id)
        assert stale_run.status == "failed"
        assert stale_run.error_code == "AGENT_RUN_TIMED_OUT"


def test_non_local_environment_rejects_unsafe_defaults(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("APP_SECRET_KEY", "change-me")
    monkeypatch.setenv("SEED_DEMO_DATA", "false")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="APP_SECRET_KEY"):
            create_app()
    finally:
        get_settings.cache_clear()


def test_nvidia_provider_requires_api_key():
    from app.config import Settings
    from app.gateways.model_gateway import ModelGateway, ModelGatewayConfigurationError, ModelMessage

    gateway = ModelGateway(Settings(ai_provider="nvidia", nvidia_api_key=""))

    async def collect_tokens():
        async for _ in gateway.stream_chat([ModelMessage(role="user", content="hello")]):
            pass

    with pytest.raises(ModelGatewayConfigurationError):
        asyncio.run(collect_tokens())
