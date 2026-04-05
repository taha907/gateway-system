from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import MongoClient

_db: Optional[Any] = None


def init_log_db(uri: str) -> None:
    global _db
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        _db = client.get_default_database()
        _db["request_logs"].create_index([("ts", -1)])
    except Exception:
        _db = None


def log_request(
    *,
    method: str,
    path: str,
    query_string: str,
    status_code: int,
    latency_ms: float,
    user: Optional[str] = None,
    role: Optional[str] = None,
    client_host: Optional[str] = None,
    auth_result: str = "n/a",
    detail: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """
    Dispatcher trafik kaydı (MongoDB, JSON belge).
    auth_result: ok | public | missing_auth | invalid_token | forbidden | exception | bypass
    """
    if _db is None:
        return
    doc = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "query_string": query_string or "",
        "status_code": status_code,
        "latency_ms": round(latency_ms, 1),
        "user": user,
        "role": role,
        "client_host": client_host,
        "auth_result": auth_result,
    }
    if detail is not None:
        doc["detail"] = detail
    if error is not None:
        doc["error"] = error
    try:
        _db["request_logs"].insert_one(doc)
    except Exception:
        pass


def recent_logs(limit: int = 200) -> list[dict]:
    if _db is None:
        return []
    cur = _db["request_logs"].find().sort("ts", -1).limit(limit)
    out = []
    for doc in cur:
        doc.pop("_id", None)
        out.append(doc)
    return out


def public_log_row(doc: dict) -> dict:
    """API ve /admin/logs için sade satır (öğrenci projesi düzeyinde alanlar)."""
    row = {
        "ts": doc.get("ts"),
        "method": doc.get("method"),
        "path": doc.get("path"),
        "q": doc.get("query_string") or "",
        "status": doc.get("status_code"),
        "ms": doc.get("latency_ms"),
        "auth": doc.get("auth_result"),
        "user": doc.get("user") or "",
        "ip": doc.get("client_host") or "",
    }
    err = doc.get("error")
    if err:
        row["err"] = str(err)[:300]
    return row
