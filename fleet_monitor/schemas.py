"""Request/response models for the HTTP API (validation lives here)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .registry import DeviceStatus, DeviceView

DEVICE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
MAX_EXTRA_FIELDS = 20

MetricValue = Union[int, float, str, bool]


class RegisterDeviceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(pattern=DEVICE_ID_PATTERN, examples=["device-01"])
    name: str = Field(min_length=1, max_length=100, examples=["Lab Device 01"])


class HeartbeatRequest(BaseModel):
    """A heartbeat. Any extra top-level fields (e.g. cpu_usage) are kept as metrics."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    timestamp: AwareDatetime = Field(examples=["2026-09-21T10:30:00Z"])
    status: str = Field(min_length=1, max_length=32, examples=["OK"])

    @model_validator(mode="after")
    def _check_metrics(self) -> "HeartbeatRequest":
        extras = self.model_extra or {}
        if len(extras) > MAX_EXTRA_FIELDS:
            raise ValueError(f"at most {MAX_EXTRA_FIELDS} extra metric fields are allowed")
        for key, value in extras.items():
            if not isinstance(value, (int, float, str, bool)):
                raise ValueError(f"metric '{key}' must be a number, string or boolean")
        return self

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(self.model_extra or {})


class DeviceResponse(BaseModel):
    id: str
    name: str
    status: DeviceStatus = Field(description="Derived: ONLINE if a heartbeat was received in the timeout window")
    last_heartbeat: datetime | None = Field(description="Device-reported timestamp of the latest heartbeat")
    last_seen: datetime | None = Field(description="Server time at which the latest heartbeat was received")
    reported_status: str | None = Field(description="Health status the device reported, e.g. OK")
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    registered_at: datetime

    @classmethod
    def from_view(cls, view: DeviceView) -> "DeviceResponse":
        hb = view.last_heartbeat
        return cls(
            id=view.id,
            name=view.name,
            status=view.status,
            last_heartbeat=hb.timestamp if hb else None,
            last_seen=view.last_seen,
            reported_status=hb.status if hb else None,
            metrics=hb.metrics if hb else {},
            registered_at=view.registered_at,
        )


class SummaryResponse(BaseModel):
    total: int
    online: int
    offline: int
