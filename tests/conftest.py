import pytest
from bellwether.db import engine
from sqlalchemy.orm import Session


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    # join_transaction_mode="create_savepoint" makes any session.commit() inside
    # a test release a SAVEPOINT rather than commit the outer transaction, so the
    # teardown rollback still fully isolates the test (e.g. seed_admin commits).
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
