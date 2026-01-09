"""Tests for the Sonarr/Radarr API client (arrapi module).

These tests validate:
- ArrClient initialization and configuration
- Connection checking functionality
- Parse title endpoint calls
- Score comparison and validation
- Error handling for API failures
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.arrapi import ArrClient, ParseResult, ScoreComparison

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def radarr_client():
    """Create a Radarr API client for testing."""
    return ArrClient(
        url="http://radarr:7878",
        api_key="test-api-key",
        app_type="radarr",
    )


@pytest.fixture
def sonarr_client():
    """Create a Sonarr API client for testing."""
    return ArrClient(
        url="http://sonarr:8989",
        api_key="test-api-key",
        app_type="sonarr",
    )


@pytest.fixture
def radarr_parse_response():
    """Sample Radarr parse API response."""
    return {
        "title": "Home Alone 1990 BluRay 1080p [Ukrainian English][Subs Ukrainian English] HDR H.265-TaurohtaR",
        "parsedMovieInfo": {
            "movieTitles": ["Home Alone"],
            "movieTitle": "Home Alone",
            "year": 1990,
            "quality": {
                "quality": {"id": 7, "name": "Bluray-1080p"},
            },
            "releaseGroup": "TaurohtaR",
        },
        "customFormats": [
            {"id": 1, "name": "HDR"},
            {"id": 2, "name": "x265"},
            {"id": 3, "name": "Bluray-1080p"},
        ],
        "customFormatScore": 11200,
    }


@pytest.fixture
def sonarr_parse_response():
    """Sample Sonarr parse API response."""
    return {
        "title": "High Potential S02E08 AMZN WEB-DL 1080p [Ukrainian English][Subs Ukrainian English]-Anonymous.mkv",
        "parsedEpisodeInfo": {
            "seriesTitle": "High Potential",
            "seasonNumber": 2,
            "episodeNumbers": [8],
            "quality": {
                "quality": {"id": 3, "name": "WEBDL-1080p"},
            },
            "releaseGroup": "Anonymous",
        },
        "customFormats": [
            {"id": 1, "name": "WEBDL-1080p"},
            {"id": 2, "name": "AMZN"},
        ],
        "customFormatScore": 9370,
    }


# =============================================================================
# ArrClient Initialization Tests
# =============================================================================


class TestArrClientInit:
    """Test ArrClient initialization."""

    def test_url_trailing_slash_removed(self):
        """URL should have trailing slash removed."""
        client = ArrClient(
            url="http://radarr:7878/",
            api_key="test",
            app_type="radarr",
        )
        assert client.url == "http://radarr:7878"

    def test_app_type_normalized_to_lowercase(self):
        """App type should be normalized to lowercase."""
        client = ArrClient(
            url="http://radarr:7878",
            api_key="test",
            app_type="RADARR",
        )
        assert client.app_type == "radarr"

    def test_headers_include_api_key(self, radarr_client):
        """Headers should include X-Api-Key."""
        headers = radarr_client._get_headers()
        assert headers["X-Api-Key"] == "test-api-key"
        assert headers["Content-Type"] == "application/json"


# =============================================================================
# Connection Check Tests
# =============================================================================


class TestCheckConnection:
    """Test connection checking functionality."""

    def test_check_connection_success(self, radarr_client):
        """Check connection should return True when API responds with 200."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__enter__.return_value = mock_client

            result = radarr_client.check_connection()

            assert result is True
            mock_client.get.assert_called_once_with("http://radarr:7878/api/v3/system/status")

    def test_check_connection_failure_non_200(self, radarr_client):
        """Check connection should return False for non-200 status."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__enter__.return_value = mock_client

            result = radarr_client.check_connection()

            assert result is False

    def test_check_connection_request_error(self, radarr_client):
        """Check connection should return False on request error."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get.side_effect = httpx.RequestError("Connection refused")
            mock_client_class.return_value.__enter__.return_value = mock_client

            result = radarr_client.check_connection()

            assert result is False

    def test_check_connection_unexpected_error(self, radarr_client):
        """Check connection should return False on unexpected error."""
        with patch("httpx.Client") as mock_client_class:
            mock_client = MagicMock()
            mock_client.get.side_effect = Exception("Unexpected error")
            mock_client_class.return_value.__enter__.return_value = mock_client

            result = radarr_client.check_connection()

            assert result is False


# =============================================================================
# Parse Title Tests
# =============================================================================


