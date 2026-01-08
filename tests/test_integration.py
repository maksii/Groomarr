"""Integration tests with mocked qBittorrent client.

These tests validate the full application flow:
- Webhook endpoints receive and validate payloads
- Background tasks process correctly
- qBittorrent API calls are made as expected
- Error handling works correctly
"""

import asyncio
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
    torrent.get = MagicMock(side_effect=lambda k, d=None: {"name": "Original.Torrent.Name-Group"}.get(k, d))
    return torrent


@pytest.fixture
def mock_torrent_files():
    """Create mock torrent files list."""
    return [
        {"name": "Original.Torrent.Name-Group/movie.mkv"},
        {"name": "Original.Torrent.Name-Group/sample.mkv"},
        {"name": "Original.Torrent.Name-Group/subs/english.srt"},
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
    with patch.dict(os.environ, {
        "QBITTORRENT_URL": "http://mock:8080",
        "QBITTORRENT_USERNAME": "test",
        "QBITTORRENT_PASSWORD": "test",
        "RULES_FILE": str(Path(__file__).parent / "fixtures" / "test_rules.yaml"),
        "INITIAL_DELAY": "0.01",  # Speed up tests
        "MAX_RETRIES": "1",
        "RETRY_DELAY": "0.01",
    }):
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
        assert data["torrent_hash"] == sonarr_payload["release"]["downloadId"]

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
        from src.main import process_rename_task
        from src import main
        
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
        from src.main import process_rename_task
        from src import main
        
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
        assert webhook.release.releaseTitle == "Example Movie 2020 DE 4K Remaster BluRay 1080p ENG H.265-ReleaseGrp"
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
# Entry Point
# =============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
