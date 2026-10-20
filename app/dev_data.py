"""Explicit local-only synthetic provisioning and credential rotation, never startup."""

import argparse
from contextlib import closing, contextmanager
from dataclasses import asdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import TextIO

from app.core.config import settings
from app.db import database, repositories
from app.services.authentication import (
    IdentityReference, generate_access_code,
    hash_access_secret, parse_access_code, resolve_principal,
)


class DevelopmentDataError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _guard_path(path: Path, environment: str) -> Path:
    if environment != "development":
        raise DevelopmentDataError("DEVELOPMENT_ONLY")
    path = Path(path).absolute()
    if path.name != "barbershop.db" or path.parent.name != "dev":
        raise DevelopmentDataError("DEDICATED_DEVELOPMENT_PATH_REQUIRED")
    if path.is_symlink() or path.parent.is_symlink():
        raise DevelopmentDataError("UNSAFE_DEVELOPMENT_PATH")
    return path.resolve()


def _private_file(path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
        or info.st_mode & 0o077 or info.st_nlink != 1
    ):
        raise DevelopmentDataError("UNSAFE_DEVELOPMENT_FILE")


def _private_directory(path: Path) -> None:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise DevelopmentDataError("UNSAFE_DEVELOPMENT_DIRECTORY")


def _confirm(input_stream: TextIO, output_stream: TextIO, phrase: str) -> None:
    if not input_stream.isatty() or not output_stream.isatty():
        raise DevelopmentDataError("INTERACTIVE_TERMINAL_REQUIRED")
    output_stream.write(f"Type {phrase} to continue. New access codes are shown once, after commit.\n")
    output_stream.flush()
    if input_stream.readline().rstrip("\r\n") != phrase:
        raise DevelopmentDataError("CONFIRMATION_REQUIRED")


def _manifest_path(path: Path) -> Path:
    return path.parent / "fixture-manifest.json"


def _schema_fingerprint() -> str:
    return hashlib.sha256(database.SCHEMA_PATH.read_bytes()).hexdigest()


def load_provisioned_identities(path: Path, *, environment: str) -> dict[str, IdentityReference]:
    """Load private server references only; DB identity must still be revalidated."""
    path = _guard_path(path, environment)
    try:
        _private_directory(path.parent)
        _private_file(path)
        manifest = _read_manifest(path)
        return {selector: IdentityReference(**reference)
                for selector, reference in manifest["identities"].items()}
    except (OSError, ValueError, KeyError, TypeError):
        raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT") from None


def _read_manifest(path: Path) -> dict:
    manifest_path = _manifest_path(path)
    _private_file(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("version")) is not int or manifest["version"] != 1
        or manifest["database"] != str(path)
        or manifest["schema_sha256"] != _schema_fingerprint()
        or type(manifest["business_id"]) is not int
        or not isinstance(manifest["identities"], dict)
        or len(manifest["identities"]) != 2
        or not isinstance(manifest["fixture"], dict)
    ):
        raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT")
    references = [IdentityReference(**value) for value in manifest["identities"].values()]
    if (
        {value.account_kind for value in references} != {"user", "customer_account"}
        or any(value.business_id != manifest["business_id"] for value in references)
    ):
        raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT")
    for selector in manifest["identities"]:
        if not isinstance(selector, str) or re.fullmatch(r"[A-Za-z0-9_-]{16}", selector) is None:
            raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT")
    return manifest


def _fixture() -> dict:
    return {
        "business_name": "Synthetic Development Barbershop",
        "timezone": "Asia/Jerusalem", "customer_name": "Synthetic Customer",
        "customer_email": "customer@example.test",
        "staff_names": ["Synthetic Barber A", "Synthetic Barber B"],
        "services": [("Haircut", 45, 80.0), ("Beard trim", 20, 40.0)],
        "working_days": [0, 1, 2, 3, 4], "start_time": "09:00", "end_time": "18:00",
        "booking_settings": [3, 0, 7, 30],
    }


