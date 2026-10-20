"""Identity and credential functions; no sessions, HTTP or provisioning state."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import re
import secrets
from typing import Any, Literal

from argon2 import PasswordHasher, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type

from app.db.repositories import (
    get_customer_account_identity,
    get_user_account_identity,
)


class AuthenticationError(ValueError):
    """Public rejection deliberately does not identify the failing credential."""

    def __init__(self):
        super().__init__("INVALID_CREDENTIALS")
        self.code = "INVALID_CREDENTIALS"


class PrincipalRole(str, Enum):
    OWNER = "OWNER"
    CUSTOMER = "CUSTOMER"


def _positive_id(value: object) -> bool:
    return type(value) is int and value > 0


@dataclass(frozen=True)
class Principal:
    subject: str
    role: PrincipalRole
    business_id: int
    customer_id: int | None = None
    owner_user_id: int | None = None

    def __post_init__(self):
        if not _positive_id(self.business_id) or not isinstance(self.role, PrincipalRole):
            raise AuthenticationError()
        if self.role is PrincipalRole.OWNER:
            if (
                not _positive_id(self.owner_user_id)
                or self.customer_id is not None
                or self.subject != f"user:{self.owner_user_id}"
            ):
                raise AuthenticationError()
        elif (
            not _positive_id(self.customer_id)
            or self.owner_user_id is not None
            or not isinstance(self.subject, str)
            or re.fullmatch(r"customer_account:[1-9][0-9]*", self.subject) is None
        ):
            raise AuthenticationError()


@dataclass(frozen=True)
class IdentityReference:
    """Trusted server-side locator, not client-provided authorization."""

    account_kind: Literal["user", "customer_account"]
    business_id: int
    account_id: int

    def __post_init__(self):
        if (
            self.account_kind not in ("user", "customer_account")
            or not _positive_id(self.business_id)
            or not _positive_id(self.account_id)
        ):
            raise AuthenticationError()


_SELECTOR_PATTERN = r"[A-Za-z0-9_-]{16}"
_SECRET_PATTERN = r"[A-Za-z0-9_-]{43}"
_ACCESS_CODE = re.compile(rf"({_SELECTOR_PATTERN})\.({_SECRET_PATTERN})")
_PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
# Non-login dummy material is generated in memory, never persisted or disclosed.
_DUMMY_HASH = _PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def generate_access_code() -> str:
    """Return a random selector and 256-bit secret; do not store or log them."""
    return f"{secrets.token_urlsafe(12)}.{secrets.token_urlsafe(32)}"


def parse_access_code(access_code: str) -> tuple[str, str]:
    if not isinstance(access_code, str) or len(access_code) != 60:
        raise AuthenticationError()
    match = _ACCESS_CODE.fullmatch(access_code)
    if match is None:
        raise AuthenticationError()
    return match[1], match[2]


def hash_access_secret(secret: str) -> str:
    if not isinstance(secret, str) or re.fullmatch(_SECRET_PATTERN, secret) is None:
        raise AuthenticationError()
    return _PASSWORD_HASHER.hash(secret)


def _has_argon2id_metadata(encoded_hash: object) -> bool:
    """Inspect metadata only; Argon2 verification validates the actual payload."""
    if not isinstance(encoded_hash, str):
        return False
    try:
        return extract_parameters(encoded_hash).type is Type.ID
    except (InvalidHashError, ValueError):
        return False


def verify_access_secret(encoded_hash: str, secret: str) -> bool:
    """Reject mismatches and malformed/non-Argon2id hashes without exposing them."""
    if (
        not isinstance(secret, str)
        or re.fullmatch(_SECRET_PATTERN, secret) is None
        or not _has_argon2id_metadata(encoded_hash)
    ):
        return False
    try:
        return _PASSWORD_HASHER.verify(encoded_hash, secret)
    except (VerificationError, InvalidHashError, UnicodeError):
        return False


def credential_fingerprint(encoded_hash: str) -> str:
    """Internal change detector, not a credential and never a public response."""
    if not _has_argon2id_metadata(encoded_hash):
        raise AuthenticationError()
    try:
        return hashlib.sha256(encoded_hash.encode("ascii")).hexdigest()
    except UnicodeError:
        raise AuthenticationError() from None


def _read_account(connection, identity: IdentityReference) -> dict[str, Any] | None:
    if identity.account_kind == "user":
        return get_user_account_identity(connection, identity.business_id, identity.account_id)
    return get_customer_account_identity(connection, identity.business_id, identity.account_id)


def _principal_from_account(
    identity: IdentityReference,
    account: dict[str, Any] | None,
) -> Principal:
    if (
        account is None
        or account["id"] != identity.account_id
        or account["business_id"] != identity.business_id
        or account["is_active"] != 1
        or account["business_is_active"] != 1
    ):
        raise AuthenticationError()
    if identity.account_kind == "user":
        if account["role"] != "OWNER":
            raise AuthenticationError()
        return Principal(
            subject=f"user:{account['id']}",
            role=PrincipalRole.OWNER,
            business_id=account["business_id"],
            owner_user_id=account["id"],
        )
    if (
        account["customer_is_active"] != 1
        or account["customer_id"] != account["linked_customer_id"]
        or account["linked_customer_business_id"] != identity.business_id
    ):
        raise AuthenticationError()
    return Principal(
        subject=f"customer_account:{account['id']}",
        role=PrincipalRole.CUSTOMER,
        business_id=account["business_id"],
        customer_id=account["customer_id"],
    )


def resolve_principal(connection, identity: IdentityReference) -> Principal:
    """Validate current DB identity truth from a trusted server-side reference."""
    if not isinstance(identity, IdentityReference):
        raise AuthenticationError()
    return _principal_from_account(identity, _read_account(connection, identity))


def authenticate_access_code(
    connection,
    access_code: str,
    provisioned_identities: Mapping[str, IdentityReference],
) -> Principal:
    """Authenticate only selectors in the supplied trusted provisioning mapping."""
    selector, secret = parse_access_code(access_code)
    identity = provisioned_identities.get(selector)
    if not isinstance(identity, IdentityReference):
        verify_access_secret(_DUMMY_HASH, secret)
        raise AuthenticationError()
    account = _read_account(connection, identity)
    try:
        principal = _principal_from_account(identity, account)
        if account["username"] != selector or not _has_argon2id_metadata(account["password_hash"]):
            raise AuthenticationError()
    except AuthenticationError:
        verify_access_secret(_DUMMY_HASH, secret)
        raise AuthenticationError() from None
    if not verify_access_secret(account["password_hash"], secret):
        raise AuthenticationError()
    return principal
