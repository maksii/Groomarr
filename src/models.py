"""Pydantic models for Sonarr and Radarr webhook payloads."""

from pydantic import BaseModel, Field

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

    class Config:
        populate_by_name = True


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
