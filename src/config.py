"""Configuration management for Groomarr."""

import logging
import re
from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def matches_indexer(indexer: str, patterns: list[str]) -> bool:
    """Check if indexer matches any pattern.

    Pattern types:
    - Plain string: exact match (case-insensitive)
    - Contains * or ?: wildcard/glob match
    - Wrapped in /.../ : regex match

    Args:
        indexer: Indexer name to check
        patterns: List of patterns to match against

    Returns:
        True if indexer matches any pattern
    """
    for pattern in patterns:
        # Regex pattern: /pattern/
        if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
            regex = pattern[1:-1]
            try:
                if re.search(regex, indexer, re.IGNORECASE):
                    return True
            except re.error as e:
                logger.warning(f"Invalid regex pattern '{pattern}': {e}")
                continue

        # Wildcard pattern: contains * or ?
        elif "*" in pattern or "?" in pattern:
            # Convert glob to regex: * -> .*, ? -> .
            regex = re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
            try:
                if re.fullmatch(regex, indexer, re.IGNORECASE):
                    return True
            except re.error as e:
                logger.warning(f"Invalid wildcard pattern '{pattern}': {e}")
                continue

        # Exact match (case-insensitive)
        else:
            if pattern.lower() == indexer.lower():
                return True

    return False


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

    # API operation delay (milliseconds between qBittorrent API calls)
    # Helps prevent silent failures when renaming many files
    api_operation_delay_ms: int = Field(default=100)

    # Config file path
    rules_file: str = Field(default="/config/rename_rules.yaml")

    class Config:
        env_prefix = ""
        case_sensitive = False


class TrackerRules:
    """Rules for a single tracker (or global fallback).

    Contains both trigger filters and rename rules that apply
    to a specific tracker or serve as global defaults.
    """

    def __init__(self):
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
    def from_dict(cls, data: dict) -> "TrackerRules":
        """Create TrackerRules from a dictionary.

        Args:
            data: Dictionary with rule settings

        Returns:
            TrackerRules instance
        """
        rules = cls()

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
        rules.validate_custom_format_score = bool(data.get("validate_custom_format_score", False))
        rules.score_validation_policy = data.get("score_validation_policy") or "block"

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


class TrackerConfig:
    """Configuration for a specific tracker with match patterns and rules."""

    def __init__(self, name: str, match: list[str], rules: TrackerRules):
        self.name = name
        self.match = match
        self.rules = rules

    @classmethod
    def from_dict(cls, data: dict) -> "TrackerConfig":
        """Create TrackerConfig from a dictionary.

        Args:
            data: Dictionary with name, match, and rules

        Returns:
            TrackerConfig instance
        """
        name = data.get("name", "unnamed")
        match = data.get("match") or []
        rules_data = data.get("rules") or {}
        rules = TrackerRules.from_dict(rules_data)
        return cls(name=name, match=match, rules=rules)


