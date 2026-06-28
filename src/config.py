"""Configuration management for Groomarr."""

import contextlib
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import yaml
from pydantic import ConfigDict, Field
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

    # Web UI
    # Directory containing the built single-page app (index.html + assets).
    # In Docker this is populated by the frontend build stage.
    static_dir: str = Field(default="frontend/dist")
    # When true, the web UI/API cannot modify rename_rules.yaml (read-only mode).
    # Useful when the config is managed externally (e.g. mounted read-only).
    config_readonly: bool = Field(default=False)

    model_config = ConfigDict(env_prefix="", case_sensitive=False)


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
        """Create TrackerRules from a dictionary, coercing malformed types.

        Hand-edited YAML can contain the wrong type for a field (e.g. a list where
        a mapping is expected, or a number where a string is expected). To keep
        loading robust — so the web UI can still display and fix a broken file
        instead of erroring — unexpected types are coerced to safe values rather
        than propagated to the engine.

        Args:
            data: Dictionary with rule settings

        Returns:
            TrackerRules instance
        """
        rules = cls()

        if not isinstance(data, dict):
            return rules

        def _str_list(key: str) -> list[str]:
            value = data.get(key)
            return [str(item) for item in value] if isinstance(value, list) else []

        # Load trigger filters
        rules.indexers_include = _str_list("indexers_include")
        rules.indexers_exclude = _str_list("indexers_exclude")
        rules.qualities_include = _str_list("qualities_include")
        rules.qualities_exclude = _str_list("qualities_exclude")
        rules.customformats_require_any = _str_list("customformats_require_any")
        rules.customformats_exclude = _str_list("customformats_exclude")
        rules.download_clients_include = _str_list("download_clients_include")
        rules.download_clients_exclude = _str_list("download_clients_exclude")
        rules.release_groups_include = _str_list("release_groups_include")
        rules.release_groups_exclude = _str_list("release_groups_exclude")

        score = data.get("min_customformat_score")
        # bool is a subclass of int — exclude it explicitly
        rules.min_customformat_score = (
            score if isinstance(score, int) and not isinstance(score, bool) else None
        )

        # Load rename rules
        prefix = data.get("prefix")
        rules.prefix = prefix if isinstance(prefix, str) else ""
        suffix = data.get("suffix")
        rules.suffix = suffix if isinstance(suffix, str) else ""
        rules.remove_patterns = _str_list("remove_patterns")
        replace = data.get("replace_patterns")
        rules.replace_patterns = (
            {str(k): str(v) for k, v in replace.items()} if isinstance(replace, dict) else {}
        )
        rules.skip_title_patterns = _str_list("skip_title_patterns")

        # Load score validation settings
        rules.validate_custom_format_score = bool(data.get("validate_custom_format_score", False))
        policy = data.get("score_validation_policy")
        rules.score_validation_policy = policy if isinstance(policy, str) and policy else "block"

        return rules

    # List-valued fields, in a stable order for serialization
    _LIST_FIELDS = (
        "indexers_include",
        "indexers_exclude",
        "qualities_include",
        "qualities_exclude",
        "customformats_require_any",
        "customformats_exclude",
        "download_clients_include",
        "download_clients_exclude",
        "release_groups_include",
        "release_groups_exclude",
        "remove_patterns",
        "skip_title_patterns",
    )

    def to_dict(self) -> dict:
        """Serialize to a minimal dict for YAML output.

        Omits empty/default values so the saved file stays clean and readable.
        The inverse of :meth:`from_dict`.
        """
        data: dict = {}

        for name in self._LIST_FIELDS:
            value = getattr(self, name)
            if value:
                data[name] = list(value)

        if self.min_customformat_score is not None:
            data["min_customformat_score"] = self.min_customformat_score

        if self.prefix:
            data["prefix"] = self.prefix
        if self.suffix:
            data["suffix"] = self.suffix
        if self.replace_patterns:
            data["replace_patterns"] = dict(self.replace_patterns)

        if self.validate_custom_format_score:
            data["validate_custom_format_score"] = True
        if self.score_validation_policy and self.score_validation_policy != "block":
            data["score_validation_policy"] = self.score_validation_policy

        return data

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

    def to_dict(self) -> dict:
        """Serialize this tracker config to a dict for YAML output."""
        return {
            "name": self.name,
            "match": list(self.match),
            "rules": self.rules.to_dict(),
        }


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
        self.config_format: str = "empty"  # hierarchical | flat | empty | missing

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
            config.config_format = "missing"
            return config

        config.config_found = True

        try:
            with open(path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            config._load_from_data(data)
        except Exception as e:
            config.config_error = str(e)

        return config

    @classmethod
    def from_dict(cls, data: dict) -> "RenameRules":
        """Build rules from an already-parsed config dict (no file access).

        Supports both the legacy flat format and the hierarchical
        ``global`` + ``trackers`` format. Used to validate or simulate a draft
        configuration posted from the web UI without touching disk.

        Args:
            data: Parsed configuration dictionary

        Returns:
            RenameRules instance
        """
        config = cls()
        config.config_found = True
        config._load_from_data(data or {})
        return config

    def _load_from_data(self, data: dict) -> None:
        """Populate global_rules and trackers from a parsed config dict."""
        # Check if new hierarchical format (has 'global' or 'trackers' key)
        if "global" in data or "trackers" in data:
            # New hierarchical format
            self.config_format = "hierarchical"
            self._load_hierarchical_format(data)
        elif data:
            # Legacy flat format - treat all as global rules
            self.config_format = "flat"
            self.global_rules = TrackerRules.from_dict(data)
            logger.debug("Loaded config in legacy flat format")
        else:
            self.config_format = "empty"

    def to_dict(self) -> dict:
        """Serialize to a hierarchical dict ({'global': ..., 'trackers': [...]}).

        Always emits the recommended hierarchical format. ``trackers`` is only
        included when at least one tracker is configured. The inverse of
        :meth:`from_dict`.
        """
        data: dict = {"global": self.global_rules.to_dict()}
        if self.trackers:
            data["trackers"] = [t.to_dict() for t in self.trackers]
        return data

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


RULES_FILE_HEADER = (
    "# Groomarr rename rules\n"
    "# This file is managed by the Groomarr web UI.\n"
    "# You can still edit it by hand, but comments are NOT preserved when the\n"
    "# file is next saved from the UI. A backup of the previous version is kept\n"
    "# alongside this file with a .bak extension.\n"
)


def save_rules(data: dict, file_path: str | None = None) -> Path:
    """Atomically write rename rules to YAML, keeping a backup of the previous file.

    The write is crash-safe: the new content is written to a temporary file in the
    same directory and then atomically moved into place via ``os.replace``. The
    previous file (if any) is copied to ``<path>.bak`` first.

    Args:
        data: Hierarchical rules dict, typically from :meth:`RenameRules.to_dict`.
        file_path: Target path (defaults to ``settings.rules_file``).

    Returns:
        The path that was written.

    Raises:
        OSError: If the file could not be written.
    """
    path = Path(file_path or settings.rules_file)
    path.parent.mkdir(parents=True, exist_ok=True)

    body = yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    content = f"{RULES_FILE_HEADER}\n{body}"

    # Backup the existing file before overwriting
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        try:
            shutil.copy2(path, backup)
        except OSError as e:
            logger.warning(f"Could not write backup {backup}: {e}")

    # Atomic write: temp file in the same directory, then replace
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise

    logger.info(f"Saved rename rules to {path}")
    return path


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
