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
    """Create a fully mocked QBitClient."""
    mock_client = MagicMock()
    mock_client.wait_for_torrent = AsyncMock(return_value=mock_torrent)
    mock_client.get_torrent_info = MagicMock(return_value=mock_torrent)
    mock_client.get_files = MagicMock(return_value=mock_torrent_files)
    mock_client.rename_torrent = MagicMock(return_value=True)
    mock_client.rename_folder = MagicMock(return_value=True)
    mock_client.rename_file = MagicMock(return_value=True)
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
        mock_qbit_client.rename_torrent = MagicMock(return_value=False)

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
# Background Task Tests
# =============================================================================


class TestBackgroundTaskProcessing:
    """Test background task rename processing."""

    @pytest.mark.asyncio
    async def test_process_rename_task_success(self, mock_qbit_client):
        """Test successful rename task processing."""
        from src import main
        from src.main import process_rename_task

        # Set the global client
        main.qbit_client = mock_qbit_client

        await process_rename_task(
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            release_title="Example Movie 2020 BluRay 1080p-Group",
            source="radarr",
            media_title="Example Movie",
        )

        # Verify qBit client methods were called
        mock_qbit_client.wait_for_torrent.assert_called_once()
        mock_qbit_client.rename_torrent.assert_called()

    @pytest.mark.asyncio
    async def test_process_rename_task_torrent_not_found(self, mock_qbit_client):
        """Test rename task when torrent is not found."""
        from src import main
        from src.main import process_rename_task

        # Make wait_for_torrent return None
        mock_qbit_client.wait_for_torrent = AsyncMock(return_value=None)
        main.qbit_client = mock_qbit_client

        # Should complete without error
        await process_rename_task(
            torrent_hash="DEADBEEF00000000000000000000000000000000",
            release_title="Missing Torrent",
            source="radarr",
            media_title="Missing Movie",
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

        assert result is True
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

        assert result is True
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

        assert result is True
        mock_qbit_client.rename_torrent.assert_called_once()
        mock_qbit_client.rename_folder.assert_called_once()
        # Files should be renamed too
        assert mock_qbit_client.rename_file.call_count > 0

    @pytest.mark.asyncio
    async def test_rename_failure_handled(self, mock_qbit_client):
        """Test rename failure is handled gracefully."""
        from src.rename import RenameMode, perform_rename

        # Make rename fail
        mock_qbit_client.rename_torrent = MagicMock(return_value=False)

        result = await perform_rename(
            qbit=mock_qbit_client,
            torrent_hash="AF35BC0E03A9D8405779A69FC9A438F1BFE90C5F",
            new_name="New Torrent Name",
            mode=RenameMode.TORRENT_ONLY,
        )

        assert result is False

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

        assert result is False

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

        assert result is True
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

        assert result is True
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
        mock_client.get_torrent_info = MagicMock(return_value=mock_torrent)
        mock_client.get_files = MagicMock(return_value=mock_single_file)
        mock_client.rename_torrent = MagicMock(return_value=True)
        mock_client.rename_folder = MagicMock(return_value=True)
        mock_client.rename_file = MagicMock(return_value=True)
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

        assert result is True
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

        assert result is True
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

        assert result is True
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

        assert result is True
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

        assert result is True
        single_file_qbit_client.rename_torrent.assert_not_called()
        single_file_qbit_client.rename_folder.assert_not_called()
        single_file_qbit_client.rename_file.assert_called_once()


# =============================================================================
# Multi-File Torrent Rename Tests (All Modes)
# =============================================================================


class TestMultiFileTorrentRename:
    """Test rename operations on multi-file torrents with all modes."""

    @pytest.mark.asyncio
    async def test_multi_file_all_modes_call_correct_methods(self, mock_qbit_client):
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
            # Reset mocks
            mock_qbit_client.rename_torrent.reset_mock()
            mock_qbit_client.rename_folder.reset_mock()
            mock_qbit_client.rename_file.reset_mock()

            result = await perform_rename(
                qbit=mock_qbit_client,
                torrent_hash="MULTIFILE0000000000000000000000000000000",
                new_name="New Multi File Name",
                mode=case["mode"],
            )

            assert result is True, f"Failed for mode: {case['mode']}"

            if case["expect_torrent"]:
                assert mock_qbit_client.rename_torrent.called, (
                    f"Expected rename_torrent for {case['mode']}"
                )
            else:
                assert not mock_qbit_client.rename_torrent.called, (
                    f"Unexpected rename_torrent for {case['mode']}"
                )

            if case["expect_folder"]:
                assert mock_qbit_client.rename_folder.called, (
                    f"Expected rename_folder for {case['mode']}"
                )
            else:
                assert not mock_qbit_client.rename_folder.called, (
                    f"Unexpected rename_folder for {case['mode']}"
                )

            if case["expect_files"]:
                assert mock_qbit_client.rename_file.called, (
                    f"Expected rename_file for {case['mode']}"
                )
            else:
                assert not mock_qbit_client.rename_file.called, (
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
        # Update torrent name for TV series
        mock_torrent.get = MagicMock(
            side_effect=lambda k, d=None: {"name": "Series.S01.Complete"}.get(k, d)
        )
        mock_client.wait_for_torrent = AsyncMock(return_value=mock_torrent)
        mock_client.get_torrent_info = MagicMock(return_value=mock_torrent)
        mock_client.get_files = MagicMock(return_value=mock_tv_series_files)
        mock_client.rename_torrent = MagicMock(return_value=True)
        mock_client.rename_folder = MagicMock(return_value=True)
        mock_client.rename_file = MagicMock(return_value=True)
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

        assert result is True

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

        assert result is True
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

        assert result is True
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
        from src.config import RenameRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should pass - indexer matches
        rules = RenameRules()
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
        from src.config import RenameRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should fail - indexer in exclude list
        rules = RenameRules()
        rules.indexers_exclude = ["TrackerA.*"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is False
        assert "exclude" in reason

    def test_run_only_on_specific_tracker(self, radarr_payload, sonarr_payload):
        """Test processing only for a specific tracker/indexer.

        This test verifies the use case where you want to rename torrents
        ONLY from a specific private tracker and skip all others.
        """
        from src.config import RenameRules
        from src.models import RadarrWebhook, SonarrWebhook
        from src.rename import should_process

        # Configure rules to only process from "PrivateTracker"
        rules = RenameRules()
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
        from src.config import RenameRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        # Configure rules to only process from two specific trackers
        rules = RenameRules()
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
        from src.config import RenameRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        # Configure rules to exclude only PublicTracker
        rules = RenameRules()
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
        from src.config import RenameRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should pass - quality matches
        rules = RenameRules()
        rules.qualities_include = [".*1080p.*"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is True

    def test_download_client_filter(self, radarr_payload):
        """Test download client filter."""
        from src.config import RenameRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should fail - client in exclude list
        rules = RenameRules()
        rules.download_clients_exclude = ["movies_qBit"]
        should_proc, reason = should_process(payload, rules)
        assert should_proc is False

    def test_custom_format_score_filter(self, radarr_payload):
        """Test custom format score filter."""
        from src.config import RenameRules
        from src.models import RadarrWebhook
        from src.rename import should_process

        payload = RadarrWebhook(**radarr_payload)

        # Should pass - score is high enough
        rules = RenameRules()
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
        from src.config import RenameRules
        from src.main import _validate_rename_score

        # Create rules with validation disabled
        rules = RenameRules()
        rules.validate_custom_format_score = False

        with patch("src.main.rules", rules):
            result = await _validate_rename_score(
                source="radarr",
                release_title="Original.Title",
                new_name="New.Title",
                hash_short="ABCD1234",
            )

            assert result is True  # Should proceed without validation

    @pytest.mark.asyncio
    async def test_validate_rename_score_enabled_safe(self, mock_arr_client):
        """When score validation passes, rename should proceed."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import RenameRules
        from src.main import _validate_rename_score

        # Create rules with validation enabled
        rules = RenameRules()
        rules.validate_custom_format_score = True
        rules.score_validation_policy = "block"

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

        with patch("src.main.rules", rules), \
             patch("src.main.radarr_client", mock_arr_client), \
             patch("src.main.settings") as mock_settings:
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                release_title="Original.Title",
                new_name="New.Title",
                hash_short="ABCD1234",
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_validate_rename_score_block_on_decrease(self, mock_arr_client):
        """When score decreases with block policy, rename should be skipped."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import RenameRules
        from src.main import _validate_rename_score

        rules = RenameRules()
        rules.validate_custom_format_score = True
        rules.score_validation_policy = "block"

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

        with patch("src.main.rules", rules), \
             patch("src.main.radarr_client", mock_arr_client), \
             patch("src.main.settings") as mock_settings:
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                release_title="Original.Title",
                new_name="New.Title",
                hash_short="ABCD1234",
            )

            assert result is False  # Should block rename

    @pytest.mark.asyncio
    async def test_validate_rename_score_warn_on_decrease(self, mock_arr_client):
        """When score decreases with warn policy, rename should proceed with warning."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import RenameRules
        from src.main import _validate_rename_score

        rules = RenameRules()
        rules.validate_custom_format_score = True
        rules.score_validation_policy = "warn"

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

        with patch("src.main.rules", rules), \
             patch("src.main.radarr_client", mock_arr_client), \
             patch("src.main.settings") as mock_settings:
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                release_title="Original.Title",
                new_name="New.Title",
                hash_short="ABCD1234",
            )

            assert result is True  # Should proceed despite score decrease

    @pytest.mark.asyncio
    async def test_validate_rename_score_api_unreachable(self):
        """When Arr API is unreachable, rename should be skipped."""
        from src.config import RenameRules
        from src.main import _validate_rename_score

        rules = RenameRules()
        rules.validate_custom_format_score = True
        rules.score_validation_policy = "block"

        # Mock client that returns None (API error)
        mock_arr_client = MagicMock()
        mock_arr_client.validate_rename = AsyncMock(return_value=None)

        with patch("src.main.rules", rules), \
             patch("src.main.radarr_client", mock_arr_client), \
             patch("src.main.settings") as mock_settings:
            mock_settings.radarr_url = "http://radarr:7878"

            result = await _validate_rename_score(
                source="radarr",
                release_title="Original.Title",
                new_name="New.Title",
                hash_short="ABCD1234",
            )

            assert result is False  # Should skip rename on API error

    @pytest.mark.asyncio
    async def test_validate_rename_score_no_client_configured(self):
        """When Arr client is not configured, rename should be skipped."""
        from src.config import RenameRules
        from src.main import _validate_rename_score

        rules = RenameRules()
        rules.validate_custom_format_score = True
        rules.score_validation_policy = "block"

        with patch("src.main.rules", rules), \
             patch("src.main.radarr_client", None), \
             patch("src.main.settings") as mock_settings:
            mock_settings.radarr_url = None

            result = await _validate_rename_score(
                source="radarr",
                release_title="Original.Title",
                new_name="New.Title",
                hash_short="ABCD1234",
            )

            assert result is False  # Should skip when client not configured

    @pytest.mark.asyncio
    async def test_validate_rename_score_sonarr_source(self, mock_arr_client):
        """Score validation should use Sonarr client for Sonarr webhooks."""
        from src.arrapi import ParseResult, ScoreComparison
        from src.config import RenameRules
        from src.main import _validate_rename_score

        rules = RenameRules()
        rules.validate_custom_format_score = True
        rules.score_validation_policy = "block"

        comparison = ScoreComparison(
            original_score=9370,
            new_score=9370,
            score_change=0,
            is_safe=True,
            original_parse=ParseResult("Orig", 9370, [], None),
            new_parse=ParseResult("New", 9370, [], None),
        )
        mock_arr_client.validate_rename = AsyncMock(return_value=comparison)

        with patch("src.main.rules", rules), \
             patch("src.main.sonarr_client", mock_arr_client), \
             patch("src.main.radarr_client", None), \
             patch("src.main.settings") as mock_settings:
            mock_settings.sonarr_url = "http://sonarr:8989"

            result = await _validate_rename_score(
                source="sonarr",
                release_title="Series.S01E01.Title",
                new_name="Series S01E01 Title",
                hash_short="EFGH5678",
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

        rules = RenameRules()
        rules.validate_custom_format_score = True

        mock_sonarr = MagicMock()
        mock_sonarr.check_connection = MagicMock(return_value=True)

        mock_radarr = MagicMock()
        mock_radarr.check_connection = MagicMock(return_value=False)

        mock_qbit = MagicMock()
        mock_qbit.check_connection = MagicMock(return_value=True)

        with patch.dict(os.environ, {
            "QBITTORRENT_URL": "http://mock:8080",
            "QBITTORRENT_USERNAME": "test",
            "QBITTORRENT_PASSWORD": "test",
            "RULES_FILE": str(Path(__file__).parent / "fixtures" / "test_rules.yaml"),
        }):
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
        """RenameRules should have correct default values for score validation."""
        from src.config import RenameRules

        rules = RenameRules()

        assert rules.validate_custom_format_score is False
        assert rules.score_validation_policy == "block"

    def test_rules_from_yaml_with_score_validation(self, tmp_path):
        """RenameRules should load score validation settings from YAML."""
        from src.config import RenameRules

        config_file = tmp_path / "test_rules.yaml"
        config_file.write_text("""
validate_custom_format_score: true
score_validation_policy: "warn"
""")

        rules = RenameRules.from_yaml(str(config_file))

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
        with patch.dict(os.environ, {
            "SONARR_URL": "http://sonarr:8989",
            "SONARR_API_KEY": "sonarr-key-123",
            "RADARR_URL": "http://radarr:7878",
            "RADARR_API_KEY": "radarr-key-456",
        }):
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
