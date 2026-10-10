# Mini Device Fleet Monitor

A small HTTP service that tracks a fleet of devices. Devices register once and
then send periodic **heartbeats**. The service keeps the latest heartbeat for
each device and reports every device as **ONLINE** (a heartbeat was received in
the last 30 seconds) or **OFFLINE** (no heartbeat for more than 30 seconds),
along with a fleet-wide summary.

The repository also contains a **device simulator** that runs 5+ devices
sending heartbeats, and lets you stop individual devices to watch them go
OFFLINE.

---

## Design / architecture

```
 simulator/simulate.py ──HTTP──▶  fleet_monitor/api.py      (FastAPI routes, HTTP errors)
                                        │
                                        ├── schemas.py       (pydantic request/response models, validation)
                                        ▼
                                  fleet_monitor/registry.py (DeviceRegistry: in-memory state, status rule)
```

| Module | Responsibility |
|---|---|
| `fleet_monitor/registry.py` | Domain logic with no HTTP knowledge. Stores devices in a dict guarded by a `threading.Lock`. Computes ONLINE/OFFLINE. |
| `fleet_monitor/schemas.py` | Input validation and the JSON shape of responses. |
| `fleet_monitor/api.py` | `create_app(registry)` builds the FastAPI app and maps domain errors to HTTP codes (404, 409, 422). |
| `fleet_monitor/__main__.py` | Entry point (`python -m fleet_monitor`): reads env-var config, configures logging, runs uvicorn. |
| `simulator/simulate.py` | Standard-library-only simulator, one thread per device. |

### How the 30-second rule is implemented

Status is **derived on every read** and never stored:

```
status = ONLINE  if (now - last_seen) <= timeout
         OFFLINE otherwise (including "never sent a heartbeat")
```

* `last_seen` is the **server's** clock when the latest heartbeat arrived, *not*
  the timestamp inside the heartbeat. A device with a wrong clock is still
  judged correctly, and devices never need clocks in sync with the server.
* Because status is computed when it is read, there is no background "sweeper"
  thread, no race between a sweeper and incoming heartbeats, and the answer can
  never be stale.
* The registry takes an injectable `clock` function. Tests pass a fake clock
  and move it forward by exactly 29.999s, 30s and 30.001s, so the timeout
  boundary is tested precisely and the test suite never sleeps.
* Boundary: the spec says OFFLINE after **more than** 30s, so a device at
  exactly 30.000s is still ONLINE.

---

## Prerequisites

* **Python 3.10+** (developed and tested with Python 3.13 on Windows 11)
* `pip`
* `curl` (optional, for the example requests)

## Build / install

There is nothing to compile. Create a virtual environment and install the dependencies:

```bash
git clone https://github.com/dubynavya/device-fleet-monitor.git
cd device-fleet-monitor
python -m venv .venv
```

Activate it:

```bash
# macOS / Linux
source .venv/bin/activate
```

```powershell
# Windows (PowerShell)
.venv\Scripts\Activate.ps1
```

Then install:

```bash
pip install -r requirements-dev.txt     # runtime + test dependencies
```

(`requirements.txt` alone is enough to run the server. The simulator needs no third-party packages.)

## Run the application

```bash
python -m fleet_monitor
```

The server listens on `http://127.0.0.1:8000`. Interactive API docs (Swagger UI) are at
<http://127.0.0.1:8000/docs>.

Configuration through environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `FLEET_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` to expose on the network) |
| `FLEET_PORT` | `8000` | Port |
| `FLEET_TIMEOUT_SECONDS` | `30` | ONLINE/OFFLINE window |
| `FLEET_LOG_LEVEL` | `INFO` | `DEBUG` also logs every heartbeat |

```bash
FLEET_PORT=9000 FLEET_TIMEOUT_SECONDS=10 python -m fleet_monitor      # bash
```
```powershell
$env:FLEET_PORT=9000; $env:FLEET_TIMEOUT_SECONDS=10; python -m fleet_monitor   # PowerShell
```

Stop with `Ctrl+C`; uvicorn shuts down gracefully.

## Run the simulator

With the server running, in a **second terminal** (same venv, or any Python 3.10+):

```bash
python simulator/simulate.py
```

This registers `device-01` … `device-05` and sends a heartbeat from each every 5 seconds.
While it runs you can type:

| Command | Effect |
|---|---|
| `stop device-03` | device-03 stops sending heartbeats |
| `start device-03` | device-03 resumes |
| `status` | prints `/summary` and each device's status as the server sees it |
| `quit` | exits (`Ctrl+C` also works) |

**To see a device go OFFLINE:** type `stop device-03`, wait a little over 30 seconds, then
type `status` (or `curl http://127.0.0.1:8000/devices/device-03`). It shows `OFFLINE`, and
`/summary` shows `online: 4, offline: 1`. Type `start device-03` and it is ONLINE again within
one interval.

Non-interactive alternative (handy for demos/CI): stop a device automatically after N seconds:

```bash
python simulator/simulate.py --kill device-03:10
```

Other options:

```
--url URL          server base URL (default http://127.0.0.1:8000)
--devices N        number of devices (default 5)
--interval SECS    seconds between heartbeats (default 5)
--prefix NAME      device id prefix (default "device")
--kill ID:SECS     stop a device after SECS seconds (repeatable)
```

Re-running the simulator against the same server reuses the already-registered devices.

## Run the tests

```bash
python -m pytest
```

59 tests, in about 2 seconds:

| File | Covers |
|---|---|
| `tests/test_registry.py` | registration, duplicates, heartbeats, the 30s boundary (29.999 / 30 / 30.001s), recovery, server-time-vs-device-time, out-of-order heartbeats, summary, filtering, concurrent registration/heartbeats from multiple threads |
| `tests/test_api.py` | every endpoint, status codes (201/200/404/409/422), input validation, `?status=` filter, ONLINE→OFFLINE→ONLINE through the HTTP API |
| `tests/test_simulator.py` | the simulator's heartbeat payload passes the server's schema, and the `--kill` argument parsing |

---

## Example API requests

> On Windows PowerShell use `curl.exe` (plain `curl` is an alias for `Invoke-WebRequest`), and
> put the JSON in a file (`-d @body.json`) to avoid quoting problems.

**Register a device**: `201 Created`, `409` if the id already exists
```bash
curl -i -X POST http://127.0.0.1:8000/devices \
  -H "Content-Type: application/json" \
  -d '{"id": "device-01", "name": "Lab Device 01"}'
```

**Send a heartbeat**: `200`, `404` if the device is not registered. Extra top-level fields are stored as metrics.
```bash
curl -X POST http://127.0.0.1:8000/devices/device-01/heartbeat \
  -H "Content-Type: application/json" \
  -d '{"timestamp": "2026-09-21T10:30:00Z", "status": "OK", "cpu_usage": 42, "signal_strength": -71}'
```

**List devices** (optionally `?status=ONLINE` or `?status=OFFLINE`)
```bash
curl http://127.0.0.1:8000/devices
```
```json
[
  {
    "id": "device-01",
    "name": "Lab Device 01",
    "status": "ONLINE",
    "last_heartbeat": "2026-09-21T10:30:00Z",
    "last_seen": "2026-10-10T07:16:25.506319Z",
    "reported_status": "OK",
    "metrics": {"cpu_usage": 42, "signal_strength": -71},
    "registered_at": "2026-10-10T07:16:22.274245Z"
  }
]
```

| Field | Meaning |
|---|---|
| `status` | ONLINE/OFFLINE, derived from the 30s rule |
| `last_heartbeat` | timestamp the device put in its latest heartbeat |
| `last_seen` | server time the latest heartbeat was received (used for the 30s rule) |
| `reported_status` | the device's own health string from the heartbeat (e.g. `OK`) |

**Get one device**: `404` if unknown
```bash
curl http://127.0.0.1:8000/devices/device-01
```

**Fleet summary**
```bash
curl http://127.0.0.1:8000/summary
```
```json
{"total": 5, "online": 4, "offline": 1}
```

**Errors** use FastAPI's standard shape, e.g. `{"detail": "device 'ghost' not found"}`.
Validation errors (`422`) include a `detail` list naming the offending field.

---

## Assumptions

* **Liveness uses server receive time**, not the device-reported `timestamp`. The
  `timestamp` is stored and returned as `last_heartbeat` but does not affect status.
