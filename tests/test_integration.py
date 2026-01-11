"""Integration tests with mocked qBittorrent client.

These tests validate the full application flow:
- Webhook endpoints receive and validate payloads
- Background tasks process correctly
- qBittorrent API calls are made as expected
- Error handling works correctly
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def radarr_payload():
    """Load Radarr webhook payload from fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "radarr_payload.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def sonarr_payload():
    """Load Sonarr webhook payload from fixture."""
    fixture_path = Path(__file__).parent / "fixtures" / "sonarr_payload.json"
    with open(fixture_path) as f:
        return json.load(f)


@pytest.fixture
def mock_torrent():
    """Create a mock torrent object."""
    torrent = MagicMock()
    torrent.name = "Original.Torrent.Name-Group"
    torrent.state = "downloading"
    torrent.get = MagicMock(
        side_effect=lambda k, d=None: {"name": "Original.Torrent.Name-Group"}.get(k, d)
    )
    return torrent


@pytest.fixture
def mock_torrent_files():
    """Create mock torrent files list (multi-file torrent with root folder)."""
    return [
        {"name": "Original.Torrent.Name-Group/movie.mkv"},
        {"name": "Original.Torrent.Name-Group/sample.mkv"},
        {"name": "Original.Torrent.Name-Group/subs/english.srt"},
    ]


@pytest.fixture
def mock_single_file():
    """Create mock single file torrent (no root folder)."""
    return [
        {"name": "Movie.2024.1080p.BluRay.mkv"},
    ]


@pytest.fixture
def mock_tv_series_files():
    """Create mock TV series files with multiple episodes."""
    return [
        {"name": "Series.S01.Complete/Series.S01E01.Pilot.mkv"},
        {"name": "Series.S01.Complete/Series.S01E02.Episode.Two.mkv"},
        {"name": "Series.S01.Complete/Series.S01 E03.Episode.Three.mkv"},
        {"name": "Series.S01.Complete/Series.S01EP04.Episode.Four.mkv"},
    ]


@pytest.fixture
def mock_qbit_client(mock_torrent, mock_torrent_files):
    """Create a fully mocked QBitClient.

    The mock supports verification by updating internal state after renames.
    """
    mock_client = MagicMock()
    mock_client.wait_for_torrent = AsyncMock(return_value=mock_torrent)

    # Track current state for verification
    current_torrent_name = {"name": "Original.Torrent.Name-Group"}
    current_files = list(mock_torrent_files)  # Copy

    # Default list of torrents for find_torrent_by_comment_id tests
    # Can be overridden in specific tests
    mock_torrents_list = []

    def get_torrent_info(torrent_hash):
        torrent = MagicMock()
        torrent.name = current_torrent_name["name"]
        torrent.state = "downloading"
        torrent.get = MagicMock(
            side_effect=lambda k, d=None: {"name": current_torrent_name["name"]}.get(k, d)
        )
        return torrent

    def rename_torrent(torrent_hash, new_name):
        current_torrent_name["name"] = new_name
        return True

    def get_files(torrent_hash):
        return current_files

    def rename_file(torrent_hash, old_path, new_path):
        # Update internal state to reflect renamed file
        for i, f in enumerate(current_files):
            if f.get("name") == old_path:
                current_files[i] = {"name": new_path}
                return True
        return True  # Still return True even if not found (for verification tests)

    def rename_folder(torrent_hash, old_path, new_path):
        # Update all file paths to reflect folder rename
        for i, f in enumerate(current_files):
            if f.get("name", "").startswith(old_path + "/"):
                current_files[i] = {"name": new_path + f["name"][len(old_path) :]}
        return True

    def get_all_torrents():
        return mock_torrents_list

    def find_torrent_by_comment_id(torrent_id):
        import re

        for torrent in mock_torrents_list:
            comment = getattr(torrent, "comment", "") or ""
            if torrent_id in comment:
                # Verify it's a proper match (not just a substring)
                pattern = rf"(?:/torrents/|^|\s)({re.escape(torrent_id)})(?:/|$|\s)"
                if re.search(pattern, comment):
                    return torrent
        return None

    mock_client.get_torrent_info = MagicMock(side_effect=get_torrent_info)
    mock_client.get_files = MagicMock(side_effect=get_files)
    mock_client.rename_torrent = AsyncMock(side_effect=rename_torrent)
    mock_client.rename_folder = AsyncMock(side_effect=rename_folder)
    mock_client.rename_file = AsyncMock(side_effect=rename_file)
    mock_client.get_all_torrents = MagicMock(side_effect=get_all_torrents)
    mock_client.find_torrent_by_comment_id = MagicMock(side_effect=find_torrent_by_comment_id)
    # Store the list reference so tests can modify it
    mock_client._mock_torrents_list = mock_torrents_list
    return mock_client


@pytest.fixture
def app_with_mocked_client(mock_qbit_client):
    """Create FastAPI app with mocked qBittorrent client."""
    # Patch the settings before importing app
    with patch.dict(
        os.environ,
        {
            "QBITTORRENT_URL": "http://mock:8080",
            "QBITTORRENT_USERNAME": "test",
            "QBITTORRENT_PASSWORD": "test",
            "RULES_FILE": str(Path(__file__).parent / "fixtures" / "test_rules.yaml"),
            "INITIAL_DELAY": "0.01",  # Speed up tests
            "MAX_RETRIES": "1",
            "RETRY_DELAY": "0.01",
        },
    ):
        # Re-import to pick up patched env
        from importlib import reload

        from src import config

        reload(config)
        from src import main

        reload(main)

        # Replace the qbit_client global
        main.qbit_client = mock_qbit_client

        yield main.app


@pytest.fixture
async def async_client(app_with_mocked_client):
    """Create async HTTP client for testing."""
    transport = ASGITransport(app=app_with_mocked_client)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# =============================================================================
# Health & Config Endpoint Tests
# =============================================================================


