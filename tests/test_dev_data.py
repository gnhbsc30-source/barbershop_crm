from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from io import StringIO
import json
import stat
from threading import Barrier, Event

import pytest

from app import dev_data
from app.core.auth_sessions import InMemorySessionStore, SessionError
from app.db import database, repositories
from app.services.authentication import (
    AuthenticationError, authenticate_access_code, credential_fingerprint, resolve_principal,
)


class Terminal(StringIO):
    def isatty(self):
        return True


@pytest.fixture
def dev_path(tmp_path):
    return tmp_path / "data" / "dev" / "barbershop.db"


def provision(path, **kwargs):
    output = kwargs.pop("output_stream", Terminal())
    identities = dev_data.provision_development_data(
        path, environment="development", input_stream=Terminal("PROVISION\n"),
        output_stream=output, **kwargs,
    )
    codes = [line.split("access code: ", 1)[1] for line in output.getvalue().splitlines() if "access code: " in line]
    return identities, codes, output


def rotate(path, selector, **kwargs):
    output = kwargs.pop("output_stream", Terminal())
    dev_data.rotate_development_credential(
        path, selector=selector, environment="development",
        input_stream=Terminal(f"ROTATE {selector}\n"), output_stream=output, **kwargs,
    )
    codes = [line.split("access code: ", 1)[1] for line in output.getvalue().splitlines() if "access code: " in line]
    return codes[0]


def account(connection, reference):
    if reference.account_kind == "user":
        return repositories.get_user_account_identity(connection, reference.business_id, reference.account_id)
    return repositories.get_customer_account_identity(connection, reference.business_id, reference.account_id)


def snapshot(path):
    with closing(database.open_development_database(path)) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall()
        return {row["name"]: [tuple(item) for item in connection.execute(f'SELECT * FROM "{row["name"]}" ORDER BY rowid')]
                for row in tables}


