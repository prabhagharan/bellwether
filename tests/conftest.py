import os
# Use litellm's bundled model-cost map instead of fetching it over the network on import.
# Without this, importing dspy (→ litellm) does a network fetch that can stall for minutes
# under load, hanging pytest collection. Set before any test module imports the LLM layer.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import pytest
from sqlalchemy import delete
from bellwether.db import engine
from sqlalchemy.orm import Session
from bellwether.models.statement import Statement
from bellwether.models.source import Source
from bellwether.models.figure import Figure
from bellwether.models.user import User


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
    # Clean slate: the app now commits to a shared dev/test Postgres, and several
    # tests scan or count whole tables (seed emptiness, ingest counts). Clear the
    # domain tables in FK order INSIDE this test's transaction so any leftover
    # committed rows can't cause spurious failures. The teardown rollback discards
    # these deletes — they never touch data outside the test.
    for model in (Statement, Source, Figure, User):
        session.execute(delete(model))
    session.flush()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