class TestHealthEndpoint:
    """Test health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, async_client):
        """Health endpoint should return ok status."""
        response = await async_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"


class TestReloadEndpoint:
    """Test config reload endpoint."""

    @pytest.mark.asyncio
    async def test_reload_returns_ok(self, async_client):
        """Reload endpoint should return ok status."""
        response = await async_client.get("/reload")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "reloaded" in data["message"].lower()


# =============================================================================
# Radarr Webhook Tests
# =============================================================================


class TestRadarrWebhook:
    """Test Radarr webhook endpoint."""

    @pytest.mark.asyncio
    async def test_grab_event_queues_rename(self, async_client, radarr_payload):
        """Grab event should queue a rename task."""
        response = await async_client.post("/webhook/radarr", json=radarr_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["torrent_hash"] == radarr_payload["downloadId"]

    @pytest.mark.asyncio
    async def test_non_grab_event_skipped(self, async_client, radarr_payload):
        """Non-Grab events should be skipped."""
        radarr_payload["eventType"] = "Download"

        response = await async_client.post("/webhook/radarr", json=radarr_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert "Grab" in data["reason"]

    @pytest.mark.asyncio
    async def test_non_qbittorrent_client_skipped(self, async_client, radarr_payload):
        """Non-qBittorrent clients should be skipped."""
        radarr_payload["downloadClientType"] = "Transmission"

        response = await async_client.post("/webhook/radarr", json=radarr_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert "Transmission" in data["reason"]

    @pytest.mark.asyncio
    async def test_invalid_payload_returns_422(self, async_client):
        """Invalid payload should return 422 Unprocessable Entity."""
        response = await async_client.post("/webhook/radarr", json={"invalid": "data"})
        assert response.status_code == 422


# =============================================================================
# Sonarr Webhook Tests
# =============================================================================


class TestSonarrWebhook:
    """Test Sonarr webhook endpoint."""

    @pytest.mark.asyncio
    async def test_grab_event_queues_rename(self, async_client, sonarr_payload):
        """Grab event should queue a rename task."""
        response = await async_client.post("/webhook/sonarr", json=sonarr_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["torrent_hash"] == sonarr_payload["downloadId"]

    @pytest.mark.asyncio
    async def test_non_grab_event_skipped(self, async_client, sonarr_payload):
        """Non-Grab events should be skipped."""
        sonarr_payload["eventType"] = "Download"

        response = await async_client.post("/webhook/sonarr", json=sonarr_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert "Grab" in data["reason"]

    @pytest.mark.asyncio
    async def test_non_qbittorrent_client_skipped(self, async_client, sonarr_payload):
        """Non-qBittorrent clients should be skipped."""
        sonarr_payload["downloadClientType"] = "Deluge"

        response = await async_client.post("/webhook/sonarr", json=sonarr_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert "Deluge" in data["reason"]

    @pytest.mark.asyncio
    async def test_missing_download_id_skipped(self, async_client, sonarr_payload):
        """Missing downloadId should be skipped."""
        # Remove downloadId from both top-level and release (Sonarr v4 uses top-level)
        sonarr_payload.pop("downloadId", None)
        sonarr_payload["release"]["downloadId"] = None

        response = await async_client.post("/webhook/sonarr", json=sonarr_payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "skipped"
        assert "downloadId" in data["reason"]

    @pytest.mark.asyncio
    async def test_invalid_payload_returns_422(self, async_client):
        """Invalid payload should return 422 Unprocessable Entity."""
        response = await async_client.post("/webhook/sonarr", json={"invalid": "data"})
        assert response.status_code == 422


# =============================================================================
# Manual Rename Endpoint Tests
# =============================================================================


class TestManualRenameEndpoint:
    """Test manual rename endpoint."""

    @pytest.mark.asyncio
    async def test_manual_rename_success(self, async_client, mock_qbit_client):
        """Manual rename should succeed with valid parameters."""
        response = await async_client.post(
            "/rename/manual",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "Renamed Torrent Name",
                "mode": "torrent_and_folder",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["torrent_hash"] == "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F"
        assert data["new_name"] == "Renamed Torrent Name"
        assert data["mode"] == "torrent_and_folder"

    @pytest.mark.asyncio
    async def test_manual_rename_torrent_only_mode(self, async_client, mock_qbit_client):
        """Manual rename with torrent_only mode should work."""
        response = await async_client.post(
            "/rename/manual",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Name",
                "mode": "torrent_only",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["mode"] == "torrent_only"

    @pytest.mark.asyncio
    async def test_manual_rename_folder_only_mode(self, async_client, mock_qbit_client):
        """Manual rename with folder_only mode should work."""
        response = await async_client.post(
            "/rename/manual",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Folder Name",
                "mode": "folder_only",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["mode"] == "folder_only"

    @pytest.mark.asyncio
    async def test_manual_rename_default_mode(self, async_client, mock_qbit_client):
        """Manual rename without mode should use default (torrent_and_folder)."""
        response = await async_client.post(
            "/rename/manual",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "Default Mode Name",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["mode"] == "torrent_and_folder"

    @pytest.mark.asyncio
    async def test_manual_rename_invalid_mode(self, async_client, mock_qbit_client):
        """Manual rename with invalid mode should return error."""
        response = await async_client.post(
            "/rename/manual",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Name",
                "mode": "invalid_mode",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "Invalid mode" in data["reason"]
        assert "torrent_only" in data["reason"]  # Valid modes should be listed

    @pytest.mark.asyncio
    async def test_manual_rename_torrent_not_found(self, async_client, mock_qbit_client):
        """Manual rename for non-existent torrent should return error."""
        # Make get_torrent_info return None
        mock_qbit_client.get_torrent_info = MagicMock(return_value=None)

        response = await async_client.post(
            "/rename/manual",
            json={
                "torrent_hash": "NOTFOUND00000000000000000000000000000000",
                "new_name": "New Name",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "not found" in data["reason"].lower()

    @pytest.mark.asyncio
    async def test_manual_rename_operation_failed(self, async_client, mock_qbit_client):
        """Manual rename should handle rename failure gracefully."""
        # Make rename operations fail
        mock_qbit_client.rename_torrent = AsyncMock(return_value=False)

        response = await async_client.post(
            "/rename/manual",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Name",
                "mode": "torrent_only",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "failed" in data["reason"].lower()

    @pytest.mark.asyncio
    async def test_manual_rename_missing_required_fields(self, async_client):
        """Manual rename without required fields should return 422."""
        response = await async_client.post("/rename/manual", json={})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_manual_rename_all_modes(self, async_client, mock_qbit_client):
        """Test all valid rename modes work."""
        valid_modes = [
            "torrent_only",
            "torrent_and_folder",
            "torrent_folder_files",
            "folder_only",
            "files_only",
        ]

        for mode in valid_modes:
            # Reset mocks for each iteration
            mock_qbit_client.rename_torrent.reset_mock()
            mock_qbit_client.rename_folder.reset_mock()
            mock_qbit_client.rename_file.reset_mock()

            response = await async_client.post(
                "/rename/manual",
                json={
                    "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                    "new_name": f"Name for {mode}",
                    "mode": mode,
                },
            )

            assert response.status_code == 200, f"Failed for mode: {mode}"
            data = response.json()
            assert data["status"] == "success", f"Failed for mode: {mode}"
            assert data["mode"] == mode


# =============================================================================
# Preview Rename Endpoint Tests
# =============================================================================


class TestPreviewRenameEndpoint:
    """Test preview rename endpoint."""

    @pytest.mark.asyncio
    async def test_preview_rename_success(self, async_client, mock_qbit_client):
        """Preview rename should return expected changes."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Torrent Name",
                "mode": "torrent_and_folder",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["torrent_hash"] == "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F"
        assert data["mode"] == "torrent_and_folder"
        assert data["current_torrent_name"] == "Original.Torrent.Name-Group"
        assert data["current_root_folder"] == "Original.Torrent.Name-Group"
        assert data["new_torrent_name"] == "New Torrent Name"
        assert data["new_root_folder"] == "New Torrent Name"
        assert data["torrent_will_change"] is True
        assert data["folder_will_change"] is True
        assert data["total_files"] == 3

    @pytest.mark.asyncio
    async def test_preview_rename_torrent_only_mode(self, async_client, mock_qbit_client):
        """Preview with torrent_only mode should only show torrent change."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Name",
                "mode": "torrent_only",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mode"] == "torrent_only"
        assert data["new_torrent_name"] == "New Name"
        assert data["torrent_will_change"] is True
        # Folder and files should not be in scope
        assert data["new_root_folder"] is None
        assert data["folder_will_change"] is False
        assert data["file_renames"] == []

    @pytest.mark.asyncio
    async def test_preview_rename_folder_only_mode(self, async_client, mock_qbit_client):
        """Preview with folder_only mode should only show folder change."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Folder Name",
                "mode": "folder_only",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mode"] == "folder_only"
        assert data["new_torrent_name"] is None
        assert data["torrent_will_change"] is False
        assert data["new_root_folder"] == "New Folder Name"
        assert data["folder_will_change"] is True
        assert data["file_renames"] == []

    @pytest.mark.asyncio
    async def test_preview_rename_files_only_mode(self, async_client, mock_qbit_client):
        """Preview with files_only mode should show file changes."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New File Name",
                "mode": "files_only",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mode"] == "files_only"
        assert data["new_torrent_name"] is None
        assert data["torrent_will_change"] is False
        assert data["new_root_folder"] is None
        assert data["folder_will_change"] is False
        assert len(data["file_renames"]) == 3
        assert data["files_will_change"] > 0

    @pytest.mark.asyncio
    async def test_preview_rename_torrent_folder_files_mode(self, async_client, mock_qbit_client):
        """Preview with torrent_folder_files mode should show all changes."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "Complete New Name",
                "mode": "torrent_folder_files",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mode"] == "torrent_folder_files"
        assert data["new_torrent_name"] == "Complete New Name"
        assert data["torrent_will_change"] is True
        assert data["new_root_folder"] == "Complete New Name"
        assert data["folder_will_change"] is True
        assert len(data["file_renames"]) == 3
        assert data["total_files"] == 3

    @pytest.mark.asyncio
    async def test_preview_rename_invalid_mode(self, async_client, mock_qbit_client):
        """Preview with invalid mode should return error."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "New Name",
                "mode": "invalid_mode",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "Invalid mode" in data["reason"]
        assert "torrent_only" in data["reason"]

    @pytest.mark.asyncio
    async def test_preview_rename_torrent_not_found(self, async_client, mock_qbit_client):
        """Preview for non-existent torrent should return error."""
        mock_qbit_client.get_torrent_info = MagicMock(return_value=None)

        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "NOTFOUND00000000000000000000000000000000",
                "new_name": "New Name",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert "not found" in data["reason"].lower()

    @pytest.mark.asyncio
    async def test_preview_rename_no_change_needed(self, async_client, mock_qbit_client):
        """Preview should show no changes when name is the same."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "Original.Torrent.Name-Group",  # Same as current
                "mode": "torrent_and_folder",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["torrent_will_change"] is False
        assert data["folder_will_change"] is False

    @pytest.mark.asyncio
    async def test_preview_rename_default_mode(self, async_client, mock_qbit_client):
        """Preview without mode should use default (torrent_and_folder)."""
        response = await async_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
                "new_name": "Default Mode Preview",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["mode"] == "torrent_and_folder"

    @pytest.mark.asyncio
    async def test_preview_rename_missing_required_fields(self, async_client):
        """Preview without required fields should return 422."""
        response = await async_client.post("/rename/preview", json={})
        assert response.status_code == 422


