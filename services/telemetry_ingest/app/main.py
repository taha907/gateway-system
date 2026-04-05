import os
from datetime import datetime, timezone
from typing import Annotated, Optional

import httpx
import jwt
from bson import ObjectId
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from pymongo import MongoClient

JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change_me")
INTERNAL_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "dev_internal_token_change_me")
INGEST_MONGO_URI = os.getenv("INGEST_MONGO_URI", "mongodb://localhost:27017/ingest")
QUERY_SERVICE_URL = os.getenv("QUERY_SERVICE_URL", "http://telemetry-query:8000")

security = HTTPBearer(auto_error=False)
app = FastAPI(title="Telemetry Ingest")


def get_db():
    client = MongoClient(INGEST_MONGO_URI, serverSelectionTimeoutMS=5000)
    return client.get_default_database()


def require_internal(x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")]):
    if x_internal_token != INTERNAL_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="forbidden")


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
    return {"service": "telemetry-ingest", "ok": True}


class TelemetryIn(BaseModel):
    satellite_id: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    value: float
    unit: str = "raw"
    recorded_at: Optional[str] = None


def _replicate_to_query(doc: dict, ingest_id: str) -> None:
    payload = {
        "ingest_id": ingest_id,
        "satellite_id": doc["satellite_id"],
        "metric": doc["metric"],
        "value": doc["value"],
        "unit": doc["unit"],
        "recorded_at": doc["recorded_at"],
        "ingested_by": doc.get("ingested_by"),
    }
    try:
        httpx.post(
            f"{QUERY_SERVICE_URL.rstrip('/')}/internal/replicate",
            json=payload,
            headers={"X-Internal-Token": INTERNAL_TOKEN},
            timeout=5.0,
        )
    except httpx.HTTPError:
        pass


@app.post("/telemetry", status_code=201)
def create_telemetry(
    body: TelemetryIn,
    _: Annotated[None, Depends(require_internal)],
    claims: Annotated[dict, Depends(require_jwt)],
):
    db = get_db()
    ts = body.recorded_at or datetime.now(timezone.utc).isoformat()
    doc = {
        "satellite_id": body.satellite_id,
        "metric": body.metric,
        "value": body.value,
        "unit": body.unit,
        "recorded_at": ts,
        "ingested_by": claims.get("sub"),
    }
    res = db["telemetry"].insert_one(doc)
    ingest_id = str(res.inserted_id)
    _replicate_to_query(doc, ingest_id)
    return {"id": ingest_id, "recorded_at": ts}


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
