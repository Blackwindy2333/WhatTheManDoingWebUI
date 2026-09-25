"""FastAPI app: public monitor UI + admin API for WhatTheManDoing WebUI."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)

from server.aggregate import DeviceAggregator, default_fetcher
from server.auth import AdminAuthenticator, extract_client_ip
from server.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_STATE_PATH,
    ConfigError,
    DeviceConfig,
    WebUIConfig,
    config_to_admin_dict,
    config_to_public_dict,
    ensure_config,
    load_config,
    save_config,
    validate_config_dict,
)
from server.devices import (
    delete_device,
    device_to_dict,
    fetch_device_history,
    test_device_connection,
    upsert_device,
)
from server.state import StateStore, utc_now_iso

ROOT = Path(__file__).resolve().parents[1]

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


def create_app(
    config: WebUIConfig | None = None,
    *,
    config_path: Path | str | None = None,
    state_store: StateStore | None = None,
    aggregator: DeviceAggregator | None = None,
    authenticator: AdminAuthenticator | None = None,
    start_aggregator: bool = False,
) -> FastAPI:
    cfg_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if config is None:
        cfg = ensure_config(cfg_path)
    else:
        cfg = config

    store = state_store or StateStore(Path(cfg_path).parent / "state.json" if config_path else DEFAULT_STATE_PATH)
    auth = authenticator or AdminAuthenticator(cfg, store)
    agg = aggregator or DeviceAggregator(cfg, fetcher=default_fetcher)

    app = FastAPI(title="WhatTheManDoing WebUI", version="1.0.0")
    app.state.config = cfg
    app.state.config_path = cfg_path
    app.state.state_store = store
    app.state.auth = auth
    app.state.aggregator = agg

    def _client_ip(request: Request) -> str:
        return extract_client_ip(
            request.headers.get("x-forwarded-for"),
            request.headers.get("x-real-ip"),
            request.client.host if request.client else None,
            app.state.config.trust_proxy,
        )

    def _bearer(authorization: str | None) -> str | None:
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return None

    def _require_admin(request: Request, authorization: str | None = Header(default=None)) -> str:
        ip = _client_ip(request)
        banned, expires_at = auth.bans.is_banned(ip)
        if banned:
            raise HTTPException(
                status_code=403,
                detail=envelope(
                    CODE_FORBIDDEN,
                    f"IP banned until {expires_at}",
                    {"banned": True, "expires_at": expires_at},
                ),
            )
        session = auth.require_session(authorization)
        if session is None:
            raise HTTPException(
                status_code=401,
                detail=envelope(CODE_UNAUTHORIZED, "admin session required"),
            )
        return ip

    def _reload_config(new_cfg: WebUIConfig) -> None:
        app.state.config = new_cfg
        auth.update_config(new_cfg)
        agg.update_config(new_cfg)
        save_config(new_cfg, app.state.config_path)

    @app.middleware("http")
    async def visit_and_ban_middleware(request: Request, call_next):
        ip = _client_ip(request)
        banned, expires_at = auth.bans.is_banned(ip)
        if banned:
            return JSONResponse(
                status_code=403,
                content=envelope(
                    CODE_FORBIDDEN,
                    f"IP banned until {expires_at}",
                    {"banned": True, "expires_at": expires_at},
                ),
            )
        # Count page / API visits (not static asset noise if possible)
        path = request.url.path
        if not path.startswith("/assets/") and path not in ("/favicon.ico",):
            day = utc_now_iso()[:10]
            store.record_visit(day)
        response = await call_next(request)
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

    # ----- admin auth ------------------------------------------------------

    @app.post("/api/admin/login")
    async def admin_login(request: Request) -> JSONResponse:
        ip = _client_ip(request)
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        token = ""
        if isinstance(body, dict):
            token = str(body.get("token") or "")
        result = auth.login(ip, token)
        if not result.get("ok"):
            status = int(result.get("status") or 401)
            code = CODE_UNAUTHORIZED if status == 401 else (
                CODE_FORBIDDEN if status == 403 else CODE_RATE_LIMITED
            )
            payload = {
                "banned": result.get("banned", False),
                "expires_at": result.get("expires_at"),
                "failures": result.get("failures"),
            }
            return JSONResponse(status_code=status, content=envelope(code, str(result.get("message")), payload))
        return ok(
            {
                "session_token": result["session_token"],
                "expires_at": result["expires_at"],
            }
        )

    @app.post("/api/admin/logout")
    async def admin_logout(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        ip = _client_ip(request)
        auth.logout(ip, _bearer(authorization))
        return ok({"logged_out": True})

    @app.get("/api/admin/me")
    def admin_me(ip: str = Depends(_require_admin)) -> JSONResponse:
        return ok({"authenticated": True, "ip": ip})

    # ----- admin config ----------------------------------------------------

    @app.get("/api/admin/config")
    def admin_get_config(ip: str = Depends(_require_admin)) -> JSONResponse:
        return ok(config_to_admin_dict(app.state.config))

    @app.patch("/api/admin/config")
    async def admin_patch_config(
        request: Request,
        ip: str = Depends(_require_admin),
    ) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return fail(400, CODE_BAD_REQUEST, "invalid JSON body")
        if not isinstance(body, dict):
            return fail(400, CODE_BAD_REQUEST, "body must be an object")
        try:
            merged = _merge_settings(app.state.config, body)
            _reload_config(merged)
        except ConfigError as exc:
            return fail(400, CODE_BAD_REQUEST, str(exc))
        store.append_audit(ip, "update_config", ",".join(sorted(body.keys())))
        return ok(config_to_admin_dict(app.state.config))

    # ----- admin devices ---------------------------------------------------

    @app.get("/api/admin/devices")
    def admin_list_devices(ip: str = Depends(_require_admin)) -> JSONResponse:
        snapshots = {s.id: s.to_public_dict() for s in agg.get_snapshots()}
        devices = []
        for device in app.state.config.devices:
            item = device_to_dict(device)
            item["health"] = snapshots.get(device.id)
            devices.append(item)
        return ok({"devices": devices})

    @app.post("/api/admin/devices")
    async def admin_create_device(
        request: Request,
        ip: str = Depends(_require_admin),
    ) -> JSONResponse:
        body = await _json_body(request)
        if body is None:
            return fail(400, CODE_BAD_REQUEST, "invalid JSON body")
        try:
            if any(d.id == str(body.get("id") or "") for d in app.state.config.devices):
                raise ConfigError(f"device id already exists: {body.get('id')}")
            merged = upsert_device(app.state.config, body)
            _reload_config(merged)
        except ConfigError as exc:
            return fail(400, CODE_BAD_REQUEST, str(exc))
        store.append_audit(ip, "device_create", str(body.get("id")))
        return ok(_device_payload(str(body.get("id"))))

    @app.put("/api/admin/devices/{device_id}")
    async def admin_update_device(
        device_id: str,
        request: Request,
        ip: str = Depends(_require_admin),
    ) -> JSONResponse:
        body = await _json_body(request)
        if body is None:
            return fail(400, CODE_BAD_REQUEST, "invalid JSON body")
        if not any(d.id == device_id for d in app.state.config.devices):
            return fail(404, CODE_NOT_FOUND, f"device not found: {device_id}")
        try:
            merged = upsert_device(app.state.config, body, replace_id=device_id)
            _reload_config(merged)
        except ConfigError as exc:
            return fail(400, CODE_BAD_REQUEST, str(exc))
        store.append_audit(ip, "device_update", device_id)
        return ok(_device_payload(device_id))

    @app.delete("/api/admin/devices/{device_id}")
    def admin_delete_device(
        device_id: str,
        ip: str = Depends(_require_admin),
    ) -> JSONResponse:
        try:
            merged = delete_device(app.state.config, device_id)
            _reload_config(merged)
        except ConfigError as exc:
            return fail(404, CODE_NOT_FOUND, str(exc))
        store.append_audit(ip, "device_delete", device_id)
        return ok({"deleted": device_id})

    @app.get("/api/admin/devices/export")
    def admin_export_devices(ip: str = Depends(_require_admin)) -> JSONResponse:
        payload = {
            "exported_at": utc_now_iso(),
            "devices": [device_to_dict(d) for d in app.state.config.devices],
        }
        store.append_audit(ip, "device_export", f"count={len(payload['devices'])}")
        return JSONResponse(
            content=envelope(CODE_OK, "ok", payload),
            headers={"Content-Disposition": "attachment; filename=devices.json"},
        )

    @app.post("/api/admin/devices/{device_id}/test")
    async def admin_test_device(
        device_id: str,
        ip: str = Depends(_require_admin),
    ) -> JSONResponse:
        device = _find_device(device_id)
        report = await test_device_connection(device)
        store.append_audit(ip, "device_test", f"{device_id} ok={report.get('ok')}")
        return ok(report)

    # ----- admin stats / bans / audit --------------------------------------

    @app.get("/api/admin/stats")
    def admin_stats(ip: str = Depends(_require_admin)) -> JSONResponse:
        return ok(store.get_visits())

    @app.get("/api/admin/bans")
    def admin_bans(ip: str = Depends(_require_admin)) -> JSONResponse:
        store.prune_expired_bans(utc_now_iso())
        return ok({"bans": store.list_bans()})

    @app.delete("/api/admin/bans/{ban_ip}")
    def admin_unban(
        ban_ip: str,
        ip: str = Depends(_require_admin),
    ) -> JSONResponse:
        removed = store.unban_ip(ban_ip)
        store.append_audit(ip, "unban", ban_ip)
        return ok({"unbanned": removed, "ip": ban_ip})

    @app.get("/api/admin/audit")
    def admin_audit(
        limit: int = Query(default=50, ge=1, le=200),
        ip: str = Depends(_require_admin),
    ) -> JSONResponse:
        return ok({"entries": store.list_audit(limit=limit)})

    def _find_device(device_id: str) -> DeviceConfig:
        for device in app.state.config.devices:
            if device.id == device_id:
                return device
        raise HTTPException(
            status_code=404,
            detail=envelope(CODE_NOT_FOUND, f"device not found: {device_id}"),
        )

    def _device_payload(device_id: str) -> dict[str, Any]:
        for device in app.state.config.devices:
            if device.id == device_id:
                item = device_to_dict(device)
                for snap in agg.get_snapshots():
                    if snap.id == device_id:
                        item["health"] = snap.to_public_dict()
                        break
                return item
        return {}

    async def _json_body(request: Request) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            return None
        return body if isinstance(body, dict) else None

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


def _merge_serve(config: WebUIConfig, body: dict[str, Any]) -> dict[str, Any]:
    if isinstance(body.get("serve"), dict):
        serve_body = body["serve"]
        return {
            "mode": serve_body.get("mode", config.serve.mode),
            "host": serve_body.get("host", config.serve.host),
            "port": serve_body.get("port", config.serve.port),
            "ssl_certfile": serve_body.get("ssl_certfile", config.serve.ssl_certfile),
            "ssl_keyfile": serve_body.get("ssl_keyfile", config.serve.ssl_keyfile),
        }
    serve = {
        "mode": config.serve.mode,
        "host": config.serve.host,
        "port": config.serve.port,
        "ssl_certfile": config.serve.ssl_certfile,
        "ssl_keyfile": config.serve.ssl_keyfile,
    }
    for src_key, dest_key in (
        ("serve_mode", "mode"),
        ("serve_host", "host"),
        ("serve_port", "port"),
        ("ssl_certfile", "ssl_certfile"),
        ("ssl_keyfile", "ssl_keyfile"),
    ):
        if src_key in body:
            serve[dest_key] = body[src_key]
    return serve


def _merge_settings(config: WebUIConfig, body: dict[str, Any]) -> WebUIConfig:
    """Merge partial admin settings into a validated WebUIConfig."""
    data = {
        "version": body.get("version", config.version),
        "admin_token": body.get("admin_token", config.admin_token) or config.admin_token,
        "session_ttl_seconds": body.get("session_ttl_seconds", config.session_ttl_seconds),
        "login_max_failures": body.get("login_max_failures", config.login_max_failures),
        "ban_duration_hours": body.get("ban_duration_hours", config.ban_duration_hours),
        "login_rate_limit_per_minute": body.get(
            "login_rate_limit_per_minute", config.login_rate_limit_per_minute
        ),
        "refresh_interval_seconds": body.get(
            "refresh_interval_seconds", config.refresh_interval_seconds
        ),
        "page_title": body.get("page_title", config.page_title),
        "page_subtitle": body.get("page_subtitle", config.page_subtitle),
        "show_history": body.get("show_history", config.show_history),
        "devices_per_page": body.get("devices_per_page", config.devices_per_page),
        "trust_proxy": body.get("trust_proxy", config.trust_proxy),
        "default_device_scheme": body.get(
            "default_device_scheme", config.default_device_scheme
        ),
        "serve": _merge_serve(config, body),
        "devices": [device_to_dict(d) for d in config.devices],
    }
    return validate_config_dict(data)
