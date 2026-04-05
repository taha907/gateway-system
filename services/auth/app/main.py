import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Optional

import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from pydantic import BaseModel, Field
from pymongo import MongoClient
from pymongo.database import Database

JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change_me")
INTERNAL_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "dev_internal_token_change_me")
AUTH_MONGO_URI = os.getenv("AUTH_MONGO_URI", "mongodb://localhost:27017/auth")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

logger = logging.getLogger(__name__)

_mongo: Optional[MongoClient] = None


def _mongo_client() -> MongoClient:
    if _mongo is None:
        raise RuntimeError("MongoDB not initialized")
    return _mongo


def get_db() -> Database:
    return _mongo_client().get_default_database()


def _ensure_default_admin(db: Database) -> None:
    """Koleksiyon boşsa tek admin ekler. delete_many yapmıyoruz (bcrypt + DB yavaşlatıyordu)."""
    users = db["users"]
    if users.count_documents({}) > 0:
        return
    now = datetime.now(timezone.utc).isoformat()
    users.insert_one(
        {
            "username": "admin",
            "password_hash": pwd_context.hash("admin123"),
            "role": "admin",
            "created_at": now,
        }
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _mongo
    _mongo = MongoClient(
        AUTH_MONGO_URI,
        serverSelectionTimeoutMS=8000,
        connectTimeoutMS=8000,
        socketTimeoutMS=15000,
    )
    try:
        _mongo.admin.command("ping")
    except Exception:
        logger.exception("auth startup: MongoDB ping failed")
        raise
    try:
        _ensure_default_admin(_mongo.get_default_database())
    except Exception:
        logger.exception("auth startup: seed atlandı, ilk girişte tekrar denenecek")
    try:
        yield
    finally:
        if _mongo is not None:
            _mongo.close()
            _mongo = None


app = FastAPI(title="Auth Service", lifespan=lifespan)


def _internal_headers(x_internal_token: Optional[str]) -> None:
    if x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid internal token")


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    expires_in: int = 86400


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/login", response_model=TokenResponse)
def login_post(
    body: LoginRequest,
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
):
    _internal_headers(x_internal_token)
    db = get_db()
    # PowerShell bazen BOM veya farklı kodlama ile gönderir; girişi normalize et.
    uname = body.username.strip().strip("\ufeff").lower()
    pwd = body.password.strip().strip("\ufeff")
    doc = db["users"].find_one({"username": uname})
    if not doc:
        logger.warning("login: kullanıcı yok, seed tekrar deneniyor")
        _ensure_default_admin(db)
        doc = db["users"].find_one({"username": uname})
    if not doc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    ph = doc.get("password_hash")
    if not ph:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    try:
        ok = pwd_context.verify(pwd, ph)
    except Exception:
        logger.exception("password verify failed")
        ok = False
    if not ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    role = doc.get("role", "viewer")
    now = datetime.now(timezone.utc)
    exp = now + timedelta(hours=24)
    payload = {
        "sub": uname,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return TokenResponse(access_token=token, expires_in=86400)


@app.get("/auth/me")
def me(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
    x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
):
    _internal_headers(x_internal_token)
    if not creds:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")
    return {"username": payload.get("sub"), "role": payload.get("role")}
