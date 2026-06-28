"""Tests for the operations dashboard API (list/stats/detail/rollback) and the
webhook instrumentation that feeds it.

Mirrors the mocked-qBittorrent + reloaded-app style of test_web_api.py, but the
mock is *stateful* so a rename and its rollback can be exercised end to end.
"""

import json
import os
import sys
from importlib import reload
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _sonarr_payload() -> dict:
    p = Path(__file__).parent / "fixtures" / "sonarr_payload.json"
    return json.loads(p.read_text(encoding="utf-8"))


class StatefulQbit:
    """A minimal stateful qBittorrent mock (one torrent that mutates on rename)."""

    def __init__(self):
        self.name = "Original.Name-GRP"
        self.files = [{"name": "Original.Name-GRP/Series.S02E05.mkv"}]

    def check_connection(self):
        return True

    def _torrent(self):
        t = MagicMock()
        t.name = self.name
        t.get = lambda k, d=None: {"name": self.name}.get(k, d)
        return t

    async def wait_for_torrent(self, **kwargs):
        return self._torrent()

    def get_torrent_info(self, h):
        return self._torrent()

    def get_files(self, h):
        return [dict(f) for f in self.files]

    async def rename_torrent(self, h, new_name):
        self.name = new_name
        return True

    async def rename_folder(self, h, old, new):
        self.files = [
            {"name": new + f["name"][len(old) :]}
            if (f["name"] == old or f["name"].startswith(old + "/"))
            else f
            for f in self.files
        ]
        return True

    async def rename_file(self, h, old, new):
        self.files = [{"name": new} if f["name"] == old else f for f in self.files]
        return True


@pytest.fixture
def ops_app(tmp_path):
    rules = Path(__file__).parent / "fixtures" / "test_rules.yaml"
    env = {
        "QBITTORRENT_URL": "http://mock:8080",
        "SONARR_URL": "",
        "SONARR_API_KEY": "",
        "RADARR_URL": "",
        "RADARR_API_KEY": "",
        "RULES_FILE": str(rules),
        "STATIC_DIR": str(tmp_path / "no_static"),
        "HISTORY_DB": str(tmp_path / "ops.db"),
        "INITIAL_DELAY": "0",
    }
    with patch.dict(os.environ, env, clear=False):
        from src import config

        reload(config)
        from src import history

        history.reset_for_tests()
        from src import main

        reload(main)
        qbit = StatefulQbit()
        main.qbit_client = qbit
        yield main.app, qbit
        history.reset_for_tests()


@pytest.fixture
async def ops_client(ops_app):
    app, qbit = ops_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c, qbit


class TestOperationsListAndStats:
    @pytest.mark.asyncio
    async def test_list_empty(self, ops_client):
        c, _ = ops_client
        r = await c.get("/api/operations")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    @pytest.mark.asyncio
    async def test_webhook_is_recorded_and_listed(self, ops_client):
        c, _ = ops_client
        r = await c.post("/webhook/sonarr", json=_sonarr_payload())
        assert r.status_code == 200

        page = (await c.get("/api/operations")).json()
        assert page["total"] == 1
        op = page["items"][0]
        assert op["source"] == "sonarr"
        assert op["status"] == "renamed"
        assert op["decision"] == "processed"

        stats = (await c.get("/api/operations/stats")).json()
        assert stats["total"] == 1
        assert stats["renamed"] == 1

    @pytest.mark.asyncio
    async def test_detail_includes_live_state_and_rollback_flag(self, ops_client):
        c, _ = ops_client
        await c.post("/webhook/sonarr", json=_sonarr_payload())
        op_id = (await c.get("/api/operations")).json()["items"][0]["id"]

        detail = (await c.get(f"/api/operations/{op_id}")).json()
        assert detail["live"]["checked"] is True
        assert detail["live"]["torrent_exists"] is True
        assert detail["live"]["matches_rename"] is True
        assert detail["can_rollback"] is True
        # decision/trigger detail present
        assert isinstance(detail["trigger_checks"], list)

    @pytest.mark.asyncio
    async def test_detail_404(self, ops_client):
        c, _ = ops_client
        r = await c.get("/api/operations/123456")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_filters_and_search(self, ops_client):
        c, _ = ops_client
        await c.post("/webhook/sonarr", json=_sonarr_payload())
        assert (await c.get("/api/operations?source=sonarr")).json()["total"] == 1
        assert (await c.get("/api/operations?source=radarr")).json()["total"] == 0
        assert (await c.get("/api/operations?status=renamed")).json()["total"] == 1


class TestRollback:
    @pytest.mark.asyncio
    async def test_full_rollback_flow(self, ops_client):
        c, qbit = ops_client
        await c.post("/webhook/sonarr", json=_sonarr_payload())
        op_id = (await c.get("/api/operations")).json()["items"][0]["id"]
        renamed_name = qbit.name
        assert renamed_name != "Original.Name-GRP"  # the rename happened

        # Preview is read-only and can roll back.
        preview = (await c.post(f"/api/operations/{op_id}/rollback/preview")).json()
        assert preview["status"] == "ok"
        assert preview["can_rollback"] is True
        assert preview["torrent_step"]["to"] == "Original.Name-GRP"

        # Execute the rollback: state is restored, op flagged, new op recorded.
        rb = (await c.post(f"/api/operations/{op_id}/rollback")).json()
        assert rb["status"] == "success"
        assert rb["torrent_reverted"] is True
        assert qbit.name == "Original.Name-GRP"
        assert rb["rollback_operation_id"] is not None

        detail = (await c.get(f"/api/operations/{op_id}")).json()
        assert detail["status"] == "rolled_back"
        assert detail["rolled_back"] is True
        assert detail["can_rollback"] is False

        # A second rollback is refused.
        again = (await c.post(f"/api/operations/{op_id}/rollback")).json()
        assert again["status"] == "unavailable"

        # The rollback itself is recorded and linked.
        page = (await c.get("/api/operations")).json()
        rb_ops = [o for o in page["items"] if o["event_type"] == "rollback"]
        assert len(rb_ops) == 1
        assert rb_ops[0]["rollback_of"] == op_id

    @pytest.mark.asyncio
    async def test_rollback_unavailable_for_skipped_op(self, ops_client):
        c, _ = ops_client
        from src import history

        op_id = history.record_operation(
            source="radarr",
            status="skipped",
            decision="skipped",
            skip_reason="quality 'CAM' in exclude list",
            media_title="Some Movie",
        )
        preview = (await c.post(f"/api/operations/{op_id}/rollback/preview")).json()
        assert preview["can_rollback"] is False
        rb = (await c.post(f"/api/operations/{op_id}/rollback")).json()
        assert rb["status"] in ("unavailable", "error")

    @pytest.mark.asyncio
    async def test_rollback_404(self, ops_client):
        c, _ = ops_client
        r = await c.post("/api/operations/999999/rollback")
        assert r.status_code == 404
