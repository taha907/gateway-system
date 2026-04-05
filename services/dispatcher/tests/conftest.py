import jwt
import pytest


@pytest.fixture(autouse=True)
def _allow_roles_in_unit_tests(monkeypatch):
    """MongoDB olmadan yerel testlerde RBAC geçerli olsun."""
    monkeypatch.setattr("app.authorization.is_request_allowed", lambda role, path: True)


@pytest.fixture
def auth_headers():
    from app.main import JWT_SECRET

    token = jwt.encode({"sub": "tester", "role": "admin"}, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}
