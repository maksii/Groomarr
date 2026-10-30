"""Shared pytest configuration.

The webhook pipeline now records operations to a SQLite history database. Several
existing tests post webhooks (whose background tasks run under the test
transport), so without isolation they would create a database next to the
fixture rules file. Point ``HISTORY_DB`` at a session-scoped temp file so the
audit store never pollutes the repository, regardless of which test runs.
"""

import functools
import os

import pytest

from src import rename


@pytest.fixture(autouse=True)
def _fast_rename_verification(monkeypatch):
    """Mocked qBittorrent never "catches up", so don't wait out the real retry window."""
    monkeypatch.setattr(
        rename,
        "_verify_rename_state",
        functools.partial(rename._verify_rename_state, max_retries=2, retry_delay=0),
    )


@pytest.fixture(scope="session", autouse=True)
def _isolated_history_db(tmp_path_factory):
    """Route all operation-history writes to a throwaway database for the session."""
    db_path = tmp_path_factory.mktemp("history") / "test_history.db"
    os.environ["HISTORY_DB"] = str(db_path)
    yield
    os.environ.pop("HISTORY_DB", None)
