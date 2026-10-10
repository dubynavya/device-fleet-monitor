"""Shared test helpers."""

from datetime import datetime, timedelta, timezone

import pytest

T0 = datetime(2026, 9, 21, 10, 30, 0, tzinfo=timezone.utc)


class FakeClock:
    def __init__(self, start: datetime = T0):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return FakeClock()