class TestFindTorrentEndpoint:
    """Test find torrent by ID endpoint."""

    @pytest.mark.asyncio
    async def test_find_torrent_by_url_success(self, async_client, mock_qbit_client):
        """Find torrent by URL should return hash when found."""
        # Setup mock torrents with comments
        torrent1 = MagicMock()
        torrent1.hash = "ABC123DEF456GHI789JKL012MNO345PQR678STU"
        torrent1.name = "Torrent.Without.ID"
        torrent1.comment = "This torrent was downloaded from SomeTracker.cc."

        torrent2 = MagicMock()
        torrent2.hash = "XYZ789ABC123DEF456GHI789JKL012MNO345PQR"
        torrent2.name = "Torrent.With.ID"
        torrent2.comment = (
            "This torrent was downloaded from domain. https://domain/torrents/342558"
        )

        torrent3 = MagicMock()
        torrent3.hash = "DEF456GHI789JKL012MNO345PQR678STU901ABC"
        torrent3.name = "Another.Torrent"
        torrent3.comment = (
            "This torrent was downloaded from domain. https://domain/torrents/999999"
        )

        mock_qbit_client._mock_torrents_list[:] = [torrent1, torrent2, torrent3]

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "https://domain/torrents/342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "found"
        assert data["torrent_id"] == "342558"
        assert data["torrent_hash"] == "XYZ789ABC123DEF456GHI789JKL012MNO345PQR"
        assert "Match found" in data["reason"]

    @pytest.mark.asyncio
    async def test_find_torrent_by_number_success(self, async_client, mock_qbit_client):
        """Find torrent by number should return hash when found."""
        # Setup mock torrents with comments
        torrent1 = MagicMock()
        torrent1.hash = "ABC123DEF456GHI789JKL012MNO345PQR678STU"
        torrent1.name = "Torrent.Without.ID"
        torrent1.comment = "This torrent was downloaded from SomeTracker.cc."

        torrent2 = MagicMock()
        torrent2.hash = "XYZ789ABC123DEF456GHI789JKL012MNO345PQR"
        torrent2.name = "Torrent.With.ID"
        torrent2.comment = (
            "This torrent was downloaded from domain. https://domain/torrents/342558"
        )

        mock_qbit_client._mock_torrents_list[:] = [torrent1, torrent2]

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "found"
        assert data["torrent_id"] == "342558"
        assert data["torrent_hash"] == "XYZ789ABC123DEF456GHI789JKL012MNO345PQR"
        assert "Match found" in data["reason"]

    @pytest.mark.asyncio
    async def test_find_torrent_not_found(self, async_client, mock_qbit_client):
        """Find torrent should return not_found when ID doesn't exist."""
        # Setup mock torrents without matching ID
        torrent1 = MagicMock()
        torrent1.hash = "ABC123DEF456GHI789JKL012MNO345PQR678STU"
        torrent1.name = "Torrent.Without.ID"
        torrent1.comment = "This torrent was downloaded from SomeTracker.cc."

        torrent2 = MagicMock()
        torrent2.hash = "XYZ789ABC123DEF456GHI789JKL012MNO345PQR"
        torrent2.name = "Torrent.With.Different.ID"
        torrent2.comment = (
            "This torrent was downloaded from domain. https://domain/torrents/999999"
        )

        mock_qbit_client._mock_torrents_list[:] = [torrent1, torrent2]

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_found"
        assert data["torrent_id"] == "342558"
        assert data["torrent_hash"] is None
        assert "No torrent found" in data["reason"]

    @pytest.mark.asyncio
    async def test_find_torrent_empty_list(self, async_client, mock_qbit_client):
        """Find torrent should return not_found when no torrents exist."""
        mock_qbit_client._mock_torrents_list[:] = []

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_found"
        assert data["torrent_id"] == "342558"
        assert data["torrent_hash"] is None

    @pytest.mark.asyncio
    async def test_find_torrent_invalid_url_format(self, async_client, mock_qbit_client):
        """Find torrent should return error for invalid URL format."""
        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "https://example.com/invalid/path/342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["torrent_id"] == "https://example.com/invalid/path/342558"
        assert data["torrent_hash"] is None
        assert "Invalid URL format" in data["reason"]

    @pytest.mark.asyncio
    async def test_find_torrent_invalid_number_format(self, async_client, mock_qbit_client):
        """Find torrent should return error for non-numeric ID."""
        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "not-a-number"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["torrent_id"] == "not-a-number"
        assert data["torrent_hash"] is None
        assert "Invalid ID format" in data["reason"]

    @pytest.mark.asyncio
    async def test_find_torrent_comment_without_url(self, async_client, mock_qbit_client):
        """Find torrent should work when comment has ID without URL."""
        # Setup mock torrent with ID in comment but not in URL format
        torrent = MagicMock()
        torrent.hash = "ABC123DEF456GHI789JKL012MNO345PQR678STU"
        torrent.name = "Torrent.With.ID.In.Comment"
        torrent.comment = "Torrent ID: 342558"

        mock_qbit_client._mock_torrents_list[:] = [torrent]

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "found"
        assert data["torrent_id"] == "342558"
        assert data["torrent_hash"] == "ABC123DEF456GHI789JKL012MNO345PQR678STU"

    @pytest.mark.asyncio
    async def test_find_torrent_multiple_matches_returns_first(
        self, async_client, mock_qbit_client
    ):
        """Find torrent should return first match when multiple torrents have same ID."""
        # Setup multiple torrents with same ID (edge case)
        torrent1 = MagicMock()
        torrent1.hash = "FIRST123DEF456GHI789JKL012MNO345PQR678STU"
        torrent1.name = "First.Torrent"
        torrent1.comment = (
            "This torrent was downloaded from domain. https://domain/torrents/342558"
        )

        torrent2 = MagicMock()
        torrent2.hash = "SECOND789ABC123DEF456GHI789JKL012MNO345PQR"
        torrent2.name = "Second.Torrent"
        torrent2.comment = (
            "This torrent was downloaded from domain. https://domain/torrents/342558"
        )

        mock_qbit_client._mock_torrents_list[:] = [torrent1, torrent2]

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "found"
        assert data["torrent_id"] == "342558"
        # Should return first match
        assert data["torrent_hash"] == "FIRST123DEF456GHI789JKL012MNO345PQR678STU"

    @pytest.mark.asyncio
    async def test_find_torrent_comment_empty(self, async_client, mock_qbit_client):
        """Find torrent should handle torrents with empty comments."""
        torrent = MagicMock()
        torrent.hash = "ABC123DEF456GHI789JKL012MNO345PQR678STU"
        torrent.name = "Torrent.Without.Comment"
        torrent.comment = ""

        mock_qbit_client._mock_torrents_list[:] = [torrent]

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_find_torrent_comment_none(self, async_client, mock_qbit_client):
        """Find torrent should handle torrents with None comments."""
        torrent = MagicMock()
        torrent.hash = "ABC123DEF456GHI789JKL012MNO345PQR678STU"
        torrent.name = "Torrent.Without.Comment"
        torrent.comment = None

        mock_qbit_client._mock_torrents_list[:] = [torrent]

        response = await async_client.post(
            "/find/torrent",
            json={"torrent_id": "342558"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_find_torrent_missing_field(self, async_client, mock_qbit_client):
        """Find torrent should return validation error for missing torrent_id."""
        response = await async_client.post(
            "/find/torrent",
            json={},
        )

        assert response.status_code == 422
        data = response.json()
        assert "torrent_id" in str(data).lower()


class TestPreviewRenameSingleFile:
    """Test preview rename on single file torrents."""

    @pytest.fixture
    def single_file_app(self, mock_torrent, mock_single_file):
        """Create FastAPI app with single file torrent mock."""
        mock_client = MagicMock()
        mock_client.wait_for_torrent = AsyncMock(return_value=mock_torrent)
        mock_client.get_torrent_info = MagicMock(return_value=mock_torrent)
        mock_client.get_files = MagicMock(return_value=mock_single_file)
        mock_client.rename_torrent = AsyncMock(return_value=True)
        mock_client.rename_folder = AsyncMock(return_value=True)
        mock_client.rename_file = AsyncMock(return_value=True)

        with patch.dict(
            os.environ,
            {
                "QBITTORRENT_URL": "http://mock:8080",
                "QBITTORRENT_USERNAME": "test",
                "QBITTORRENT_PASSWORD": "test",
                "RULES_FILE": str(Path(__file__).parent / "fixtures" / "test_rules.yaml"),
            },
        ):
            from importlib import reload

            from src import config

            reload(config)
            from src import main

            reload(main)

            main.qbit_client = mock_client
            yield main.app

    @pytest.fixture
    async def single_file_client(self, single_file_app):
        """Create async client for single file tests."""
        transport = ASGITransport(app=single_file_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    @pytest.mark.asyncio
    async def test_preview_single_file_no_folder(self, single_file_client):
        """Preview single file torrent should show warning for no root folder."""
        response = await single_file_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "SINGLEFILE000000000000000000000000000000",
                "new_name": "Renamed Movie",
                "mode": "torrent_and_folder",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["current_root_folder"] is None
        assert data["folder_will_change"] is False
        assert "No root folder" in data["warnings"][0]


class TestPreviewRenameTVSeries:
    """Test preview rename on TV series torrents."""

    @pytest.fixture
    def tv_series_app(self, mock_torrent, mock_tv_series_files):
        """Create FastAPI app with TV series mock."""
        mock_torrent.get = MagicMock(
            side_effect=lambda k, d=None: {"name": "Series.S01.Complete"}.get(k, d)
        )
        mock_client = MagicMock()
        mock_client.wait_for_torrent = AsyncMock(return_value=mock_torrent)
        mock_client.get_torrent_info = MagicMock(return_value=mock_torrent)
        mock_client.get_files = MagicMock(return_value=mock_tv_series_files)
        mock_client.rename_torrent = AsyncMock(return_value=True)
        mock_client.rename_folder = AsyncMock(return_value=True)
        mock_client.rename_file = AsyncMock(return_value=True)

        with patch.dict(
            os.environ,
            {
                "QBITTORRENT_URL": "http://mock:8080",
                "QBITTORRENT_USERNAME": "test",
                "QBITTORRENT_PASSWORD": "test",
                "RULES_FILE": str(Path(__file__).parent / "fixtures" / "test_rules.yaml"),
            },
        ):
            from importlib import reload

            from src import config

            reload(config)
            from src import main

            reload(main)

            main.qbit_client = mock_client
            yield main.app

    @pytest.fixture
    async def tv_series_client(self, tv_series_app):
        """Create async client for TV series tests."""
        transport = ASGITransport(app=tv_series_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

    @pytest.mark.asyncio
    async def test_preview_tv_series_preserves_episodes(self, tv_series_client):
        """Preview should show episode identifiers preserved in file names."""
        response = await tv_series_client.post(
            "/rename/preview",
            json={
                "torrent_hash": "TVSERIES00000000000000000000000000000000",
                "new_name": "Series S01 1080p WEBDL",
                "mode": "torrent_folder_files",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert len(data["file_renames"]) == 4

        # Check that episode identifiers are preserved in new paths
        new_paths = [f["new_path"] for f in data["file_renames"]]
        assert any("S01E01" in path for path in new_paths)
        assert any("S01E02" in path for path in new_paths)
        assert any("S01E03" in path for path in new_paths)
        assert any("S01E04" in path for path in new_paths)


# =============================================================================
# Background Task Tests
# =============================================================================


class TestBackgroundTaskProcessing:
    """Test background task rename processing."""

    @pytest.mark.asyncio
    async def test_process_rename_task_success(self, mock_qbit_client):
        """Test successful rename task processing."""
        from src import main
        from src.config import TrackerRules
        from src.main import process_rename_task

        # Set the global client
        main.qbit_client = mock_qbit_client

        # Create effective rules for the test
        effective_rules = TrackerRules()

        await process_rename_task(
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            release_title="Example Movie 2020 BluRay 1080p-Group",
            source="radarr",
            media_title="Example Movie",
            effective_rules=effective_rules,
            tracker_name=None,
        )

        # Verify qBit client methods were called
        mock_qbit_client.wait_for_torrent.assert_called_once()
        mock_qbit_client.rename_torrent.assert_called()

    @pytest.mark.asyncio
    async def test_process_rename_task_torrent_not_found(self, mock_qbit_client):
        """Test rename task when torrent is not found."""
        from src import main
        from src.config import TrackerRules
        from src.main import process_rename_task

        # Make wait_for_torrent return None
        mock_qbit_client.wait_for_torrent = AsyncMock(return_value=None)
        main.qbit_client = mock_qbit_client

        # Create effective rules for the test
        effective_rules = TrackerRules()

        # Should complete without error
        await process_rename_task(
            torrent_hash="DEADBEEF00000000000000000000000000000000",
            release_title="Missing Torrent",
            source="radarr",
            media_title="Missing Movie",
            effective_rules=effective_rules,
            tracker_name=None,
        )

        # Rename should not be called
        mock_qbit_client.rename_torrent.assert_not_called()


# =============================================================================
# Rename Operation Tests
# =============================================================================


class TestPerformRename:
    """Test rename operation execution."""

    @pytest.mark.asyncio
    async def test_rename_torrent_only_mode(self, mock_qbit_client):
        """Test torrent_only rename mode."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            new_name="New Torrent Name",
            mode=RenameMode.TORRENT_ONLY,
        )

        assert result.success is True
        assert result.torrent_renamed is True
        mock_qbit_client.rename_torrent.assert_called_once()
        mock_qbit_client.rename_folder.assert_not_called()

    @pytest.mark.asyncio
    async def test_rename_torrent_and_folder_mode(self, mock_qbit_client):
        """Test torrent_and_folder rename mode."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            new_name="New Torrent Name",
            mode=RenameMode.TORRENT_AND_FOLDER,
        )

        assert result.success is True
        assert result.torrent_renamed is True
        assert result.folder_renamed is True
        mock_qbit_client.rename_torrent.assert_called_once()
        mock_qbit_client.rename_folder.assert_called_once()

    @pytest.mark.asyncio
    async def test_rename_all_mode(self, mock_qbit_client):
        """Test torrent_folder_files rename mode."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            new_name="New Torrent Name",
            mode=RenameMode.TORRENT_FOLDER_FILES,
        )

        assert result.success is True
        assert result.torrent_renamed is True
        assert result.folder_renamed is True
        mock_qbit_client.rename_torrent.assert_called_once()
        mock_qbit_client.rename_folder.assert_called_once()
        # Files should be renamed too
        assert mock_qbit_client.rename_file.call_count > 0

    @pytest.mark.asyncio
    async def test_rename_failure_handled(self, mock_qbit_client):
        """Test rename failure is handled gracefully."""
        from src.rename import RenameMode, perform_rename

        # Make rename fail
        mock_qbit_client.rename_torrent = AsyncMock(return_value=False)

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            new_name="New Torrent Name",
            mode=RenameMode.TORRENT_ONLY,
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_torrent_not_found_returns_false(self, mock_qbit_client):
        """Test when torrent is not found."""
        from src.rename import RenameMode, perform_rename

        mock_qbit_client.get_torrent_info = MagicMock(return_value=None)

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="NOTFOUND00000000000000000000000000000000",
            new_name="New Name",
            mode=RenameMode.TORRENT_ONLY,
        )

        assert result.success is False

    @pytest.mark.asyncio
    async def test_rename_folder_only_mode(self, mock_qbit_client):
        """Test folder_only rename mode - only renames root folder."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            new_name="New Folder Name",
            mode=RenameMode.FOLDER_ONLY,
        )

        assert result.success is True
        assert result.folder_renamed is True
        assert result.torrent_renamed is False
        mock_qbit_client.rename_torrent.assert_not_called()
        mock_qbit_client.rename_folder.assert_called_once()
        mock_qbit_client.rename_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_rename_files_only_mode(self, mock_qbit_client):
        """Test files_only rename mode - only renames individual files."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            new_name="New File Name",
            mode=RenameMode.FILES_ONLY,
        )

        assert result.success is True
        assert result.files_renamed > 0
        assert result.torrent_renamed is False
        assert result.folder_renamed is False
        mock_qbit_client.rename_torrent.assert_not_called()
        mock_qbit_client.rename_folder.assert_not_called()
        # Files should be renamed
        assert mock_qbit_client.rename_file.call_count > 0


# =============================================================================
# Single File Torrent Rename Tests
# =============================================================================


class TestSingleFileTorrentRename:
    """Test rename operations on single file torrents (no root folder)."""

    @pytest.fixture
    def single_file_qbit_client(self, mock_torrent, mock_single_file):
        """Create a mock QBitClient with single file torrent."""
        mock_client = MagicMock()
        mock_client.wait_for_torrent = AsyncMock(return_value=mock_torrent)

        # Track current state for verification
        current_torrent_name = {"name": "Original.Torrent.Name-Group"}
        current_files = list(mock_single_file)

        def get_torrent_info(torrent_hash):
            torrent = MagicMock()
            torrent.name = current_torrent_name["name"]
            torrent.state = "downloading"
            torrent.get = MagicMock(
                side_effect=lambda k, d=None: {"name": current_torrent_name["name"]}.get(k, d)
            )
            return torrent

        def rename_torrent(torrent_hash, new_name):
            current_torrent_name["name"] = new_name
            return True

        def get_files(torrent_hash):
            return current_files

        def rename_file(torrent_hash, old_path, new_path):
            for i, f in enumerate(current_files):
                if f.get("name") == old_path:
                    current_files[i] = {"name": new_path}
                    return True
            return True

        mock_client.get_torrent_info = MagicMock(side_effect=get_torrent_info)
        mock_client.get_files = MagicMock(side_effect=get_files)
        mock_client.rename_torrent = AsyncMock(side_effect=rename_torrent)
        mock_client.rename_folder = AsyncMock(return_value=True)
        mock_client.rename_file = AsyncMock(side_effect=rename_file)
        return mock_client

    @pytest.mark.asyncio
    async def test_single_file_torrent_only_mode(self, single_file_qbit_client):
        """Test torrent_only mode on single file torrent."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=single_file_qbit_client,
            torrent_hash="SINGLEFILE000000000000000000000000000000",
            new_name="Renamed Movie 2024",
            mode=RenameMode.TORRENT_ONLY,
        )

        assert result.success is True
        assert result.torrent_renamed is True
        single_file_qbit_client.rename_torrent.assert_called_once()
        single_file_qbit_client.rename_folder.assert_not_called()
        single_file_qbit_client.rename_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_file_torrent_and_folder_mode(self, single_file_qbit_client):
        """Test torrent_and_folder mode on single file torrent (no folder to rename)."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=single_file_qbit_client,
            torrent_hash="SINGLEFILE000000000000000000000000000000",
            new_name="Renamed Movie 2024",
            mode=RenameMode.TORRENT_AND_FOLDER,
        )

        assert result.success is True
        assert result.torrent_renamed is True
        single_file_qbit_client.rename_torrent.assert_called_once()
        # No root folder in single file torrent, so rename_folder should not be called
        single_file_qbit_client.rename_folder.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_file_torrent_folder_files_mode(self, single_file_qbit_client):
        """Test torrent_folder_files mode on single file torrent."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=single_file_qbit_client,
            torrent_hash="SINGLEFILE000000000000000000000000000000",
            new_name="Renamed Movie 2024",
            mode=RenameMode.TORRENT_FOLDER_FILES,
        )

        assert result.success is True
        assert result.torrent_renamed is True
        single_file_qbit_client.rename_torrent.assert_called_once()
        # No root folder, so rename_folder should not be called
        single_file_qbit_client.rename_folder.assert_not_called()
        # But file should be renamed
        single_file_qbit_client.rename_file.assert_called_once()

    @pytest.mark.asyncio
    async def test_single_file_folder_only_mode(self, single_file_qbit_client):
        """Test folder_only mode on single file torrent (nothing to rename)."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=single_file_qbit_client,
            torrent_hash="SINGLEFILE000000000000000000000000000000",
            new_name="Renamed Movie 2024",
            mode=RenameMode.FOLDER_ONLY,
        )

        # Single file has no folder, so already_complete should be True
        assert result.success is True
        assert result.already_complete is True
        single_file_qbit_client.rename_torrent.assert_not_called()
        single_file_qbit_client.rename_folder.assert_not_called()
        single_file_qbit_client.rename_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_single_file_files_only_mode(self, single_file_qbit_client):
        """Test files_only mode on single file torrent."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=single_file_qbit_client,
            torrent_hash="SINGLEFILE000000000000000000000000000000",
            new_name="Renamed Movie 2024",
            mode=RenameMode.FILES_ONLY,
        )

        assert result.success is True
        assert result.files_renamed == 1
        single_file_qbit_client.rename_torrent.assert_not_called()
        single_file_qbit_client.rename_folder.assert_not_called()
        single_file_qbit_client.rename_file.assert_called_once()


# =============================================================================
# Multi-File Torrent Rename Tests (All Modes)
# =============================================================================


class TestMultiFileTorrentRename:
    """Test rename operations on multi-file torrents with all modes."""

    @pytest.fixture
    def fresh_multi_file_mock(self, mock_torrent_files):
        """Create a fresh mock QBitClient for each test case."""

        def create_mock():
            mock_client = MagicMock()
            current_torrent_name = {"name": "Original.Torrent.Name-Group"}
            current_files = [dict(f) for f in mock_torrent_files]

            def get_torrent_info(torrent_hash):
                torrent = MagicMock()
                torrent.name = current_torrent_name["name"]
                torrent.state = "downloading"
                torrent.get = MagicMock(
                    side_effect=lambda k, d=None: {"name": current_torrent_name["name"]}.get(k, d)
                )
                return torrent

            def rename_torrent(torrent_hash, new_name):
                current_torrent_name["name"] = new_name
                return True

            def get_files(torrent_hash):
                return current_files

            def rename_file(torrent_hash, old_path, new_path):
                for i, f in enumerate(current_files):
                    if f.get("name") == old_path:
                        current_files[i] = {"name": new_path}
                        return True
                return True

            def rename_folder(torrent_hash, old_path, new_path):
                for i, f in enumerate(current_files):
                    if f.get("name", "").startswith(old_path + "/"):
                        current_files[i] = {"name": new_path + f["name"][len(old_path) :]}
                return True

            mock_client.get_torrent_info = MagicMock(side_effect=get_torrent_info)
            mock_client.get_files = MagicMock(side_effect=get_files)
            mock_client.rename_torrent = AsyncMock(side_effect=rename_torrent)
            mock_client.rename_folder = AsyncMock(side_effect=rename_folder)
            mock_client.rename_file = AsyncMock(side_effect=rename_file)
            return mock_client

        return create_mock

    @pytest.mark.asyncio
    async def test_multi_file_all_modes_call_correct_methods(self, fresh_multi_file_mock):
        """Test all rename modes call the correct qBit methods for multi-file torrents."""
        from src.rename import RenameMode, perform_rename

        test_cases = [
            {
                "mode": RenameMode.TORRENT_ONLY,
                "expect_torrent": True,
                "expect_folder": False,
                "expect_files": False,
            },
            {
                "mode": RenameMode.TORRENT_AND_FOLDER,
                "expect_torrent": True,
                "expect_folder": True,
                "expect_files": False,
            },
            {
                "mode": RenameMode.TORRENT_FOLDER_FILES,
                "expect_torrent": True,
                "expect_folder": True,
                "expect_files": True,
            },
            {
                "mode": RenameMode.FOLDER_ONLY,
                "expect_torrent": False,
                "expect_folder": True,
                "expect_files": False,
            },
            {
                "mode": RenameMode.FILES_ONLY,
                "expect_torrent": False,
                "expect_folder": False,
                "expect_files": True,
            },
        ]

        for case in test_cases:
            # Create a fresh mock for each mode to avoid state contamination
            mock_client = fresh_multi_file_mock()

            result = await perform_rename(
                qbit=mock_client,
                torrent_hash="MULTIFILE0000000000000000000000000000000",
                new_name="New Multi File Name",
                mode=case["mode"],
            )

            assert result.success is True, f"Failed for mode: {case['mode']}"

            if case["expect_torrent"]:
                assert mock_client.rename_torrent.called, (
                    f"Expected rename_torrent for {case['mode']}"
                )
            else:
                assert not mock_client.rename_torrent.called, (
                    f"Unexpected rename_torrent for {case['mode']}"
                )

            if case["expect_folder"]:
                assert mock_client.rename_folder.called, (
                    f"Expected rename_folder for {case['mode']}"
                )
            else:
                assert not mock_client.rename_folder.called, (
                    f"Unexpected rename_folder for {case['mode']}"
                )

            if case["expect_files"]:
                assert mock_client.rename_file.called, f"Expected rename_file for {case['mode']}"
            else:
                assert not mock_client.rename_file.called, (
                    f"Unexpected rename_file for {case['mode']}"
                )


# =============================================================================
# TV Series Multi-Episode Rename Tests
# =============================================================================


class TestTVSeriesRename:
    """Test rename operations on TV series with episode preservation."""

    @pytest.fixture
    def tv_series_qbit_client(self, mock_torrent, mock_tv_series_files):
        """Create a mock QBitClient with TV series files."""
        mock_client = MagicMock()

        # Track current state for verification
        current_torrent_name = {"name": "Series.S01.Complete"}
        current_files = list(mock_tv_series_files)

        def get_torrent_info(torrent_hash):
            torrent = MagicMock()
            torrent.name = current_torrent_name["name"]
            torrent.state = "downloading"
            torrent.get = MagicMock(
                side_effect=lambda k, d=None: {"name": current_torrent_name["name"]}.get(k, d)
            )
            return torrent

        def rename_torrent(torrent_hash, new_name):
            current_torrent_name["name"] = new_name
            return True

        def get_files(torrent_hash):
            return current_files

        def rename_file(torrent_hash, old_path, new_path):
            for i, f in enumerate(current_files):
                if f.get("name") == old_path:
                    current_files[i] = {"name": new_path}
                    return True
            return True

        def rename_folder(torrent_hash, old_path, new_path):
            for i, f in enumerate(current_files):
                if f.get("name", "").startswith(old_path + "/"):
                    current_files[i] = {"name": new_path + f["name"][len(old_path) :]}
            return True

        mock_client.wait_for_torrent = AsyncMock(return_value=mock_torrent)
        mock_client.get_torrent_info = MagicMock(side_effect=get_torrent_info)
        mock_client.get_files = MagicMock(side_effect=get_files)
        mock_client.rename_torrent = AsyncMock(side_effect=rename_torrent)
        mock_client.rename_folder = AsyncMock(side_effect=rename_folder)
        mock_client.rename_file = AsyncMock(side_effect=rename_file)
        return mock_client

    @pytest.mark.asyncio
    async def test_tv_series_files_renamed_with_episode_preserved(self, tv_series_qbit_client):
        """Test that TV series files are renamed with episode numbers preserved."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=tv_series_qbit_client,
            torrent_hash="TVSERIES00000000000000000000000000000000",
            new_name="Series S01 1080p WEBDL",
            mode=RenameMode.TORRENT_FOLDER_FILES,
        )

        assert result.success is True

        # Check that rename_file was called for each episode
        assert tv_series_qbit_client.rename_file.call_count == 4

        # Get all the new file paths that were used
        rename_calls = tv_series_qbit_client.rename_file.call_args_list
        new_paths = [call[0][2] for call in rename_calls]  # Third argument is new_path

        # Verify episode numbers are preserved in new paths
        assert any("S01E01" in path for path in new_paths), f"S01E01 not found in {new_paths}"
        assert any("S01E02" in path for path in new_paths), f"S01E02 not found in {new_paths}"
        assert any("S01E03" in path for path in new_paths), f"S01E03 not found in {new_paths}"
        assert any("S01E04" in path for path in new_paths), f"S01E04 not found in {new_paths}"

    @pytest.mark.asyncio
    async def test_tv_series_torrent_only_mode(self, tv_series_qbit_client):
        """Test torrent_only mode doesn't touch files."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=tv_series_qbit_client,
            torrent_hash="TVSERIES00000000000000000000000000000000",
            new_name="Series S01 1080p WEBDL",
            mode=RenameMode.TORRENT_ONLY,
        )

        assert result.success is True
        assert result.torrent_renamed is True
        tv_series_qbit_client.rename_torrent.assert_called_once()
        tv_series_qbit_client.rename_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_tv_series_files_only_mode(self, tv_series_qbit_client):
        """Test files_only mode renames all episode files."""
        from src.rename import RenameMode, perform_rename

        result = await perform_rename(
            qbit=tv_series_qbit_client,
            torrent_hash="TVSERIES00000000000000000000000000000000",
            new_name="Series S01 1080p WEBDL",
            mode=RenameMode.FILES_ONLY,
        )

        assert result.success is True
        assert result.files_renamed == 4
        tv_series_qbit_client.rename_torrent.assert_not_called()
        tv_series_qbit_client.rename_folder.assert_not_called()
        # All 4 episode files should be renamed
        assert tv_series_qbit_client.rename_file.call_count == 4