def validate_development_data(path: Path, *, environment: str) -> dict[str, IdentityReference]:
    """Read-only startup validation; no provisioning, prompts, repair or reset."""
    identities = load_provisioned_identities(path, environment=environment)
    path = _guard_path(path, environment)
    try:
        with closing(database.open_development_database(path)) as connection:
            manifest = _read_manifest(path)
            _validate_references(connection, manifest)
            if repositories.get_development_fixture_snapshot(connection, manifest["business_id"]) != manifest["fixture"]:
                raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT")
    except (sqlite3.Error, ValueError, KeyError, TypeError, OSError):
        raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT") from None
    return identities


@contextmanager
def _provision_lock(path: Path):
    descriptor = os.open(path.parent / ".provision.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DevelopmentDataError("PROVISIONING_IN_PROGRESS") from None
        yield
    finally:
        os.close(descriptor)


def _account(connection, reference: IdentityReference) -> dict:
    resolve_principal(connection, reference)
    if reference.account_kind == "user":
        return repositories.get_user_account_identity(connection, reference.business_id, reference.account_id)
    return repositories.get_customer_account_identity(connection, reference.business_id, reference.account_id)


def _validate_references(connection, manifest: dict) -> None:
    for selector, values in manifest["identities"].items():
        if _account(connection, IdentityReference(**values))["username"] != selector:
            raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT")


def _write_manifest(path: Path, manifest: dict) -> None:
    descriptor = os.open(_manifest_path(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(manifest, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())


def _deliver(output_stream: TextIO, credentials: dict[str, str]) -> None:
    try:
        for label, access_code in credentials.items():
            output_stream.write(f"{label} access code: {access_code}\n")
        output_stream.flush()
    except (OSError, ValueError, KeyboardInterrupt):
        raise DevelopmentDataError("CREDENTIAL_DELIVERY_INTERRUPTED_ROTATE_AGAIN") from None


def provision_development_data(
    path: Path, *, environment: str, input_stream: TextIO, output_stream: TextIO,
) -> dict[str, IdentityReference]:
    path = _guard_path(path, environment)
    manifest_path = _manifest_path(path)
    if os.path.lexists(path) != os.path.lexists(manifest_path):
        raise DevelopmentDataError("UNKNOWN_OR_INCOMPLETE_DEVELOPMENT_DATA")
    if path.exists():
        identities = validate_development_data(path, environment=environment)
        if not input_stream.isatty() or not output_stream.isatty():
            raise DevelopmentDataError("INTERACTIVE_TERMINAL_REQUIRED")
        output_stream.write("Recognized dataset unchanged; credentials were not regenerated.\n")
        return identities
    _confirm(input_stream, output_stream, "PROVISION")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _private_directory(path.parent)
    with _provision_lock(path):
        if os.path.lexists(path) or os.path.lexists(manifest_path):
            raise DevelopmentDataError("DEVELOPMENT_DATA_CHANGED_RETRY")
        owner_code, customer_code = generate_access_code(), generate_access_code()
        owner_selector, owner_secret = parse_access_code(owner_code)
        customer_selector, customer_secret = parse_access_code(customer_code)
        if owner_selector == customer_selector:
            raise DevelopmentDataError("SELECTOR_COLLISION_RETRY_EXPLICITLY")
        owner_hash, customer_hash = hash_access_secret(owner_secret), hash_access_secret(customer_secret)
        try:
            with closing(database.open_development_database(path, create=True)) as connection:
                with database.transaction(connection):
                    business_id = repositories.create_development_fixture(
                        connection, fixture=_fixture(), owner_username=owner_selector,
                        owner_hash=owner_hash, customer_username=customer_selector, customer_hash=customer_hash,
                    )
                    fixture = repositories.get_development_fixture_snapshot(connection, business_id)
                    identities = {
                        owner_selector: IdentityReference("user", business_id, fixture["users"][0]["id"]),
                        customer_selector: IdentityReference("customer_account", business_id, fixture["customer_accounts"][0]["id"]),
                    }
                    _write_manifest(path, {
                        "version": 1, "database": str(path), "schema_sha256": _schema_fingerprint(),
                        "business_id": business_id, "fixture": fixture,
                        "identities": {selector: asdict(reference) for selector, reference in identities.items()},
                    })
        except (OSError, sqlite3.Error, ValueError):
            raise DevelopmentDataError("PROVISIONING_INCOMPLETE_NO_AUTOMATIC_RESET") from None
        _deliver(output_stream, {"OWNER": owner_code, "CUSTOMER": customer_code})
        return identities


def rotate_development_credential(
    path: Path, *, selector: str, environment: str,
    input_stream: TextIO, output_stream: TextIO,
) -> None:
    identities = load_provisioned_identities(path, environment=environment)
    path = _guard_path(path, environment)
    reference = identities.get(selector)
    if reference is None:
        raise DevelopmentDataError("PROVISIONED_IDENTITY_NOT_FOUND")
    try:
        with closing(database.open_development_database(path)) as connection:
            before = _account(connection, reference)
            if before["username"] != selector:
                raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT")
            if not input_stream.isatty() or not output_stream.isatty():
                raise DevelopmentDataError("INTERACTIVE_TERMINAL_REQUIRED")
            output_stream.write(
                f"Rotate {reference.account_kind} {reference.account_id} in business {reference.business_id}.\n"
            )
            _confirm(input_stream, output_stream,
                     f"ROTATE {selector}")
            new_code = generate_access_code()
            _, new_secret = parse_access_code(new_code)
            new_code = f"{selector}.{new_secret}"
            new_hash = hash_access_secret(new_secret)
            with database.transaction(connection):
                current = _account(connection, reference)
                if current["username"] != selector:
                    raise DevelopmentDataError("DEVELOPMENT_DATA_INCONSISTENT")
                if not repositories.replace_account_credential(
                    connection, account_kind=reference.account_kind, business_id=reference.business_id,
                    account_id=reference.account_id, username=selector,
                    expected_hash=before["password_hash"], new_hash=new_hash,
                ):
                    raise DevelopmentDataError("CREDENTIAL_CHANGED_RETRY_EXPLICITLY")
    except (sqlite3.Error, OSError):
        raise DevelopmentDataError("ROTATION_FAILED_VERIFY_STATE_OR_ROTATE_AGAIN") from None
    except ValueError as error:
        if isinstance(error, DevelopmentDataError):
            raise
        raise DevelopmentDataError("DEVELOPMENT_IDENTITY_INVALID") from None
    _deliver(output_stream, {reference.account_kind.upper(): new_code})


def main(argv=None, *, input_stream=None, output_stream=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("provision")
    rotate = commands.add_parser("rotate-credential")
    rotate.add_argument("--identity", required=True)
    args = parser.parse_args(argv)
    input_stream = input_stream if input_stream is not None else sys.stdin
    output_stream = output_stream if output_stream is not None else sys.stdout
    try:
        path = _guard_path(args.database, settings.environment)
        if path != database.DATABASE_PATH.resolve():
            raise DevelopmentDataError("CONFIGURED_DATABASE_MISMATCH")
        if args.command == "provision":
            provision_development_data(path, environment=settings.environment,
                                       input_stream=input_stream, output_stream=output_stream)
        else:
            rotate_development_credential(path, selector=args.identity, environment=settings.environment,
                                          input_stream=input_stream, output_stream=output_stream)
        return 0
    except (DevelopmentDataError, KeyboardInterrupt, OSError) as error:
        code = error.code if isinstance(error, DevelopmentDataError) else "DEVELOPMENT_OPERATION_INTERRUPTED_VERIFY_STATE"
        sys.stderr.write(f"{code}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
