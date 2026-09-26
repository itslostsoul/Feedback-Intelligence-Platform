from fastapi.testclient import TestClient

from app.main import app


def test_app_starts_up() -> None:
    """Constructing the client exercises app import + route registration."""
    with TestClient(app):
        pass


def test_root_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "AI Backend Operational"}


def test_health_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_cors_allows_configured_local_frontend_origin() -> None:
    """A frontend dev server on one of the default CORS_ORIGINS must be able
    to call the API without extra backend configuration."""
    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_preflight_request_is_handled() -> None:
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/decisions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_health_db_endpoint_returns_well_formed_response() -> None:
    """DB may or may not be reachable in this environment; either way the
    endpoint must respond with a predictable, well-formed body rather than
    an unhandled exception."""
    with TestClient(app) as client:
        response = client.get("/api/v1/health/db")

    assert response.status_code in (200, 503)
    body = response.json()
    if response.status_code == 200:
        assert body == {"status": "ok", "detail": None}
    else:
        assert body["error"]["code"] == "database_unavailable"