class RenameRules:
    """Hierarchical rename rules with global defaults and tracker-specific overrides.

    Supports two YAML formats:
    1. Legacy flat format: All rules at root level (treated as global)
    2. New hierarchical format: 'global' section + 'trackers' list

    When a webhook is received:
    1. Check if indexer matches any tracker-specific config (first match wins)
    2. If matched, use that tracker's rules exclusively (global not applied)
    3. If no match, use global rules
    """

    def __init__(self):
        # Config file state
        self.config_path: str = ""
        self.config_found: bool = False
        self.config_error: str | None = None

        # Global rules (fallback when no tracker matches)
        self.global_rules: TrackerRules = TrackerRules()

        # Tracker-specific configurations (checked in order, first match wins)
        self.trackers: list[TrackerConfig] = []

    @classmethod
    def from_yaml(cls, file_path: str) -> "RenameRules":
        """Load rules from YAML file.

        Supports both legacy flat format and new hierarchical format.

        Args:
            file_path: Path to YAML configuration file

        Returns:
            RenameRules instance
        """
        config = cls()
        config.config_path = file_path

        path = Path(file_path)
        if not path.exists():
            config.config_found = False
            return config

        config.config_found = True

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            # Check if new hierarchical format (has 'global' or 'trackers' key)
            if "global" in data or "trackers" in data:
                # New hierarchical format
                config._load_hierarchical_format(data)
            else:
                # Legacy flat format - treat all as global rules
                config.global_rules = TrackerRules.from_dict(data)
                logger.debug("Loaded config in legacy flat format")

        except Exception as e:
            config.config_error = str(e)

        return config

    def _load_hierarchical_format(self, data: dict):
        """Load configuration in hierarchical format.

        Args:
            data: Parsed YAML data
        """
        # Load global rules
        global_data = data.get("global") or {}
        self.global_rules = TrackerRules.from_dict(global_data)

        # Load tracker-specific configurations
        trackers_data = data.get("trackers") or []
        for tracker_data in trackers_data:
            if isinstance(tracker_data, dict):
                tracker_config = TrackerConfig.from_dict(tracker_data)
                if tracker_config.match:  # Only add if has match patterns
                    self.trackers.append(tracker_config)
                else:
                    logger.warning(
                        f"Tracker '{tracker_config.name}' has no match patterns, skipping"
                    )

        if self.trackers:
            logger.debug(
                f"Loaded config with {len(self.trackers)} tracker-specific configs: "
                f"{', '.join(t.name for t in self.trackers)}"
            )
        else:
            logger.debug("Loaded config in hierarchical format (global only, no trackers)")

    def get_rules_for_indexer(self, indexer: str) -> tuple[TrackerRules, str | None]:
        """Get the appropriate rules for an indexer.

        Checks tracker-specific configs first (in order), falls back to global.

        Args:
            indexer: Indexer name from webhook

        Returns:
            Tuple of (TrackerRules to use, tracker name if matched or None for global)
        """
        # Check each tracker config in order
        for tracker_config in self.trackers:
            if matches_indexer(indexer, tracker_config.match):
                logger.debug(f"Indexer '{indexer}' matched tracker '{tracker_config.name}'")
                return tracker_config.rules, tracker_config.name

        # No match - use global rules
        logger.debug(f"Indexer '{indexer}' using global rules")
        return self.global_rules, None

    # Convenience properties for backward compatibility
    # These delegate to global_rules for code that accesses rules directly

    @property
    def validate_custom_format_score(self) -> bool:
        """Get global score validation setting."""
        return self.global_rules.validate_custom_format_score

    @property
    def score_validation_policy(self) -> str:
        """Get global score validation policy."""
        return self.global_rules.score_validation_policy

    def has_trigger_filters(self) -> bool:
        """Check if any trigger filters are configured (global or tracker-specific)."""
        if self.global_rules.has_trigger_filters():
            return True
        return any(t.rules.has_trigger_filters() for t in self.trackers)

    def has_rename_rules(self) -> bool:
        """Check if any rename rules are configured (global or tracker-specific)."""
        if self.global_rules.has_rename_rules():
            return True
        return any(t.rules.has_rename_rules() for t in self.trackers)

    def get_active_filters_summary(self) -> list[str]:
        """Get summary of active filters (global and tracker-specific)."""
        summary = []

        # Global filters
        global_filters = self.global_rules.get_active_filters_summary()
        if global_filters:
            summary.extend([f"global: {f}" for f in global_filters])

        # Tracker-specific info
        if self.trackers:
            summary.append(f"trackers: {len(self.trackers)} configured")

        return summary

    def get_active_rules_summary(self) -> list[str]:
        """Get summary of active rename rules (global and tracker-specific)."""
        summary = []

        # Global rules
        global_rules = self.global_rules.get_active_rules_summary()
        if global_rules:
            summary.extend([f"global: {r}" for r in global_rules])

        # Tracker-specific info
        for tracker in self.trackers:
            tracker_rules = tracker.rules.get_active_rules_summary()
            if tracker_rules:
                summary.append(f"{tracker.name}: {', '.join(tracker_rules)}")

        return summary


# Global instances
settings = Settings()
rules = RenameRules.from_yaml(settings.rules_file)


def reload_rules():
    """Reload rules from file (useful for runtime updates)."""
    global rules
    rules = RenameRules.from_yaml(settings.rules_file)
    logger.info("Rules reloaded")


class HealthCheckFilter(logging.Filter):
    """Filter to suppress successful health check logs.

    Filters out:
    - httpx INFO logs for /api/v3/system/status (Arr API health checks)
    - uvicorn access logs for successful GET /health requests
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False to suppress the log record, True to keep it."""
        msg = record.getMessage()

        # Filter httpx logs for Arr API health checks (system/status endpoint)
        if record.name == "httpx" and "/api/v3/system/status" in msg and "200 OK" in msg:
            return False

        # Filter uvicorn access logs for successful /health requests
        # Return True (keep) unless it's a successful health check
        return not (record.name == "uvicorn.access" and "GET /health" in msg and " 200 " in msg)


def setup_logging():
    """Configure logging based on settings.

    Supports two formats:
    - "text": Human-readable format (default)
    - "json": JSON format for log aggregation systems

    Also applies filters to suppress successful health check logs.
    """
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    # Create health check filter
    health_filter = HealthCheckFilter()

    if settings.log_format.lower() == "json":
        # JSON logging for production/log aggregation
        try:
            from pythonjsonlogger import jsonlogger

            handler = logging.StreamHandler()
            handler.addFilter(health_filter)
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
            # Still apply filter to root logger
            for handler in logging.getLogger().handlers:
                handler.addFilter(health_filter)
    else:
        # Human-readable text format (default)
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        # Apply filter to all handlers
        for handler in logging.getLogger().handlers:
            handler.addFilter(health_filter)

    # Apply filter to uvicorn.access logger specifically
    # Uvicorn sets up its own handlers, so we need to filter at the logger level
    uvicorn_access_logger = logging.getLogger("uvicorn.access")
    uvicorn_access_logger.addFilter(health_filter)
