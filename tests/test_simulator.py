import argparse

import pytest

from fleet_monitor.schemas import HeartbeatRequest
from simulator.simulate import FleetClient, SimulatedDevice, parse_kill


def test_simulator_payload_is_accepted_by_server_schema():
    device = SimulatedDevice(FleetClient("http://unused"), "device-01", interval=5)
    payload = device._payload()
    parsed = HeartbeatRequest.model_validate(payload)
    assert parsed.status == "OK"
    assert set(parsed.metrics) == {"cpu_usage", "signal_strength"}


@pytest.mark.parametrize(
    "value, expected",
    [("device-03:10", ("device-03", 10.0)), ("a:b:0.5", ("a:b", 0.5))],
)
def test_parse_kill_valid(value, expected):
    assert parse_kill(value) == expected


@pytest.mark.parametrize("value", ["device-03", ":10", "device-03:", "device-03:x", "device-03:-1"])
def test_parse_kill_invalid(value):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_kill(value)
