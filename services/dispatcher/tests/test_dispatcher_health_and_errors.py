from fastapi.testclient import TestClient


def test_health_returns_200():
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_unknown_path_returns_404_not_200():
    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/does-not-exist")
        assert resp.status_code == 404

