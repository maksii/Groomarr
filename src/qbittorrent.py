"""qBittorrent API client wrapper."""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from qbittorrentapi import Client
from qbittorrentapi.exceptions import APIConnectionError, LoginFailed

logger = logging.getLogger(__name__)


class QBitClient:
    """Wrapper for qBittorrent API client with retry logic."""

    def __init__(self, url: str, username: str, password: str):
        self.url = url
        self.username = username
        self.password = password
        self._client: Optional[Client] = None

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

    async def wait_for_torrent(
        self,
        torrent_hash: str,
        initial_delay: float,
        max_retries: int,
        retry_delay: float,
    ) -> Optional[Dict[str, Any]]:
        """Poll for torrent with retry logic.

        Args:
            torrent_hash: The torrent info hash to look for
            initial_delay: Seconds to wait before first attempt
            max_retries: Maximum number of retry attempts
            retry_delay: Seconds between retries

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
                        f"Found torrent {hash_short}... "
                        f"name='{torrent.name}' state={torrent.state}"
                    )
                    return torrent

                logger.debug(
                    f"Torrent {hash_short}... not found, "
                    f"attempt {attempt + 1}/{max_retries}"
                )

            except Exception as e:
                logger.warning(
                    f"Error checking for torrent {hash_short}...: {e}, "
                    f"attempt {attempt + 1}/{max_retries}"
                )

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)

        logger.warning(
            f"Torrent {hash_short}... not found after {max_retries} attempts"
        )
        return None

    def get_torrent_info(self, torrent_hash: str) -> Optional[Dict[str, Any]]:
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

    def get_files(self, torrent_hash: str) -> List[Dict[str, Any]]:
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

    def rename_torrent(self, torrent_hash: str, new_name: str) -> bool:
        """Rename torrent display name.

        Args:
            torrent_hash: The torrent info hash
            new_name: New name for the torrent

        Returns:
            True if successful, False otherwise
        """
        try:
            client = self._ensure_connected()
            client.torrents_rename(torrent_hash=torrent_hash, new_torrent_name=new_name)
            logger.info(f"Renamed torrent {torrent_hash[:8]}... to '{new_name}'")
            return True
        except Exception as e:
            logger.error(f"Error renaming torrent: {e}")
            return False

    def rename_folder(self, torrent_hash: str, old_path: str, new_path: str) -> bool:
        """Rename a folder within a torrent.

        Args:
            torrent_hash: The torrent info hash
            old_path: Current folder path
            new_path: New folder path

        Returns:
            True if successful, False otherwise
        """
        try:
            client = self._ensure_connected()
            client.torrents_rename_folder(
                torrent_hash=torrent_hash, old_path=old_path, new_path=new_path
            )
            logger.info(
                f"Renamed folder in {torrent_hash[:8]}...: '{old_path}' -> '{new_path}'"
            )
            return True
        except Exception as e:
            logger.error(f"Error renaming folder: {e}")
            return False

    def rename_file(self, torrent_hash: str, old_path: str, new_path: str) -> bool:
        """Rename a file within a torrent.

        Args:
            torrent_hash: The torrent info hash
            old_path: Current file path
            new_path: New file path

        Returns:
            True if successful, False otherwise
        """
        try:
            client = self._ensure_connected()
            client.torrents_rename_file(
                torrent_hash=torrent_hash, old_path=old_path, new_path=new_path
            )
            logger.info(
                f"Renamed file in {torrent_hash[:8]}...: '{old_path}' -> '{new_path}'"
            )
            return True
        except Exception as e:
            logger.error(f"Error renaming file: {e}")
            return False
