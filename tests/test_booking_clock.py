import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core import booking_clock


@pytest.mark.parametrize("zone,expected", [("Asia/Jerusalem", "True"), ("UTC", "False"), ("Europe/Berlin", "False")])
def test_process_timezone_prerequisite_in_isolated_process(zone, expected):
    code = "from app.core.booking_clock import scheduling_clock_ready; print(scheduling_clock_ready('Asia/Jerusalem'))"
    result = subprocess.run([sys.executable, "-B", "-c", code], env={**os.environ, "TZ": zone}, text=True, capture_output=True, check=True)
    assert result.stdout.strip() == expected


def test_unknown_os_timezone_and_unsupported_business_fail_closed(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: Path("/unknown/timezone"))
    assert not booking_clock.scheduling_clock_ready("Asia/Jerusalem")
    assert not booking_clock.scheduling_clock_ready("UTC")


def test_known_os_timezone_without_env_is_accepted(monkeypatch):
    monkeypatch.delenv("TZ", raising=False)
    monkeypatch.setattr(Path, "resolve", lambda *a, **k: Path("/system/zoneinfo/Asia/Jerusalem"))
    # The current offset is independently verified; do not infer readiness from
    # an abbreviation such as IST alone.
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from types import SimpleNamespace
    offset = int(datetime.now(ZoneInfo("Asia/Jerusalem")).utcoffset().total_seconds())
    monkeypatch.setattr(booking_clock.time, "localtime", lambda stamp: SimpleNamespace(tm_gmtoff=offset))
    assert booking_clock.scheduling_clock_ready("Asia/Jerusalem")


def test_runtime_env_edit_without_matching_effective_clock_is_refused(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv("TZ", "Asia/Jerusalem")
    monkeypatch.setattr(booking_clock.time, "localtime", lambda stamp: SimpleNamespace(tm_gmtoff=0))
    assert not booking_clock.scheduling_clock_ready("Asia/Jerusalem")
