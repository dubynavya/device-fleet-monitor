import pytest
from fastapi.testclient import TestClient

from fleet_monitor.api import create_app
from fleet_monitor.registry import DeviceRegistry

HB = {"timestamp": "2026-09-21T10:30:00Z", "status": "OK"}


@pytest.fixture
def client(clock):
    return TestClient(create_app(DeviceRegistry(clock=clock)))


def register(client, device_id="device-01", name="Lab Device 01"):
    return client.post("/devices", json={"id": device_id, "name": name})


# --- POST /devices ----------------------------------------------------------

def test_register_device(client):
    r = register(client)
    assert r.status_code == 201
    assert r.headers["location"] == "/devices/device-01"
    body = r.json()
    assert body["id"] == "device-01"
    assert body["name"] == "Lab Device 01"
    assert body["status"] == "OFFLINE"
    assert body["last_heartbeat"] is None


def test_register_duplicate_returns_409(client):
    register(client)
    r = register(client, name="Other")
    assert r.status_code == 409
    assert "already registered" in r.json()["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"id": "device-01"},
        {"name": "x"},
        {"id": "", "name": "x"},
        {"id": "has space", "name": "x"},
        {"id": "a/b", "name": "x"},
        {"id": "x" * 65, "name": "x"},
        {"id": "device-01", "name": ""},
        {"id": "device-01", "name": "   "},
        {"id": "device-01", "name": "x" * 101},
        {"id": "device-01", "name": "x", "unexpected": 1},
        {"id": 123, "name": "x"},
    ],
)
def test_register_invalid_payload_returns_422(client, payload):
    assert client.post("/devices", json=payload).status_code == 422


def test_register_malformed_json_returns_422(client):
    r = client.post("/devices", content=b"{not json", headers={"content-type": "application/json"})
    assert r.status_code == 422


# --- POST /devices/{id}/heartbeat ------------------------------------------

def test_heartbeat_marks_device_online(client):
    register(client)
    r = client.post("/devices/device-01/heartbeat", json=HB)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ONLINE"
    assert body["last_heartbeat"] == "2026-09-21T10:30:00Z"
    assert body["reported_status"] == "OK"


def test_heartbeat_with_extra_metrics(client):
    register(client)
    r = client.post(
        "/devices/device-01/heartbeat",
        json={**HB, "cpu_usage": 42, "signal_strength": -71},
    )
    assert r.status_code == 200
    assert r.json()["metrics"] == {"cpu_usage": 42, "signal_strength": -71}


def test_heartbeat_unknown_device_returns_404(client):
    r = client.post("/devices/ghost/heartbeat", json=HB)
    assert r.status_code == 404
    assert r.json()["detail"] == "device 'ghost' not found"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"status": "OK"},
        {"timestamp": "2026-09-21T10:30:00Z"},
        {"timestamp": "not-a-date", "status": "OK"},
        {"timestamp": "2026-09-21T10:30:00", "status": "OK"},  # no timezone
        {"timestamp": "2026-09-21T10:30:00Z", "status": ""},
        {**HB, "nested": {"a": 1}},
        {**HB, "list": [1, 2]},
    ],
)
def test_heartbeat_invalid_payload_returns_422(client, payload):
    register(client)
    assert client.post("/devices/device-01/heartbeat", json=payload).status_code == 422


# --- GET /devices and /devices/{id} ----------------------------------------

def test_list_devices(client):
    register(client, "device-02", "Two")
    register(client, "device-01", "One")
    client.post("/devices/device-01/heartbeat", json=HB)

    r = client.get("/devices")
    assert r.status_code == 200
    body = r.json()
    assert [d["id"] for d in body] == ["device-01", "device-02"]
    assert [d["status"] for d in body] == ["ONLINE", "OFFLINE"]
    assert {"id", "name", "status", "last_heartbeat"} <= body[0].keys()


def test_list_devices_empty(client):
    assert client.get("/devices").json() == []


def test_list_devices_filter_by_status(client):
    register(client, "device-01")
    register(client, "device-02")
    client.post("/devices/device-02/heartbeat", json=HB)

    online = client.get("/devices", params={"status": "ONLINE"}).json()
    offline = client.get("/devices", params={"status": "OFFLINE"}).json()
    assert [d["id"] for d in online] == ["device-02"]
    assert [d["id"] for d in offline] == ["device-01"]
    assert client.get("/devices", params={"status": "BOGUS"}).status_code == 422


def test_get_device(client):
    register(client)
    r = client.get("/devices/device-01")
    assert r.status_code == 200
    assert r.json()["name"] == "Lab Device 01"


def test_get_unknown_device_returns_404(client):
    assert client.get("/devices/ghost").status_code == 404


# --- timeout behaviour through the API -------------------------------------

def test_status_transitions_through_api(client, clock):
    register(client)
    client.post("/devices/device-01/heartbeat", json=HB)
    assert client.get("/devices/device-01").json()["status"] == "ONLINE"

    clock.advance(30)
    assert client.get("/devices/device-01").json()["status"] == "ONLINE"

    clock.advance(1)
    assert client.get("/devices/device-01").json()["status"] == "OFFLINE"
    assert client.get("/summary").json() == {"total": 1, "online": 0, "offline": 1}

    client.post("/devices/device-01/heartbeat", json=HB)
    assert client.get("/devices/device-01").json()["status"] == "ONLINE"


# --- GET /summary -----------------------------------------------------------

def test_summary(client, clock):
    for i in range(1, 6):
        register(client, f"device-0{i}")
    for i in (1, 2, 3):
        client.post(f"/devices/device-0{i}/heartbeat", json=HB)
    assert client.get("/summary").json() == {"total": 5, "online": 3, "offline": 2}

    clock.advance(31)
    client.post("/devices/device-01/heartbeat", json=HB)
    assert client.get("/summary").json() == {"total": 5, "online": 1, "offline": 4}
