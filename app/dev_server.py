"""Supported local HTTP launcher; no provisioning or production deployment."""

import sys

import uvicorn

from app.core.config import settings
from app.core.local_security import validate_local_startup
from app.db import database
from app.dev_data import DevelopmentDataError


def main() -> int:
    try:
        validate_local_startup(database.DATABASE_PATH, settings.environment)
    except (DevelopmentDataError, OSError):
        sys.stderr.write("DEVELOPMENT_STARTUP_REFUSED\n")
        return 1
    uvicorn.run(
        "app.main:app", host="127.0.0.1", port=8000, workers=1, reload=False,
        proxy_headers=False, forwarded_allow_ips="", access_log=False,
        server_header=False, log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
