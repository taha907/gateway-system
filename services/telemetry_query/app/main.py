import os
from typing import Annotated, Any, Optional

import jwt
from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from pymongo import MongoClient

JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change_me")
INTERNAL_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "dev_internal_token_change_me")
QUERY_MONGO_URI = os.getenv("QUERY_MONGO_URI", "mongodb://localhost:27017/query")

security = HTTPBearer(auto_error=False)
app = FastAPI(title="Telemetry Query")


def get_db():
    client = MongoClient(QUERY_MONGO_URI, serverSelectionTimeoutMS=5000)
    return client.get_default_database()


def require_internal(x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")]):
    if x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


class ReplicateIn(BaseModel):
    ingest_id: str = Field(min_length=1)
    satellite_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    value: float
    unit: str = "raw"
    recorded_at: str
    ingested_by: Optional[str] = None


@app.post("/internal/replicate", status_code=201)
def internal_replicate(
    body: ReplicateIn,
    _: Annotated[None, Depends(require_internal)],
):
    """Ingest servisinden JSON ile çoğaltma (sadece iç ağ token)."""
    db = get_db()
    db["telemetry"].insert_one(
        {
            "ingest_id": body.ingest_id,
            "satellite_id": body.satellite_id,
            "metric": body.metric,
            "value": body.value,
            "unit": body.unit,
            "recorded_at": body.recorded_at,
            "ingested_by": body.ingested_by,
        }
    )
    return {"ok": True}


def require_jwt(
    creds: Annotated[Optional[HTTPAuthorizationCredentials], Depends(security)],
):
    if not creds:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    try:
        return jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ping")
def ping(
    _: Annotated[None, Depends(require_internal)],
    __: Annotated[dict, Depends(require_jwt)],
):
    return {"service": "telemetry-query", "ok": True}


@app.get("/telemetry")
def list_telemetry(
    _: Annotated[None, Depends(require_internal)],
    __: Annotated[dict, Depends(require_jwt)],
    satellite_id: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=500),
):
    db = get_db()
    q: dict[str, Any] = {}
    if satellite_id:
        q["satellite_id"] = satellite_id
    cur = db["telemetry"].find(q).sort("recorded_at", -1).limit(limit)
    out = []
    for doc in cur:
        out.append(
            {
                "satellite_id": doc["satellite_id"],
                "metric": doc["metric"],
                "value": doc["value"],
                "unit": doc["unit"],
                "recorded_at": doc["recorded_at"],
            }
        )
    return {"items": out}


@app.get("/telemetry/{telemetry_id}")
def get_one(
    telemetry_id: str,
    _: Annotated[None, Depends(require_internal)],
    __: Annotated[dict, Depends(require_jwt)],
):
    if not ObjectId.is_valid(telemetry_id):
        raise HTTPException(status_code=400, detail="invalid id")
    db = get_db()
    doc = db["telemetry"].find_one({"_id": ObjectId(telemetry_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="not found")
    doc.pop("_id", None)
    return {
        "id": telemetry_id,
        "satellite_id": doc["satellite_id"],
        "metric": doc["metric"],
        "value": doc["value"],
        "unit": doc["unit"],
        "recorded_at": doc["recorded_at"],
    }