# =============================================================================
# Trigger Filter Tests
# =============================================================================


class TestTriggerFilters:
    """Test trigger filter logic."""

    def test_indexer_include_filter(self, radarr_payload):
        """Test indexer include filter."""
        from src.config import TrackerRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should pass - indexer matches
        rules = TrackerRules()
        rules.indexers_include = ["TrackerA.*"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is True

        # Should fail - indexer doesn't match
        rules.indexers_include = ["OtherTracker.*"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is False
        assert "indexer" in reason

    def test_indexer_exclude_filter(self, radarr_payload):
        """Test indexer exclude filter."""
        from src.config import TrackerRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should fail - indexer in exclude list
        rules = TrackerRules()
        rules.indexers_exclude = ["TrackerA.*"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is False
        assert "exclude" in reason

    def test_run_only_on_specific_tracker(self, radarr_payload, sonarr_payload):
        """Test processing only for a specific tracker/indexer.

        This test verifies the use case where you want to rename torrents
        ONLY from a specific private tracker and skip all others.
        """
        from src.config import TrackerRules
        from src.models import RadarrWebhook, SonarrWebhook
        from src.rename import should_process

        # Configure rules to only process from "PrivateTracker"
        rules = TrackerRules()
        rules.indexers_include = ["PrivateTracker.*"]

        # Test with Radarr - TrackerA should NOT be processed
        radarr = RadarrWebhook(**radarr_payload)
        should_proc, reason = should_process(radarr, rules)
        assert should_proc is False
        assert "indexer" in reason.lower()

        # Modify payload to use the target tracker - should be processed
        radarr_payload["release"]["indexer"] = "PrivateTracker (API)"
        radarr_private = RadarrWebhook(**radarr_payload)
        should_proc, reason = should_process(radarr_private, rules)
        assert should_proc is True

        # Test with Sonarr - TrackerB should NOT be processed
        sonarr = SonarrWebhook(**sonarr_payload)
        should_proc, reason = should_process(sonarr, rules)
        assert should_proc is False

        # Modify to use target tracker
        sonarr_payload["release"]["indexer"] = "PrivateTracker (Prowlarr)"
        sonarr_private = SonarrWebhook(**sonarr_payload)
        should_proc, reason = should_process(sonarr_private, rules)
        assert should_proc is True

    def test_multiple_specific_trackers(self, radarr_payload):
        """Test processing from multiple specific trackers."""
        from src.config import TrackerRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        # Configure rules to only process from two specific trackers
        rules = TrackerRules()
        rules.indexers_include = ["TrackerA.*", "TrackerB.*"]

        # TrackerA should be processed
        payload = RadarrWebhook(**radarr_payload)
        should_proc, reason = should_process(payload, rules)
        assert should_proc is True

        # TrackerB should be processed
        radarr_payload["release"]["indexer"] = "TrackerB (API)"
        payload_b = RadarrWebhook(**radarr_payload)
        should_proc, reason = should_process(payload_b, rules)
        assert should_proc is True

        # TrackerC should NOT be processed
        radarr_payload["release"]["indexer"] = "TrackerC (API)"
        payload_c = RadarrWebhook(**radarr_payload)
        should_proc, reason = should_process(payload_c, rules)
        assert should_proc is False

    def test_exclude_specific_tracker(self, radarr_payload):
        """Test excluding a specific tracker while allowing all others."""
        from src.config import TrackerRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        # Configure rules to exclude only PublicTracker
        rules = TrackerRules()
        rules.indexers_exclude = ["PublicTracker.*"]

        # TrackerA should be processed (not in exclude list)
        payload = RadarrWebhook(**radarr_payload)
        should_proc, reason = should_process(payload, rules)
        assert should_proc is True

        # PublicTracker should NOT be processed
        radarr_payload["release"]["indexer"] = "PublicTracker (RARBG)"
        payload_public = RadarrWebhook(**radarr_payload)
        should_proc, reason = should_process(payload_public, rules)
        assert should_proc is False
        assert "exclude" in reason

    def test_quality_filter(self, radarr_payload):
        """Test quality filter."""
        from src.config import TrackerRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should pass - quality matches
        rules = TrackerRules()
        rules.qualities_include = [".*1080p.*"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is True

    def test_download_client_filter(self, radarr_payload):
        """Test download client filter."""
        from src.config import TrackerRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should fail - client in exclude list
        rules = TrackerRules()
        rules.download_clients_exclude = ["movies_qBit"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is False

    def test_custom_format_score_filter(self, radarr_payload):
        """Test custom format score filter."""
        from src.config import TrackerRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should pass - score is high enough
        rules = TrackerRules()
        rules.min_customformat_score = 1000
        should_proc, reason = should_process(payload, rules)
        assert should_proc is True

        # Should fail - score too low
        rules.min_customformat_score = 10000
        should_proc, reason = should_process(payload, rules)
        assert should_proc is False
        assert "score" in reason


# =============================================================================
# Model Validation Tests
# =============================================================================


class TestModelValidation:
    """Test Pydantic model validation."""

    def test_radarr_webhook_parsing(self, radarr_payload):
        """Test Radarr webhook model parsing."""
        from src.models import RadarrWebhook

        webhook = RadarrWebhook(**radarr_payload)

        assert webhook.eventType == "Grab"
        assert webhook.movie.title == "Example Movie"
        assert (
            webhook.release.releaseTitle
            == "Example Movie 2020 DE 4K Remaster BluRay 1080p ENG H.265-ReleaseGrp"
        )
        assert webhook.downloadId == "AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F"
        assert webhook.downloadClientType == "qBittorrent"

    def test_sonarr_webhook_parsing(self, sonarr_payload):
        """Test Sonarr webhook model parsing."""
        from src.models import SonarrWebhook

        webhook = SonarrWebhook(**sonarr_payload)

        assert webhook.eventType == "Grab"
        assert webhook.series.title == "Example Series"
        assert len(webhook.episodes) == 1
        assert webhook.episodes[0].seasonNumber == 2
        assert webhook.episodes[0].episodeNumber == 5
        assert webhook.get_download_id() == "1234567890ABCDEF1234567890ABCDEF12345678"
        assert "Example.Series" in webhook.get_release_title()

    def test_radarr_webhook_minimal_payload(self):
        """Test Radarr webhook with minimal required fields."""
        from src.models import RadarrWebhook

        minimal = {
            "eventType": "Grab",
            "movie": {"id": 1, "title": "Test"},
            "release": {"releaseTitle": "Test Release", "quality": "1080p"},
            "downloadId": "ABCD1234",
            "downloadClient": "qbit",
            "downloadClientType": "qBittorrent",
        }

        webhook = RadarrWebhook(**minimal)
        assert webhook.movie.title == "Test"

    def test_sonarr_webhook_minimal_payload(self):
        """Test Sonarr webhook with minimal required fields."""
        from src.models import SonarrWebhook

        minimal = {
            "eventType": "Grab",
            "series": {"id": 1, "title": "Test Series"},
            "release": {"releaseTitle": "Test.S01E01", "quality": "720p"},
        }

        webhook = SonarrWebhook(**minimal)
        assert webhook.series.title == "Test Series"


# =============================================================================
# QBitClient Tests
# =============================================================================


class TestQBitClient:
    """Test QBitClient wrapper."""

    def test_client_initialization(self):
        """Test client initialization."""
        from src.qbittorrent import QBitClient

        client = QBitClient(
            url="http://localhost:8080",
            username="admin",
            password="password",
        )

        assert client.url == "http://localhost:8080"
        assert client.username == "admin"
        assert client._client is None  # Lazy connection

    @pytest.mark.asyncio
    async def test_wait_for_torrent_polling(self):
        """Test torrent polling logic."""
        from src.qbittorrent import QBitClient

        client = QBitClient("http://test", "user", "pass")

        # Mock the internal client with proper torrent object
        mock_torrent = MagicMock()
        mock_torrent.name = "test"
        mock_torrent.state = "downloading"

        mock_internal = MagicMock()
        mock_internal.app_version = MagicMock(return_value="4.5.0")
        mock_internal.torrents_info = MagicMock(return_value=[mock_torrent])
        client._client = mock_internal

        result = await client.wait_for_torrent(
            torrent_hash="ABCD1234",
            initial_delay=0.01,
            max_retries=1,
            retry_delay=0.01,
        )

        assert result is not None
        assert result.name == "test"


# =============================================================================
# Score Validation Tests
# =============================================================================


class TestScoreValidation:
    """Test score validation functionality with Arr API integration."""

    @pytest.fixture
    def mock_arr_client(self):
        """Create a mock ArrClient for testing."""
        mock_client = MagicMock()
        mock_client.check_connection = MagicMock(return_value=True)
        return mock_client

    @pytest.fixture
    def score_validation_env(self):
        """Environment variables for score validation testing."""
        return {
            "QBITTORRENT_URL": "http://mock:8080",
            "QBITTORRENT_USERNAME": "test",
            "QBITTORRENT_PASSWORD": "test",
            "RADARR_URL": "http://radarr:7878",
            "RADARR_API_KEY": "test-radarr-key",
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "test-sonarr-key",
            "RULES_FILE": str(Path(__file__).parent / "fixtures" / "test_rules.yaml"),
            "INITIAL_DELAY": "0.01",
            "MAX_RETRIES": "1",
            "RETRY_DELAY": "0.01",
        }

    @pytest.mark.asyncio
    async def test_validate_rename_score_disabled(self, mock_qbit_client, mock_torrent):
        """When score validation is disabled, rename should proceed without API call."""
        from src.config import TrackerRules
        from src.main import _validate_rename_score

        # Create effective rules with validation disabled
        effective_rules = TrackerRules()
        effective_rules.validate_custom_format_score = False

        result = await _validate_rename_score(
            source="radarr",
            current_name="Current.Torrent.Name",
            new_name="New.Title",
            hash_short="ABCD1234",
            effective_rules=effective_rules,
        )

        assert result is True  # Should proceed without validation

    @pytest.mark.asyncio
    async def test_validate_rename_score_enabled_safe(self, mock_arr_client):
        """When score validation passes, rename should proceed."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import TrackerRules
        from src.main import _validate_rename_score

        # Create effective rules with validation enabled
        effective_rules = TrackerRules()
        effective_rules.validate_custom_format_score = True
        effective_rules.score_validation_policy = "block"

        # Create a safe comparison (scores equal)
        comparison = ScoreComparison(
            original_score=11200,
            new_score=11200,
            score_change=0,
            is_safe=True,
            original_parse=ParseResult("Orig", 11200, [], None),
            new_parse=ParseResult("New", 11200, [], None),
        )
        mock_arr_client.validate_rename = AsyncMock(return_value=comparison)

        with (
            patch("src.main.radarr_client", mock_arr_client),
            patch("src.main.settings") as mock_settings,
        ):
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                current_name="Current.Torrent.Name",
                new_name="New.Title",
                hash_short="ABCD1234",
                effective_rules=effective_rules,
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_validate_rename_score_block_on_decrease(self, mock_arr_client):
        """When score decreases with block policy, rename should be skipped."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import TrackerRules
        from src.main import _validate_rename_score

        effective_rules = TrackerRules()
        effective_rules.validate_custom_format_score = True
        effective_rules.score_validation_policy = "block"

        # Create an unsafe comparison (score decreased)
        comparison = ScoreComparison(
            original_score=11200,
            new_score=8000,
            score_change=-3200,
            is_safe=False,
            original_parse=ParseResult("Orig", 11200, [], None),
            new_parse=ParseResult("New", 8000, [], None),
        )
        mock_arr_client.validate_rename = AsyncMock(return_value=comparison)

        with (
            patch("src.main.radarr_client", mock_arr_client),
            patch("src.main.settings") as mock_settings,
        ):
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                current_name="Current.Torrent.Name",
                new_name="New.Title",
                hash_short="ABCD1234",
                effective_rules=effective_rules,
            )

            assert result is False  # Should block rename

    @pytest.mark.asyncio
    async def test_validate_rename_score_warn_on_decrease(self, mock_arr_client):
        """When score decreases with warn policy, rename should proceed with warning."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import TrackerRules
        from src.main import _validate_rename_score

        effective_rules = TrackerRules()
        effective_rules.validate_custom_format_score = True
        effective_rules.score_validation_policy = "warn"

        # Create an unsafe comparison (score decreased)
        comparison = ScoreComparison(
            original_score=11200,
            new_score=8000,
            score_change=-3200,
            is_safe=False,
            original_parse=ParseResult("Orig", 11200, [], None),
            new_parse=ParseResult("New", 8000, [], None),
        )
        mock_arr_client.validate_rename = AsyncMock(return_value=comparison)

        with (
            patch("src.main.radarr_client", mock_arr_client),
            patch("src.main.settings") as mock_settings,
        ):
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                current_name="Current.Torrent.Name",
                new_name="New.Title",
                hash_short="ABCD1234",
                effective_rules=effective_rules,
            )

            assert result is True  # Should proceed despite score decrease

    @pytest.mark.asyncio
    async def test_validate_rename_score_api_unreachable(self):
        """When Arr API is unreachable, rename should be skipped."""
        from src.config import TrackerRules
        from src.main import _validate_rename_score

        effective_rules = TrackerRules()
        effective_rules.validate_custom_format_score = True
        effective_rules.score_validation_policy = "block"

        # Mock client that returns None (API error)
        mock_arr_client = MagicMock()
        mock_arr_client.validate_rename = AsyncMock(return_value=None)

        with (
            patch("src.main.radarr_client", mock_arr_client),
            patch("src.main.settings") as mock_settings,
        ):
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                current_name="Current.Torrent.Name",
                new_name="New.Title",
                hash_short="ABCD1234",
                effective_rules=effective_rules,
            )

            assert result is False  # Should skip rename on API error

    @pytest.mark.asyncio
    async def test_validate_rename_score_no_client_configured(self):
        """When Arr client is not configured, rename should be skipped."""
        from src.config import TrackerRules
        from src.main import _validate_rename_score

        effective_rules = TrackerRules()
        effective_rules.validate_custom_format_score = True
        effective_rules.score_validation_policy = "block"

        with (
            patch("src.main.radarr_client", None),
            patch("src.main.settings") as mock_settings,
        ):
            mock_settings.radarr_url = None

            result = await _validate_rename_score(
                source="radarr",
                current_name="Current.Torrent.Name",
                new_name="New.Title",
                hash_short="ABCD1234",
                effective_rules=effective_rules,
            )

            assert result is False  # Should skip when client not configured

    @pytest.mark.asyncio
    async def test_validate_rename_score_sonarr_source(self, mock_arr_client):
        """Score validation should use Sonarr client for Sonarr webhooks."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import TrackerRules
        from src.main import _validate_rename_score

        effective_rules = TrackerRules()
        effective_rules.validate_custom_format_score = True
        effective_rules.score_validation_policy = "block"

        comparison = ScoreComparison(
            original_score=9370,
            new_score=9370,
            score_change=0,
            is_safe=True,
            original_parse=ParseResult("Orig", 9370, [], None),
            new_parse=ParseResult("New", 9370, [], None),
        )
        mock_arr_client.validate_rename = AsyncMock(return_value=comparison)

        with (
            patch("src.main.sonarr_client", mock_arr_client),
            patch("src.main.radarr_client", None),
            patch("src.main.settings") as mock_settings,
        ):
            mock_settings.sonarr_url = "http://sonarr:8989"

            result = await _validate_rename_score(
                source="sonarr",
                current_name="Current.Series.S01E01.Name",
                new_name="Series S01E01 Title",
                hash_short="EFGH5678",
                effective_rules=effective_rules,
            )

            assert result is True
            mock_arr_client.validate_rename.assert_called_once()


class TestHealthEndpointWithArrClients:
    """Test health endpoint includes Arr API status."""

    @pytest.mark.asyncio
    async def test_health_includes_arr_status_when_configured(self):
        """Health endpoint should include Sonarr/Radarr status when configured."""
        # This test verifies the structure of the health endpoint
        # when Arr clients are configured
        from src.config import RenameRules

        # Create rules with score validation enabled
        rules = RenameRules()
        rules.global_rules.validate_custom_format_score = True

        mock_sonarr = MagicMock()
        mock_sonarr.check_connection = MagicMock(return_value=True)

        mock_radarr = MagicMock()
        mock_radarr.check_connection = MagicMock(return_value=False)

        mock_qbit = MagicMock()
        mock_qbit.check_connection = MagicMock(return_value=True)

        with patch.dict(
            os.environ,
            {
                "QBITTORRENT_URL": "http://mock:8080",
                "QBITTORRENT_USERNAME": "test",
                "QBITTORRENT_PASSWORD": "test",
                "RULES_FILE": str(Path(__file__).parent / "fixtures" / "test_rules.yaml"),
            },
        ):
            from importlib import reload

            from src import config

            reload(config)
            from src import main

            reload(main)

            # Replace clients
            main.qbit_client = mock_qbit
            main.sonarr_client = mock_sonarr
            main.radarr_client = mock_radarr

            # Patch rules to enable score validation
            with patch.object(main, "rules", rules):
                transport = ASGITransport(app=main.app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    response = await client.get("/health")

                    assert response.status_code == 200
                    data = response.json()

                    assert data["status"] == "ok"
                    assert data["qbittorrent"] == "connected"
                    assert data["score_validation"] is True
                    assert data["sonarr"] == "connected"
                    assert data["radarr"] == "disconnected"


class TestConfigScoreValidation:
    """Test score validation configuration loading."""

    def test_rules_default_values(self):
        """TrackerRules should have correct default values for score validation."""
        from src.config import TrackerRules

        rules = TrackerRules()

        assert rules.validate_custom_format_score is False
        assert rules.score_validation_policy == "block"

    def test_rules_from_yaml_with_score_validation(self, tmp_path):
        """RenameRules should load score validation settings from YAML (legacy format)."""
        from src.config import RenameRules

        config_file = tmp_path / "test_rules.yaml"
        config_file.write_text("""
validate_custom_format_score: true
score_validation_policy: "warn"
""")

        rules = RenameRules.from_yaml(str(config_file))

        # Properties delegate to global_rules for backward compatibility
        assert rules.validate_custom_format_score is True
        assert rules.score_validation_policy == "warn"

    def test_settings_arr_api_defaults(self):
        """Settings should have None defaults for Arr API config."""
        with patch.dict(os.environ, {}, clear=True):
            from importlib import reload

            from src import config

            reload(config)

            # Access settings after reload
            settings = config.Settings()

            assert settings.sonarr_url is None
            assert settings.sonarr_api_key is None
            assert settings.radarr_url is None
            assert settings.radarr_api_key is None

    def test_settings_arr_api_from_env(self):
        """Settings should load Arr API config from environment."""
        with patch.dict(
            os.environ,
            {
                "SONARR_URL": "http://sonarr:8989",
                "SONARR_API_KEY": "sonarr-key-123",
                "RADARR_URL": "http://radarr:7878",
                "RADARR_API_KEY": "radarr-key-456",
            },
        ):
            from importlib import reload

            from src import config

            reload(config)

            settings = config.Settings()

            assert settings.sonarr_url == "http://sonarr:8989"
            assert settings.sonarr_api_key == "sonarr-key-123"
            assert settings.radarr_url == "http://radarr:7878"
            assert settings.radarr_api_key == "radarr-key-456"


# =============================================================================
# Entry Point
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
