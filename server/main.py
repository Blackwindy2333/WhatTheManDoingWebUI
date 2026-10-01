"""FastAPI app: public monitor UI for WhatTheManDoing WebUI.

Configuration is file-only (config.json). There is no admin API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from server.aggregate import DeviceAggregator, default_fetcher
from server.config import (
    DEFAULT_CONFIG_PATH,
    DeviceConfig,
    WebUIConfig,
    config_to_public_dict,
    ensure_config,
)
from server.devices import UpstreamAuthError, UpstreamHistoryError, fetch_device_history
from server.log_setup import get_logger, setup_logging

logger = get_logger("http")

ROOT = Path(__file__).resolve().parents[1]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

CODE_OK = 0
CODE_BAD_REQUEST = 40000
CODE_UNAUTHORIZED = 40100
CODE_FORBIDDEN = 40300
CODE_NOT_FOUND = 40400
CODE_RATE_LIMITED = 42900
CODE_INTERNAL = 50000


def envelope(code: int, message: str, data: Any = None) -> dict[str, Any]:
    return {"code": code, "message": message, "data": data}


def ok(data: Any = None, message: str = "ok") -> JSONResponse:
    return JSONResponse(content=envelope(CODE_OK, message, data))


def fail(status_code: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=envelope(code, message, None))


def extract_client_ip(
    x_forwarded_for: str | None,
    x_real_ip: str | None,
    client_host: str | None,
    trust_proxy: bool,
) -> str:
    if trust_proxy:
        if x_forwarded_for:
            first = x_forwarded_for.split(",")[0].strip()
            if first:
                return first
        if x_real_ip and x_real_ip.strip():
            return x_real_ip.strip()
    return client_host or "unknown"


def create_app(
    config: WebUIConfig | None = None,
    *,
    config_path: Path | str | None = None,
    aggregator: DeviceAggregator | None = None,
    start_aggregator: bool = False,
) -> FastAPI:
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if config is None:
        cfg = ensure_config(cfg_path)
    else:
        cfg = config

    agg = aggregator or DeviceAggregator(cfg, fetcher=default_fetcher)

    # File + console logging (logs/ is gitignored)
    log_base = Path(cfg_path).parent if config_path else ROOT
    try:
        log_path = setup_logging(cfg.log, base=log_base, force=True)
        logger.info("logging configured path=%s level=%s", log_path, cfg.log.level)
    except OSError:
        logging.getLogger("webui").exception("failed to configure file logging")

    app = FastAPI(title="WhatTheManDoing WebUI", version="2.0.0")
    app.state.config = cfg
    app.state.config_path = cfg_path
    app.state.aggregator = agg

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail and "message" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        if exc.status_code == 401:
            code = CODE_UNAUTHORIZED
        elif exc.status_code == 403:
            code = CODE_FORBIDDEN
        elif exc.status_code == 404:
            code = CODE_NOT_FOUND
        elif exc.status_code == 429:
            code = CODE_RATE_LIMITED
        elif exc.status_code >= 500:
            code = CODE_INTERNAL
        else:
            code = CODE_BAD_REQUEST
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(code, str(detail), None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content=envelope(CODE_BAD_REQUEST, "validation error", {"errors": exc.errors()}),
        )

    def _client_ip(request: Request) -> str:
        return extract_client_ip(
            request.headers.get("x-forwarded-for"),
            request.headers.get("x-real-ip"),
            request.client.host if request.client else None,
            app.state.config.trust_proxy,
        )

    @app.middleware("http")
    async def access_log_middleware(request: Request, call_next):
        started = time.perf_counter()
        ip = _client_ip(request)
        path = request.url.path
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception(
                "request error %s %s ip=%s duration_ms=%s",
                request.method,
                path,
                ip,
                duration_ms,
            )
            raise
        duration_ms = int((time.perf_counter() - started) * 1000)
        if app.state.config.log.access_log:
            is_static = path in ("/", "/styles.css", "/app.js") or path.startswith("/assets/")
            level = logging.DEBUG if is_static else logging.INFO
            logger.log(
                level,
                "request %s %s -> %s duration_ms=%s ip=%s",
                request.method,
                path,
                response.status_code,
                duration_ms,
                ip,
            )
        return response

    # ----- static front-end -------------------------------------------------
    index_path = ROOT / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def index() -> FileResponse:
        return FileResponse(index_path)

    @app.get("/styles.css")
    def styles() -> FileResponse:
        return FileResponse(ROOT / "styles.css", media_type="text/css")

    @app.get("/app.js")
    def app_js() -> FileResponse:
        return FileResponse(ROOT / "app.js", media_type="text/javascript")

    # ----- public API ------------------------------------------------------

    @app.get("/api/public/config")
    def public_config() -> JSONResponse:
        return ok(config_to_public_dict(app.state.config))

    @app.get("/api/public/probe")
    def public_probe(request: Request) -> JSONResponse:
        """Diagnose reverse-proxy / tunnel headers and scheme detection."""
        headers = {k.lower(): v for k, v in request.headers.items()}
        return ok(
            {
                "client_host": request.client.host if request.client else None,
                "resolved_ip": _client_ip(request),
                "method": request.method,
                "path": request.url.path,
                "scheme": request.url.scheme,
                "trust_proxy": app.state.config.trust_proxy,
                "forwarded": {
                    "x_forwarded_for": headers.get("x-forwarded-for"),
                    "x_forwarded_proto": headers.get("x-forwarded-proto"),
                    "x_real_ip": headers.get("x-real-ip"),
                    "x_forwarded_host": headers.get("x-forwarded-host"),
                },
                "host": headers.get("host"),
                "user_agent": headers.get("user-agent"),
                "note": (
                    "If scheme is http but you opened https://, TLS is terminated upstream "
                    "(good). If this request never arrives when users see Invalid HTTP, "
                    "HTTPS/TLS is being sent directly to this HTTP port."
                ),
            }
        )

    @app.get("/api/public/devices")
    def public_devices() -> JSONResponse:
        return ok({"devices": agg.get_public_devices()})

    @app.get("/api/public/devices/{device_id}/history")
    async def public_history(
        device_id: str,
        limit: int = Query(default=30, ge=1, le=200),
    ) -> JSONResponse:
        device = _find_device(device_id)
        try:
            data = await fetch_device_history(device, limit=limit)
        except UpstreamAuthError as exc:
            return fail(401, CODE_UNAUTHORIZED, str(exc))
        except UpstreamHistoryError as exc:
            return fail(502, CODE_INTERNAL, f"history fetch failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            return fail(502, CODE_INTERNAL, f"history fetch failed: {exc}")
        return ok(data)

    @app.get("/api/public/stream")
    async def public_stream(request: Request) -> StreamingResponse:
        queue = agg.subscribe()

        async def event_gen():
            try:
                # Initial snapshot
                yield _sse({"type": "devices", "data": agg.get_public_devices()})
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=15.0)
                        yield _sse(message)
                    except asyncio.TimeoutError:
                        yield _sse({"type": "ping", "data": {"ts": utc_now_iso()}})
            finally:
                agg.unsubscribe(queue)

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    def _find_device(device_id: str) -> DeviceConfig:
        for device in app.state.config.devices:
            if device.id == device_id:
                return device
        raise HTTPException(
            status_code=404,
            detail=envelope(CODE_NOT_FOUND, f"device not found: {device_id}"),
        )

    if start_aggregator:
        @app.on_event("startup")
        async def _startup() -> None:
            await agg.start()

        @app.on_event("shutdown")
        async def _shutdown() -> None:
            await agg.stop()

    return app


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
