"""Configuration management for Groomarr."""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Required - qBittorrent Connection
    qbittorrent_url: str = Field(default="http://localhost:8080")
    qbittorrent_username: str = Field(default="admin")
    qbittorrent_password: str = Field(default="adminadmin")

    # API Server
    api_port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # Rename Mode
    # Options: torrent_only, torrent_and_folder, torrent_folder_files, folder_only, files_only
    rename_mode: str = Field(default="torrent_and_folder")

    # Timing
    initial_delay: float = Field(default=2.0)
    max_retries: int = Field(default=10)
    retry_delay: float = Field(default=3.0)

    # Config file path
    rules_file: str = Field(default="/config/rename_rules.yaml")

    class Config:
        env_prefix = ""
        case_sensitive = False


class RenameRules:
    """Rename rules and trigger filters loaded from YAML config."""

    def __init__(self):
        # Trigger filters
        self.indexers_include: List[str] = []
        self.indexers_exclude: List[str] = []
        self.qualities_include: List[str] = []
        self.qualities_exclude: List[str] = []
        self.customformats_require_any: List[str] = []
        self.customformats_exclude: List[str] = []
        self.min_customformat_score: Optional[int] = None
        self.download_clients_include: List[str] = []
        self.download_clients_exclude: List[str] = []
        self.release_groups_include: List[str] = []
        self.release_groups_exclude: List[str] = []

        # Rename rules
        self.prefix: str = ""
        self.suffix: str = ""
        self.remove_patterns: List[str] = []
        self.replace_patterns: Dict[str, str] = {}
        self.skip_title_patterns: List[str] = []

    @classmethod
    def from_yaml(cls, file_path: str) -> "RenameRules":
        """Load rules from YAML file."""
        rules = cls()

        path = Path(file_path)
        if not path.exists():
            logger.info(f"Rules file not found at {file_path}, using defaults")
            return rules

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # Load trigger filters
            rules.indexers_include = data.get("indexers_include") or []
            rules.indexers_exclude = data.get("indexers_exclude") or []
            rules.qualities_include = data.get("qualities_include") or []
            rules.qualities_exclude = data.get("qualities_exclude") or []
            rules.customformats_require_any = data.get("customformats_require_any") or []
            rules.customformats_exclude = data.get("customformats_exclude") or []
            rules.min_customformat_score = data.get("min_customformat_score")
            rules.download_clients_include = data.get("download_clients_include") or []
            rules.download_clients_exclude = data.get("download_clients_exclude") or []
            rules.release_groups_include = data.get("release_groups_include") or []
            rules.release_groups_exclude = data.get("release_groups_exclude") or []

            # Load rename rules
            rules.prefix = data.get("prefix") or ""
            rules.suffix = data.get("suffix") or ""
            rules.remove_patterns = data.get("remove_patterns") or []
            rules.replace_patterns = data.get("replace_patterns") or {}
            rules.skip_title_patterns = data.get("skip_title_patterns") or []

            logger.info(f"Loaded rules from {file_path}")

        except Exception as e:
            logger.error(f"Error loading rules file: {e}")

        return rules


# Global instances
settings = Settings()
rules = RenameRules.from_yaml(settings.rules_file)


def reload_rules():
    """Reload rules from file (useful for runtime updates)."""
    global rules
    rules = RenameRules.from_yaml(settings.rules_file)
    logger.info("Rules reloaded")


def setup_logging():
    """Configure logging based on settings."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