class TestParseTitle:
    """Test parse_title functionality."""

    @pytest.mark.asyncio
    async def test_parse_title_radarr_success(self, radarr_client, radarr_parse_response):
        """Parse title should return ParseResult for Radarr."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = radarr_parse_response

        with patch.object(radarr_client, "_get_client") as mock_get_client:
            mock_async_client = AsyncMock()
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_async_client

            result = await radarr_client.parse_title("Test Movie Title")

            assert result is not None
            assert result.custom_format_score == 11200
            assert "HDR" in result.custom_formats
            assert "x265" in result.custom_formats
            assert result.parsed_title == "Home Alone"

    @pytest.mark.asyncio
    async def test_parse_title_sonarr_success(self, sonarr_client, sonarr_parse_response):
        """Parse title should return ParseResult for Sonarr."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = sonarr_parse_response

        with patch.object(sonarr_client, "_get_client") as mock_get_client:
            mock_async_client = AsyncMock()
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_async_client

            result = await sonarr_client.parse_title("Test Series Title")

            assert result is not None
            assert result.custom_format_score == 9370
            assert "WEBDL-1080p" in result.custom_formats
            assert result.parsed_title == "High Potential"

    @pytest.mark.asyncio
    async def test_parse_title_api_error(self, radarr_client):
        """Parse title should return None on API error."""
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(radarr_client, "_get_client") as mock_get_client:
            mock_async_client = AsyncMock()
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_async_client

            result = await radarr_client.parse_title("Test Title")

            assert result is None

    @pytest.mark.asyncio
    async def test_parse_title_request_error(self, radarr_client):
        """Parse title should return None on request error."""
        with patch.object(radarr_client, "_get_client") as mock_get_client:
            mock_async_client = AsyncMock()
            mock_async_client.get = AsyncMock(side_effect=httpx.RequestError("Connection refused"))
            mock_get_client.return_value = mock_async_client

            result = await radarr_client.parse_title("Test Title")

            assert result is None

    @pytest.mark.asyncio
    async def test_parse_title_empty_custom_formats(self, radarr_client):
        """Parse title should handle empty custom formats."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "title": "Test",
            "parsedMovieInfo": {"movieTitles": ["Test"]},
            "customFormats": [],
            "customFormatScore": 0,
        }

        with patch.object(radarr_client, "_get_client") as mock_get_client:
            mock_async_client = AsyncMock()
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_async_client

            result = await radarr_client.parse_title("Test Title")

            assert result is not None
            assert result.custom_format_score == 0
            assert result.custom_formats == []

    @pytest.mark.asyncio
    async def test_parse_title_missing_parsed_info(self, radarr_client):
        """Parse title should handle missing parsed info gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "title": "Unparseable Title",
            "customFormats": [],
            "customFormatScore": 0,
        }

        with patch.object(radarr_client, "_get_client") as mock_get_client:
            mock_async_client = AsyncMock()
            mock_async_client.get = AsyncMock(return_value=mock_response)
            mock_get_client.return_value = mock_async_client

            result = await radarr_client.parse_title("Unparseable Title")

            assert result is not None
            assert result.parsed_title is None


# =============================================================================
# Validate Rename Tests
# =============================================================================


