"""FastAPI application exposing the device registry over HTTP."""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from .registry import (
    DeviceAlreadyExistsError,
    DeviceNotFoundError,
    DeviceRegistry,
    DeviceStatus,
    Heartbeat,
)
from .schemas import DeviceResponse, HeartbeatRequest, RegisterDeviceRequest, SummaryResponse

logger = logging.getLogger("fleet_monitor")


def create_app(registry: DeviceRegistry | None = None) -> FastAPI:
    """Build the app. Tests pass in a registry with a fake clock."""
    registry = registry or DeviceRegistry()
    app = FastAPI(
        title="Mini Device Fleet Monitor",
        version="1.0.0",
        description=(
            "Tracks heartbeats from a fleet of devices. A device is ONLINE if a "
            f"heartbeat was received within the last {registry.timeout.total_seconds():g}s."
        ),
    )
    app.state.registry = registry

    @app.exception_handler(DeviceNotFoundError)
    async def _not_found(_: Request, exc: DeviceNotFoundError):
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": f"device '{exc.args[0]}' not found"},
        )

    @app.post(
        "/devices",
        status_code=status.HTTP_201_CREATED,
        response_model=DeviceResponse,
        responses={409: {"description": "Device already registered"}},
    )
    def register_device(body: RegisterDeviceRequest, response: Response) -> DeviceResponse:
        try:
            view = registry.register(body.id, body.name)
        except DeviceAlreadyExistsError:
            raise HTTPException(status.HTTP_409_CONFLICT, f"device '{body.id}' is already registered")
        logger.info("registered device id=%s name=%r", view.id, view.name)
        response.headers["Location"] = f"/devices/{view.id}"
        return DeviceResponse.from_view(view)

    @app.post(
        "/devices/{device_id}/heartbeat",
        response_model=DeviceResponse,
        responses={404: {"description": "Device not registered"}},
    )
    def heartbeat(device_id: str, body: HeartbeatRequest) -> DeviceResponse:
        view = registry.record_heartbeat(
            device_id,
            Heartbeat(timestamp=body.timestamp, status=body.status, metrics=body.metrics),
        )
        logger.debug("heartbeat device id=%s status=%s", device_id, body.status)
        return DeviceResponse.from_view(view)

    @app.get("/devices", response_model=list[DeviceResponse])
    def list_devices(
        status_filter: DeviceStatus | None = Query(None, alias="status", description="Filter by ONLINE/OFFLINE"),
    ) -> list[DeviceResponse]:
        return [DeviceResponse.from_view(v) for v in registry.list(status_filter)]

    @app.get(
        "/devices/{device_id}",
        response_model=DeviceResponse,
        responses={404: {"description": "Device not registered"}},
    )
    def get_device(device_id: str) -> DeviceResponse:
        return DeviceResponse.from_view(registry.get(device_id))

    @app.get("/summary", response_model=SummaryResponse)
    def summary() -> SummaryResponse:
        return SummaryResponse(**registry.summary())

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
