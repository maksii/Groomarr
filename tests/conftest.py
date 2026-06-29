"""Shared pytest configuration.

The webhook pipeline now records operations to a SQLite history database. Several
existing tests post webhooks (whose background tasks run under the test
transport), so without isolation they would create a database next to the
fixture rules file. Point ``HISTORY_DB`` at a session-scoped temp file so the
audit store never pollutes the repository, regardless of which test runs.
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_history_db(tmp_path_factory):
    """Route all operation-history writes to a throwaway database for the session."""
    db_path = tmp_path_factory.mktemp("history") / "test_history.db"
    os.environ["HISTORY_DB"] = str(db_path)
    yield
    os.environ.pop("HISTORY_DB", None)
