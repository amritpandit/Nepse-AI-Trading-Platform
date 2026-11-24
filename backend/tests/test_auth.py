from app.core.config import settings
from app.core.security import ALGORITHM, create_access_token
import jwt


def test_register_and_login(client) -> None:
    register_response = client.post(
        "/auth/register",
        json={"email": "researcher@example.com", "password": "secret123", "role": "researcher"},
    )

    assert register_response.status_code == 201
    assert register_response.json()["token_type"] == "bearer"
    assert register_response.json()["access_token"]

    login_response = client.post(
        "/auth/login",
        json={"email": "researcher@example.com", "password": "secret123"},
    )

    assert login_response.status_code == 200
    assert login_response.json()["access_token"]


def test_openapi_documents_manual_bearer_token_authorization(client) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    security_schemes = response.json()["components"]["securitySchemes"]
    assert security_schemes["HTTPBearer"] == {"type": "http", "scheme": "bearer"}


def test_access_token_uses_explicit_hs256_and_preserves_subject() -> None:
    token = create_access_token(subject="researcher@example.com")

    assert jwt.get_unverified_header(token)["alg"] == ALGORITHM == "HS256"
    payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])

    assert payload["sub"] == "researcher@example.com"
    assert isinstance(payload["exp"], int)
