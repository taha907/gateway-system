import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
import jwt
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app import authorization
from app import request_log as reqlog

JWT_SECRET = os.getenv("JWT_SECRET", "dev_secret_change_me")
INTERNAL_TOKEN = os.getenv("INTERNAL_SERVICE_TOKEN", "dev_internal_token_change_me")
DISPATCHER_MONGO_URI = os.getenv("DISPATCHER_MONGO_URI", "mongodb://localhost:27017/dispatcher")
STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    authorization.init_mongo(DISPATCHER_MONGO_URI)
    reqlog.init_log_db(DISPATCHER_MONGO_URI)
    yield


app = FastAPI(title="Dispatcher", lifespan=lifespan)

REQUESTS_TOTAL = Counter(
    "dispatcher_requests_total",
    "Total requests received by dispatcher",
    ["method", "path", "status_code"],
)
REQUEST_DURATION = Histogram(
    "dispatcher_request_duration_seconds",
    "Request duration in seconds",
    ["method", "path"],
)


def _is_public(path: str, method: str) -> bool:
    if path in ("/health", "/metrics"):
        return True
    if path == "/api/auth/login" and method == "POST":
        return True
    return False


def _decode_jwt(auth_header: Optional[str]) -> tuple[Optional[dict], Optional[JSONResponse], str]:
    """reason: ok | missing_auth | invalid_token"""
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return (
            None,
            JSONResponse(status_code=401, content={"detail": "missing or invalid authorization"}),
            "missing_auth",
        )
    token = auth_header.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload, None, "ok"
    except jwt.PyJWTError:
        return None, JSONResponse(status_code=401, content={"detail": "invalid token"}), "invalid_token"


def _ensure_access(payload: dict, resource_path: str) -> tuple[Optional[JSONResponse], Optional[str]]:
    role = payload.get("role") or "viewer"
    if not authorization.is_request_allowed(str(role), resource_path):
        return JSONResponse(status_code=403, content={"detail": "forbidden"}), "forbidden"
    return None, None


def _decode_jwt_optional(auth_header: Optional[str]) -> Optional[dict]:
    """Sadece log için; hata fırlatmaz."""
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


async def _forward(
    request: Request,
    base_url: str,
    upstream_path: str,
    *,
    extra_headers: Optional[dict[str, str]] = None,
) -> Response:
    method = request.method
    url = f"{base_url.rstrip('/')}{upstream_path}"
    params = dict(request.query_params)
    body = await request.body()
    headers: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in ("host", "content-length"):
            continue
        headers[k] = v
    headers["X-Internal-Token"] = INTERNAL_TOKEN
    if extra_headers:
        headers.update(extra_headers)

    timeout = httpx.Timeout(connect=10.0, read=120.0, write=120.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            upstream = await client.request(
                method,
                url,
                params=params,
                content=body if body else None,
                headers=headers,
            )
        except httpx.ConnectError:
            return JSONResponse(
                status_code=503,
                content={"detail": "upstream service unavailable"},
            )
        except httpx.TimeoutException:
            return JSONResponse(
                status_code=504,
                content={"detail": "upstream service timeout"},
            )

    out_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() in ("content-type", "content-length")
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=out_headers)


def _service_base_url(service: str) -> str:
    if service == "ingest":
        return os.getenv("INGEST_SERVICE_URL", "http://telemetry-ingest:8000")
    if service == "query":
        return os.getenv("QUERY_SERVICE_URL", "http://telemetry-query:8000")
    if service == "auth":
        return os.getenv("AUTH_SERVICE_URL", "http://auth:8000")
    raise ValueError(f"unknown service: {service}")


