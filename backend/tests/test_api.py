import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def make_client(tmp_path: Path) -> TestClient:
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


def test_login_and_seeded_agent(tmp_path: Path):
    client = make_client(tmp_path)
    headers = auth_headers(client)
    response = client.get("/api/agents", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["name"] == "Demo Agent"


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
