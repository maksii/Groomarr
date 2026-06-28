"""FastAPI application for Groomarr webhook service."""

import asyncio
import contextlib
import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, config, history
from .api import check_connection_async
from .api import router as api_router
from .arrapi import ArrClient
from .config import TrackerRules, reload_rules, settings, setup_logging
from .models import (
    FileRenamePreview,
    FindTorrentRequest,
    FindTorrentResponse,
    ManualRenameRequest,
    ManualRenameResponse,
    PreviewRenameResponse,
    RadarrWebhook,
    SonarrWebhook,
    WebhookResponse,
)
from .qbittorrent import QBitClient
from .rename import (
    RenameConflictError,
    RenameMode,
    apply_rename_rules,
    apply_rename_rules_traced,
    explain_filters,
    get_root_folder,
    perform_rename,
    should_process,
    validate_rename_plan,
)

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

    # Initialize the operation history store (best-effort; never blocks startup)
    history.init_db()

    # Initialize qBittorrent client
    qbit_client = QBitClient(
        url=settings.qbittorrent_url,
        username=settings.qbittorrent_username,
        password=settings.qbittorrent_password,
        api_delay=settings.api_operation_delay_ms / 1000.0,  # Convert ms to seconds
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
    if config.rules.validate_custom_format_score:
        logger.info(f"Score validation enabled (policy: {config.rules.score_validation_policy})")
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


def _log_full_payload(source: str, payload: dict):
    """Log the complete payload structure for debugging purposes.

    This is useful for discovering the structure of webhooks from new sources
    like Prowlarr.

    Args:
        source: Source application (prowlarr)
        payload: Raw JSON payload dictionary
    """
    logger.info(f"[{source}] ===== Received webhook payload =====")
    logger.info(f"[{source}] Payload keys: {list(payload.keys())}")

    # Log the entire payload as formatted JSON
    try:
        payload_json = json.dumps(payload, indent=2, ensure_ascii=False)
        logger.info(f"[{source}] Full payload structure:\n{payload_json}")
    except Exception as e:
        logger.warning(f"[{source}] Failed to serialize payload as JSON: {e}")
        logger.info(f"[{source}] Payload type: {type(payload)}, value: {payload}")

    # Log specific fields if they exist (help identify structure)
    if "eventType" in payload:
        logger.info(f"[{source}] Event type: {payload['eventType']}")

    if "indexerId" in payload:
        logger.info(f"[{source}] Indexer ID: {payload['indexerId']}")

    if "indexer" in payload:
        logger.info(f"[{source}] Indexer: {payload['indexer']}")

    if "release" in payload:
        logger.info(f"[{source}] Release information present: {type(payload['release'])}")

    logger.info(f"[{source}] ===== End of payload =====")


def _log_config_state():
    """Log the current config file state and active rules."""
    from pathlib import Path

    if not config.rules.config_found:
        logger.warning(f"Config file not found: {config.rules.config_path}")

        # Show what's actually in the config directory to help debug
        config_dir = Path(config.rules.config_path).parent
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
        example_path = Path(config.rules.config_path + ".example")
        if example_path.exists():
            logger.info(
                f"Hint: Found {example_path.name} - copy it to rename_rules.yaml to get started"
            )

        logger.info("Using default settings (no filters, no rename rules)")
        return

    if config.rules.config_error:
        logger.error(f"Config file error: {config.rules.config_error}")
        logger.info("Using default settings due to config error")
        return

    logger.info(f"Config file loaded: {config.rules.config_path}")

    # Log trigger filters
    if config.rules.has_trigger_filters():
        filters = config.rules.get_active_filters_summary()
        logger.info(f"Active trigger filters: {', '.join(filters)}")
    else:
        logger.info("Trigger filters: none (processing all webhooks)")

    # Log rename rules
    if config.rules.has_rename_rules():
        rename_rules = config.rules.get_active_rules_summary()
        logger.info(f"Active rename rules: {', '.join(rename_rules)}")
    else:
        logger.info("Rename rules: none (titles will pass through unchanged)")


app = FastAPI(
    title="Groomarr",
    description="Grooms rough releases into presentable ones — webhook service for renaming torrents in qBittorrent based on Sonarr/Radarr events",
    version=__version__,
    lifespan=lifespan,
)

# Web UI / management API (rules editor, simulator, status, settings)
app.include_router(api_router)

# Maximum accepted body size for /api/* requests (defense against oversized payloads)
MAX_API_BODY_BYTES = 2 * 1024 * 1024  # 2 MB

# Doc routes load Swagger UI / ReDoc from a CDN with inline bootstrap scripts,
# so the strict CSP would break them. Exempt only these EXACT paths (a prefix
# match would also exempt SPA routes like /docsx).
_CSP_EXEMPT_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}

