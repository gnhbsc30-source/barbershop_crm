"""Identity/credential tests use only the isolated temporary test database."""

from dataclasses import FrozenInstanceError
import hashlib

from argon2 import extract_parameters
from argon2.low_level import Type
import pytest

from app.db.repositories import (
    get_customer_account_identity,
    get_user_account_identity,
)
from app.services import authentication as auth
from app.services.authentication import (
    AuthenticationError,
    IdentityReference,
    Principal,
    PrincipalRole,
    authenticate_access_code,
    credential_fingerprint,
    generate_access_code,
    hash_access_secret,
    parse_access_code,
    resolve_principal,
    verify_access_secret,
)


# Deliberately synthetic values, used only in temporary test databases.
OWNER_SELECTOR = "o" * 16
CUSTOMER_SELECTOR = "c" * 16
SECRET = "s" * 43


@pytest.fixture(scope="module")
def encoded_hash():
    return hash_access_secret(SECRET)


@pytest.fixture
def identities(test_database, encoded_hash):
    connection = test_database
    connection.executemany(
        "INSERT INTO businesses (id, business_name) VALUES (?, ?)",
        [(1, "Synthetic Shop A"), (2, "Synthetic Shop B")],
    )
    connection.executemany(
        """
        INSERT INTO users (id, business_id, username, password_hash, role)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(1, 1, OWNER_SELECTOR, encoded_hash, "OWNER"),
         (2, 2, OWNER_SELECTOR, encoded_hash, "OWNER")],
    )
    connection.executemany(
        "INSERT INTO customers (id, business_id, name, customer_type) VALUES (?, ?, ?, ?)",
        [(10, 1, "Synthetic Customer A", "REGISTERED"),
         (20, 2, "Synthetic Customer B", "REGISTERED"),
         (30, 2, "Unlinked Synthetic Customer", "REGISTERED")],
    )
    connection.executemany(
        """
        INSERT INTO customer_accounts (id, business_id, customer_id, username, password_hash)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(1, 1, 10, CUSTOMER_SELECTOR, encoded_hash),
         (2, 2, 20, CUSTOMER_SELECTOR, encoded_hash)],
    )
    connection.commit()
    return {
        OWNER_SELECTOR: IdentityReference("user", 1, 1),
        CUSTOMER_SELECTOR: IdentityReference("customer_account", 1, 1),
    }


def assert_invalid(operation):
    with pytest.raises(AuthenticationError) as caught:
        operation()
    assert caught.value.code == "INVALID_CREDENTIALS"
    assert str(caught.value) == "INVALID_CREDENTIALS"
    assert SECRET not in str(caught.value)


def test_valid_owner_identity_and_login(test_database, identities):
    principal = authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{SECRET}", identities)
    assert principal == Principal("user:1", PrincipalRole.OWNER, 1, owner_user_id=1)
    assert resolve_principal(test_database, identities[OWNER_SELECTOR]) == principal
    assert principal.customer_id is None


def test_valid_customer_uses_account_to_customer_link(test_database, identities):
    principal = authenticate_access_code(test_database, f"{CUSTOMER_SELECTOR}.{SECRET}", identities)
    assert principal == Principal("customer_account:1", PrincipalRole.CUSTOMER, 1, customer_id=10)
    assert resolve_principal(test_database, identities[CUSTOMER_SELECTOR]) == principal
    assert principal.owner_user_id is None


def test_cross_role_numeric_ids_have_distinct_subjects(test_database, identities):
    owner = resolve_principal(test_database, identities[OWNER_SELECTOR])
    customer = resolve_principal(test_database, identities[CUSTOMER_SELECTOR])
    assert owner.subject != customer.subject
    assert owner.subject == "user:1"
    assert customer.subject == "customer_account:1"


def test_principal_and_reference_are_immutable(test_database, identities):
    principal = resolve_principal(test_database, identities[OWNER_SELECTOR])
    with pytest.raises(FrozenInstanceError):
        principal.business_id = 2
    with pytest.raises(FrozenInstanceError):
        identities[OWNER_SELECTOR].business_id = 2