class TestValidateRename:
    """Test validate_rename functionality."""

    @pytest.mark.asyncio
    async def test_validate_rename_safe_same_score(self, radarr_client):
        """Validate rename should mark as safe when scores are equal."""
        original_parse = ParseResult(
            title="Original",
            custom_format_score=11200,
            custom_formats=["HDR", "x265"],
            parsed_title="Movie",
        )
        new_parse = ParseResult(
            title="New",
            custom_format_score=11200,
            custom_formats=["HDR", "x265"],
            parsed_title="Movie",
        )

        with patch.object(radarr_client, "parse_title") as mock_parse:
            mock_parse.side_effect = [original_parse, new_parse]

            result = await radarr_client.validate_rename("Original", "New")

            assert result is not None
            assert result.is_safe is True
            assert result.score_change == 0
            assert result.original_score == 11200
            assert result.new_score == 11200

    @pytest.mark.asyncio
    async def test_validate_rename_safe_score_increased(self, radarr_client):
        """Validate rename should mark as safe when score increases."""
        original_parse = ParseResult(
            title="Original",
            custom_format_score=8000,
            custom_formats=["x265"],
            parsed_title="Movie",
        )
        new_parse = ParseResult(
            title="New",
            custom_format_score=11200,
            custom_formats=["HDR", "x265"],
            parsed_title="Movie",
        )

        with patch.object(radarr_client, "parse_title") as mock_parse:
            mock_parse.side_effect = [original_parse, new_parse]

            result = await radarr_client.validate_rename("Original", "New")

            assert result is not None
            assert result.is_safe is True
            assert result.score_change == 3200

    @pytest.mark.asyncio
    async def test_validate_rename_unsafe_score_decreased(self, radarr_client):
        """Validate rename should mark as unsafe when score decreases."""
        original_parse = ParseResult(
            title="Original",
            custom_format_score=11200,
            custom_formats=["HDR", "x265"],
            parsed_title="Movie",
        )
        new_parse = ParseResult(
            title="New",
            custom_format_score=8000,
            custom_formats=["x265"],
            parsed_title="Movie",
        )

        with patch.object(radarr_client, "parse_title") as mock_parse:
            mock_parse.side_effect = [original_parse, new_parse]

            result = await radarr_client.validate_rename("Original", "New")

            assert result is not None
            assert result.is_safe is False
            assert result.score_change == -3200

    @pytest.mark.asyncio
    async def test_validate_rename_original_parse_fails(self, radarr_client):
        """Validate rename should return None if original parse fails."""
        with patch.object(radarr_client, "parse_title") as mock_parse:
            mock_parse.return_value = None

            result = await radarr_client.validate_rename("Original", "New")

            assert result is None

    @pytest.mark.asyncio
    async def test_validate_rename_new_parse_fails(self, radarr_client):
        """Validate rename should return None if new parse fails."""
        original_parse = ParseResult(
            title="Original",
            custom_format_score=11200,
            custom_formats=["HDR"],
            parsed_title="Movie",
        )

        with patch.object(radarr_client, "parse_title") as mock_parse:
            mock_parse.side_effect = [original_parse, None]

            result = await radarr_client.validate_rename("Original", "New")

            assert result is None


# =============================================================================
# DataClass Tests
# =============================================================================


class TestParseResult:
    """Test ParseResult dataclass."""

    def test_parse_result_creation(self):
        """ParseResult should be created with all fields."""
        result = ParseResult(
            title="Test Title",
            custom_format_score=1000,
            custom_formats=["HDR", "x265"],
            parsed_title="Test",
        )

        assert result.title == "Test Title"
        assert result.custom_format_score == 1000
        assert result.custom_formats == ["HDR", "x265"]
        assert result.parsed_title == "Test"

    def test_parse_result_with_none_parsed_title(self):
        """ParseResult should accept None for parsed_title."""
        result = ParseResult(
            title="Test",
            custom_format_score=0,
            custom_formats=[],
            parsed_title=None,
        )

        assert result.parsed_title is None


class TestScoreComparison:
    """Test ScoreComparison dataclass."""

    def test_score_comparison_creation(self):
        """ScoreComparison should be created with all fields."""
        original = ParseResult("Orig", 1000, [], None)
        new = ParseResult("New", 1500, [], None)

        comparison = ScoreComparison(
            original_score=1000,
            new_score=1500,
            score_change=500,
            is_safe=True,
            original_parse=original,
            new_parse=new,
        )

        assert comparison.original_score == 1000
        assert comparison.new_score == 1500
        assert comparison.score_change == 500
        assert comparison.is_safe is True
        assert comparison.original_parse == original
        assert comparison.new_parse == new


# =============================================================================
# Client Lifecycle Tests
# =============================================================================


class TestClientLifecycle:
    """Test client lifecycle management."""

    @pytest.mark.asyncio
    async def test_close_client(self, radarr_client):
        """Close should properly clean up the async client."""
        # Create a mock client
        mock_async_client = AsyncMock()
        mock_async_client.is_closed = False
        mock_async_client.aclose = AsyncMock()
        radarr_client._client = mock_async_client

        await radarr_client.close()

        mock_async_client.aclose.assert_called_once()
        assert radarr_client._client is None

    @pytest.mark.asyncio
    async def test_close_already_closed(self, radarr_client):
        """Close should handle already closed client gracefully."""
        radarr_client._client = None

        # Should not raise
        await radarr_client.close()

        assert radarr_client._client is None

    @pytest.mark.asyncio
    async def test_get_client_creates_new(self, radarr_client):
        """_get_client should create new client when none exists."""
        assert radarr_client._client is None

        with patch("httpx.AsyncClient") as mock_async_client_class:
            mock_client = AsyncMock()
            mock_client.is_closed = False
            mock_async_client_class.return_value = mock_client

            client = await radarr_client._get_client()

            assert client == mock_client
            mock_async_client_class.assert_called_once()
