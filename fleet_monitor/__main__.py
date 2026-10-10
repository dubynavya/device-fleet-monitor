"""Run the server: ``python -m fleet_monitor``.

Configuration (environment variables):
    FLEET_HOST             bind address           (default 127.0.0.1)
    FLEET_PORT             port                   (default 8000)
    FLEET_TIMEOUT_SECONDS  ONLINE/OFFLINE window  (default 30)
    FLEET_LOG_LEVEL        logging level          (default INFO)
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta

import uvicorn

from .api import create_app
from .registry import DeviceRegistry


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        sys.exit(f"error: {name} must be a number, got {raw!r}")
    if value <= 0:
        sys.exit(f"error: {name} must be positive, got {raw!r}")
    return value


def main() -> None:
    host = os.environ.get("FLEET_HOST", "127.0.0.1")
    port = int(_env_float("FLEET_PORT", 8000))
    timeout = _env_float("FLEET_TIMEOUT_SECONDS", 30)
    log_level = os.environ.get("FLEET_LOG_LEVEL", "INFO").upper()

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app(DeviceRegistry(timeout=timedelta(seconds=timeout)))
    logging.getLogger("fleet_monitor").info(
        "starting on http://%s:%d (timeout=%gs)", host, port, timeout
    )
    # uvicorn handles SIGINT/SIGTERM and shuts down gracefully.
    uvicorn.run(app, host=host, port=port, log_level=log_level.lower())


if __name__ == "__main__":
    main()
