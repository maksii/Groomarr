"""Tests for the operation history / audit store (src/history.py)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import config, history  # noqa: E402


@pytest.fixture
def hist(tmp_path, monkeypatch):
    """A fresh, isolated history database per test."""
    db = tmp_path / "history.db"
    monkeypatch.setattr(config.settings, "history_db", str(db))
    monkeypatch.setattr(config.settings, "history_retention_days", 0, raising=False)
    monkeypatch.setattr(config.settings, "history_retention_max_rows", 0, raising=False)
    history.reset_for_tests()
    yield history
    history.reset_for_tests()


class TestRecordAndRead:
    def test_record_returns_id_and_get_roundtrips(self, hist):
        oid = hist.record_operation(
            source="sonarr",
            event_type="Grab",
            status="renamed",
            decision="processed",
            media_title="Show S01E01",
            used_global=False,
            dry_run=True,
            applied_json={"torrent": ["a", "b"], "folder": None, "files": [["x", "y"]]},
            rule_steps_json=[{"rule": "r", "before": "a", "after": "b"}],
        )
        assert isinstance(oid, int)
        row = hist.get_operation(oid)
        assert row is not None
        assert row["source"] == "sonarr"
        assert row["status"] == "renamed"
        assert row["media_title"] == "Show S01E01"
        # bools coerced to 0/1
        assert row["used_global"] == 0
        assert row["dry_run"] == 1
        # JSON columns stored as strings
        assert '"torrent"' in row["applied_json"]
        assert row["rule_steps_json"].startswith("[")
        # timestamps populated
        assert row["created_at"] and row["updated_at"]

    def test_get_missing_returns_none(self, hist):
        assert hist.get_operation(999_999) is None

    def test_unknown_fields_ignored(self, hist):
        # A bogus column name must be silently ignored (no SQL error, no injection).
        oid = hist.record_operation(source="manual", bogus_column="x", status="renamed")
        assert isinstance(oid, int)
        assert hist.get_operation(oid)["source"] == "manual"

    def test_update_changes_fields(self, hist):
        oid = hist.record_operation(source="sonarr", status="queued")
        hist.update_operation(oid, status="renamed", files_renamed=3)
        row = hist.get_operation(oid)
        assert row["status"] == "renamed"
        assert row["files_renamed"] == 3

    def test_update_none_id_is_noop(self, hist):
        # Must not raise when the original record failed to insert.
        hist.update_operation(None, status="x")


class TestRobustness:
    def test_record_never_raises_on_unserializable_json(self, hist):
        # A non-serializable JSON field falls back to a safe default instead of raising.
        oid = hist.record_operation(source="manual", applied_json=object(), status="renamed")
        assert isinstance(oid, int)
        row = hist.get_operation(oid)
        assert row["applied_json"] in ("{}", "[]")

    def test_string_json_passed_through(self, hist):
        oid = hist.record_operation(source="manual", file_plan_json='[{"old_path":"a"}]')
        assert hist.get_operation(oid)["file_plan_json"] == '[{"old_path":"a"}]'


class TestListSearchFilter:
    def _seed(self, hist):
        hist.record_operation(
            source="sonarr",
            status="renamed",
            decision="processed",
            media_title="Reign S01-S02",
            release_title="Reign.S01-S02",
            torrent_hash="aaaa",
            indexer="Toloka",
        )
        hist.record_operation(
            source="radarr",
            status="skipped",
            decision="skipped",
            media_title="Some Movie",
            release_title="Some.Movie.CAM",
            torrent_hash="bbbb",
        )
        hist.record_operation(
            source="sonarr",
            status="failed",
            decision="processed",
            media_title="Frieren S01E12",
        )

    def test_list_all_newest_first(self, hist):
        self._seed(hist)
        page = hist.list_operations()
        assert page["total"] == 3
        assert len(page["items"]) == 3
        # newest first (last inserted has the highest id)
        assert page["items"][0]["media_title"] == "Frieren S01E12"

    def test_filter_by_source(self, hist):
        self._seed(hist)
        page = hist.list_operations(source="sonarr")
        assert page["total"] == 2
        assert all(i["source"] == "sonarr" for i in page["items"])

    def test_filter_by_status(self, hist):
        self._seed(hist)
        assert hist.list_operations(status="skipped")["total"] == 1
        assert hist.list_operations(status="renamed")["total"] == 1

    def test_filter_by_decision(self, hist):
        self._seed(hist)
        assert hist.list_operations(decision="processed")["total"] == 2

    def test_search_matches_title_and_hash_and_indexer(self, hist):
        self._seed(hist)
        assert hist.list_operations(q="Reign")["total"] == 1
        assert hist.list_operations(q="aaaa")["total"] == 1  # torrent hash
        assert hist.list_operations(q="Toloka")["total"] == 1  # indexer
        assert hist.list_operations(q="CAM")["total"] == 1  # release title
        assert hist.list_operations(q="nonexistent")["total"] == 0

    def test_pagination(self, hist):
        for i in range(5):
            hist.record_operation(source="manual", media_title=f"Op {i}")
        page = hist.list_operations(limit=2, offset=0)
        assert page["total"] == 5 and len(page["items"]) == 2
        page2 = hist.list_operations(limit=2, offset=4)
        assert len(page2["items"]) == 1

    def test_limit_is_bounded(self, hist):
        page = hist.list_operations(limit=99999)
        assert page["limit"] <= history.MAX_LIMIT


class TestStats:
    def test_stats_aggregates(self, hist):
        hist.record_operation(source="sonarr", status="renamed")
        hist.record_operation(source="sonarr", status="renamed")
        hist.record_operation(source="radarr", status="skipped")
        hist.record_operation(source="manual", status="failed")
        s = hist.stats()
        assert s["total"] == 4
        assert s["renamed"] == 2
        assert s["skipped"] == 1
        assert s["failed"] == 1
        assert s["last_24h"] == 4
        assert s["by_source"]["sonarr"] == 2
        assert s["by_status"]["renamed"] == 2
        assert s["last_operation_at"] is not None

    def test_stats_empty(self, hist):
        s = hist.stats()
        assert s["total"] == 0
        assert s["last_operation_at"] is None


class TestRetention:
    def test_prune_max_rows_keeps_newest(self, hist, monkeypatch):
        monkeypatch.setattr(config.settings, "history_retention_max_rows", 3)
        ids = [hist.record_operation(source="manual", media_title=f"Op {i}") for i in range(6)]
        page = hist.list_operations()
        assert page["total"] == 3
        kept = {i["id"] for i in page["items"]}
        # the three newest ids survive
        assert kept == set(ids[-3:])
