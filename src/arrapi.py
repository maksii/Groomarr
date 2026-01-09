"""Sonarr/Radarr API client for parse endpoint integration."""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Result from parsing a release title via Arr API."""

    title: str
    custom_format_score: int
    custom_formats: list[str]
    parsed_title: str | None  # What Arr extracted as the title


@dataclass
class ScoreComparison:
    """Comparison of custom format scores between original and new names."""

    original_score: int
    new_score: int
    score_change: int  # new - original (negative = decrease)
    is_safe: bool  # True if new_score >= original_score
    original_parse: ParseResult | None
    new_parse: ParseResult | None


class ArrClient:
    """Client for Sonarr/Radarr API interactions.

    Provides methods to parse release titles and compare custom format scores
    to validate renames won't negatively impact Sonarr/Radarr matching.
    """

    def __init__(self, url: str, api_key: str, app_type: str):
        """Initialize the Arr API client.

        Args:
            url: Base URL of the Sonarr/Radarr instance (e.g., http://sonarr:8989)
            api_key: API key for authentication
            app_type: Type of application ("sonarr" or "radarr")
        """
        # Normalize URL (remove trailing slash)
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.app_type = app_type.lower()
        self._client: httpx.AsyncClient | None = None

    def _get_headers(self) -> dict[str, str]:
        """Get headers for API requests."""
        return {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self._get_headers(),
                timeout=httpx.Timeout(10.0),
            )
        return self._client

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def check_connection(self) -> bool:
        """Check if the Arr API is reachable (synchronous).

        Returns:
            True if connected, False otherwise
        """
        try:
            with httpx.Client(headers=self._get_headers(), timeout=5.0) as client:
                response = client.get(f"{self.url}/api/v3/system/status")
                if response.status_code == 200:
                    logger.debug(f"{self.app_type} API connection successful")
                    return True
                else:
                    logger.warning(f"{self.app_type} API returned status {response.status_code}")
                    return False
        except httpx.RequestError as e:
            logger.warning(f"{self.app_type} API connection failed: {e}")
            return False
        except Exception as e:
            logger.error(f"{self.app_type} API unexpected error: {e}")
            return False

    async def check_connection_async(self) -> bool:
        """Check if the Arr API is reachable (asynchronous).

        Returns:
            True if connected, False otherwise
        """
        try:
            client = await self._get_client()
            response = await client.get(f"{self.url}/api/v3/system/status")
            if response.status_code == 200:
                logger.debug(f"{self.app_type} API connection successful")
                return True
            else:
                logger.warning(f"{self.app_type} API returned status {response.status_code}")
                return False
        except httpx.RequestError as e:
            logger.warning(f"{self.app_type} API connection failed: {e}")
            return False
        except Exception as e:
            logger.error(f"{self.app_type} API unexpected error: {e}")
            return False

    async def parse_title(self, title: str) -> ParseResult | None:
        """Parse a release title using the Arr API.

        Calls /api/v3/parse?title={title} and extracts the custom format score
        and other parsed information.

        Args:
            title: Release title to parse

        Returns:
            ParseResult with parsed information, or None if parsing failed
        """
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.url}/api/v3/parse",
                params={"title": title},
            )

            if response.status_code != 200:
                logger.warning(
                    f"[{self.app_type}] Parse API returned status {response.status_code} for '{title[:50]}...'"
                )
                return None

            data = response.json()

            # Extract custom format score (at root level)
            custom_format_score = data.get("customFormatScore", 0)

            # Extract custom format names
            custom_formats = []
            for cf in data.get("customFormats", []):
                if isinstance(cf, dict) and "name" in cf:
                    custom_formats.append(cf["name"])

            # Extract parsed title based on app type
            parsed_title = None
            if self.app_type == "radarr":
                parsed_info = data.get("parsedMovieInfo", {})
                if parsed_info:
                    # Try to get movie titles
                    movie_titles = parsed_info.get("movieTitles", [])
                    if movie_titles:
                        parsed_title = movie_titles[0]
                    elif parsed_info.get("movieTitle"):
                        parsed_title = parsed_info["movieTitle"]
            else:  # sonarr
                parsed_info = data.get("parsedEpisodeInfo", {})
                if parsed_info:
                    parsed_title = parsed_info.get("seriesTitle")

            logger.debug(
                f"[{self.app_type}] Parsed '{title[:40]}...' -> "
                f"score={custom_format_score}, formats={custom_formats}"
            )

            return ParseResult(
                title=title,
                custom_format_score=custom_format_score,
                custom_formats=custom_formats,
                parsed_title=parsed_title,
            )

        except httpx.RequestError as e:
            logger.error(f"[{self.app_type}] Parse API request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"[{self.app_type}] Parse API unexpected error: {e}")
            return None

    async def validate_rename(self, original_title: str, new_title: str) -> ScoreComparison | None:
        """Compare custom format scores between original and new title.

        Args:
            original_title: Original release title
            new_title: Proposed new title after rename

        Returns:
            ScoreComparison with comparison results, or None if validation failed
        """
        # Parse both titles
        original_parse = await self.parse_title(original_title)
        if original_parse is None:
            logger.warning(
                f"[{self.app_type}] Failed to parse original title, cannot validate rename"
            )
            return None

        new_parse = await self.parse_title(new_title)
        if new_parse is None:
            logger.warning(f"[{self.app_type}] Failed to parse new title, cannot validate rename")
            return None

        # Calculate score comparison
        score_change = new_parse.custom_format_score - original_parse.custom_format_score
        is_safe = new_parse.custom_format_score >= original_parse.custom_format_score

        comparison = ScoreComparison(
            original_score=original_parse.custom_format_score,
            new_score=new_parse.custom_format_score,
            score_change=score_change,
            is_safe=is_safe,
            original_parse=original_parse,
            new_parse=new_parse,
        )

        logger.info(
            f"[{self.app_type}] Score validation: "
            f"'{original_title[:30]}...' ({original_parse.custom_format_score}) -> "
            f"'{new_title[:30]}...' ({new_parse.custom_format_score}), "
            f"change={score_change:+d}, safe={is_safe}"
        )

        return comparison
