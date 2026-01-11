"""Pydantic models for Sonarr and Radarr webhook payloads."""

from pydantic import BaseModel, ConfigDict, Field

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
