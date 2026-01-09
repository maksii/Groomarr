"""FastAPI application for Groomarr webhook service."""

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .arrapi import ArrClient
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

# Global clients
qbit_client: QBitClient = None
sonarr_client: ArrClient | None = None
radarr_client: ArrClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    global qbit_client, sonarr_client, radarr_client

    logger.info(f"Starting Groomarr service v{__version__}")
    logger.info(f"qBittorrent URL: {settings.qbittorrent_url}")
    logger.info(f"Rename mode: {settings.rename_mode}")
    if settings.dry_run:
        logger.warning("DRY RUN MODE ENABLED - no actual renames will be performed")

    # Log config file state
    _log_config_state()

    # Initialize qBittorrent client
    qbit_client = QBitClient(
        url=settings.qbittorrent_url,
        username=settings.qbittorrent_username,
        password=settings.qbittorrent_password,
    )

    # Initialize Sonarr client (if configured)
    if settings.sonarr_url and settings.sonarr_api_key:
        sonarr_client = ArrClient(
            url=settings.sonarr_url,
            api_key=settings.sonarr_api_key,
            app_type="sonarr",
        )
        if sonarr_client.check_connection():
            logger.info(f"Sonarr API connected: {settings.sonarr_url}")
        else:
            logger.warning(f"Sonarr API not reachable: {settings.sonarr_url}")
    else:
        logger.info("Sonarr API not configured (SONARR_URL/SONARR_API_KEY)")

    # Initialize Radarr client (if configured)
    if settings.radarr_url and settings.radarr_api_key:
        radarr_client = ArrClient(
            url=settings.radarr_url,
            api_key=settings.radarr_api_key,
            app_type="radarr",
        )
        if radarr_client.check_connection():
            logger.info(f"Radarr API connected: {settings.radarr_url}")
        else:
            logger.warning(f"Radarr API not reachable: {settings.radarr_url}")
    else:
        logger.info("Radarr API not configured (RADARR_URL/RADARR_API_KEY)")

    # Log score validation state
    if rules.validate_custom_format_score:
        logger.info(f"Score validation enabled (policy: {rules.score_validation_policy})")
    else:
        logger.info("Score validation disabled")

    yield

    # Cleanup Arr clients
    if sonarr_client:
        await sonarr_client.close()
    if radarr_client:
        await radarr_client.close()

    logger.info("Shutting down Groomarr service")


def _log_payload_diagnostics(source: str, payload: dict):
    """Log diagnostic information about a webhook payload.

    This helps debug issues with malformed or unexpected payloads.

    Args:
        source: Source application (radarr/sonarr)
        payload: Raw JSON payload dictionary
    """
    # List expected fields based on source
    if source == "radarr":
        expected_fields = [
            "eventType",
            "movie",
            "release",
            "downloadId",
            "downloadClient",
            "downloadClientType",
        ]
    else:  # sonarr
        expected_fields = [
            "eventType",
            "series",
            "episodes",
            "release",
            "downloadId",  # Sonarr v4+ puts this at top level
            "downloadClient",
            "downloadClientType",
        ]

    # Check which fields are present/missing
    present = [f for f in expected_fields if f in payload]
    missing = [f for f in expected_fields if f not in payload]

    logger.debug(f"[{source}] Payload keys received: {list(payload.keys())}")

    if missing:
        logger.info(f"[{source}] Missing expected fields: {', '.join(missing)}")
    if present:
        logger.debug(f"[{source}] Present expected fields: {', '.join(present)}")

    # Log specific useful info if available
    if "eventType" in payload:
        logger.debug(f"[{source}] Event type: {payload['eventType']}")

    if "movie" in payload and isinstance(payload["movie"], dict):
        movie = payload["movie"]
        logger.debug(f"[{source}] Movie: {movie.get('title', 'unknown')}")

    if "series" in payload and isinstance(payload["series"], dict):
        series = payload["series"]
        logger.debug(f"[{source}] Series: {series.get('title', 'unknown')}")

    if "release" in payload and isinstance(payload["release"], dict):
        release = payload["release"]
        release_title = release.get("releaseTitle") or release.get("title", "unknown")
        logger.debug(f"[{source}] Release: {release_title[:80]}...")

    if "downloadId" in payload:
        did = payload["downloadId"]
        hash_preview = did[:8] if did else "empty"
        logger.debug(f"[{source}] Download ID: {hash_preview}...")

    if "downloadClientType" in payload:
        logger.debug(f"[{source}] Download client type: {payload['downloadClientType']}")


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
    version=__version__,
    lifespan=lifespan,
)


