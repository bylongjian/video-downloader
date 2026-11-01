import sqlite3
import time
import os


ACCOUNT = {"email": "reader@example.com", "password": "password123"}


def signup(client, email=ACCOUNT["email"]):
    response = client.post("/api/auth/register", json={**ACCOUNT, "email": email})
    assert response.status_code == 201
    return response.json()["data"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_register_creates_account_and_session(client):
    data = signup(client, " Reader@Example.com ")
    assert data["user"] == {"id": 1, "email": ACCOUNT["email"]}
    assert data["token_type"] == "bearer"
    assert "password" not in str(data)
    with sqlite3.connect(os.environ["DATABASE_PATH"]) as db:
        hashed = db.execute("SELECT password_hash FROM users").fetchone()[0]
        assert hashed != ACCOUNT["password"]
        assert db.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 1
    assert client.get("/api/auth/me", headers=bearer(data["token"])).json()["data"]["user"] == data["user"]
    assert data['quota']['summary']['remaining']==3
    assert data['quota']['chat']['remaining']==10


def test_register_duplicate_email(client):
    signup(client)
    response = client.post("/api/auth/register", json={**ACCOUNT, "email": "READER@example.com"})
    assert response.status_code == 409
    assert response.json()["code"] == "EMAIL_EXISTS"


def test_login_restores_persisted_account(client):
    from backend.main import app
    from fastapi.testclient import TestClient

    user = signup(client)["user"]
    with TestClient(app) as restarted:
        response = restarted.post("/api/auth/login", json=ACCOUNT)
        assert response.status_code == 200
        assert response.json()["data"]["user"] == user


def test_login_wrong_credentials(client):
    signup(client)
    responses = [client.post("/api/auth/login", json=credentials) for credentials in (
        {**ACCOUNT, "password": "wrong-password"},
        {**ACCOUNT, "email": "unknown@example.com"},
    )]
    assert all(response.status_code == 401 for response in responses)
    assert responses[0].json() == responses[1].json()
    assert responses[0].json()["code"] == "INVALID_CREDENTIALS"


def test_session_me_logout_and_replay(client):
    headers = bearer(signup(client)["token"])
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    assert client.post("/api/auth/logout", headers=headers).json()["data"] == {"logged_out": True}
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_session_invalid_or_expired(client):
    import jwt
    import os

    data = signup(client)
    claims = jwt.decode(data["token"], os.environ["JWT_SECRET"], algorithms=["HS256"])
    claims["exp"] = int(time.time()) - 1
    expired = jwt.encode(claims, os.environ["JWT_SECRET"], algorithm="HS256")
    for headers in ({}, bearer("invalid-token"), bearer(expired)):
        response = client.get("/api/auth/me", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_sessions_are_independent(client):
    first = bearer(signup(client)["token"])
    second = bearer(client.post("/api/auth/login", json=ACCOUNT).json()["data"]["token"])
    client.post("/api/auth/logout", headers=first)
    assert client.get("/api/auth/me", headers=first).status_code == 401
    assert client.get("/api/auth/me", headers=second).status_code == 200


def test_account_database_unavailable_does_not_authenticate(client,monkeypatch):
    from backend import database
    data=signup(client)
    def unavailable(*args):
        raise sqlite3.OperationalError('database is locked')
    monkeypatch.setattr(database,'get_session',unavailable)
    response=client.get('/api/auth/me',headers=bearer(data['token']))
    assert response.status_code==503
    assert response.json()['code']=='ACCOUNT_SERVICE_UNAVAILABLE'
