import threading
from datetime import datetime, timedelta

import pytest

from fleet_monitor.registry import (
    DeviceAlreadyExistsError,
    DeviceNotFoundError,
    DeviceRegistry,
    DeviceStatus,
    Heartbeat,
)
from tests.conftest import T0


@pytest.fixture
def registry(clock):
    return DeviceRegistry(clock=clock)


def hb(ts: datetime = T0, status: str = "OK", **metrics) -> Heartbeat:
    return Heartbeat(timestamp=ts, status=status, metrics=metrics)


# --- registration -----------------------------------------------------------

def test_register_creates_offline_device(registry):
    view = registry.register("device-01", "Lab Device 01")
    assert view.id == "device-01"
    assert view.name == "Lab Device 01"
    assert view.status == DeviceStatus.OFFLINE  # no heartbeat yet
    assert view.last_heartbeat is None


def test_register_duplicate_raises(registry):
    registry.register("device-01", "A")
    with pytest.raises(DeviceAlreadyExistsError):
        registry.register("device-01", "B")
    assert registry.get("device-01").name == "A"  # original untouched


def test_get_unknown_device_raises(registry):
    with pytest.raises(DeviceNotFoundError):
        registry.get("nope")


def test_rejects_non_positive_timeout():
    with pytest.raises(ValueError):
        DeviceRegistry(timeout=timedelta(0))


# --- heartbeats -------------------------------------------------------------

def test_heartbeat_marks_device_online(registry, clock):
    registry.register("device-01", "A")
    view = registry.record_heartbeat("device-01", hb(cpu_usage=42))
    assert view.status == DeviceStatus.ONLINE
    assert view.last_heartbeat.timestamp == T0
    assert view.last_heartbeat.metrics == {"cpu_usage": 42}
    assert view.last_seen == clock.now


def test_heartbeat_for_unregistered_device_raises(registry):
    with pytest.raises(DeviceNotFoundError):
        registry.record_heartbeat("ghost", hb())


def test_newer_heartbeat_replaces_older(registry, clock):
    registry.register("d", "D")
    registry.record_heartbeat("d", hb(T0, status="OK"))
    clock.advance(5)
    view = registry.record_heartbeat("d", hb(T0 + timedelta(seconds=5), status="DEGRADED"))
    assert view.last_heartbeat.status == "DEGRADED"


def test_out_of_order_heartbeat_keeps_newer_payload_but_refreshes_liveness(registry, clock):
    registry.register("d", "D")
    registry.record_heartbeat("d", hb(T0 + timedelta(seconds=10), status="NEW"))
    clock.advance(25)
    view = registry.record_heartbeat("d", hb(T0, status="STALE"))
    assert view.last_heartbeat.status == "NEW"
    assert view.last_seen == clock.now
    clock.advance(25)  # 50s after first, 25s after the late one
    assert registry.get("d").status == DeviceStatus.ONLINE


# --- 30-second ONLINE/OFFLINE rule -----------------------------------------

def test_online_just_before_timeout(registry, clock):
    registry.register("d", "D")
    registry.record_heartbeat("d", hb())
    clock.advance(29.999)
    assert registry.get("d").status == DeviceStatus.ONLINE


def test_online_at_exactly_timeout(registry, clock):
    # "OFFLINE if no heartbeat for *more than* 30 seconds" => 30.000s is ONLINE.
    registry.register("d", "D")
    registry.record_heartbeat("d", hb())
    clock.advance(30)
    assert registry.get("d").status == DeviceStatus.ONLINE


def test_offline_after_timeout(registry, clock):
    registry.register("d", "D")
    registry.record_heartbeat("d", hb())
    clock.advance(30.001)
    assert registry.get("d").status == DeviceStatus.OFFLINE


def test_device_recovers_after_new_heartbeat(registry, clock):
    registry.register("d", "D")
    registry.record_heartbeat("d", hb())
    clock.advance(60)
    assert registry.get("d").status == DeviceStatus.OFFLINE
    registry.record_heartbeat("d", hb(T0 + timedelta(seconds=60)))
    assert registry.get("d").status == DeviceStatus.ONLINE


def test_liveness_uses_server_receive_time_not_device_timestamp(registry, clock):
    # A device with a badly skewed clock should still be ONLINE if it is
    # actually sending heartbeats right now.
    registry.register("d", "D")
    registry.record_heartbeat("d", hb(T0 - timedelta(days=1)))
    assert registry.get("d").status == DeviceStatus.ONLINE


def test_custom_timeout(clock):
    registry = DeviceRegistry(timeout=timedelta(seconds=5), clock=clock)
    registry.register("d", "D")
    registry.record_heartbeat("d", hb())
    clock.advance(6)
    assert registry.get("d").status == DeviceStatus.OFFLINE


# --- listing and summary ----------------------------------------------------

def test_list_sorted_and_filterable(registry, clock):
    for i in (3, 1, 2):
        registry.register(f"device-0{i}", f"D{i}")
    registry.record_heartbeat("device-02", hb())

    assert [v.id for v in registry.list()] == ["device-01", "device-02", "device-03"]
    assert [v.id for v in registry.list(DeviceStatus.ONLINE)] == ["device-02"]
    assert [v.id for v in registry.list(DeviceStatus.OFFLINE)] == ["device-01", "device-03"]


def test_summary_counts(registry, clock):
    assert registry.summary() == {"total": 0, "online": 0, "offline": 0}
    for i in range(1, 6):
        registry.register(f"d{i}", f"D{i}")
    registry.record_heartbeat("d1", hb())
    clock.advance(20)
    registry.record_heartbeat("d2", hb())
    registry.record_heartbeat("d3", hb())
    assert registry.summary() == {"total": 5, "online": 3, "offline": 2}
    clock.advance(15)  # d1 now 35s stale, d2/d3 15s
    assert registry.summary() == {"total": 5, "online": 2, "offline": 3}


# --- concurrency ------------------------------------------------------------

def test_concurrent_registration_and_heartbeats(registry):
    n_threads, per_thread = 8, 50
    errors: list[Exception] = []

    def worker(t: int) -> None:
        try:
            for i in range(per_thread):
                device_id = f"t{t}-d{i}"
                registry.register(device_id, device_id)
                registry.record_heartbeat(device_id, hb())
                registry.summary()
        except Exception as exc:  # pragma: no cover - surfaced via assert
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []
    assert registry.summary() == {
        "total": n_threads * per_thread,
        "online": n_threads * per_thread,
        "offline": 0,
    }


def test_concurrent_duplicate_registration_only_one_wins(registry):
    results: list[str] = []
    barrier = threading.Barrier(10)

    def worker() -> None:
        barrier.wait()
        try:
            registry.register("same", "x")
            results.append("ok")
        except DeviceAlreadyExistsError:
            results.append("dup")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert results.count("ok") == 1
    assert results.count("dup") == 9
