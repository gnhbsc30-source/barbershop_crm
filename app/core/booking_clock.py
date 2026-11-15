"""A local development prerequisite check, not a datetime conversion layer."""

from datetime import datetime
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo


def scheduling_clock_ready(business_timezone: str) -> bool:
    if business_timezone != "Asia/Jerusalem":
        return False
    configured = os.environ.get("TZ")
    if configured is None:
        try:
            target = str(Path("/etc/localtime").resolve(strict=True))
        except OSError:
            return False
        configured = target.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in target else None
    if configured != business_timezone:
        return False
    # Catch a runtime TZ edit that has not actually configured the C-library clock.
    # This inspects the current clock only; it does not interpret customer datetimes.
    try:
        stamp = time.time()
        return time.localtime(stamp).tm_gmtoff == int(datetime.fromtimestamp(stamp, ZoneInfo(business_timezone)).utcoffset().total_seconds())
    except (AttributeError, ValueError, OSError, KeyError):
        return False
