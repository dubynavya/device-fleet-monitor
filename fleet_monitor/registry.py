"""In-memory, thread-safe device registry.

Device status is never stored. It is derived on every read by comparing the
time the server last *received* a heartbeat against the current clock. This
keeps the ONLINE/OFFLINE rule correct without any background sweeper thread,
and means the result can never be stale.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

Clock = Callable[[], datetime]

DEFAULT_TIMEOUT = timedelta(seconds=30)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DeviceStatus(str, Enum):
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"


class DeviceAlreadyExistsError(Exception):
    pass


class DeviceNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Heartbeat:
    timestamp: datetime  # as reported by the device
    status: str  # device-reported health, e.g. "OK"
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class _DeviceRecord:
    id: str
    name: str
    registered_at: datetime
    last_heartbeat: Heartbeat | None = None
    last_seen: datetime | None = None  # server receive time of the last heartbeat


@dataclass(frozen=True)
class DeviceView:
    """Immutable snapshot of a device, including its derived status."""

    id: str
    name: str
    status: DeviceStatus
    registered_at: datetime
    last_heartbeat: Heartbeat | None
    last_seen: datetime | None


class DeviceRegistry:
    def __init__(self, timeout: timedelta = DEFAULT_TIMEOUT, clock: Clock = utc_now):
        if timeout <= timedelta(0):
            raise ValueError("timeout must be positive")
        self._timeout = timeout
        self._clock = clock
        self._devices: dict[str, _DeviceRecord] = {}
        self._lock = threading.Lock()

    @property
    def timeout(self) -> timedelta:
        return self._timeout

    def register(self, device_id: str, name: str) -> DeviceView:
        with self._lock:
            if device_id in self._devices:
                raise DeviceAlreadyExistsError(device_id)
            record = _DeviceRecord(id=device_id, name=name, registered_at=self._clock())
            self._devices[device_id] = record
            return self._view(record, record.registered_at)

    def record_heartbeat(self, device_id: str, heartbeat: Heartbeat) -> DeviceView:
        with self._lock:
            record = self._devices.get(device_id)
            if record is None:
                raise DeviceNotFoundError(device_id)
            now = self._clock()
            # Any heartbeat that arrives proves the device is alive right now.
            record.last_seen = now
            # Only keep the payload if it is not older than what we already
            # have, so a delayed/out-of-order heartbeat can't roll data back.
            current = record.last_heartbeat
            if current is None or heartbeat.timestamp >= current.timestamp:
                record.last_heartbeat = heartbeat
            return self._view(record, now)

    def get(self, device_id: str) -> DeviceView:
        with self._lock:
            record = self._devices.get(device_id)
            if record is None:
                raise DeviceNotFoundError(device_id)
            return self._view(record, self._clock())

    def list(self, status: DeviceStatus | None = None) -> list[DeviceView]:
        with self._lock:
            now = self._clock()
            views = [self._view(r, now) for r in self._devices.values()]
        views.sort(key=lambda v: v.id)
        if status is not None:
            views = [v for v in views if v.status == status]
        return views

    def summary(self) -> dict[str, int]:
        views = self.list()
        online = sum(1 for v in views if v.status == DeviceStatus.ONLINE)
        return {"total": len(views), "online": online, "offline": len(views) - online}

    def _status(self, record: _DeviceRecord, now: datetime) -> DeviceStatus:
        # ONLINE if a heartbeat arrived within the last `timeout` (inclusive);
        # OFFLINE only once *more than* `timeout` has elapsed, per the spec.
        if record.last_seen is None:
            return DeviceStatus.OFFLINE
        if now - record.last_seen <= self._timeout:
            return DeviceStatus.ONLINE
        return DeviceStatus.OFFLINE

    def _view(self, record: _DeviceRecord, now: datetime) -> DeviceView:
        return DeviceView(
            id=record.id,
            name=record.name,
            status=self._status(record, now),
            registered_at=record.registered_at,
            last_heartbeat=record.last_heartbeat,
            last_seen=record.last_seen,
        )