# Body-size limit applies to all endpoints that read a request body.
_BODY_LIMITED_PREFIXES = ("/api/", "/webhook/", "/rename/", "/find/")

# Strict Content-Security-Policy for the app. 'unsafe-inline' is allowed for
# styles only (UI primitives set inline positioning styles); scripts stay
# locked to same-origin, which is the meaningful XSS control.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """Enforce a request-body size limit on the API and add security headers.

    The interactive API docs (/docs, /redoc) load Swagger UI from a CDN, so the
    strict CSP is not applied to those paths (it would break them).
    """
    path = request.url.path

    if path.startswith(_BODY_LIMITED_PREFIXES):
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_API_BODY_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"status": "error", "reason": "Request body too large"},
                    )
            except ValueError:
                pass

    response = await call_next(request)

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if path not in _CSP_EXEMPT_PATHS:
        response.headers.setdefault("Content-Security-Policy", _CSP)

    return response


# =============================================================================
# Background Task Processing
# =============================================================================


def _decision_detail(
    rules: TrackerRules,
    *,
    release_title: str,
    indexer: str,
    quality: str,
    release_group: str,
    custom_formats: list[str] | None,
    custom_format_score: int | None,
    download_client: str,
) -> tuple[str, list[dict], list[dict]]:
    """Compute the audit detail for a webhook: target name, rule trace, filter checks.

    Uses the exact production engine (the same functions that drive the rename and
    the simulator), so the recorded explanation matches real behavior. Never
    raises — the audit log must never break webhook handling.
    """
    try:
        checks = explain_filters(
            rules,
            indexer=indexer,
            quality=quality,
            release_group=release_group,
            custom_formats=custom_formats,
            custom_format_score=custom_format_score,
            download_client=download_client,
        )
    except Exception:  # noqa: BLE001 - detail is best-effort
        checks = []
    try:
        new_name, steps = apply_rename_rules_traced(release_title, rules)
    except Exception:  # noqa: BLE001
        new_name, steps = release_title, []
    return new_name, steps, checks


def _preview_file_plan(
    files: list[dict], new_name: str, mode: RenameMode
) -> tuple[list[dict], str | None, str | None]:
    """Build a display-only file plan (used for dry-run records). Never raises.

    Returns ``(file_plan, folder_old, folder_new)`` where file_plan is a list of
    ``{old_path, new_path, changed}`` dicts. Mirrors the preview endpoint without
    mutating anything.
    """
    try:
        root_folder = get_root_folder(files)
        folder_old = root_folder
        folder_new = (
            new_name
            if root_folder
            and mode
            in (
                RenameMode.TORRENT_AND_FOLDER,
                RenameMode.TORRENT_FOLDER_FILES,
                RenameMode.FOLDER_ONLY,
            )
            else root_folder
        )
        plan: list[dict] = []
        if mode in (RenameMode.TORRENT_FOLDER_FILES, RenameMode.FILES_ONLY):
            rename_plan, _warnings = validate_rename_plan(files, new_name, root_folder)
            plan = [
                {"old_path": old, "new_path": new, "changed": old != new}
                for old, new in rename_plan
            ]
        return plan, folder_old, folder_new
    except Exception:  # noqa: BLE001 - preview is best-effort
        return [], None, None


async def _record(**fields) -> int | None:
    """Insert an operation record off the event loop. Never raises."""
    try:
        return await asyncio.to_thread(history.record_operation, **fields)
    except Exception:  # noqa: BLE001
        return None


async def _update(op_id: int | None, **fields) -> None:
    """Update an operation record off the event loop. Never raises."""
    if not op_id:
        return
    with contextlib.suppress(Exception):
        await asyncio.to_thread(history.update_operation, op_id, **fields)


async def _validate_rename_score(
    source: str,
    current_name: str,
    new_name: str,
    hash_short: str,
    effective_rules: TrackerRules,
) -> bool:
    """Validate rename using Arr API custom format score comparison.

    Compares the current torrent name in qBittorrent against the proposed new name
    to ensure the rename won't negatively impact Sonarr/Radarr custom format scoring.

    Args:
        source: Source application (radarr/sonarr)
        current_name: Current torrent name in qBittorrent
        new_name: Proposed new name after rename
        hash_short: Short torrent hash for logging
        effective_rules: TrackerRules to use for validation settings

    Returns:
        True if rename should proceed, False if it should be skipped
    """
    # Check if validation is enabled
    if not effective_rules.validate_custom_format_score:
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
    comparison = await arr_client.validate_rename(current_name, new_name)

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
    if effective_rules.score_validation_policy == "warn":
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
    effective_rules: TrackerRules,
    tracker_name: str | None = None,
    op_id: int | None = None,
):
    """Background task to process rename operation.

    Args:
        torrent_hash: Torrent info hash (downloadId)
        release_title: Original release title from webhook
        source: Source application (radarr/sonarr)
        media_title: Movie or series title for logging
        effective_rules: TrackerRules to use (global or tracker-specific)
        tracker_name: Name of matched tracker (None if using global rules)
        op_id: History operation id to update as the task progresses (best-effort).
    """
    hash_short = torrent_hash[:8]
    rules_info = f"tracker '{tracker_name}'" if tracker_name else "global"
    logger.info(
        f"[{source}] Processing rename for '{media_title}' ({hash_short}...) using {rules_info}"
    )
    await _update(op_id, status="processing")

    # Wait for torrent to appear in qBittorrent
    torrent = await qbit_client.wait_for_torrent(
        torrent_hash=torrent_hash,
        initial_delay=settings.initial_delay,
        max_retries=settings.max_retries,
        retry_delay=settings.retry_delay,
    )

    if not torrent:
        logger.warning(f"[{source}] Torrent {hash_short}... not found after waiting, giving up")
        await _update(
            op_id,
            status="failed",
            error="Torrent never appeared in qBittorrent (gave up after retries)",
        )
        return

    current_name = getattr(torrent, "name", "") or ""

    # Apply rename rules to get new name
    new_name = apply_rename_rules(release_title, effective_rules)

    if new_name == release_title:
        logger.info(f"[{source}] No rename rules applied, using original title")

    logger.info(f"[{source}] Renaming to: '{new_name}'")
    await _update(op_id, old_name=current_name, new_name=new_name)

    # Validate rename using Arr API (if enabled)
    # Compare current torrent name against proposed new name
    should_rename = await _validate_rename_score(
        source, current_name, new_name, hash_short, effective_rules
    )
    if not should_rename:
        await _update(
            op_id,
            status="skipped",
            decision="skipped",
            skip_reason="rename blocked by custom-format score validation / Arr API check",
        )
        return

    # Get rename mode
    try:
        mode = RenameMode(settings.rename_mode)
    except ValueError:
        logger.warning(f"Invalid rename mode '{settings.rename_mode}', using torrent_and_folder")
        mode = RenameMode.TORRENT_AND_FOLDER
    await _update(op_id, rename_mode=mode.value)

    # Check for dry run mode
    if settings.dry_run:
        logger.info(
            f"[{source}] DRY RUN: Would rename '{media_title}' ({hash_short}...) "
            f"to '{new_name}' with mode={mode.value}"
        )
        # Record what WOULD have happened so the dashboard can show the preview.
        files = await asyncio.to_thread(qbit_client.get_files, torrent_hash)
        plan, folder_old, folder_new = _preview_file_plan(files, new_name, mode)
        layout_kind = None
        try:
            from .structure import analyze_torrent

            layout_kind = analyze_torrent(files).kind.value
        except Exception:  # noqa: BLE001
            layout_kind = None
        await _update(
            op_id,
            status="dry_run",
            file_plan_json=plan,
            folder_old=folder_old,
            folder_new=folder_new,
            layout_kind=layout_kind,
            files_total=len(files),
        )
        return

    # Perform rename
    result = await perform_rename(
        qbit=qbit_client,
        torrent_hash=torrent_hash,
        new_name=new_name,
        mode=mode,
    )

    # Record the executed plan + outcome for the audit log and rollback.
    await _update(op_id, **_result_record_fields(result, current_name, new_name))

    if result.success:
        if result.already_complete:
            logger.info(f"[{source}] '{media_title}' ({hash_short}...) already renamed")
        else:
            logger.info(f"[{source}] Successfully renamed '{media_title}' ({hash_short}...)")
    else:
        error_detail = ""
        if result.verification_errors:
            error_detail = f": {', '.join(result.verification_errors[:3])}"
        logger.error(f"[{source}] Failed to rename '{media_title}' ({hash_short}...){error_detail}")


def _result_record_fields(result, fallback_old: str, target_new: str) -> dict:
    """Translate a RenameResult into history columns (executed plan + outcome).

    Pure/never raises so it is safe to call inline; the caller persists the dict.
    """
    if result.already_complete and result.success:
        status = "no_change"
    elif result.success:
        status = "renamed"
    else:
        status = "failed"

    applied = {
        "torrent": list(result.applied_torrent_rename) if result.applied_torrent_rename else None,
        "folder": list(result.applied_folder_rename) if result.applied_folder_rename else None,
        "files": [[o, n] for o, n in result.applied_file_renames],
    }
    file_plan = [
        {"old_path": o, "new_path": n, "changed": True} for o, n in result.applied_file_renames
    ]
    folder_old = result.applied_folder_rename[0] if result.applied_folder_rename else None
    folder_new = result.applied_folder_rename[1] if result.applied_folder_rename else None
    old_name = result.applied_torrent_rename[0] if result.applied_torrent_rename else fallback_old
    return {
        "status": status,
        "old_name": old_name,
        "new_name": target_new,
        "folder_old": folder_old,
        "folder_new": folder_new,
        "layout_kind": result.layout_kind,
        "applied_json": applied,
        "file_plan_json": file_plan,
        "files_renamed": result.files_renamed,
        "files_failed": result.files_failed,
        "files_skipped": result.files_skipped,
        "files_total": result.total_files_processed,
        "error": "; ".join(result.verification_errors[:3]) if result.verification_errors else "",
    }


# =============================================================================
# API Endpoints
# =============================================================================


@app.get("/health")
async def health():
    """Health check endpoint for Docker.

    Returns service status including qBittorrent and Arr API connectivity.
    """
    # Run connectivity checks in parallel, off the event loop (non-blocking).
    qbit_res, sonarr_connected, radarr_connected = await asyncio.gather(
        check_connection_async(qbit_client),
        check_connection_async(sonarr_client),
        check_connection_async(radarr_client),
    )
    qbit_connected = bool(qbit_res)

    # Build response
    response = {
        "status": "ok" if qbit_connected else "degraded",
        "version": __version__,
        "qbittorrent": "connected" if qbit_connected else "disconnected",
        "dry_run": settings.dry_run,
        "score_validation": config.rules.validate_custom_format_score,
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
    _log_config_state()
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
    torrent = await asyncio.to_thread(qbit_client.get_torrent_info, request.torrent_hash)
    if not torrent:
        logger.warning(f"[manual] Torrent {hash_short}... not found")
        await _record(
            source="manual",
            event_type="manual",
            status="failed",
            decision="error",
            torrent_hash=request.torrent_hash,
            new_name=request.new_name,
            mode=request.mode,
            media_title=request.new_name,
            error="Torrent not found",
        )
        return ManualRenameResponse(
            status="error",
            torrent_hash=request.torrent_hash,
            reason="Torrent not found",
        )

    current_name = torrent.get("name", "") or ""

    # Perform the rename
    result = await perform_rename(
        qbit=qbit_client,
        torrent_hash=request.torrent_hash,
        new_name=request.new_name,
        mode=mode,
    )

    # Record the manual operation (executed plan + outcome) for the audit log.
    await _record(
        source="manual",
        event_type="manual",
        decision="processed",
        torrent_hash=request.torrent_hash,
        media_title=request.new_name,
        rename_mode=request.mode,
        **_result_record_fields(result, current_name, request.new_name),
    )

    if result.success:
        if result.already_complete:
            logger.info(f"[manual] {hash_short}... already fully renamed to '{request.new_name}'")
            return ManualRenameResponse(
                status="success",
                torrent_hash=request.torrent_hash,
                new_name=request.new_name,
                mode=request.mode,
                reason="Already renamed, no changes needed",
            )
        else:
            summary_parts = []
            if result.torrent_renamed:
                summary_parts.append("torrent")
            if result.folder_renamed:
                summary_parts.append("folder")
            if result.files_renamed > 0:
                summary_parts.append(f"{result.files_renamed} files")
            if result.files_skipped > 0:
                summary_parts.append(f"{result.files_skipped} already correct")

            logger.info(f"[manual] Successfully renamed {hash_short}... to '{request.new_name}'")
            return ManualRenameResponse(
                status="success",
                torrent_hash=request.torrent_hash,
                new_name=request.new_name,
                mode=request.mode,
                reason=f"Renamed: {', '.join(summary_parts)}" if summary_parts else None,
            )
    else:
        error_msg = "Rename operation failed"
        if result.verification_errors:
            error_msg = f"Verification failed: {'; '.join(result.verification_errors[:3])}"
        elif result.files_failed > 0:
            error_msg = (
                f"Partial failure: {result.files_renamed} files renamed, "
                f"{result.files_failed} failed"
            )

        logger.error(f"[manual] Failed to rename {hash_short}...: {error_msg}")
        return ManualRenameResponse(
            status="error",
            torrent_hash=request.torrent_hash,
            new_name=request.new_name,
            mode=request.mode,
            reason=error_msg,
        )


@app.post("/rename/preview", response_model=PreviewRenameResponse)
async def preview_rename(request: ManualRenameRequest):
    """Preview a rename operation without making changes.

    This endpoint shows exactly what would happen if you performed
    a manual rename, including torrent name, folder, and file changes.

    Args:
        request: ManualRenameRequest with torrent_hash, new_name, and mode

    Returns:
        PreviewRenameResponse with all expected changes
    """
    hash_short = (
        request.torrent_hash[:8] if len(request.torrent_hash) >= 8 else request.torrent_hash
    )
    logger.info(f"[preview] Received preview request for {hash_short}... mode={request.mode}")

    # Validate rename mode
    try:
        mode = RenameMode(request.mode)
    except ValueError:
        valid_modes = [m.value for m in RenameMode]
        return PreviewRenameResponse(
            status="error",
            torrent_hash=request.torrent_hash,
            mode=request.mode,
            reason=f"Invalid mode '{request.mode}'. Valid modes: {', '.join(valid_modes)}",
        )

    # Check if torrent exists
    torrent = await asyncio.to_thread(qbit_client.get_torrent_info, request.torrent_hash)
    if not torrent:
        logger.warning(f"[preview] Torrent {hash_short}... not found")
        return PreviewRenameResponse(
            status="error",
            torrent_hash=request.torrent_hash,
            mode=request.mode,
            reason="Torrent not found",
        )

    current_name = torrent.get("name", "")
    new_name = request.new_name

    # Get files and root folder
    files = await asyncio.to_thread(qbit_client.get_files, request.torrent_hash)
    root_folder = get_root_folder(files)

    # Build response
    response = PreviewRenameResponse(
        status="ok",
        torrent_hash=request.torrent_hash,
        mode=request.mode,
        current_torrent_name=current_name,
        current_root_folder=root_folder,
        total_files=len(files),
    )

    warnings: list[str] = []

    # Check torrent rename
    if mode in [
        RenameMode.TORRENT_ONLY,
        RenameMode.TORRENT_AND_FOLDER,
        RenameMode.TORRENT_FOLDER_FILES,
    ]:
        response.new_torrent_name = new_name
        response.torrent_will_change = current_name != new_name

    # Check folder rename
    if mode in [
        RenameMode.TORRENT_AND_FOLDER,
        RenameMode.TORRENT_FOLDER_FILES,
        RenameMode.FOLDER_ONLY,
    ]:
        if root_folder:
            response.new_root_folder = new_name
            response.folder_will_change = root_folder != new_name
        else:
            warnings.append("No root folder to rename (single file or flat structure)")

    # Check file renames
    if mode in [RenameMode.TORRENT_FOLDER_FILES, RenameMode.FILES_ONLY]:
        try:
            rename_plan, plan_warnings = validate_rename_plan(files, new_name, root_folder)
            warnings.extend(plan_warnings)

            file_renames = []
            files_changed = 0
            for old_path, new_path in rename_plan:
                will_change = old_path != new_path
                if will_change:
                    files_changed += 1
                file_renames.append(
                    FileRenamePreview(
                        old_path=old_path,
                        new_path=new_path,
                        will_change=will_change,
                    )
                )

            response.file_renames = file_renames
            response.files_will_change = files_changed

        except RenameConflictError as e:
            response.status = "error"
            response.reason = str(e)
            logger.warning(f"[preview] Conflict detected for {hash_short}...: {e}")

    response.warnings = warnings

    logger.info(
        f"[preview] Preview complete for {hash_short}...: "
        f"torrent_change={response.torrent_will_change}, "
        f"folder_change={response.folder_will_change}, "
        f"files_change={response.files_will_change}/{response.total_files}"
    )

    return response


@app.post("/find/torrent", response_model=FindTorrentResponse)
async def find_torrent_by_id(request: FindTorrentRequest):
    """Find a torrent by tracker ID from its comment.

    Accepts either a full URL (e.g., https://domain/torrents/342558)
    or just the ID number (e.g., 342558). Searches through all torrents
    in qBittorrent and matches the ID in the comment property.

    Args:
        request: FindTorrentRequest with torrent_id (URL or number)

    Returns:
        FindTorrentResponse with torrent hash if found
    """
    torrent_id_input = request.torrent_id.strip()
    logger.info(f"[find] Received request to find torrent with ID: {torrent_id_input}")

    # Extract ID from URL or use as-is if it's just a number
    torrent_id = None
    if torrent_id_input.startswith("http"):
        # Extract ID from URL pattern like https://domain/torrents/342558
        match = re.search(r"/torrents/(\d+)", torrent_id_input)
        if match:
            torrent_id = match.group(1)
        else:
            logger.warning(f"[find] Could not extract ID from URL: {torrent_id_input}")
            return FindTorrentResponse(
                status="error",
                torrent_id=torrent_id_input,
                reason="Invalid URL format. Expected pattern: .../torrents/ID",
            )
    else:
        # Assume it's just the ID number
        if torrent_id_input.isdigit():
            torrent_id = torrent_id_input
        else:
            logger.warning(f"[find] Invalid ID format (not a number): {torrent_id_input}")
            return FindTorrentResponse(
                status="error",
                torrent_id=torrent_id_input,
                reason="Invalid ID format. Expected a number or URL containing /torrents/ID",
            )

    if not torrent_id:
        return FindTorrentResponse(
            status="error",
            torrent_id=torrent_id_input,
            reason="Could not extract torrent ID from input",
        )

    # Search for torrent with matching ID in comment
    torrent = await asyncio.to_thread(qbit_client.find_torrent_by_comment_id, torrent_id)

    if torrent:
        torrent_hash = torrent.hash
        hash_short = torrent_hash[:8]
        logger.info(
            f"[find] Found torrent with ID {torrent_id}: hash={hash_short}... name='{torrent.name}'"
        )
        return FindTorrentResponse(
            status="found",
            torrent_id=torrent_id,
            torrent_hash=torrent_hash,
            reason=f"Match found, hash: {hash_short}...",
        )
    else:
        logger.info(f"[find] No torrent found with ID {torrent_id} in comments")
        return FindTorrentResponse(
            status="not_found",
            torrent_id=torrent_id,
            reason=f"No torrent found with ID {torrent_id} in comment property",
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
        await _record(
            source="radarr",
            event_type="Test",
            status="test",
            decision="n/a",
            media_title="Connection test",
        )
        return WebhookResponse(status="ok", reason="Test webhook received successfully")

    # For non-test events, validate with Pydantic model
    try:
        payload = RadarrWebhook(**raw_payload)
    except Exception as e:
        logger.error(f"[radarr] Payload validation failed: {e}")
        _log_payload_diagnostics("radarr", raw_payload)
        await _record(
            source="radarr",
            event_type=event_type,
            status="failed",
            decision="error",
            error="Invalid payload structure",
        )
        return JSONResponse(
            status_code=422,
            content={"status": "error", "reason": "Invalid payload structure"},
        )

    hash_short = payload.downloadId[:8] if payload.downloadId else "unknown"
    movie_title = payload.movie.title

    logger.info(
        f"[radarr] Received webhook: {payload.eventType} for '{movie_title}' ({hash_short}...)"
    )

    # Common fields recorded for every (non-test) Radarr webhook.
    base = {
        "source": "radarr",
        "event_type": payload.eventType,
        "media_title": movie_title,
        "release_title": payload.release.releaseTitle,
        "indexer": payload.release.indexer or "",
        "quality": payload.release.quality or "",
        "release_group": payload.release.releaseGroup or "",
        "download_client": payload.downloadClient or "",
        "torrent_hash": payload.downloadId or "",
    }

    # Check event type
    if payload.eventType != "Grab":
        logger.info(f"[radarr] Skipping event type '{payload.eventType}' (not Grab)")
        reason = f"event type '{payload.eventType}' not Grab"
        await _record(**base, status="skipped", decision="skipped", skip_reason=reason)
        return WebhookResponse(
            status="skipped",
            reason=reason,
            torrent_hash=payload.downloadId,
        )

    # Check download client type
    if payload.downloadClientType != "qBittorrent":
        logger.info(
            f"[radarr] Skipping download client '{payload.downloadClientType}' (not qBittorrent)"
        )
        reason = f"download client '{payload.downloadClientType}' not qBittorrent"
        await _record(**base, status="skipped", decision="skipped", skip_reason=reason)
        return WebhookResponse(
            status="skipped",
            reason=reason,
            torrent_hash=payload.downloadId,
        )

    # Get indexer and resolve appropriate rules (tracker-specific or global)
    indexer = payload.release.indexer or ""
    effective_rules, tracker_name = config.rules.get_rules_for_indexer(indexer)
    rules_info = f"tracker '{tracker_name}'" if tracker_name else "global"
    logger.debug(f"[radarr] Using {rules_info} rules for indexer '{indexer}'")

    new_name, steps, checks = _decision_detail(
        effective_rules,
        release_title=payload.release.releaseTitle,
        indexer=indexer,
        quality=payload.release.quality or "",
        release_group=payload.release.releaseGroup or "",
        custom_formats=payload.release.customFormats or [],
        custom_format_score=payload.release.customFormatScore,
        download_client=payload.downloadClient or "",
    )
    detail = {
        "tracker_name": tracker_name,
        "used_global": tracker_name is None,
        "rename_mode": settings.rename_mode,
        "dry_run": settings.dry_run,
        "new_name": new_name,
        "rule_steps_json": steps,
        "trigger_checks_json": checks,
    }

    # Apply trigger filters using the effective rules
    should_proc, skip_reason = should_process(payload, effective_rules)
    if not should_proc:
        logger.info(f"[radarr] Skipping {hash_short}...: {skip_reason}")
        await _record(
            **base, **detail, status="skipped", decision="skipped", skip_reason=skip_reason
        )
        return WebhookResponse(
            status="skipped",
            reason=skip_reason,
            torrent_hash=payload.downloadId,
        )

    op_id = await _record(**base, **detail, status="queued", decision="processed")

    # Queue background task with effective rules
    background_tasks.add_task(
        process_rename_task,
        torrent_hash=payload.downloadId,
        release_title=payload.release.releaseTitle,
        source="radarr",
        media_title=movie_title,
        effective_rules=effective_rules,
        tracker_name=tracker_name,
        op_id=op_id,
    )

    logger.info(f"[radarr] Queued rename for '{movie_title}' ({hash_short}...) using {rules_info}")
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
        await _record(
            source="sonarr",
            event_type="Test",
            status="test",
            decision="n/a",
            media_title="Connection test",
        )
        return WebhookResponse(status="ok", reason="Test webhook received successfully")

    # For non-test events, validate with Pydantic model
    try:
        payload = SonarrWebhook(**raw_payload)
    except Exception as e:
        logger.error(f"[sonarr] Payload validation failed: {e}")
        _log_payload_diagnostics("sonarr", raw_payload)
        await _record(
            source="sonarr",
            event_type=event_type,
            status="failed",
            decision="error",
            error="Invalid payload structure",
        )
        return JSONResponse(
            status_code=422,
            content={"status": "error", "reason": "Invalid payload structure"},
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

    media_title = f"{series_title} {episode_info}".strip()
    base = {
        "source": "sonarr",
        "event_type": payload.eventType,
        "media_title": media_title,
        "release_title": payload.get_release_title(),
        "indexer": payload.release.indexer or "",
        "quality": payload.release.quality or "",
        "release_group": payload.release.releaseGroup or "",
        "download_client": payload.downloadClient or "",
        "torrent_hash": download_id or "",
    }

    # Check event type
    if payload.eventType != "Grab":
        logger.info(f"[sonarr] Skipping event type '{payload.eventType}' (not Grab)")
        reason = f"event type '{payload.eventType}' not Grab"
        await _record(**base, status="skipped", decision="skipped", skip_reason=reason)
        return WebhookResponse(
            status="skipped",
            reason=reason,
            torrent_hash=download_id,
        )

    # Check download client type
    if payload.downloadClientType != "qBittorrent":
        logger.info(
            f"[sonarr] Skipping download client '{payload.downloadClientType}' (not qBittorrent)"
        )
        reason = f"download client '{payload.downloadClientType}' not qBittorrent"
        await _record(**base, status="skipped", decision="skipped", skip_reason=reason)
        return WebhookResponse(
            status="skipped",
            reason=reason,
            torrent_hash=download_id,
        )

    # Check we have download ID
    if not download_id:
        logger.warning("[sonarr] No downloadId found in payload")
        await _record(
            **base, status="skipped", decision="skipped", skip_reason="no downloadId in payload"
        )
        return WebhookResponse(
            status="skipped",
            reason="no downloadId in payload",
        )

    # Get indexer and resolve appropriate rules (tracker-specific or global)
    indexer = payload.release.indexer or ""
    effective_rules, tracker_name = config.rules.get_rules_for_indexer(indexer)
    rules_info = f"tracker '{tracker_name}'" if tracker_name else "global"
    logger.debug(f"[sonarr] Using {rules_info} rules for indexer '{indexer}'")

    # Get release title
    release_title = payload.get_release_title()

    new_name, steps, checks = _decision_detail(
        effective_rules,
        release_title=release_title,
        indexer=indexer,
        quality=payload.release.quality or "",
        release_group=payload.release.releaseGroup or "",
        custom_formats=payload.release.customFormats or [],
        custom_format_score=payload.release.customFormatScore,
        download_client=payload.downloadClient or "",
    )
    detail = {
        "tracker_name": tracker_name,
        "used_global": tracker_name is None,
        "rename_mode": settings.rename_mode,
        "dry_run": settings.dry_run,
        "new_name": new_name,
        "rule_steps_json": steps,
        "trigger_checks_json": checks,
    }

    # Apply trigger filters using the effective rules
    should_proc, skip_reason = should_process(payload, effective_rules)
    if not should_proc:
        logger.info(f"[sonarr] Skipping {hash_short}...: {skip_reason}")
        await _record(
            **base, **detail, status="skipped", decision="skipped", skip_reason=skip_reason
        )
        return WebhookResponse(
            status="skipped",
            reason=skip_reason,
            torrent_hash=download_id,
        )

    op_id = await _record(**base, **detail, status="queued", decision="processed")

    # Queue background task with effective rules
    background_tasks.add_task(
        process_rename_task,
        torrent_hash=download_id,
        release_title=release_title,
        source="sonarr",
        media_title=media_title,
        effective_rules=effective_rules,
        tracker_name=tracker_name,
        op_id=op_id,
    )

    logger.info(
        f"[sonarr] Queued rename for '{series_title}' {episode_info} ({hash_short}...) "
        f"using {rules_info}"
    )
    return WebhookResponse(
        status="queued",
        torrent_hash=download_id,
    )


@app.post("/webhook/prowlarr", response_model=WebhookResponse)
async def prowlarr_webhook(request: Request):
    """Handle Prowlarr webhook for testing and debugging.

    This endpoint accepts any JSON payload from Prowlarr and logs the complete
    structure for debugging purposes. Unlike Sonarr/Radarr endpoints, this does
    not perform any renaming operations - it's purely for payload inspection.

    Configure in Prowlarr: Settings -> Connect -> Webhook
    - URL: http://groomarr:8000/webhook/prowlarr
    - Events: Any events you want to test
    """
    # Parse raw JSON - accept any valid JSON payload
    try:
        raw_payload = await request.json()
    except Exception as e:
        logger.error(f"[prowlarr] Failed to parse JSON payload: {e}")
        return WebhookResponse(status="error", reason="Invalid JSON payload")

    # Log the complete payload structure for debugging
    _log_full_payload("prowlarr", raw_payload)

    # Extract event type if available
    event_type = raw_payload.get("eventType", raw_payload.get("event", "unknown"))
    logger.info(f"[prowlarr] Webhook received successfully (event type: {event_type})")

    await _record(
        source="prowlarr",
        event_type=str(event_type),
        status="received",
        decision="n/a",
        media_title="Prowlarr webhook (debug)",
    )

    return WebhookResponse(
        status="ok",
        reason=f"Payload received and logged for debugging (event type: {event_type})",
    )


# =============================================================================
# Error Handlers
# =============================================================================


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler.

    Logs the full detail server-side but returns a generic message to the client
    so internal details (paths, stack info) are never leaked.
    """
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"status": "error", "reason": "Internal server error"},
    )