# =============================================================================
# Background Task Processing
# =============================================================================


async def _validate_rename_score(
    source: str,
    release_title: str,
    new_name: str,
    hash_short: str,
) -> bool:
    """Validate rename using Arr API custom format score comparison.

    Args:
        source: Source application (radarr/sonarr)
        release_title: Original release title
        new_name: Proposed new name
        hash_short: Short torrent hash for logging

    Returns:
        True if rename should proceed, False if it should be skipped
    """
    # Check if validation is enabled
    if not rules.validate_custom_format_score:
        return True

    # Get the appropriate client
    arr_client = radarr_client if source == "radarr" else sonarr_client

    if arr_client is None:
        arr_url = settings.radarr_url if source == "radarr" else settings.sonarr_url
        if arr_url:
            logger.warning(
                f"[{source}] Skipping rename: {source.title()} API not configured properly "
                f"(check API key)"
            )
        else:
            logger.warning(
                f"[{source}] Skipping rename: {source.title()} URL not configured "
                f"(set {source.upper()}_URL and {source.upper()}_API_KEY)"
            )
        return False

    # Validate the rename
    comparison = await arr_client.validate_rename(release_title, new_name)

    if comparison is None:
        # API error - skip rename
        arr_url = settings.radarr_url if source == "radarr" else settings.sonarr_url
        logger.warning(
            f"[{source}] Skipping rename for {hash_short}...: "
            f"{source.title()} API unreachable at {arr_url}"
        )
        return False

    if comparison.is_safe:
        # Score is same or improved - proceed
        return True

    # Score decreased - check policy
    if rules.score_validation_policy == "warn":
        logger.warning(
            f"[{source}] Proceeding with rename despite score decrease: "
            f"{comparison.original_score} -> {comparison.new_score} "
            f"({comparison.score_change:+d})"
        )
        return True
    else:
        # Default to "block"
        logger.warning(
            f"[{source}] Skipping rename for {hash_short}...: "
            f"score would decrease from {comparison.original_score} to "
            f"{comparison.new_score} ({comparison.score_change:+d})"
        )
        return False


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
        logger.warning(f"[{source}] Torrent {hash_short}... not found after waiting, giving up")
        return

    # Apply rename rules to get new name
    new_name = apply_rename_rules(release_title, rules)

    if new_name == release_title:
        logger.info(f"[{source}] No rename rules applied, using original title")

    logger.info(f"[{source}] Renaming to: '{new_name}'")

    # Validate rename using Arr API (if enabled)
    should_rename = await _validate_rename_score(source, release_title, new_name, hash_short)
    if not should_rename:
        return

    # Get rename mode
    try:
        mode = RenameMode(settings.rename_mode)
    except ValueError:
        logger.warning(f"Invalid rename mode '{settings.rename_mode}', using torrent_and_folder")
        mode = RenameMode.TORRENT_AND_FOLDER

    # Check for dry run mode
    if settings.dry_run:
        logger.info(
            f"[{source}] DRY RUN: Would rename '{media_title}' ({hash_short}...) "
            f"to '{new_name}' with mode={mode.value}"
        )
        return

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
    """Health check endpoint for Docker.

    Returns service status including qBittorrent and Arr API connectivity.
    """
    qbit_connected = qbit_client.check_connection() if qbit_client else False
    sonarr_connected = sonarr_client.check_connection() if sonarr_client else None
    radarr_connected = radarr_client.check_connection() if radarr_client else None

    # Build response
    response = {
        "status": "ok" if qbit_connected else "degraded",
        "version": __version__,
        "qbittorrent": "connected" if qbit_connected else "disconnected",
        "dry_run": settings.dry_run,
        "score_validation": rules.validate_custom_format_score,
    }

    # Add Sonarr status (only if configured)
    if sonarr_connected is not None:
        response["sonarr"] = "connected" if sonarr_connected else "disconnected"

    # Add Radarr status (only if configured)
    if radarr_connected is not None:
        response["radarr"] = "connected" if radarr_connected else "disconnected"

    return response


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
    hash_short = (
        request.torrent_hash[:8] if len(request.torrent_hash) >= 8 else request.torrent_hash
    )
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
async def radarr_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Radarr webhook for Grab events.

    Configure in Radarr: Settings -> Connect -> Webhook
    - URL: http://groomarr:8000/webhook/radarr
    - Events: On Grab only
    """
    # Parse raw JSON first to handle test events before Pydantic validation
    try:
        raw_payload = await request.json()
    except Exception as e:
        logger.error(f"[radarr] Failed to parse JSON payload: {e}")
        return WebhookResponse(status="error", reason="Invalid JSON payload")

    event_type = raw_payload.get("eventType", "unknown")

    # Handle test events - Radarr sends minimal payload for test
    if event_type == "Test":
        logger.info("[radarr] Received test webhook - connection successful")
        _log_payload_diagnostics("radarr", raw_payload)
        return WebhookResponse(status="ok", reason="Test webhook received successfully")

    # For non-test events, validate with Pydantic model
    try:
        payload = RadarrWebhook(**raw_payload)
    except Exception as e:
        logger.error(f"[radarr] Payload validation failed: {e}")
        _log_payload_diagnostics("radarr", raw_payload)
        return JSONResponse(
            status_code=422,
            content={"status": "error", "reason": f"Invalid payload: {str(e)[:200]}"},
        )

    hash_short = payload.downloadId[:8] if payload.downloadId else "unknown"
    movie_title = payload.movie.title

    logger.info(
        f"[radarr] Received webhook: {payload.eventType} for '{movie_title}' ({hash_short}...)"
    )

    # Check event type
    if payload.eventType != "Grab":
        logger.info(f"[radarr] Skipping event type '{payload.eventType}' (not Grab)")
        return WebhookResponse(
            status="skipped",
            reason=f"event type '{payload.eventType}' not Grab",
            torrent_hash=payload.downloadId,
        )

    # Check download client type
    if payload.downloadClientType != "qBittorrent":
        logger.info(
            f"[radarr] Skipping download client '{payload.downloadClientType}' (not qBittorrent)"
        )
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
async def sonarr_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle Sonarr webhook for Grab events.

    Configure in Sonarr: Settings -> Connect -> Webhook
    - URL: http://groomarr:8000/webhook/sonarr
    - Events: On Grab only
    """
    # Parse raw JSON first to handle test events before Pydantic validation
    try:
        raw_payload = await request.json()
    except Exception as e:
        logger.error(f"[sonarr] Failed to parse JSON payload: {e}")
        return WebhookResponse(status="error", reason="Invalid JSON payload")

    event_type = raw_payload.get("eventType", "unknown")

    # Handle test events - Sonarr sends minimal payload for test
    if event_type == "Test":
        logger.info("[sonarr] Received test webhook - connection successful")
        _log_payload_diagnostics("sonarr", raw_payload)
        return WebhookResponse(status="ok", reason="Test webhook received successfully")

    # For non-test events, validate with Pydantic model
    try:
        payload = SonarrWebhook(**raw_payload)
    except Exception as e:
        logger.error(f"[sonarr] Payload validation failed: {e}")
        _log_payload_diagnostics("sonarr", raw_payload)
        return JSONResponse(
            status_code=422,
            content={"status": "error", "reason": f"Invalid payload: {str(e)[:200]}"},
        )

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
        logger.info(f"[sonarr] Skipping event type '{payload.eventType}' (not Grab)")
        return WebhookResponse(
            status="skipped",
            reason=f"event type '{payload.eventType}' not Grab",
            torrent_hash=download_id,
        )

    # Check download client type
    if payload.downloadClientType != "qBittorrent":
        logger.info(
            f"[sonarr] Skipping download client '{payload.downloadClientType}' (not qBittorrent)"
        )
        return WebhookResponse(
            status="skipped",
            reason=f"download client '{payload.downloadClientType}' not qBittorrent",
            torrent_hash=download_id,
        )

    # Check we have download ID
    if not download_id:
        logger.warning("[sonarr] No downloadId found in payload")
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

    logger.info(f"[sonarr] Queued rename for '{series_title}' {episode_info} ({hash_short}...)")
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
