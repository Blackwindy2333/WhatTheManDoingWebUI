"""Aggregate device status by polling configured WhatTheManDoing APIs."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from server.config import DeviceConfig, WebUIConfig
from server.log_setup import get_logger

logger = get_logger("aggregate")

FetchResult = dict[str, Any]
Fetcher = Callable[[DeviceConfig], Awaitable[FetchResult]]


@dataclass
class DeviceSnapshot:
    id: str
    name: str
    enabled: bool
    online: bool = False
    status: str = "unknown"
    app: dict[str, Any] | None = None
    timestamp: str | None = None
    latency_ms: int | None = None
    http_status: int | None = None
    error: str | None = None
    updated_at: float = field(default_factory=time.time)

    def to_public_dict(self) -> dict[str, Any]:
        online = self.online and self.enabled
        healthy = (
            online
            and self.error is None
            and self.status not in ("stopped", "offline")
        )
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "online": online,
            "status": self.status,
            "app": self.app,
            "timestamp": self.timestamp,
            "latency_ms": self.latency_ms,
            "http_status": self.http_status,
            "error": self.error,
            "updated_at": self.updated_at,
            "healthy": healthy,
        }


def normalize_api_url(base: str) -> str:
    return base.rstrip("/")


async def default_fetcher(
    device: DeviceConfig,
    client: httpx.AsyncClient | None = None,
    timeout: float = 5.0,
) -> FetchResult:
    """Fetch current status from a device API. Prefer /devices/{id}, fall back to /devices then /status."""
    base = normalize_api_url(device.api_base_url)
    headers = {"Accept": "application/json"}
    if device.viewer_token:
        headers["Authorization"] = f"Bearer {device.viewer_token}"

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=timeout)
    assert client is not None
    started = time.perf_counter()
    try:
        candidates = [f"{base}/devices/{device.id}", f"{base}/devices", f"{base}/status"]
        last_error = "no response"
        last_http: int | None = None
        for url in candidates:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                last_error = str(exc) or exc.__class__.__name__
                continue
            last_http = resp.status_code
            latency_ms = int((time.perf_counter() - started) * 1000)
            if resp.status_code == 401:
                return {
                    "ok": False,
                    "http_status": 401,
                    "latency_ms": latency_ms,
                    "error": "unauthorized (viewer_token?)",
                }
            if resp.status_code == 404 and url != candidates[-1]:
                last_error = "not found"
                continue
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "http_status": resp.status_code,
                    "latency_ms": latency_ms,
                    "error": f"HTTP {resp.status_code}",
                }
            try:
                body = resp.json()
            except ValueError:
                return {
                    "ok": False,
                    "http_status": resp.status_code,
                    "latency_ms": latency_ms,
                    "error": "invalid JSON",
                }
            data = body.get("data") if isinstance(body, dict) else None
            code = body.get("code") if isinstance(body, dict) else None
            if code not in (0, None):
                return {
                    "ok": False,
                    "http_status": resp.status_code,
                    "latency_ms": latency_ms,
                    "error": str(body.get("message") or f"code {code}"),
                }
            snapshot_data = _extract_device_payload(data, device.id)
            return {
                "ok": True,
                "http_status": resp.status_code,
                "latency_ms": latency_ms,
                "error": None,
                "payload": snapshot_data,
            }
        return {
            "ok": False,
            "http_status": last_http,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": last_error,
        }
    finally:
        if owns_client:
            await client.aclose()


def _extract_device_payload(data: Any, device_id: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    # /devices/{id} or /status returns device object directly
    if "status" in data or "app" in data or "device_id" in data:
        return data
    devices = data.get("devices")
    if isinstance(devices, list):
        for item in devices:
            if isinstance(item, dict) and item.get("device_id") == device_id:
                return item
        if len(devices) == 1 and isinstance(devices[0], dict):
            return devices[0]
        # Multi-device list without a match — do not fabricate a device
        return {}
    return data


def build_snapshot(device: DeviceConfig, result: FetchResult) -> DeviceSnapshot:
    snap = DeviceSnapshot(id=device.id, name=device.name, enabled=device.enabled)
    snap.latency_ms = result.get("latency_ms")
    snap.http_status = result.get("http_status")
    snap.updated_at = time.time()
    if not result.get("ok"):
        snap.online = False
        snap.status = "offline"
        snap.error = result.get("error") or "fetch failed"
        return snap
    payload = result.get("payload") or {}
    if not isinstance(payload, dict):
        payload = {}
    if not payload:
        snap.online = False
        snap.status = "unknown"
        snap.error = "empty device payload"
        return snap
    snap.status = str(payload.get("status") or "active")
    # Explicit online flag wins; stopped is never online
    if payload.get("online") is False or snap.status == "stopped":
        snap.online = False
    else:
        snap.online = bool(payload.get("online", True))
    app = payload.get("app")
    if isinstance(app, dict):
        snap.app = {
            "process_name": app.get("process_name"),
            "display_name": app.get("display_name"),
            "window_title": app.get("window_title"),
        }
    else:
        snap.app = None
    snap.timestamp = payload.get("timestamp")
    snap.error = None
    return snap


class DeviceAggregator:
    def __init__(
        self,
        config: WebUIConfig,
        fetcher: Fetcher | None = None,
    ):
        self.config = config
        self._fetcher = fetcher
        self._snapshots: dict[str, DeviceSnapshot] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._listeners: list[asyncio.Queue[dict[str, Any]]] = []

    def update_config(self, config: WebUIConfig) -> None:
        self.config = config

    def set_fetcher(self, fetcher: Fetcher | None) -> None:
        self._fetcher = fetcher

    def get_snapshots(self) -> list[DeviceSnapshot]:
        # Preserve config order for enabled+disabled listing
        out: list[DeviceSnapshot] = []
        for device in self.config.devices:
            existing = self._snapshots.get(device.id)
            if existing is None:
                out.append(DeviceSnapshot(id=device.id, name=device.name, enabled=device.enabled))
            else:
                out.append(existing)
        return out

    def get_public_devices(self) -> list[dict[str, Any]]:
        return [s.to_public_dict() for s in self.get_snapshots()]

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self._listeners.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        if queue in self._listeners:
            self._listeners.remove(queue)

    async def refresh_once(self) -> list[DeviceSnapshot]:
        devices = [d for d in self.config.devices if d.enabled]
        results = await asyncio.gather(
            *(self._fetch_one(d) for d in devices),
            return_exceptions=True,
        )
        async with self._lock:
            for device, result in zip(devices, results):
                if isinstance(result, Exception):
                    snap = DeviceSnapshot(id=device.id, name=device.name, enabled=device.enabled)
                    snap.online = False
                    snap.status = "offline"
                    snap.error = str(result) or result.__class__.__name__
                    snap.updated_at = time.time()
                    self._snapshots[device.id] = snap
                    logger.warning(
                        "device fetch exception id=%s error=%s",
                        device.id,
                        snap.error,
                    )
                else:
                    if not isinstance(result, dict):
                        snap = DeviceSnapshot(id=device.id, name=device.name, enabled=device.enabled)
                        snap.online = False
                        snap.status = "offline"
                        snap.error = f"invalid fetcher result: {type(result).__name__}"
                        snap.updated_at = time.time()
                        self._snapshots[device.id] = snap
                        logger.warning(
                            "device fetch invalid result id=%s type=%s",
                            device.id,
                            type(result).__name__,
                        )
                        continue
                    snap = build_snapshot(device, result)
                    self._snapshots[device.id] = snap
                    if not result.get("ok"):
                        logger.warning(
                            "device fetch failed id=%s status=%s error=%s",
                            device.id,
                            result.get("http_status"),
                            result.get("error"),
                        )
                    else:
                        logger.debug(
                            "device fetch ok id=%s latency_ms=%s",
                            device.id,
                            result.get("latency_ms"),
                        )
            # Drop snapshots for removed devices
            known = {d.id for d in self.config.devices}
            for key in list(self._snapshots):
                if key not in known:
                    self._snapshots.pop(key, None)
            payload = self.get_public_devices()
        await self._notify(payload)
        return self.get_snapshots()

    async def _fetch_one(self, device: DeviceConfig) -> FetchResult:
        if self._fetcher is not None:
            return await self._fetcher(device)
        return await default_fetcher(device)

    async def _notify(self, payload: list[dict[str, Any]]) -> None:
        for queue in list(self._listeners):
            message = {"type": "devices", "data": payload}
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped.clear()
        self._task = asyncio.create_task(self._loop(), name="device-aggregator")

    async def stop(self) -> None:
        self._stopped.set()
        task = self._task
        self._task = None
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()

    async def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                await self.refresh_once()
            except Exception:
                # Keep the loop alive even if a refresh batch fails.
                logger.exception("aggregator refresh batch failed")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.config.refresh_interval_seconds)
            except asyncio.TimeoutError:
                continue