# =============================================================================
# Single-Page App (static frontend)
# =============================================================================

# The built frontend (Vite output) is served from settings.static_dir. In Docker
# this directory is populated by the frontend build stage. When it is absent
# (e.g. running the API alone), the UI routes return a helpful 404.
_STATIC_DIR = Path(settings.static_dir)
_ASSETS_DIR = _STATIC_DIR / "assets"
_INDEX_FILE = _STATIC_DIR / "index.html"

# Path prefixes that must never be served the SPA shell (API surfaces return JSON)
_NON_SPA_PREFIXES = ("api/", "webhook/", "rename/", "find/")
_NON_SPA_EXACT = {"health", "reload", "openapi.json", "docs", "redoc"}

if _ASSETS_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS_DIR)), name="assets")


def _serve_index() -> FileResponse | JSONResponse:
    """Serve the SPA shell, or a clear message if the UI has not been built."""
    if _INDEX_FILE.is_file():
        return FileResponse(str(_INDEX_FILE), headers={"Cache-Control": "no-cache"})
    return JSONResponse(
        status_code=404,
        content={
            "status": "error",
            "reason": "Web UI not built. Build the frontend (npm run build) or use the API.",
        },
    )


@app.get("/", include_in_schema=False)
async def spa_root():
    """Serve the SPA entry point."""
    return _serve_index()


@app.get("/{full_path:path}", include_in_schema=False)
async def spa_catch_all(full_path: str):
    """Serve the SPA for client-side routes; JSON 404 for unknown API paths.

    Declared last so all explicit API/webhook/docs routes match first.
    """
    if full_path.startswith(_NON_SPA_PREFIXES) or full_path in _NON_SPA_EXACT:
        return JSONResponse(status_code=404, content={"status": "error", "reason": "Not found"})
    return _serve_index()


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
