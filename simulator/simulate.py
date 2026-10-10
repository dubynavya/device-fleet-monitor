"""Device fleet simulator (standard library only).

Registers N devices with the fleet monitor and sends a heartbeat from each one
on a fixed interval, each from its own thread. Individual devices can be
stopped and restarted while it runs to watch them go OFFLINE and back ONLINE.

    python simulator/simulate.py                       # 5 devices, every 5s
    python simulator/simulate.py --devices 8 --interval 2
    python simulator/simulate.py --kill device-03:10   # stop device-03 after 10s

Interactive commands (type while it runs):
    stop <id>     stop sending heartbeats for a device
    start <id>    resume a stopped device
    status        print the server's view of the fleet
    quit          stop everything and exit (Ctrl+C also works)
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

REQUEST_TIMEOUT = 5  # seconds


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status = status


class FleetClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, method: str, path: str, body: dict | None = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get("detail", exc.reason)
            except (ValueError, AttributeError):
                detail = exc.reason
            raise ApiError(exc.code, str(detail)) from None

    def register(self, device_id: str, name: str) -> None:
        self._request("POST", "/devices", {"id": device_id, "name": name})

    def heartbeat(self, device_id: str, payload: dict) -> None:
        self._request("POST", f"/devices/{device_id}/heartbeat", payload)

    def devices(self) -> list[dict]:
        return self._request("GET", "/devices")

    def summary(self) -> dict:
        return self._request("GET", "/summary")


class SimulatedDevice:
    def __init__(self, client: FleetClient, device_id: str, interval: float):
        self.client = client
        self.id = device_id
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.id, daemon=True)
        self._thread.start()

    def request_stop(self) -> None:
        """Signal the device to stop without waiting for it."""
        self._stop.set()

    def stop(self) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout=REQUEST_TIMEOUT + 1)

    def _payload(self) -> dict:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "status": "OK",
            "cpu_usage": random.randint(5, 95),
            "signal_strength": random.randint(-90, -40),
        }

    def _run(self) -> None:
        # Stagger start-up a little so devices don't all fire in lockstep.
        if self._stop.wait(random.uniform(0, min(1.0, self.interval))):
            return
        while not self._stop.is_set():
            try:
                self.client.heartbeat(self.id, self._payload())
                log(f"{self.id} -> heartbeat")
            except ApiError as exc:
                log(f"{self.id} heartbeat rejected: {exc}")
            except (urllib.error.URLError, OSError) as exc:
                log(f"{self.id} heartbeat failed (server unreachable?): {exc}")
            self._stop.wait(self.interval)


def print_status(client: FleetClient) -> None:
    try:
        summary = client.summary()
        devices = client.devices()
    except (ApiError, urllib.error.URLError, OSError) as exc:
        log(f"could not fetch status: {exc}")
        return
    log(f"fleet: total={summary['total']} online={summary['online']} offline={summary['offline']}")
    for d in devices:
        print(f"    {d['id']:<14} {d['status']:<8} last_heartbeat={d['last_heartbeat']}")


def parse_kill(value: str) -> tuple[str, float]:
    device_id, sep, seconds = value.rpartition(":")
    try:
        delay = float(seconds)
    except ValueError:
        delay = -1
    if not sep or not device_id or delay < 0:
        raise argparse.ArgumentTypeError("expected <device-id>:<seconds>, e.g. device-03:10")
    return device_id, delay


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate devices sending heartbeats.")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="fleet monitor base URL")
    parser.add_argument("--devices", type=int, default=5, help="number of devices (default 5)")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds between heartbeats (default 5)")
    parser.add_argument("--prefix", default="device", help="device id prefix (default 'device')")
    parser.add_argument(
        "--kill",
        type=parse_kill,
        action="append",
        default=[],
        metavar="ID:SECONDS",
        help="stop a device automatically after SECONDS (repeatable)",
    )
    args = parser.parse_args(argv)
    if args.devices < 1:
        parser.error("--devices must be at least 1")
    if args.interval <= 0:
        parser.error("--interval must be positive")

    client = FleetClient(args.url)
    devices: dict[str, SimulatedDevice] = {}

    for i in range(1, args.devices + 1):
        device_id = f"{args.prefix}-{i:02d}"
        try:
            client.register(device_id, f"Simulated Device {i:02d}")
            log(f"registered {device_id}")
        except ApiError as exc:
            if exc.status != 409:
                log(f"failed to register {device_id}: {exc}")
                return 1
            log(f"{device_id} already registered, reusing it")
        except (urllib.error.URLError, OSError) as exc:
            log(f"cannot reach fleet monitor at {args.url}: {exc}")
            return 1
        devices[device_id] = SimulatedDevice(client, device_id, args.interval)

    for d in devices.values():
        d.start()
    log(f"{len(devices)} devices sending heartbeats every {args.interval:g}s")

    timers = []
    for device_id, delay in args.kill:
        if device_id not in devices:
            log(f"--kill: unknown device {device_id!r}, ignoring")
            continue
        def _kill(d=devices[device_id], delay=delay):
            d.stop()
            log(f"{d.id} stopped by --kill after {delay:g}s; it should go OFFLINE after the timeout")
        t = threading.Timer(delay, _kill)
        t.daemon = True
        t.start()
        timers.append(t)

    log("commands: stop <id> | start <id> | status | quit")
    try:
        for line in sys.stdin:
            parts = line.split()
            if not parts:
                continue
            cmd, rest = parts[0].lower(), parts[1:]
            if cmd in ("quit", "exit", "q"):
                break
            if cmd == "status":
                print_status(client)
            elif cmd in ("stop", "start") and len(rest) == 1:
                device = devices.get(rest[0])
                if device is None:
                    log(f"unknown device {rest[0]!r}; known: {', '.join(devices)}")
                elif cmd == "stop":
                    device.stop()
                    log(f"{device.id} stopped")
                else:
                    device.start()
                    log(f"{device.id} started")
            else:
                log("commands: stop <id> | start <id> | status | quit")
        else:
            # stdin closed (e.g. running in the background): keep simulating
            # until interrupted. Sleep in short steps so Ctrl+C is honoured on
            # Windows, where an untimed wait() blocks KeyboardInterrupt.
            while True:
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for t in timers:
            t.cancel()
        log("shutting down simulator")
        for d in devices.values():
            d.request_stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
