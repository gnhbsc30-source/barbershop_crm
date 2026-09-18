import pytest

from app.db import database


@pytest.fixture
def test_database(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test.db"

    monkeypatch.setattr(
        database,
        "DATABASE_PATH",
        test_db_path,
    )

    database.initialize_database()

    connection = database.get_connection()

    yield connection

    connection.close()