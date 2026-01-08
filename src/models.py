"""Pydantic models for Sonarr and Radarr webhook payloads."""

from typing import List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Radarr Models
# =============================================================================


class MovieInfo(BaseModel):
    """Movie information from Radarr webhook."""

    id: int
    title: str
    year: Optional[int] = None
    tmdbId: Optional[int] = None
    imdbId: Optional[str] = None


class RadarrRelease(BaseModel):
    """Release information from Radarr webhook."""

    releaseTitle: str
    quality: str
    releaseGroup: Optional[str] = None
    size: Optional[int] = None
    indexer: Optional[str] = None
    customFormats: Optional[List[str]] = None
    customFormatScore: Optional[int] = None


class RadarrWebhook(BaseModel):
    """Radarr webhook payload for Grab event."""

    eventType: str
    movie: MovieInfo
    release: RadarrRelease
    downloadId: str  # Top-level - this is the torrent hash
    downloadClient: str
    downloadClientType: str
    instanceName: Optional[str] = None
    applicationUrl: Optional[str] = None


# =============================================================================
# Sonarr Models
# =============================================================================


class SeriesInfo(BaseModel):
    """Series information from Sonarr webhook."""

    id: int
    title: str
    path: Optional[str] = None
    tvdbId: Optional[int] = None
    tvMazeId: Optional[int] = None
    imdbId: Optional[str] = None
    type: Optional[str] = None


class EpisodeInfo(BaseModel):
    """Episode information from Sonarr webhook."""

    id: int
    episodeNumber: int
    seasonNumber: int
    title: Optional[str] = None
    airDate: Optional[str] = None
    airDateUtc: Optional[str] = None


class SonarrRelease(BaseModel):
    """Release information from Sonarr webhook.

    Note: Sonarr uses 'title' instead of 'releaseTitle',
    and downloadId is inside release, not top-level.
    """

    title: str = Field(alias="releaseTitle", default="")  # Can be either
    quality: str
    releaseGroup: Optional[str] = None
    size: Optional[int] = None
    indexer: Optional[str] = None
    downloadId: Optional[str] = None  # In Sonarr, downloadId is here
    customFormats: Optional[List[str]] = None
    customFormatScore: Optional[int] = None

    class Config:
        populate_by_name = True


class SonarrWebhook(BaseModel):
    """Sonarr webhook payload for Grab event."""

    eventType: str
    series: SeriesInfo
    episodes: List[EpisodeInfo] = []
    release: SonarrRelease
    downloadClient: Optional[str] = None
    downloadClientType: Optional[str] = None
    downloadId: Optional[str] = None  # Sonarr v4+ puts downloadId at top level
    instanceName: Optional[str] = None
    applicationUrl: Optional[str] = None

    def get_download_id(self) -> Optional[str]:
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
    reason: Optional[str] = None
    torrent_hash: Optional[str] = None


# =============================================================================
# Manual Rename Models
# =============================================================================


class ManualRenameRequest(BaseModel):
    """Request model for manual rename endpoint."""

    torrent_hash: str = Field(
        ..., description="Torrent info hash to rename"
    )
    new_name: str = Field(
        ..., description="New name to apply to the torrent"
    )
    mode: str = Field(
        default="torrent_and_folder",
        description="Rename mode: torrent_only, torrent_and_folder, torrent_folder_files, folder_only, files_only",
    )


class ManualRenameResponse(BaseModel):
    """Response model for manual rename endpoint."""

    status: str
    torrent_hash: str
    new_name: Optional[str] = None
    mode: Optional[str] = None
    reason: Optional[str] = None
