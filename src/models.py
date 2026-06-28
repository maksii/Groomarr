"""Pydantic models for Sonarr and Radarr webhook payloads."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# =============================================================================
# Radarr Models
# =============================================================================


class MovieInfo(BaseModel):
    """Movie information from Radarr webhook."""

    id: int
    title: str
    year: int | None = None
    tmdbId: int | None = None
    imdbId: str | None = None


class RadarrRelease(BaseModel):
    """Release information from Radarr webhook."""

    releaseTitle: str
    quality: str
    releaseGroup: str | None = None
    size: int | None = None
    indexer: str | None = None
    customFormats: list[str] | None = None
    customFormatScore: int | None = None


class RadarrWebhook(BaseModel):
    """Radarr webhook payload for Grab event."""

    eventType: str
    movie: MovieInfo
    release: RadarrRelease
    downloadId: str  # Top-level - this is the torrent hash
    downloadClient: str
    downloadClientType: str
    instanceName: str | None = None
    applicationUrl: str | None = None


# =============================================================================
# Sonarr Models
# =============================================================================


class SeriesInfo(BaseModel):
    """Series information from Sonarr webhook."""

    id: int
    title: str
    path: str | None = None
    tvdbId: int | None = None
    tvMazeId: int | None = None
    imdbId: str | None = None
    type: str | None = None


class EpisodeInfo(BaseModel):
    """Episode information from Sonarr webhook."""

    id: int
    episodeNumber: int
    seasonNumber: int
    title: str | None = None
    airDate: str | None = None
    airDateUtc: str | None = None


class SonarrRelease(BaseModel):
    """Release information from Sonarr webhook.

    Note: Sonarr uses 'title' instead of 'releaseTitle',
    and downloadId is inside release, not top-level.
    """

    title: str = Field(alias="releaseTitle", default="")  # Can be either
    quality: str
    releaseGroup: str | None = None
    size: int | None = None
    indexer: str | None = None
    downloadId: str | None = None  # In Sonarr, downloadId is here
    customFormats: list[str] | None = None
    customFormatScore: int | None = None

    model_config = ConfigDict(populate_by_name=True)


class SonarrWebhook(BaseModel):
    """Sonarr webhook payload for Grab event."""

    eventType: str
    series: SeriesInfo
    episodes: list[EpisodeInfo] = []
    release: SonarrRelease
    downloadClient: str | None = None
    downloadClientType: str | None = None
    downloadId: str | None = None  # Sonarr v4+ puts downloadId at top level
    instanceName: str | None = None
    applicationUrl: str | None = None

    def get_download_id(self) -> str | None:
        """Get download ID from payload.

        Sonarr v4+ puts downloadId at the top level (like Radarr).
        Older versions may have it inside release object.
        Check both locations for compatibility.
        """
        return self.downloadId or self.release.downloadId

    def get_release_title(self) -> str:
        """Get release title."""
        return self.release.title


# =============================================================================
# Common Response Models
# =============================================================================


class WebhookResponse(BaseModel):
    """Response model for webhook endpoints."""

    status: str
    reason: str | None = None
    torrent_hash: str | None = None


# =============================================================================
# Manual Rename Models
# =============================================================================


class ManualRenameRequest(BaseModel):
    """Request model for manual rename endpoint."""

    torrent_hash: str = Field(..., description="Torrent info hash to rename")
    new_name: str = Field(..., description="New name to apply to the torrent")
    mode: str = Field(
        default="torrent_and_folder",
        description="Rename mode: torrent_only, torrent_and_folder, torrent_folder_files, folder_only, files_only",
    )


class ManualRenameResponse(BaseModel):
    """Response model for manual rename endpoint."""

    status: str
    torrent_hash: str
    new_name: str | None = None
    mode: str | None = None
    reason: str | None = None


# =============================================================================
# Preview Rename Models
# =============================================================================


class FileRenamePreview(BaseModel):
    """Preview of a single file rename."""

    old_path: str = Field(..., description="Current file path")
    new_path: str = Field(..., description="New file path after rename")
    will_change: bool = Field(..., description="Whether the path will actually change")


class PreviewRenameResponse(BaseModel):
    """Response model for rename preview endpoint."""

    status: str = Field(..., description="Status: ok, error")
    torrent_hash: str = Field(..., description="Torrent info hash")
    mode: str = Field(..., description="Rename mode that would be used")
    reason: str | None = Field(default=None, description="Error reason if status is error")

    # Current state
    current_torrent_name: str | None = Field(
        default=None, description="Current torrent display name"
    )
    current_root_folder: str | None = Field(
        default=None, description="Current root folder name (if exists)"
    )

    # Proposed changes
    new_torrent_name: str | None = Field(
        default=None, description="New torrent name (if renaming torrent)"
    )
    new_root_folder: str | None = Field(
        default=None, description="New root folder name (if renaming folder)"
    )
    file_renames: list[FileRenamePreview] = Field(
        default_factory=list, description="List of file renames (if renaming files)"
    )

    # Summary
    torrent_will_change: bool = Field(default=False, description="Whether torrent name will change")
    folder_will_change: bool = Field(default=False, description="Whether root folder will change")
    files_will_change: int = Field(default=0, description="Number of files that will be renamed")
    total_files: int = Field(default=0, description="Total number of files in torrent")
    warnings: list[str] = Field(
        default_factory=list, description="Any warnings about the rename operation"
    )


# =============================================================================
# Find Torrent by ID Models
# =============================================================================


class FindTorrentRequest(BaseModel):
    """Request model for find torrent by ID endpoint."""

    torrent_id: str = Field(
        ...,
        description="Torrent ID from tracker (can be URL like https://domain/torrents/342558 or just number like 342558)",
    )


class FindTorrentResponse(BaseModel):
    """Response model for find torrent by ID endpoint."""

    status: str = Field(..., description="Status: found, not_found, error")
    torrent_id: str = Field(..., description="The torrent ID that was searched")
    torrent_hash: str | None = Field(default=None, description="Torrent hash if found")
    reason: str | None = Field(default=None, description="Error or not found reason")


# =============================================================================
# Rules Management Models (web UI / config API)
# =============================================================================

# Filter list fields whose entries are interpreted as regular expressions.
# (customformats_* use exact membership, not regex, so are excluded.)
_REGEX_LIST_FIELDS = (
    "indexers_include",
    "indexers_exclude",
    "qualities_include",
    "qualities_exclude",
    "download_clients_include",
    "download_clients_exclude",
    "release_groups_include",
    "release_groups_exclude",
    "remove_patterns",
    "skip_title_patterns",
)


class RuleSet(BaseModel):
    """A set of trigger filters + rename rules (global defaults or per-tracker).

    Mirrors :class:`src.config.TrackerRules`. Regex-bearing fields are validated
    so the UI can surface bad patterns before they are saved.
    """

    # Trigger filters
    indexers_include: list[str] = Field(default_factory=list)
    indexers_exclude: list[str] = Field(default_factory=list)
    qualities_include: list[str] = Field(default_factory=list)
    qualities_exclude: list[str] = Field(default_factory=list)
    customformats_require_any: list[str] = Field(default_factory=list)
    customformats_exclude: list[str] = Field(default_factory=list)
    min_customformat_score: int | None = None
    download_clients_include: list[str] = Field(default_factory=list)
    download_clients_exclude: list[str] = Field(default_factory=list)
    release_groups_include: list[str] = Field(default_factory=list)
    release_groups_exclude: list[str] = Field(default_factory=list)

    # Rename rules
    prefix: str = ""
    suffix: str = ""
    remove_patterns: list[str] = Field(default_factory=list)
    replace_patterns: dict[str, str] = Field(default_factory=dict)
    skip_title_patterns: list[str] = Field(default_factory=list)

    # Score validation
    validate_custom_format_score: bool = False
    score_validation_policy: Literal["block", "warn"] = "block"

    @field_validator(*_REGEX_LIST_FIELDS)
    @classmethod
    def _validate_regex_lists(cls, v: list[str]) -> list[str]:
        for i, pattern in enumerate(v):
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid regex at position {i + 1} ('{pattern}'): {e}") from e
        return v

    @field_validator("replace_patterns")
    @classmethod
    def _validate_replace_keys(cls, v: dict[str, str]) -> dict[str, str]:
        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"invalid replace pattern '{pattern}': {e}") from e
        return v

    @model_validator(mode="after")
    def _bound_pattern_sizes(self) -> "RuleSet":
        """Cap pattern count and length (defense-in-depth against abuse / ReDoS)."""
        max_items, max_len = 1000, 2000
        list_fields = (
            self.indexers_include,
            self.indexers_exclude,
            self.qualities_include,
            self.qualities_exclude,
            self.customformats_require_any,
            self.customformats_exclude,
            self.download_clients_include,
            self.download_clients_exclude,
            self.release_groups_include,
            self.release_groups_exclude,
            self.remove_patterns,
            self.skip_title_patterns,
        )
        total = sum(len(f) for f in list_fields) + len(self.replace_patterns)
        if total > max_items:
            raise ValueError(f"too many patterns ({total}); maximum is {max_items}")
        for field in list_fields:
            for item in field:
                if len(item) > max_len:
                    raise ValueError(f"pattern too long ({len(item)} chars); maximum is {max_len}")
        for key, value in self.replace_patterns.items():
            if len(key) > max_len or len(value) > max_len:
                raise ValueError(f"replace pattern too long; maximum is {max_len} chars")
        return self


class TrackerConfigModel(BaseModel):
    """A tracker-specific configuration: match patterns + its rule set."""

    name: str = Field(default="", description="Friendly name for this tracker config")
    match: list[str] = Field(
        default_factory=list,
        description="Indexer match patterns (exact, wildcard, or /regex/)",
    )
    rules: RuleSet = Field(default_factory=RuleSet)

    @field_validator("match")
    @classmethod
    def _validate_match(cls, v: list[str]) -> list[str]:
        for pattern in v:
            # Only /regex/ forms are compiled; exact and glob always parse.
            if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
                try:
                    re.compile(pattern[1:-1])
                except re.error as e:
                    raise ValueError(f"invalid regex match '{pattern}': {e}") from e
        return v


class RulesConfig(BaseModel):
    """Full rules configuration: global defaults + ordered tracker overrides."""

    global_: RuleSet = Field(default_factory=RuleSet, alias="global")
    trackers: list[TrackerConfigModel] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class ConfigMeta(BaseModel):
    """Metadata about the loaded configuration file."""

    config_path: str
    config_found: bool
    config_error: str | None = None
    config_format: Literal["hierarchical", "flat", "empty", "missing"] = "empty"
    readonly: bool = False


class ConfigResponse(BaseModel):
    """Response for GET /api/config."""

    meta: ConfigMeta
    config: RulesConfig


class SaveConfigResponse(BaseModel):
    """Response for PUT /api/config."""

    status: str = "ok"
    warnings: list[str] = Field(default_factory=list)
    meta: ConfigMeta
    config: RulesConfig


class SimulateRelease(BaseModel):
    """A sample release used to simulate rule behavior."""

    release_title: str = Field(default="", max_length=2000)
    indexer: str = Field(default="", max_length=500)
    quality: str = Field(default="", max_length=200)
    release_group: str = Field(default="", max_length=200)
    custom_formats: list[str] = Field(default_factory=list, max_length=100)
    custom_format_score: int | None = None
    download_client: str = Field(default="", max_length=200)


class SimulateRequest(BaseModel):
    """Request for POST /api/rules/simulate.

    If ``config`` is omitted, the currently-saved rules are used. Providing a
    draft config lets the UI preview unsaved edits.
    """

    release: SimulateRelease
    config: RulesConfig | None = None


class SimulateStep(BaseModel):
    """A single recorded transformation step in the rename trace."""

    rule: str
    before: str
    after: str
    error: str | None = None


class SimulateResponse(BaseModel):
    """Response for POST /api/rules/simulate."""

    status: str = "ok"
    matched_tracker: str | None = None
    used_global: bool = True
    would_process: bool = False
    skip_reason: str = ""
    original_title: str = ""
    new_title: str = ""
    changed: bool = False
    steps: list[SimulateStep] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ValidatePatternRequest(BaseModel):
    """Request for POST /api/rules/validate-pattern."""

    pattern: str = Field(..., max_length=2000)
    kind: Literal["regex", "match"] = "regex"


class ValidatePatternResponse(BaseModel):
    """Response for POST /api/rules/validate-pattern."""

    valid: bool
    kind: Literal["regex", "match"]
    interpreted: str | None = None  # for match: exact | wildcard | regex
    error: str | None = None


class SettingsView(BaseModel):
    """Non-sensitive runtime settings exposed to the UI (never secrets)."""

    rename_mode: str
    dry_run: bool
    initial_delay: float
    max_retries: int
    retry_delay: float
    api_operation_delay_ms: int
    log_level: str
    log_format: str
    rules_file: str
    config_readonly: bool
    qbittorrent_url: str
    sonarr_configured: bool
    radarr_configured: bool


class StatusView(BaseModel):
    """Service status + connectivity for the UI."""

    status: str
    version: str
    qbittorrent: str
    sonarr: str | None = None
    radarr: str | None = None
    dry_run: bool
    score_validation: bool
    config_found: bool
    config_error: str | None = None
    readonly: bool = False
