import os
from typing import Optional

from pymongo import MongoClient
from pymongo.database import Database

_mongo: Optional[MongoClient] = None
_db: Optional[Database] = None


def init_mongo(uri: str) -> bool:
    global _mongo, _db
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        _mongo = client
        _db = client.get_default_database()
        _seed_if_empty(_db)
        return True
    except Exception:
        _mongo = None
        _db = None
        return False


def _seed_if_empty(db: Database) -> None:
    col = db["role_permissions"]
    if col.count_documents({}) > 0:
        return
    col.insert_many(
        [
            {
                "role": "admin",
                "prefixes": [
                    "/api/telemetry/ingest",
                    "/api/telemetry/query",
                    "/api/auth",
                    "/admin",
                ],
            },
            {
                "role": "operator",
                "prefixes": ["/api/telemetry/ingest", "/api/telemetry/query"],
            },
            {"role": "viewer", "prefixes": ["/api/telemetry/query"]},
        ]
    )


def is_request_allowed(role: str, resource_path: str) -> bool:
    if _db is None:
        return False
    doc = _db["role_permissions"].find_one({"role": role})
    if not doc:
        return False
    for p in doc.get("prefixes", []):
        if resource_path == p or resource_path.startswith(p + "/") or resource_path.startswith(p + "?"):
            return True
    return False
