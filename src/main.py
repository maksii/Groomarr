"""FastAPI application for Groomarr webhook service."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from .config import reload_rules, rules, settings, setup_logging
from .models import (
    ManualRenameRequest,
    ManualRenameResponse,
    RadarrWebhook,
    SonarrWebhook,
    WebhookResponse,
)
from .qbittorrent import QBitClient
from .rename import RenameMode, apply_rename_rules, perform_rename, should_process

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Global qBittorrent client
qbit_client: QBitClient = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global qbit_client

    logger.info("Starting Groomarr service")
    logger.info(f"qBittorrent URL: {settings.qbittorrent_url}")
    logger.info(f"Rename mode: {settings.rename_mode}")

    # Log config file state
    _log_config_state()

    # Initialize qBittorrent client
    qbit_client = QBitClient(
        url=settings.qbittorrent_url,
        username=settings.qbittorrent_username,
        password=settings.qbittorrent_password,
    )

    yield

    logger.info("Shutting down Groomarr service")


def _log_config_state():
    """Log the current config file state and active rules."""
    from pathlib import Path

    if not rules.config_found:
        logger.warning(f"Config file not found: {rules.config_path}")

        # Show what's actually in the config directory to help debug
        config_dir = Path(rules.config_path).parent
        if config_dir.exists():
            files = list(config_dir.iterdir())
            if files:
                file_names = [f.name for f in files]
                logger.info(f"Files in {config_dir}: {', '.join(file_names)}")
            else:
                logger.info(f"Directory {config_dir} exists but is empty")
        else:
            logger.warning(f"Directory {config_dir} does not exist")

        # Check if example file exists and hint the user
        example_path = Path(rules.config_path + ".example")
        if example_path.exists():
            logger.info(
                f"Hint: Found {example_path.name} - copy it to rename_rules.yaml to get started"
            )

        logger.info("Using default settings (no filters, no rename rules)")
        return

    if rules.config_error:
        logger.error(f"Config file error: {rules.config_error}")
        logger.info("Using default settings due to config error")
        return

    logger.info(f"Config file loaded: {rules.config_path}")

    # Log trigger filters
    if rules.has_trigger_filters():
        filters = rules.get_active_filters_summary()
        logger.info(f"Active trigger filters: {', '.join(filters)}")
    else:
        logger.info("Trigger filters: none (processing all webhooks)")

    # Log rename rules
    if rules.has_rename_rules():
        rename_rules = rules.get_active_rules_summary()
        logger.info(f"Active rename rules: {', '.join(rename_rules)}")
    else:
        logger.info("Rename rules: none (titles will pass through unchanged)")


app = FastAPI(
    title="Groomarr",
    description="Grooms rough releases into presentable ones — webhook service for renaming torrents in qBittorrent based on Sonarr/Radarr events",
    version="1.0.0",
    lifespan=lifespan,
)


# =============================================================================
# Background Task Processing
# =============================================================================


async def process_rename_task(
    torrent_hash: str,
    release_title: str,
    source: str,
    media_title: str,
):
    """Background task to process rename operation.

    Args:
        torrent_hash: Torrent info hash (downloadId)
        release_title: Original release title from webhook
        source: Source application (radarr/sonarr)
        media_title: Movie or series title for logging
    """
    hash_short = torrent_hash[:8]
    logger.info(f"[{source}] Processing rename for '{media_title}' ({hash_short}...)")

    # Wait for torrent to appear in qBittorrent
    torrent = await qbit_client.wait_for_torrent(
        torrent_hash=torrent_hash,
        initial_delay=settings.initial_delay,
        max_retries=settings.max_retries,
        retry_delay=settings.retry_delay,
    )

    if not torrent:
        logger.warning(
            f"[{source}] Torrent {hash_short}... not found after waiting, giving up"
        )
        return

    # Apply rename rules to get new name
    new_name = apply_rename_rules(release_title, rules)

    if new_name == release_title:
        logger.info(f"[{source}] No rename rules applied, using original title")

    logger.info(f"[{source}] Renaming to: '{new_name}'")

    # Get rename mode
    try:
        mode = RenameMode(settings.rename_mode)
    except ValueError:
        logger.warning(
            f"Invalid rename mode '{settings.rename_mode}', using torrent_and_folder"
        )
        mode = RenameMode.TORRENT_AND_FOLDER

    # Perform rename
    success = await perform_rename(
        qbit=qbit_client,
        torrent_hash=torrent_hash,
        new_name=new_name,
        mode=mode,
    )

    if success:
        logger.info(f"[{source}] Successfully renamed '{media_title}' ({hash_short}...)")
    else:
        logger.error(f"[{source}] Failed to rename '{media_title}' ({hash_short}...)")


# =============================================================================
# API Endpoints
# =============================================================================


@app.get("/health")
async def health():
    """Health check endpoint for Docker."""
    return {"status": "ok"}


@app.get("/reload")
async def reload_config():
    """Reload rename rules from file."""
    reload_rules()
    return {"status": "ok", "message": "Rules reloaded"}


@app.post("/rename/manual", response_model=ManualRenameResponse)
async def manual_rename(request: ManualRenameRequest):
    """Manually rename a torrent by hash.

    This endpoint allows direct renaming of a torrent without waiting
    for a webhook event. Useful for fixing torrent names manually.

    Args:
        request: ManualRenameRequest with torrent_hash, new_name, and mode

    Returns:
        ManualRenameResponse with status and details
    """
    hash_short = request.torrent_hash[:8] if len(request.torrent_hash) >= 8 else request.torrent_hash
    logger.info(f"[manual] Received rename request for {hash_short}... mode={request.mode}")

    # Validate rename mode
    try:
        mode = RenameMode(request.mode)
    except ValueError:
        valid_modes = [m.value for m in RenameMode]
        return ManualRenameResponse(
            status="error",
            torrent_hash=request.torrent_hash,
            reason=f"Invalid mode '{request.mode}'. Valid modes: {', '.join(valid_modes)}",
        )

    # Check if torrent exists
    torrent = qbit_client.get_torrent_info(request.torrent_hash)
    if not torrent:
        logger.warning(f"[manual] Torrent {hash_short}... not found")
        return ManualRenameResponse(
            status="error",
            torrent_hash=request.torrent_hash,
            reason="Torrent not found",
        )

    # Perform the rename
    success = await perform_rename(
        qbit=qbit_client,
        torrent_hash=request.torrent_hash,
        new_name=request.new_name,
        mode=mode,
    )

    if success:
        logger.info(f"[manual] Successfully renamed {hash_short}... to '{request.new_name}'")
        return ManualRenameResponse(
            status="success",
            torrent_hash=request.torrent_hash,
            new_name=request.new_name,
            mode=request.mode,
        )
    else:
        logger.error(f"[manual] Failed to rename {hash_short}...")
        return ManualRenameResponse(
            status="error",
            torrent_hash=request.torrent_hash,
            new_name=request.new_name,
            mode=request.mode,
            reason="Rename operation failed",
        )


@app.post("/webhook/radarr", response_model=WebhookResponse)
async def radarr_webhook(payload: RadarrWebhook, background_tasks: BackgroundTasks):
    """Handle Radarr webhook for Grab events.

    Configure in Radarr: Settings -> Connect -> Webhook
    - URL: http://groomarr:8000/webhook/radarr
    - Events: On Grab only
    """
    hash_short = payload.downloadId[:8] if payload.downloadId else "unknown"
    movie_title = payload.movie.title

    logger.info(
        f"[radarr] Received webhook: {payload.eventType} for '{movie_title}' ({hash_short}...)"
    )

    # Check event type
    if payload.eventType != "Grab":
        return WebhookResponse(
            status="skipped",
            reason=f"event type '{payload.eventType}' not Grab",
            torrent_hash=payload.downloadId,
        )

    # Check download client type
    if payload.downloadClientType != "qBittorrent":
        return WebhookResponse(
            status="skipped",
            reason=f"download client '{payload.downloadClientType}' not qBittorrent",
            torrent_hash=payload.downloadId,
        )

    # Apply trigger filters
    should_proc, skip_reason = should_process(payload, rules)
    if not should_proc:
        logger.info(f"[radarr] Skipping {hash_short}...: {skip_reason}")
        return WebhookResponse(
            status="skipped",
            reason=skip_reason,
            torrent_hash=payload.downloadId,
        )

    # Queue background task
    background_tasks.add_task(
        process_rename_task,
        torrent_hash=payload.downloadId,
        release_title=payload.release.releaseTitle,
        source="radarr",
        media_title=movie_title,
    )

    logger.info(f"[radarr] Queued rename for '{movie_title}' ({hash_short}...)")
    return WebhookResponse(
        status="queued",
        torrent_hash=payload.downloadId,
    )


@app.post("/webhook/sonarr", response_model=WebhookResponse)
async def sonarr_webhook(payload: SonarrWebhook, background_tasks: BackgroundTasks):
    """Handle Sonarr webhook for Grab events.

    Configure in Sonarr: Settings -> Connect -> Webhook
    - URL: http://groomarr:8000/webhook/sonarr
    - Events: On Grab only
    """
    download_id = payload.get_download_id()
    hash_short = download_id[:8] if download_id else "unknown"
    series_title = payload.series.title

    # Build episode info for logging
    if payload.episodes:
        ep = payload.episodes[0]
        episode_info = f"S{ep.seasonNumber:02d}E{ep.episodeNumber:02d}"
        if len(payload.episodes) > 1:
            episode_info += f" (+{len(payload.episodes) - 1} more)"
    else:
        episode_info = ""

    logger.info(
        f"[sonarr] Received webhook: {payload.eventType} for "
        f"'{series_title}' {episode_info} ({hash_short}...)"
    )

    # Check event type
    if payload.eventType != "Grab":
        return WebhookResponse(
            status="skipped",
            reason=f"event type '{payload.eventType}' not Grab",
            torrent_hash=download_id,
        )

    # Check download client type
    if payload.downloadClientType != "qBittorrent":
        return WebhookResponse(
            status="skipped",
            reason=f"download client '{payload.downloadClientType}' not qBittorrent",
            torrent_hash=download_id,
        )

    # Check we have download ID
    if not download_id:
        return WebhookResponse(
            status="skipped",
            reason="no downloadId in payload",
        )

    # Apply trigger filters
    should_proc, skip_reason = should_process(payload, rules)
    if not should_proc:
        logger.info(f"[sonarr] Skipping {hash_short}...: {skip_reason}")
        return WebhookResponse(
            status="skipped",
            reason=skip_reason,
            torrent_hash=download_id,
        )

    # Get release title
    release_title = payload.get_release_title()

    # Queue background task
    background_tasks.add_task(
        process_rename_task,
        torrent_hash=download_id,
        release_title=release_title,
        source="sonarr",
        media_title=f"{series_title} {episode_info}",
    )

    logger.info(
        f"[sonarr] Queued rename for '{series_title}' {episode_info} ({hash_short}...)"
    )
    return WebhookResponse(
        status="queued",
        torrent_hash=download_id,
    )


# =============================================================================
# Error Handlers
# =============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "reason": str(exc)},
    )


# =============================================================================
# Entry Point
# =============================================================================


def main():
    """Run the application with uvicorn."""
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
