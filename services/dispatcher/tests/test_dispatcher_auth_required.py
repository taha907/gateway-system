from fastapi.testclient import TestClient


def test_telemetry_without_token_returns_401():
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/telemetry/ingest/ping")
        assert resp.status_code == 401