@app.middleware("http")
async def dispatch_auth_and_log(request: Request, call_next):
    start = time.perf_counter()
    path = request.url.path
    method = request.method
    query_string = request.url.query or ""
    client = request.client
    client_host = client.host if client else None

    user: Optional[str] = None
    role: Optional[str] = None
    auth_result = "bypass"
    early_response: Optional[Response] = None

    if path.startswith("/api/") or path.startswith("/admin/"):
        if _is_public(path, method):
            auth_result = "public"
        else:
            payload, err, jwt_reason = _decode_jwt(request.headers.get("authorization"))
            if err:
                early_response = err
                auth_result = jwt_reason
            else:
                assert payload is not None
                user = str(payload.get("sub") or "")
                role = str(payload.get("role") or "viewer")
                deny, _deny_reason = _ensure_access(payload, path)
                if deny:
                    early_response = deny
                    auth_result = "forbidden"
                else:
                    auth_result = "ok"

    if early_response is not None:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        reqlog.log_request(
            method=method,
            path=path,
            query_string=query_string,
            status_code=early_response.status_code,
            latency_ms=elapsed_ms,
            user=user or None,
            role=role,
            client_host=client_host,
            auth_result=auth_result,
        )
        return early_response

    try:
        resp = await call_next(request)
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        reqlog.log_request(
            method=method,
            path=path,
            query_string=query_string,
            status_code=500,
            latency_ms=elapsed_ms,
            user=user,
            role=role,
            client_host=client_host,
            auth_result="exception",
            error=repr(e),
        )
        raise

    elapsed_ms = (time.perf_counter() - start) * 1000.0
    if user is None:
        pl = _decode_jwt_optional(request.headers.get("authorization"))
        if pl:
            user = str(pl.get("sub") or "") or None
            role = str(pl.get("role") or "viewer")

    reqlog.log_request(
        method=method,
        path=path,
        query_string=query_string,
        status_code=resp.status_code,
        latency_ms=elapsed_ms,
        user=user,
        role=role,
        client_host=client_host,
        auth_result=auth_result,
    )
    return resp


@app.get("/ui")
async def telemetry_ui():
    """Etkileşimli telemetri arayüzü (tek giriş noktası: Dispatcher)."""
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return JSONResponse(status_code=404, content={"detail": "ui not found"})
    return FileResponse(index)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/logs", response_class=HTMLResponse)
async def admin_logs_html():
    rows = [reqlog.public_log_row(d) for d in reqlog.recent_logs(200)]
    tr = "".join(
        f"<tr><td>{r.get('ts','')}</td><td>{r.get('method','')}</td><td>{r.get('path','')}</td>"
        f"<td>{r.get('q','')}</td><td>{r.get('status','')}</td><td>{r.get('ms','')}</td>"
        f"<td>{r.get('auth','')}</td><td>{r.get('user','')}</td>"
        f"<td>{r.get('ip','')}</td></tr>"
        for r in rows
    )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Dispatcher logları</title>
<style>table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ccc;padding:6px;font-size:12px}}th{{text-align:left}}</style>
</head><body>
<h1>Dispatcher istek logları</h1>
<table><thead><tr><th>Zaman</th><th>Metot</th><th>Yol</th><th>Query</th><th>HTTP</th><th>ms</th><th>Auth</th><th>Kullanıcı</th><th>İstemci</th></tr></thead>
<tbody>{tr}</tbody></table>
<p><a href="/admin/logs/json">JSON</a></p>
</body></html>"""
    return HTMLResponse(content=html)


@app.get("/admin/logs/json")
async def admin_logs_json():
    return {"items": [reqlog.public_log_row(d) for d in reqlog.recent_logs(200)]}


@app.api_route("/api/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def route_auth(path: str, request: Request):
    start = time.perf_counter()
    resp: Optional[Response] = None
    try:
        resp = await _forward(request, _service_base_url("auth"), f"/auth/{path}")
        return resp
    finally:
        elapsed = time.perf_counter() - start
        status_code = getattr(resp, "status_code", 500)
        REQUESTS_TOTAL.labels(request.method, "/api/auth/{path}", str(status_code)).inc()
        REQUEST_DURATION.labels(request.method, "/api/auth/{path}").observe(elapsed)


@app.api_route("/api/telemetry/ingest/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def route_ingest(path: str, request: Request):
    start = time.perf_counter()
    resp: Optional[Response] = None
    try:
        resp = await _forward(request, _service_base_url("ingest"), f"/{path}")
        return resp
    finally:
        elapsed = time.perf_counter() - start
        status_code = getattr(resp, "status_code", 500)
        REQUESTS_TOTAL.labels(request.method, "/api/telemetry/ingest/{path}", str(status_code)).inc()
        REQUEST_DURATION.labels(request.method, "/api/telemetry/ingest/{path}").observe(elapsed)


@app.api_route("/api/telemetry/query/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def route_query(path: str, request: Request):
    start = time.perf_counter()
    resp: Optional[Response] = None
    try:
        resp = await _forward(request, _service_base_url("query"), f"/{path}")
        return resp
    finally:
        elapsed = time.perf_counter() - start
        status_code = getattr(resp, "status_code", 500)
        REQUESTS_TOTAL.labels(request.method, "/api/telemetry/query/{path}", str(status_code)).inc()
        REQUEST_DURATION.labels(request.method, "/api/telemetry/query/{path}").observe(elapsed)