def test_initial_dataset_identity_codes_permissions_and_no_secret_manifest(dev_path, caplog):
    identities, codes, _ = provision(dev_path)
    assert len(identities) == len(codes) == 2
    assert dev_data.load_provisioned_identities(dev_path, environment="development") == identities
    manifest_text = dev_data._manifest_path(dev_path).read_text()
    with closing(database.open_development_database(dev_path)) as connection:
        assert {authenticate_access_code(connection, code, identities).role.value for code in codes} == {"OWNER", "CUSTOMER"}
        fixture = repositories.get_development_fixture_snapshot(connection, 1)
        assert len(fixture["staff"]) == len(fixture["services"]) == 2
        assert len(fixture["staff_services"]) == 4
        assert len(fixture["business_working_hours"]) == 7
        assert len(fixture["working_hours"]) == 14
        assert connection.execute("SELECT count(*) FROM appointments").fetchone()[0] == 0
        setting = fixture["business_settings"][0]
        assert [setting[key] for key in ("booking_window_months", "minimum_booking_notice_minutes", "alternative_search_window_days", "booking_interval_minutes")] == [3, 0, 7, 30]
        for reference in identities.values():
            assert account(connection, reference)["password_hash"].startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    for code in codes:
        assert code not in manifest_text and code not in caplog.text
        assert code.split(".")[1] not in manifest_text
    assert "password_hash" not in manifest_text
    assert stat.S_IMODE(dev_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(dev_data._manifest_path(dev_path).stat().st_mode) == 0o600
    assert stat.S_IMODE(dev_path.parent.stat().st_mode) == 0o700


def add_appointment(path):
    with closing(database.open_development_database(path)) as connection:
        with database.transaction(connection):
            repositories.create_appointment(
                connection, 1, 1, 1, "2027-01-05T10:00:00", "2027-01-05T10:45:00",
                items=[{"service_id": 1, "service_name": "Haircut", "duration_minutes": 45, "price": 80.0}],
            )


def test_repeat_is_non_destructive_without_credentials_or_duplicates(dev_path, monkeypatch):
    identities, codes, _ = provision(dev_path)
    add_appointment(dev_path)
    before = snapshot(dev_path)
    manifest = dev_data._manifest_path(dev_path).read_bytes()
    monkeypatch.setattr(dev_data, "generate_access_code", lambda: pytest.fail("must not regenerate"))
    again, new_codes, _ = provision(dev_path)
    assert again == identities and new_codes == []
    assert snapshot(dev_path) == before
    assert dev_data._manifest_path(dev_path).read_bytes() == manifest


@pytest.mark.parametrize("kind", ["database_only", "manifest_only", "inconsistent", "bad_schema", "bad_json", "bad_shape"])
def test_unknown_incomplete_or_inconsistent_data_refused_without_changes(dev_path, kind):
    if kind in ("database_only", "manifest_only"):
        dev_path.parent.mkdir(parents=True, mode=0o700)
        target = dev_path if kind == "database_only" else dev_data._manifest_path(dev_path)
        target.write_text("unknown")
    else:
        provision(dev_path)
        if kind == "inconsistent":
            with closing(database.open_development_database(dev_path)) as connection:
                connection.execute("UPDATE staff SET name = 'Changed' WHERE id = 1")
                connection.commit()
        else:
            target = dev_data._manifest_path(dev_path)
            manifest = json.loads(target.read_text())
            if kind == "bad_schema":
                manifest["schema_sha256"] = "different"
            elif kind == "bad_shape":
                manifest["identities"] = []
            target.write_text("invalid json" if kind == "bad_json" else json.dumps(manifest))
    before = {path.name: path.read_bytes() for path in dev_path.parent.iterdir()}
    with pytest.raises(dev_data.DevelopmentDataError):
        provision(dev_path)
    assert {path.name: path.read_bytes() for path in dev_path.parent.iterdir()} == before


@pytest.mark.parametrize("kind", ["user", "customer_account"])
def test_rotation_rejects_old_code_and_preserves_other_business_data(dev_path, kind, caplog):
    identities, codes, _ = provision(dev_path)
    add_appointment(dev_path)
    selector = next(key for key, ref in identities.items() if ref.account_kind == kind)
    before = snapshot(dev_path)
    manifest = dev_data._manifest_path(dev_path).read_bytes()
    new_code = rotate(dev_path, selector)
    with closing(database.open_development_database(dev_path)) as connection:
        assert authenticate_access_code(connection, new_code, identities) == resolve_principal(connection, identities[selector])
        for code in codes:
            if code.startswith(selector + "."):
                with pytest.raises(AuthenticationError):
                    authenticate_access_code(connection, code, identities)
            else:
                assert authenticate_access_code(connection, code, identities)
    after = snapshot(dev_path)
    table = "users" if kind == "user" else "customer_accounts"
    assert before[table] != after[table]
    before.pop(table)
    after.pop(table)
    assert before == after
    assert dev_data._manifest_path(dev_path).read_bytes() == manifest
    assert new_code not in caplog.text


def test_rotation_fingerprint_rejects_old_sessions_but_not_other_identity(dev_path):
    identities, codes, _ = provision(dev_path)
    store = InMemorySessionStore()
    issued = {}
    with closing(database.open_development_database(dev_path)) as connection:
        for code in codes:
            selector = code.split(".")[0]
            principal = authenticate_access_code(connection, code, identities)
            issued[selector] = [store.create(principal, credential_fingerprint(account(connection, identities[selector])["password_hash"])) for _ in range(2)]
    selector = next(iter(identities))
    rotate(dev_path, selector)
    # This is test composition, not a DB dependency inside the session store.
    with closing(database.open_development_database(dev_path)) as connection:
        for key, sessions in issued.items():
            principal = resolve_principal(connection, identities[key])
            fingerprint = credential_fingerprint(account(connection, identities[key])["password_hash"])
            for session in sessions:
                if key == selector:
                    with pytest.raises(SessionError):
                        store.validate_identity(session.token, principal, fingerprint)
                else:
                    assert store.validate_identity(session.token, principal, fingerprint)


@pytest.mark.parametrize("operation", ["provision", "rotate"])
@pytest.mark.parametrize("failure", ["not_tty", "not_confirmed"])
def test_terminal_and_confirmation_are_required(dev_path, operation, failure):
    if operation == "rotate":
        identities, _, _ = provision(dev_path)
        selector = next(iter(identities))
        before = snapshot(dev_path)
    input_stream = StringIO("PROVISION\n") if failure == "not_tty" else Terminal("NO\n")
    with pytest.raises(dev_data.DevelopmentDataError, match="INTERACTIVE_TERMINAL_REQUIRED|CONFIRMATION_REQUIRED"):
        if operation == "provision":
            dev_data.provision_development_data(dev_path, environment="development", input_stream=input_stream, output_stream=Terminal())
        else:
            dev_data.rotate_development_credential(dev_path, selector=selector, environment="development", input_stream=input_stream, output_stream=Terminal())
    if operation == "provision":
        assert not dev_path.parent.exists()
    else:
        assert snapshot(dev_path) == before


def test_concurrent_rotations_cannot_overwrite_each_other(dev_path, monkeypatch):
    identities, _, _ = provision(dev_path)
    selector = next(iter(identities))
    barrier = Barrier(2)
    original = dev_data.hash_access_secret

    def synchronize(secret):
        barrier.wait(timeout=5)
        return original(secret)

    monkeypatch.setattr(dev_data, "hash_access_secret", synchronize)

    def attempt(_):
        try:
            return rotate(dev_path, selector)
        except dev_data.DevelopmentDataError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count("CREDENTIAL_CHANGED_RETRY_EXPLICITLY") == 1
    code = next(value for value in results if "." in value)
    with closing(database.open_development_database(dev_path)) as connection:
        assert authenticate_access_code(connection, code, identities)


class InterruptedDelivery(Terminal):
    def write(self, value):
        if "access code: " in value:
            raise OSError("terminal unavailable")
        return super().write(value)


@pytest.mark.parametrize("operation", ["provision", "rotate"])
def test_interrupted_delivery_recovers_only_through_explicit_rotation(dev_path, operation, caplog):
    if operation == "rotate":
        identities, _, _ = provision(dev_path)
        selector = next(iter(identities))
    with pytest.raises(dev_data.DevelopmentDataError) as caught:
        if operation == "provision":
            provision(dev_path, output_stream=InterruptedDelivery())
        else:
            rotate(dev_path, selector, output_stream=InterruptedDelivery())
    assert str(caught.value) == "CREDENTIAL_DELIVERY_INTERRUPTED_ROTATE_AGAIN"
    identities = dev_data.load_provisioned_identities(dev_path, environment="development")
    selector = next(iter(identities))
    recovered = rotate(dev_path, selector)
    with closing(database.open_development_database(dev_path)) as connection:
        assert authenticate_access_code(connection, recovered, identities)
    assert recovered not in caplog.text


def test_production_wrong_path_and_unknown_identity_are_refused(dev_path):
    with pytest.raises(dev_data.DevelopmentDataError, match="DEVELOPMENT_ONLY"):
        dev_data.provision_development_data(dev_path, environment="production", input_stream=Terminal("PROVISION\n"), output_stream=Terminal())
    with pytest.raises(dev_data.DevelopmentDataError, match="DEDICATED_DEVELOPMENT_PATH_REQUIRED"):
        provision(dev_path.parent.parent / "barbershop.db")
    assert not dev_path.parent.exists()
    provision(dev_path)
    before = snapshot(dev_path)
    with pytest.raises(dev_data.DevelopmentDataError, match="PROVISIONED_IDENTITY_NOT_FOUND"):
        rotate(dev_path, "unknown")
    assert snapshot(dev_path) == before


def test_symlinks_and_non_private_manifest_are_refused(dev_path, tmp_path):
    provision(dev_path)
    manifest = dev_data._manifest_path(dev_path)
    manifest.chmod(0o644)
    with pytest.raises(dev_data.DevelopmentDataError):
        rotate(dev_path, "unknown")
    manifest.chmod(0o600)
    link = tmp_path / "dev"
    link.symlink_to(dev_path.parent, target_is_directory=True)
    with pytest.raises(dev_data.DevelopmentDataError, match="UNSAFE_DEVELOPMENT_PATH"):
        provision(link / "barbershop.db")


def test_cli_requires_configured_path_and_reports_only_error_codes(dev_path, monkeypatch, capsys):
    monkeypatch.setattr(database, "DATABASE_PATH", dev_path)
    output = Terminal()
    assert dev_data.main(["--database", str(dev_path), "provision"], input_stream=Terminal("PROVISION\n"), output_stream=output) == 0
    assert dev_data.main(["--database", str(dev_path), "rotate-credential", "--identity", "unknown"], input_stream=Terminal(), output_stream=Terminal()) == 1
    assert capsys.readouterr().err == "PROVISIONED_IDENTITY_NOT_FOUND\n"
    other = dev_path.parent.parent / "other" / "dev" / "barbershop.db"
    assert dev_data.main(["--database", str(other), "provision"], input_stream=Terminal(), output_stream=Terminal()) == 1
    assert capsys.readouterr().err == "CONFIGURED_DATABASE_MISMATCH\n"


def test_repeat_after_rotation_preserves_new_credential(dev_path):
    identities, _, _ = provision(dev_path)
    selector = next(iter(identities))
    new_code = rotate(dev_path, selector)
    before = snapshot(dev_path)
    assert provision(dev_path)[1] == []
    assert snapshot(dev_path) == before
    with closing(database.open_development_database(dev_path)) as connection:
        assert authenticate_access_code(connection, new_code, identities)


@pytest.mark.parametrize("statement", [
    "UPDATE users SET is_active = 0 WHERE id = 1",
    "UPDATE users SET role = 'STAFF' WHERE id = 1",
    "UPDATE customer_accounts SET is_active = 0 WHERE id = 1",
    "UPDATE customers SET is_active = 0 WHERE id = 1",
    "UPDATE businesses SET is_active = 0 WHERE id = 1",
])
def test_invalid_identity_rotation_is_refused_without_repair(dev_path, statement):
    identities, _, _ = provision(dev_path)
    kind = "customer_account" if "customer" in statement else "user"
    selector = next(key for key, ref in identities.items() if ref.account_kind == kind)
    with closing(database.open_development_database(dev_path)) as connection:
        connection.execute(statement)
        connection.commit()
    before = snapshot(dev_path)
    with pytest.raises(dev_data.DevelopmentDataError, match="DEVELOPMENT_IDENTITY_INVALID"):
        rotate(dev_path, selector)
    assert snapshot(dev_path) == before


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_rotation_failure_after_update_rolls_back_without_secret_delivery(dev_path, monkeypatch, failure):
    identities, codes, _ = provision(dev_path)
    before = snapshot(dev_path)
    actual = repositories.replace_account_credential
    output = Terminal()

    def fail_after_update(*args, **kwargs):
        assert actual(*args, **kwargs)
        raise failure("injected failure")

    monkeypatch.setattr(repositories, "replace_account_credential", fail_after_update)
    with pytest.raises(failure):
        rotate(dev_path, next(iter(identities)), output_stream=output)
    assert snapshot(dev_path) == before
    assert "access code: " not in output.getvalue()
    with closing(database.open_development_database(dev_path)) as connection:
        for code in codes:
            assert authenticate_access_code(connection, code, identities)


def test_initial_manifest_failure_rolls_back_and_never_automatically_resets(dev_path, monkeypatch):
    def fail(*args):
        raise OSError("injected manifest failure")

    monkeypatch.setattr(dev_data, "_write_manifest", fail)
    output = Terminal()
    with pytest.raises(dev_data.DevelopmentDataError, match="PROVISIONING_INCOMPLETE"):
        provision(dev_path, output_stream=output)
    assert "access code: " not in output.getvalue()
    with closing(database.open_development_database(dev_path)) as connection:
        assert connection.execute("SELECT count(*) FROM businesses").fetchone()[0] == 0
    before = dev_path.read_bytes()
    with pytest.raises(dev_data.DevelopmentDataError, match="UNKNOWN_OR_INCOMPLETE"):
        provision(dev_path)
    assert dev_path.read_bytes() == before


def test_simultaneous_provisioning_is_exclusively_claimed(dev_path, monkeypatch):
    entered, release = Event(), Event()
    actual = dev_data.hash_access_secret

    def block(secret):
        entered.set()
        assert release.wait(timeout=5)
        return actual(secret)

    monkeypatch.setattr(dev_data, "hash_access_secret", block)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(provision, dev_path)
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(dev_data.DevelopmentDataError, match="PROVISIONING_IN_PROGRESS"):
                provision(dev_path)
        finally:
            release.set()
        identities, codes, _ = first.result(timeout=5)
    assert len(identities) == len(codes) == 2
    with closing(database.open_development_database(dev_path)) as connection:
        assert connection.execute("SELECT count(*) FROM businesses").fetchone()[0] == 1


def test_rotation_changes_only_target_hash_and_update_timestamp(dev_path):
    identities, _, _ = provision(dev_path)
    selector = next(key for key, ref in identities.items() if ref.account_kind == "user")
    with closing(database.open_development_database(dev_path)) as connection:
        connection.execute("INSERT INTO users (business_id, username, password_hash, role) VALUES (1, 'other', 'untouched', 'OWNER')")
        connection.execute("UPDATE services SET default_price = 123 WHERE id = 1")
        connection.commit()
        before_rows = [dict(row) for row in connection.execute("SELECT * FROM users ORDER BY id")]
    before = snapshot(dev_path)
    rotate(dev_path, selector)
    after = snapshot(dev_path)
    with closing(database.open_development_database(dev_path)) as connection:
        after_rows = [dict(row) for row in connection.execute("SELECT * FROM users ORDER BY id")]
    assert before_rows[1:] == after_rows[1:]
    assert before_rows[0]["password_hash"] != after_rows[0]["password_hash"]
    for field in ("password_hash", "updated_at"):
        before_rows[0].pop(field)
        after_rows[0].pop(field)
    assert before_rows[0] == after_rows[0]
    before.pop("users")
    after.pop("users")
    assert before == after


def test_noninteractive_output_also_refuses_generation(dev_path):
    with pytest.raises(dev_data.DevelopmentDataError, match="INTERACTIVE_TERMINAL_REQUIRED"):
        dev_data.provision_development_data(dev_path, environment="development", input_stream=Terminal("PROVISION\n"), output_stream=StringIO())
    assert not dev_path.parent.exists()


def test_runtime_manifest_is_ignored_and_contains_no_hashes(dev_path):
    provision(dev_path)
    text = dev_data._manifest_path(dev_path).read_text()
    assert "$argon2" not in text and "password_hash" not in text


@pytest.mark.parametrize("kind", ["tenant", "account", "username"])
def test_inconsistent_manifest_identity_is_refused(dev_path, kind):
    identities, _, _ = provision(dev_path)
    manifest_path = dev_data._manifest_path(dev_path)
    manifest = json.loads(manifest_path.read_text())
    selector = next(iter(identities))
    if kind == "tenant":
        manifest["identities"][selector]["business_id"] = 999
    elif kind == "account":
        manifest["identities"][selector]["account_id"] = 999
    else:
        manifest["identities"]["x" * 16] = manifest["identities"].pop(selector)
    manifest_path.write_text(json.dumps(manifest))
    before = snapshot(dev_path)
    with pytest.raises(dev_data.DevelopmentDataError):
        provision(dev_path)
    assert snapshot(dev_path) == before


def test_cli_development_guard_creates_nothing(dev_path, monkeypatch, capsys):
    monkeypatch.setattr(database, "DATABASE_PATH", dev_path)
    monkeypatch.setattr(dev_data.settings, "environment", "production")
    assert dev_data.main(["--database", str(dev_path), "provision"], input_stream=Terminal("PROVISION\n"), output_stream=Terminal()) == 1
    assert capsys.readouterr().err == "DEVELOPMENT_ONLY\n"
    assert not dev_path.parent.exists()