@pytest.mark.parametrize("values", [
    {"subject": "user:1", "role": "OWNER", "business_id": 1, "owner_user_id": 1},
    {"subject": "user:1", "role": PrincipalRole.OWNER, "business_id": True, "owner_user_id": 1},
    {"subject": "user:2", "role": PrincipalRole.OWNER, "business_id": 1, "owner_user_id": 1},
    {"subject": "user:1", "role": PrincipalRole.OWNER, "business_id": 1},
    {"subject": "user:1", "role": PrincipalRole.OWNER, "business_id": 1, "owner_user_id": 1, "customer_id": 10},
    {"subject": "user:1", "role": PrincipalRole.CUSTOMER, "business_id": 1, "customer_id": 10},
    {"subject": "customer_account:1", "role": PrincipalRole.CUSTOMER, "business_id": 1},
    {"subject": "customer_account:1", "role": PrincipalRole.CUSTOMER, "business_id": 1, "customer_id": 10, "owner_user_id": 1},
])
def test_principal_rejects_inconsistent_shape(values):
    assert_invalid(lambda: Principal(**values))


@pytest.mark.parametrize("values", [
    ("staff", 1, 1), ("user", 0, 1), ("user", 1, -1),
    ("customer_account", True, 1), ("user", 1, "1"),
])
def test_reference_rejects_invalid_shape(values):
    assert_invalid(lambda: IdentityReference(*values))


@pytest.mark.parametrize("selector,statement", [
    (OWNER_SELECTOR, "UPDATE users SET is_active = 0 WHERE id = 1"),
    (CUSTOMER_SELECTOR, "UPDATE customer_accounts SET is_active = 0 WHERE id = 1"),
    (CUSTOMER_SELECTOR, "UPDATE customers SET is_active = 0 WHERE id = 10"),
    (OWNER_SELECTOR, "UPDATE businesses SET is_active = 0 WHERE id = 1"),
    (CUSTOMER_SELECTOR, "UPDATE businesses SET is_active = 0 WHERE id = 1"),
    (OWNER_SELECTOR, "UPDATE users SET role = 'MANAGER' WHERE id = 1"),
    (OWNER_SELECTOR, "UPDATE users SET role = 'STAFF' WHERE id = 1"),
])
def test_inactive_or_unsupported_identity_fails_generically(test_database, identities, selector, statement):
    test_database.execute(statement)
    assert_invalid(lambda: resolve_principal(test_database, identities[selector]))
    assert_invalid(lambda: authenticate_access_code(test_database, f"{selector}.{SECRET}", identities))


@pytest.mark.parametrize("kind,selector,account_id", [
    ("user", OWNER_SELECTOR, 999),
    ("customer_account", CUSTOMER_SELECTOR, 999),
    ("user", OWNER_SELECTOR, 2),
    ("customer_account", CUSTOMER_SELECTOR, 2),
])
def test_missing_or_foreign_tenant_account_is_rejected(test_database, identities, kind, selector, account_id):
    identities[selector] = IdentityReference(kind, 1, account_id)
    assert_invalid(lambda: resolve_principal(test_database, identities[selector]))
    assert_invalid(lambda: authenticate_access_code(test_database, f"{selector}.{SECRET}", identities))


@pytest.mark.parametrize("customer_id", [999, 30])
def test_corrupt_missing_or_foreign_customer_link_is_rejected(test_database, identities, customer_id):
    # Simulate corrupt legacy data; normal writes are protected by the composite FK.
    test_database.execute("PRAGMA foreign_keys = OFF")
    test_database.execute("UPDATE customer_accounts SET customer_id = ? WHERE id = 1", (customer_id,))
    test_database.commit()
    test_database.execute("PRAGMA foreign_keys = ON")
    account = get_customer_account_identity(test_database, 1, 1)
    assert account["linked_customer_id"] is None
    assert_invalid(lambda: resolve_principal(test_database, identities[CUSTOMER_SELECTOR]))
    assert_invalid(lambda: authenticate_access_code(test_database, f"{CUSTOMER_SELECTOR}.{SECRET}", identities))


def test_missing_business_is_rejected(test_database, identities):
    test_database.execute("PRAGMA foreign_keys = OFF")
    test_database.execute("DELETE FROM businesses WHERE id = 1")
    test_database.commit()
    test_database.execute("PRAGMA foreign_keys = ON")
    for reference in identities.values():
        assert_invalid(lambda: resolve_principal(test_database, reference))


