"""qBittorrent API client wrapper."""

import asyncio
import logging
import re
import time
from typing import Any

from qbittorrentapi import Client
from qbittorrentapi.exceptions import APIConnectionError, LoginFailed

logger = logging.getLogger(__name__)

# Default delay between API operations in seconds
DEFAULT_API_DELAY = 0.1  # 100ms


class QBitClient:
    """Wrapper for qBittorrent API client with retry logic."""

    def __init__(
        self, url: str, username: str, password: str, api_delay: float = DEFAULT_API_DELAY
    ):
        """Initialize qBittorrent client.

        Args:
            url: qBittorrent Web UI URL
            username: qBittorrent username
            password: qBittorrent password
            api_delay: Delay between API operations in seconds (default 0.1s/100ms)
        """
        self.url = url
        self.username = username
        self.password = password
        self.api_delay = api_delay
        self._client: Client | None = None
        self._last_operation_time: float = 0

    def _get_client(self) -> Client:
        """Get or create qBittorrent client with lazy connection."""
        if self._client is None:
            self._client = Client(
                host=self.url,
                username=self.username,
                password=self.password,
            )
            logger.info(f"Connected to qBittorrent at {self.url}")
        return self._client

    def _ensure_connected(self) -> Client:
        """Ensure client is connected, reconnect if needed."""
        client = self._get_client()
        try:
            # Try a simple API call to verify connection
            client.app_version()
            return client
        except (APIConnectionError, LoginFailed) as e:
            logger.warning(f"Connection lost, reconnecting: {e}")
            self._client = None
            return self._get_client()

    def check_connection(self) -> bool:
        """Check if qBittorrent is reachable.

        Returns:
            True if connected, False otherwise
        """
        try:
            client = self._get_client()
            client.app_version()
            return True
        except Exception:
            return False

    async def _apply_rate_limit(self) -> None:
        """Apply rate limiting between API operations.

        Ensures minimum delay between consecutive API calls to prevent
        silent failures when qBittorrent can't keep up with rapid requests.

        Uses asyncio.sleep() to avoid blocking the event loop.
        """
        if self.api_delay <= 0:
            return

        elapsed = time.time() - self._last_operation_time
        if elapsed < self.api_delay:
            await asyncio.sleep(self.api_delay - elapsed)
        self._last_operation_time = time.time()

    async def wait_for_torrent(
        self,
        torrent_hash: str,
        initial_delay: float,
        max_retries: int,
        retry_delay: float,
        use_exponential_backoff: bool = True,
        max_delay: float = 60.0,
    ) -> dict[str, Any] | None:
        """Poll for torrent with retry logic and exponential backoff.

        Args:
            torrent_hash: The torrent info hash to look for
            initial_delay: Seconds to wait before first attempt
            max_retries: Maximum number of retry attempts
            retry_delay: Base seconds between retries
            use_exponential_backoff: If True, delay doubles each attempt
            max_delay: Maximum delay between retries (cap for exponential backoff)

        Returns:
            Torrent info dict if found, None otherwise
        """
        # Initial delay to give qBittorrent time to add the torrent
        await asyncio.sleep(initial_delay)

        hash_short = torrent_hash[:8]

        for attempt in range(max_retries):
            try:
                client = self._ensure_connected()
                torrents = client.torrents_info(torrent_hashes=torrent_hash)

                if torrents:
                    torrent = torrents[0]
                    logger.info(
                        f"Found torrent {hash_short}... name='{torrent.name}' state={torrent.state}"
                    )
                    return torrent

                logger.debug(
                    f"Torrent {hash_short}... not found, attempt {attempt + 1}/{max_retries}"
                )

            except Exception as e:
                logger.warning(
                    f"Error checking for torrent {hash_short}...: {e}, "
                    f"attempt {attempt + 1}/{max_retries}"
                )

            if attempt < max_retries - 1:
                # Calculate delay with optional exponential backoff
                if use_exponential_backoff:
                    delay = min(retry_delay * (2**attempt), max_delay)
                else:
                    delay = retry_delay
                await asyncio.sleep(delay)

        logger.warning(f"Torrent {hash_short}... not found after {max_retries} attempts")
        return None

    def get_torrent_info(self, torrent_hash: str) -> dict[str, Any] | None:
        """Get torrent information by hash.

        Args:
            torrent_hash: The torrent info hash

        Returns:
            Torrent info dict if found, None otherwise
        """
        try:
            client = self._ensure_connected()
            torrents = client.torrents_info(torrent_hashes=torrent_hash)
            return torrents[0] if torrents else None
        except Exception as e:
            logger.error(f"Error getting torrent info: {e}")
            return None

    def get_files(self, torrent_hash: str) -> list[dict[str, Any]]:
        """Get list of files in a torrent.

        Args:
            torrent_hash: The torrent info hash

        Returns:
            List of file info dicts
        """
        try:
            client = self._ensure_connected()
            return list(client.torrents_files(torrent_hash=torrent_hash))
        except Exception as e:
            logger.error(f"Error getting torrent files: {e}")
            return []

    async def rename_torrent(self, torrent_hash: str, new_name: str) -> bool:
        """Rename torrent display name.

        Args:
            torrent_hash: The torrent info hash
            new_name: New name for the torrent

        Returns:
            True if API call succeeded, False otherwise
        """
        try:
            await self._apply_rate_limit()
            client = self._ensure_connected()
            client.torrents_rename(torrent_hash=torrent_hash, new_torrent_name=new_name)
            logger.info(f"Renamed torrent {torrent_hash[:8]}... to '{new_name}'")
            return True
        except Exception as e:
            logger.error(f"Error renaming torrent: {e}")
            return False

    async def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> bool:
        """Rename a folder within a torrent.

        Args:
            torrent_hash: The torrent info hash
            old_path: Current folder path
            new_path: New folder path

        Returns:
            True if API call succeeded, False otherwise
        """
        try:
            await self._apply_rate_limit()
            client = self._ensure_connected()
            client.torrents_rename_folder(
                torrent_hash=torrent_hash, old_path=old_path, new_path=new_path
            )
            logger.info(f"Renamed folder in {torrent_hash[:8]}...: '{old_path}' -> '{new_path}'")
            return True
        except Exception as e:
            logger.error(f"Error renaming folder: {e}")
            return False

    async def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> bool:
        """Rename a file within a torrent.

        Args:
            torrent_hash: The torrent info hash
            old_path: Current file path
            new_path: New file path

        Returns:
            True if API call succeeded, False otherwise
        """
        try:
            await self._apply_rate_limit()
            client = self._ensure_connected()
            client.torrents_rename_file(
                torrent_hash=torrent_hash, old_path=old_path, new_path=new_path
            )
            logger.info(f"Renamed file in {torrent_hash[:8]}...: '{old_path}' -> '{new_path}'")
            return True
        except Exception as e:
            logger.error(f"Error renaming file: {e}")
            return False

    def get_all_torrents(self) -> list[dict[str, Any]]:
        """Get all torrents from qBittorrent.

        Returns:
            List of torrent info dicts
        """
        try:
            client = self._ensure_connected()
            return list(client.torrents_info())
        except Exception as e:
            logger.error(f"Error getting all torrents: {e}")
            return []

    def find_torrent_by_comment_id(self, torrent_id: str) -> dict[str, Any] | None:
        """Find a torrent by matching ID in its comment property.

        Searches through all torrents and checks if the comment contains
        the specified torrent ID.

        Args:
            torrent_id: The torrent ID to search for

        Returns:
            Torrent info dict if found, None otherwise
        """
        try:
            torrents = self.get_all_torrents()
            for torrent in torrents:
                comment = getattr(torrent, "comment", "") or ""
                # Check if comment contains the torrent ID
                # Pattern: "https://domain/torrents/342558" or just "342558"
                if torrent_id in comment:
                    # Verify it's a proper match (not just a substring)
                    # Look for the ID as a standalone number or in the URL pattern
                    # Match ID as standalone number or in URL pattern
                    pattern = rf"(?:/torrents/|^|\s)({re.escape(torrent_id)})(?:/|$|\s)"
                    if re.search(pattern, comment):
                        logger.info(
                            f"Found torrent with ID {torrent_id} in comment: "
                            f"hash={torrent.hash[:8]}... name='{torrent.name}'"
                        )
                        return torrent
            return None
        except Exception as e:
            logger.error(f"Error finding torrent by comment ID: {e}")
            return None
