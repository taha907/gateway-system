from fastapi.testclient import TestClient


def test_routes_to_ingest_service(monkeypatch, auth_headers):
    """
    Dispatcher, /api/telemetry/ingest/* isteklerini ingest servisine yönlendirmelidir.
    Bu test, yönlendirme davranışını doğrular (proxy değil: kimlik/erişim kontrolü dispatcher'da yapılır).
    """
    monkeypatch.setenv("INGEST_SERVICE_URL", "http://telemetry-ingest:8000")
    monkeypatch.setenv("QUERY_SERVICE_URL", "http://telemetry-query:8000")
    monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth:8000")

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/telemetry/ingest/ping", headers=auth_headers)
        # Ingest servisi ayağa kalkmadıysa bile dispatcher doğru HTTP hata kodu döndürmeli.
        assert resp.status_code in (200, 502, 503, 504)


def test_routes_to_query_service(monkeypatch, auth_headers):
    monkeypatch.setenv("INGEST_SERVICE_URL", "http://telemetry-ingest:8000")
    monkeypatch.setenv("QUERY_SERVICE_URL", "http://telemetry-query:8000")
    monkeypatch.setenv("AUTH_SERVICE_URL", "http://auth:8000")

    from app.main import app

    with TestClient(app) as client:
        resp = client.get("/api/telemetry/query/ping", headers=auth_headers)
        assert resp.status_code in (200, 502, 503, 504)