def test_untrusted_identity_objects_are_rejected(test_database):
    assert_invalid(lambda: resolve_principal(test_database, {"account_kind": "user", "business_id": 1, "account_id": 1}))


def test_selector_must_match_the_referenced_account(test_database, identities):
    identities[CUSTOMER_SELECTOR] = identities[OWNER_SELECTOR]
    assert_invalid(lambda: authenticate_access_code(test_database, f"{CUSTOMER_SELECTOR}.{SECRET}", identities))


def test_wrong_secret_has_generic_public_failure(test_database, identities):
    assert_invalid(lambda: authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{'w' * 43}", identities))


def test_unknown_selector_performs_dummy_verification(test_database, identities, monkeypatch):
    calls = []
    actual_verify = auth.verify_access_secret

    def track_verify(encoded, secret):
        calls.append((encoded, secret))
        return actual_verify(encoded, secret)

    monkeypatch.setattr(auth, "verify_access_secret", track_verify)
    assert_invalid(lambda: authenticate_access_code(test_database, f"{'x' * 16}.{SECRET}", identities))
    assert len(calls) == 1
    assert calls[0] == (auth._DUMMY_HASH, SECRET)


def test_existing_account_not_in_trusted_mapping_is_unknown(test_database, identities):
    assert_invalid(lambda: authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{SECRET}", {}))


@pytest.mark.parametrize("value", [
    None, 123, b"not-a-string", "", ".", "owner.secret",
    f"{'o' * 15}.{SECRET}", f"{OWNER_SELECTOR}.{'s' * 42}",
    f"{OWNER_SELECTOR}.{SECRET} ", f"{OWNER_SELECTOR}.{SECRET}\n",
    f"{OWNER_SELECTOR}.{SECRET}.extra", f"{'é' * 16}.{SECRET}",
    f"{OWNER_SELECTOR}.{'s' * 42}/", f"{OWNER_SELECTOR}.{'s' * 42}\x00",
    "x" * 10000,
])
def test_malformed_access_codes_are_rejected(value):
    assert_invalid(lambda: parse_access_code(value))


def test_generated_access_codes_are_random_and_parseable(monkeypatch):
    first, second = generate_access_code(), generate_access_code()
    assert first != second
    selector, secret = parse_access_code(first)
    assert len(selector) == 16
    assert len(secret) == 43
    calls = []

    def fake_token_urlsafe(size):
        calls.append(size)
        return "o" * 16 if size == 12 else SECRET

    monkeypatch.setattr(auth.secrets, "token_urlsafe", fake_token_urlsafe)
    assert generate_access_code() == f"{OWNER_SELECTOR}.{SECRET}"
    assert calls == [12, 32]


def test_argon2id_parameters_verification_and_random_salt(encoded_hash):
    parameters = extract_parameters(encoded_hash)
    assert parameters.type is Type.ID
    assert parameters.memory_cost == 19 * 1024
    assert parameters.time_cost == 2
    assert parameters.parallelism == 1
    assert parameters.salt_len == 16
    assert parameters.hash_len == 32
    assert verify_access_secret(encoded_hash, SECRET)
    assert not verify_access_secret(encoded_hash, "w" * 43)
    assert hash_access_secret(SECRET) != encoded_hash


@pytest.mark.parametrize("secret", [None, b"s" * 43, "", "s" * 42, "s" * 44, "é" * 43])
def test_secret_hashing_rejects_invalid_input(secret, encoded_hash):
    assert_invalid(lambda: hash_access_secret(secret))
    assert not verify_access_secret(encoded_hash, secret)


@pytest.mark.parametrize("invalid_hash", [None, "", "plaintext", "$argon2id$broken", "$argon2i$broken"])
def test_invalid_stored_hashes_do_not_escape_verification(invalid_hash):
    assert not verify_access_secret(invalid_hash, SECRET)
    assert_invalid(lambda: credential_fingerprint(invalid_hash))


def test_invalid_database_hash_has_generic_failure(test_database, identities):
    test_database.execute("UPDATE users SET password_hash = 'invalid' WHERE id = 1")
    assert_invalid(lambda: authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{SECRET}", identities))


def test_fingerprint_changes_after_database_hash_replacement(test_database, identities, encoded_hash):
    before = credential_fingerprint(encoded_hash)
    new_hash = hash_access_secret("n" * 43)
    test_database.execute("UPDATE users SET password_hash = ? WHERE id = 1", (new_hash,))
    after = credential_fingerprint(get_user_account_identity(test_database, 1, 1)["password_hash"])
    assert before == credential_fingerprint(encoded_hash)
    assert len(before) == len(after) == 64
    assert before != after
    assert_invalid(lambda: authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{SECRET}", identities))
    assert authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{'n' * 43}", identities).role is PrincipalRole.OWNER


def test_repository_reads_are_tenant_scoped_and_do_not_filter_policy(test_database, identities):
    assert get_user_account_identity(test_database, 1, 2) is None
    assert get_customer_account_identity(test_database, 1, 2) is None
    test_database.execute("UPDATE users SET role = 'MANAGER', is_active = 0 WHERE id = 1")
    test_database.execute("UPDATE customer_accounts SET is_active = 0 WHERE id = 1")
    owner = get_user_account_identity(test_database, 1, 1)
    customer = get_customer_account_identity(test_database, 1, 1)
    assert owner["role"] == "MANAGER"
    assert owner["is_active"] == customer["is_active"] == 0
    assert test_database.in_transaction  # Neither reads nor authentication commit/rollback.
    assert_invalid(lambda: authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{SECRET}", identities))
    assert test_database.in_transaction
    test_database.rollback()
    assert get_user_account_identity(test_database, 1, 1)["role"] == "OWNER"


@pytest.mark.parametrize("selector,kind", [(OWNER_SELECTOR, "user"), (CUSTOMER_SELECTOR, "customer_account")])
def test_second_tenant_can_authenticate_with_its_own_mapping(test_database, identities, selector, kind):
    principal = authenticate_access_code(
        test_database, f"{selector}.{SECRET}", {selector: IdentityReference(kind, 2, 2)}
    )
    assert principal.business_id == 2
    assert principal.subject == ("user:2" if kind == "user" else "customer_account:2")
    assert principal.customer_id == (None if kind == "user" else 20)


def test_metadata_is_not_payload_validation(test_database, identities):
    malformed = "$argon2id$v=19$m=19456,t=2,p=1$!$!"
    assert auth._has_argon2id_metadata(malformed)
    assert not verify_access_secret(malformed, SECRET)
    test_database.execute("UPDATE users SET password_hash = ? WHERE id = 1", (malformed,))
    assert_invalid(lambda: authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{SECRET}", identities))


def test_exact_fingerprint_and_same_secret_rehash(encoded_hash):
    assert credential_fingerprint(encoded_hash) == hashlib.sha256(encoded_hash.encode("ascii")).hexdigest()
    assert credential_fingerprint(hash_access_secret(SECRET)) != credential_fingerprint(encoded_hash)


@pytest.mark.parametrize("invalid", ["missing", "inactive", "mismatch"])
def test_invalid_mapped_identity_performs_dummy_verification(test_database, identities, monkeypatch, invalid):
    if invalid == "missing":
        identities[OWNER_SELECTOR] = IdentityReference("user", 1, 999)
    elif invalid == "inactive":
        test_database.execute("UPDATE users SET is_active = 0 WHERE id = 1")
    else:
        identities[OWNER_SELECTOR] = identities[CUSTOMER_SELECTOR]
    calls = []
    monkeypatch.setattr(auth, "verify_access_secret", lambda encoded, secret: calls.append(encoded) or False)
    assert_invalid(lambda: authenticate_access_code(test_database, f"{OWNER_SELECTOR}.{SECRET}", identities))
    assert calls == [auth._DUMMY_HASH]


@pytest.mark.parametrize("pending", [False, True])
def test_successful_authentication_preserves_transaction_state(test_database, identities, pending):
    if pending:
        test_database.execute("UPDATE businesses SET display_name = 'Pending' WHERE id = 1")
    changes = test_database.total_changes
    for selector in identities:
        authenticate_access_code(test_database, f"{selector}.{SECRET}", identities)
    assert test_database.in_transaction is pending
    assert test_database.total_changes == changes
