"""Configuration management for Groomarr."""

import logging
from pathlib import Path

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
    log_format: str = Field(default="text")  # "text" or "json"

    # Rename Mode
    # Options: torrent_only, torrent_and_folder, torrent_folder_files, folder_only, files_only
    rename_mode: str = Field(default="torrent_and_folder")

    # Dry Run Mode - logs what would be renamed without making changes
    dry_run: bool = Field(default=False)

    # Sonarr API (optional - for score validation)
    sonarr_url: str | None = Field(default=None)
    sonarr_api_key: str | None = Field(default=None)

    # Radarr API (optional - for score validation)
    radarr_url: str | None = Field(default=None)
    radarr_api_key: str | None = Field(default=None)

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
        # Config file state
        self.config_path: str = ""
        self.config_found: bool = False
        self.config_error: str | None = None

        # Trigger filters
        self.indexers_include: list[str] = []
        self.indexers_exclude: list[str] = []
        self.qualities_include: list[str] = []
        self.qualities_exclude: list[str] = []
        self.customformats_require_any: list[str] = []
        self.customformats_exclude: list[str] = []
        self.min_customformat_score: int | None = None
        self.download_clients_include: list[str] = []
        self.download_clients_exclude: list[str] = []
        self.release_groups_include: list[str] = []
        self.release_groups_exclude: list[str] = []

        # Rename rules
        self.prefix: str = ""
        self.suffix: str = ""
        self.remove_patterns: list[str] = []
        self.replace_patterns: dict[str, str] = {}
        self.skip_title_patterns: list[str] = []

        # Score validation settings
        self.validate_custom_format_score: bool = False
        self.score_validation_policy: str = "block"  # "block" or "warn"

    @classmethod
    def from_yaml(cls, file_path: str) -> "RenameRules":
        """Load rules from YAML file."""
        rules = cls()
        rules.config_path = file_path

        path = Path(file_path)
        if not path.exists():
            rules.config_found = False
            return rules

        rules.config_found = True

        try:
            with open(path, encoding="utf-8") as f:
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

            # Load score validation settings
            rules.validate_custom_format_score = bool(
                data.get("validate_custom_format_score", False)
            )
            rules.score_validation_policy = data.get("score_validation_policy") or "block"

        except Exception as e:
            rules.config_error = str(e)

        return rules

    def has_trigger_filters(self) -> bool:
        """Check if any trigger filters are configured."""
        return bool(
            self.indexers_include
            or self.indexers_exclude
            or self.qualities_include
            or self.qualities_exclude
            or self.customformats_require_any
            or self.customformats_exclude
            or self.min_customformat_score is not None
            or self.download_clients_include
            or self.download_clients_exclude
            or self.release_groups_include
            or self.release_groups_exclude
        )

    def has_rename_rules(self) -> bool:
        """Check if any rename rules are configured."""
        return bool(
            self.prefix
            or self.suffix
            or self.remove_patterns
            or self.replace_patterns
            or self.skip_title_patterns
        )

    def get_active_filters_summary(self) -> list[str]:
        """Get list of active trigger filter names."""
        active = []
        if self.indexers_include:
            active.append(f"indexers_include ({len(self.indexers_include)})")
        if self.indexers_exclude:
            active.append(f"indexers_exclude ({len(self.indexers_exclude)})")
        if self.qualities_include:
            active.append(f"qualities_include ({len(self.qualities_include)})")
        if self.qualities_exclude:
            active.append(f"qualities_exclude ({len(self.qualities_exclude)})")
        if self.customformats_require_any:
            active.append(f"customformats_require_any ({len(self.customformats_require_any)})")
        if self.customformats_exclude:
            active.append(f"customformats_exclude ({len(self.customformats_exclude)})")
        if self.min_customformat_score is not None:
            active.append(f"min_customformat_score ({self.min_customformat_score})")
        if self.download_clients_include:
            active.append(f"download_clients_include ({len(self.download_clients_include)})")
        if self.download_clients_exclude:
            active.append(f"download_clients_exclude ({len(self.download_clients_exclude)})")
        if self.release_groups_include:
            active.append(f"release_groups_include ({len(self.release_groups_include)})")
        if self.release_groups_exclude:
            active.append(f"release_groups_exclude ({len(self.release_groups_exclude)})")
        return active

    def get_active_rules_summary(self) -> list[str]:
        """Get list of active rename rule names."""
        active = []
        if self.prefix:
            active.append(f"prefix: '{self.prefix}'")
        if self.suffix:
            active.append(f"suffix: '{self.suffix}'")
        if self.remove_patterns:
            active.append(f"remove_patterns ({len(self.remove_patterns)})")
        if self.replace_patterns:
            active.append(f"replace_patterns ({len(self.replace_patterns)})")
        if self.skip_title_patterns:
            active.append(f"skip_title_patterns ({len(self.skip_title_patterns)})")
        return active


# Global instances
settings = Settings()
rules = RenameRules.from_yaml(settings.rules_file)


def reload_rules():
    """Reload rules from file (useful for runtime updates)."""
    global rules
    rules = RenameRules.from_yaml(settings.rules_file)
    logger.info("Rules reloaded")


def setup_logging():
    """Configure logging based on settings.

    Supports two formats:
    - "text": Human-readable format (default)
    - "json": JSON format for log aggregation systems
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    if settings.log_format.lower() == "json":
        # JSON logging for production/log aggregation
        try:
            from pythonjsonlogger import jsonlogger

            handler = logging.StreamHandler()
            formatter = jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
            handler.setFormatter(formatter)

            root_logger = logging.getLogger()
            root_logger.setLevel(log_level)
            root_logger.addHandler(handler)
        except ImportError:
            # Fallback to text if python-json-logger not installed
            logging.basicConfig(
                level=log_level,
                format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            logging.warning("python-json-logger not installed, using text format")
    else:
        # Human-readable text format (default)
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