* **Exactly 30s is still ONLINE**. The spec says OFFLINE after *more than* 30 seconds.
* **A newly registered device is OFFLINE** until its first heartbeat.
* **Heartbeats from unregistered devices are rejected** (`404`), not auto-registered, as
  the spec says "a *registered* device should be able to send a heartbeat".
* **Duplicate registration is a `409 Conflict`** and does not overwrite the existing device.
* **Out-of-order heartbeats:** any heartbeat refreshes liveness, but the stored payload is
  only replaced if its `timestamp` is not older than the one already stored, so a delayed
  packet can't roll the data back.
* `timestamp` must be ISO-8601 **with a timezone** (e.g. `Z`). A naive timestamp is ambiguous
  and is rejected.
* Heartbeat `status` is a free-form string (1–32 chars), because the spec only shows `"OK"`.
  Extra fields must be scalar (number/string/bool), at most 20 of them.
* Device ids: 1–64 chars of letters, digits, `.`, `_`, `-`, starting with a letter or digit.
  This keeps them URL-safe.
* A single server instance. No authentication is required.

## Known limitations

* **In-memory storage**: all devices are lost when the server restarts. The simulator
  re-registers on start-up, so this doesn't matter for the demo.
* **Single process**: state lives in one process, so the service can't be scaled
  horizontally or run with multiple uvicorn workers.
* **No authentication**: any client can register devices or send heartbeats for any device.
* **No history**: only the latest heartbeat per device is kept, and there are no time series.
* **No delete/rename** endpoint for devices.
* `GET /devices` returns the full list with no pagination, which is fine for hundreds of
  devices but not for millions.
* Status changes are derived when read, so nothing fires an event or log line at the moment a
  device goes OFFLINE.

## What I would improve with one more day

1. **Persistence**: a SQLite store behind the same registry interface (it's already a seam
   via `create_app(registry)`), so restarts don't lose state.
2. **Status-change events**: a lightweight background checker that logs or notifies (webhook)
   on ONLINE→OFFLINE transitions, keeping the read path's derived status as the source of truth.
3. **Simple web dashboard** that polls `/devices` and `/summary` and colours devices by status.
4. **Auth**: a per-device token issued at registration and required for heartbeats.
5. **Packaging**: a Dockerfile/docker-compose (server + simulator), a GitHub Actions workflow
   running the tests, and linting with `ruff`.
6. Pagination and `DELETE /devices/{id}`.

---

## AI Usage

* **Tools used:** Claude Code (Anthropic Claude), working inside the repository.
* **What for:** I used it as a pair programmer. It helped me plan the structure (keeping the registry logic separate from the API layer), write the first drafts of the code, simulator and tests, and put together this README. I reviewed the code, ran the tests, and tested the app by hand before submitting.
* **Something changed / rejected:**
  - One option for the 30-second rule was a background thread that marks devices OFFLINE every few seconds. I went with working out the status each time it's requested instead. It's simpler, there's no timing race between the thread and incoming heartbeats, and I can test it exactly by passing in a fake clock instead of waiting 30 real seconds in tests.
  - The first version of the "device not found" handler raised an exception inside FastAPI's error handler. That would have returned a 500 error instead of a 404, so it was changed to return the 404 response directly, and a test checks the exact response.
* **Personally verified before submitting:** I ran the server and the simulator on my own machine with the real 30-second timeout and had device-03 stop after 10 seconds. At first `/summary` showed all 5 devices online. About 40 seconds later it showed `{"total":5,"online":4,"offline":1}`, and `/devices` showed device-03 as OFFLINE while the other four stayed ONLINE. I also went through the test that checks the boundary: exactly 30 seconds is still ONLINE, and anything over 30 seconds is OFFLINE.
---

## Project layout

```
device-fleet-monitor/
├── fleet_monitor/
│   ├── __main__.py      # entry point: python -m fleet_monitor
│   ├── api.py           # FastAPI routes
│   ├── registry.py      # in-memory, thread-safe registry + status rule
│   └── schemas.py       # request/response models + validation
├── simulator/
│   └── simulate.py      # device simulator (stdlib only)
├── tests/
│   ├── conftest.py      # FakeClock fixture
│   ├── test_api.py
│   ├── test_registry.py
│   └── test_simulator.py
├── requirements.txt
├── requirements-dev.txt
└── pytest.ini
```
