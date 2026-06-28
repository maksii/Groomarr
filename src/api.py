"""Web UI / management API for Groomarr.

These endpoints back the single-page app: reading and writing the rename rules,
simulating how a release would be filtered and renamed, validating patterns, and
reporting status. They are namespaced under ``/api`` and are independent of the
Sonarr/Radarr webhook endpoints (which are unchanged).

Security notes:
- The service ships with NO authentication. Run it on a trusted network or
  behind an authenticating reverse proxy. The UI surfaces this prominently.
- The rules file path is fixed server-side (``settings.rules_file``); clients
  never control where data is written (no path traversal).
- Simulation executes user-supplied regular expressions. CPython's ``re`` holds
  the GIL during a match, so evaluation runs in a separate worker process that is
  force-killed on timeout (and inputs are bounded) — keeping the event loop
  responsive even against catastrophic-backtracking (ReDoS) patterns.
"""

import asyncio
import logging
import multiprocessing as mp
import re
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool

from . import __version__, config
from .config import TrackerRules
from .models import (
    ConfigMeta,
    RulesConfig,
    RuleSet,
    SettingsView,
    SimulateRequest,
    SimulateResponse,
    StatusView,
    TrackerConfigModel,
    ValidatePatternRequest,
    ValidatePatternResponse,
)
from .simulate_worker import _worker

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["web-ui"])

# Bounds for the simulation endpoint (defense-in-depth against ReDoS / abuse)
SIMULATE_TIMEOUT_S = 2.0
MAX_SIMULATE_TRACKERS = 200


async def check_connection_async(client) -> bool | None:
    """Run a client's blocking check_connection() off the event loop.

    Returns None when no client is configured, otherwise a bool. Never raises.
    Shared by /api/status and /health so neither blocks the event loop.
    """
    if client is None:
        return None
    try:
        # Hard cap so a host that accepts TCP but stalls on the API can't hang us.
        return await asyncio.wait_for(run_in_threadpool(client.check_connection), timeout=10.0)
    except Exception as e:  # noqa: BLE001 - connectivity check must never raise
        logger.warning(f"Connection check failed: {e}")
        return False


# =============================================================================
# Serialization helpers
# =============================================================================


def _ruleset_to_model(tr: TrackerRules) -> RuleSet:
    """Convert an engine TrackerRules into a RuleSet model WITHOUT validation.

    ``model_construct`` is used so that a hand-edited config containing an invalid
    regex still loads in the UI (where the user can fix it) instead of failing.
    """
    return RuleSet.model_construct(
        indexers_include=list(tr.indexers_include),
        indexers_exclude=list(tr.indexers_exclude),
        qualities_include=list(tr.qualities_include),
        qualities_exclude=list(tr.qualities_exclude),
        customformats_require_any=list(tr.customformats_require_any),
        customformats_exclude=list(tr.customformats_exclude),
        min_customformat_score=tr.min_customformat_score,
        download_clients_include=list(tr.download_clients_include),
        download_clients_exclude=list(tr.download_clients_exclude),
        release_groups_include=list(tr.release_groups_include),
        release_groups_exclude=list(tr.release_groups_exclude),
        prefix=tr.prefix,
        suffix=tr.suffix,
        remove_patterns=list(tr.remove_patterns),
        replace_patterns=dict(tr.replace_patterns),
        skip_title_patterns=list(tr.skip_title_patterns),
        validate_custom_format_score=tr.validate_custom_format_score,
        score_validation_policy=tr.score_validation_policy,
    )


def _config_response_dict() -> dict:
    """Build the GET /api/config payload from the live rules (read-safe)."""
    rr = config.rules
    cfg = RulesConfig.model_construct(
        global_=_ruleset_to_model(rr.global_rules),
        trackers=[
            TrackerConfigModel.model_construct(
                name=t.name,
                match=list(t.match),
                rules=_ruleset_to_model(t.rules),
            )
            for t in rr.trackers
        ],
    )
    meta = ConfigMeta(
        config_path=rr.config_path or config.settings.rules_file,
        config_found=rr.config_found,
        config_error=rr.config_error,
        config_format=rr.config_format,  # type: ignore[arg-type]
        readonly=config.settings.config_readonly,
    )
    return {
        "meta": meta.model_dump(mode="json"),
        "config": cfg.model_dump(by_alias=True, mode="json"),
    }


# =============================================================================
# Config endpoints
# =============================================================================


@router.get("/config")
async def get_config() -> dict:
    """Return the current rename rules and metadata about the config file."""
    return _config_response_dict()


@router.put("/config")
async def put_config(new_config: RulesConfig) -> dict:
    """Validate, save, and reload the rename rules.

    The request body is validated by Pydantic (invalid regex -> HTTP 422 with the
    offending field), written atomically with a ``.bak`` backup, then reloaded.
    """
    if config.settings.config_readonly:
        raise HTTPException(
            status_code=403,
            detail="Configuration is read-only (config_readonly is enabled).",
        )

    warnings: list[str] = []
    for idx, tracker in enumerate(new_config.trackers):
        label = tracker.name or f"#{idx + 1}"
        if not tracker.name:
            warnings.append(f"Tracker {label} has no name.")
        if not tracker.match:
            warnings.append(
                f"Tracker '{label}' has no match patterns and will be ignored at runtime."
            )

    prior_format = config.rules.config_format
    if prior_format == "flat":
        warnings.append(
            "Saved in the hierarchical format; the previous flat-format file was backed up "
            "to a .bak file."
        )

    # Convert validated request -> engine object -> minimal YAML dict
    data = new_config.model_dump(by_alias=True)
    rules_obj = config.RenameRules.from_dict(data)
    out = rules_obj.to_dict()

    try:
        await run_in_threadpool(config.save_rules, out)
    except OSError as e:
        logger.error(f"Failed to save rules file: {e}")
        raise HTTPException(status_code=500, detail="Failed to write the rules file.") from e

    await run_in_threadpool(config.reload_rules)

    response = _config_response_dict()
    response["status"] = "ok"
    response["warnings"] = warnings
    return response


# =============================================================================
# Simulation / preview endpoints
# =============================================================================


# A "spawn" context: clean child interpreters we can force-kill, independent of
# the (threaded) parent process.
_MP_CONTEXT = mp.get_context("spawn")


def _run_simulation_isolated(
    rules_dict: dict, release: dict, timeout: float
) -> tuple[str, dict | None]:
    """Run a simulation in a separate process, killing it if it exceeds ``timeout``.

    CPython's ``re`` holds the GIL for the duration of a match, so a thread +
    timeout cannot interrupt a catastrophic-backtracking pattern — only a separate
    process can be terminated. Returns ``(status, payload)`` where status is
    ``"ok"`` | ``"err"`` | ``"timeout"``. Blocking — call via run_in_threadpool.
    Falls back to in-process execution only if a worker cannot be started.
    """
    parent_conn, child_conn = _MP_CONTEXT.Pipe(duplex=False)
    proc = _MP_CONTEXT.Process(target=_worker, args=(child_conn, rules_dict, release), daemon=True)
    try:
        proc.start()
    except Exception as e:  # pragma: no cover - process creation unavailable
        # Degrade safely: never run untrusted regex in-thread (that could hang the
        # event loop on a ReDoS pattern). Report the simulation as unavailable.
        logger.error(f"Could not start simulation worker process: {e}")
        parent_conn.close()
        child_conn.close()
        return "err", None

    child_conn.close()  # only the worker writes
    try:
        if parent_conn.poll(timeout):
            status, payload = parent_conn.recv()
            return status, payload
        proc.terminate()  # kill a runaway regex (ReDoS)
        return "timeout", None
    except (EOFError, OSError):
        return "err", None
    finally:
        parent_conn.close()
        proc.join(timeout=1)
        if proc.is_alive():
            proc.kill()
            proc.join(timeout=1)


@router.post("/rules/simulate", response_model=SimulateResponse)
async def simulate_rules(req: SimulateRequest) -> SimulateResponse:
    """Simulate how a sample release would be filtered and renamed.

    Uses the *exact* production engine so the preview matches real behavior. If
    ``config`` is provided the draft (unsaved) rules are used; otherwise the saved
    rules are used.

    User patterns run against user-supplied text, so this is a ReDoS surface. The
    work runs in a separate process that is force-killed after SIMULATE_TIMEOUT_S,
    keeping the event loop responsive even against catastrophic patterns.
    """
    if req.config is not None:
        if len(req.config.trackers) > MAX_SIMULATE_TRACKERS:
            raise HTTPException(
                status_code=422,
                detail=f"Too many trackers to simulate (max {MAX_SIMULATE_TRACKERS}).",
            )
        rules_dict = req.config.model_dump(by_alias=True)
    else:
        rules_dict = config.rules.to_dict()

    release_dict = req.release.model_dump()
    title = req.release.release_title

    try:
        status, payload = await run_in_threadpool(
            _run_simulation_isolated, rules_dict, release_dict, SIMULATE_TIMEOUT_S
        )
    except Exception as e:  # noqa: BLE001 - never leak internals to the client
        logger.warning(f"Rule simulation failed: {e}")
        return SimulateResponse(
            status="error",
            original_title=title,
            errors=["Simulation failed. Check your patterns and try again."],
        )

    if status == "ok" and payload is not None:
        return SimulateResponse(**payload)
    if status == "timeout":
        return SimulateResponse(
            status="error",
            original_title=title,
            errors=[
                "Rule evaluation timed out — a pattern may have catastrophic backtracking "
                "(ReDoS). Simplify the regex."
            ],
        )
    return SimulateResponse(
        status="error",
        original_title=title,
        errors=["Simulation failed. Check your patterns and try again."],
    )


@router.post("/rules/validate-pattern", response_model=ValidatePatternResponse)
async def validate_pattern(req: ValidatePatternRequest) -> ValidatePatternResponse:
    """Validate a single regex pattern or indexer match pattern.

    Compilation only (no matching), so this is cheap and not subject to ReDoS.
    """
    pattern = req.pattern

    if req.kind == "regex":
        try:
            re.compile(pattern)
            return ValidatePatternResponse(valid=True, kind="regex")
        except re.error as e:
            return ValidatePatternResponse(valid=False, kind="regex", error=str(e))

    # kind == "match" — mirror config.matches_indexer interpretation
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        try:
            re.compile(pattern[1:-1])
            return ValidatePatternResponse(valid=True, kind="match", interpreted="regex")
        except re.error as e:
            return ValidatePatternResponse(
                valid=False, kind="match", interpreted="regex", error=str(e)
            )
    if "*" in pattern or "?" in pattern:
        return ValidatePatternResponse(valid=True, kind="match", interpreted="wildcard")
    return ValidatePatternResponse(valid=True, kind="match", interpreted="exact")


# =============================================================================
# Settings & status endpoints
# =============================================================================


def _strip_userinfo(url: str) -> str:
    """Remove any embedded credentials (user:pass@) from a URL before exposing it."""
    try:
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            return urlunparse(parsed._replace(netloc=netloc))
    except ValueError:
        return ""
    return url


@router.get("/settings", response_model=SettingsView)
async def get_settings() -> SettingsView:
    """Return non-sensitive runtime settings. Never returns secrets."""
    s = config.settings
    return SettingsView(
        rename_mode=s.rename_mode,
        dry_run=s.dry_run,
        initial_delay=s.initial_delay,
        max_retries=s.max_retries,
        retry_delay=s.retry_delay,
        api_operation_delay_ms=s.api_operation_delay_ms,
        log_level=s.log_level,
        log_format=s.log_format,
        rules_file=s.rules_file,
        config_readonly=s.config_readonly,
        qbittorrent_url=_strip_userinfo(s.qbittorrent_url),
        sonarr_configured=bool(s.sonarr_url and s.sonarr_api_key),
        radarr_configured=bool(s.radarr_url and s.radarr_api_key),
    )


@router.get("/status", response_model=StatusView)
async def get_status() -> StatusView:
    """Return service status + connectivity (blocking checks run off the loop)."""
    from . import main  # lazy import to avoid a circular import at module load

    qbit = main.qbit_client
    sonarr = main.sonarr_client
    radarr = main.radarr_client

    # Run all connectivity checks in parallel, off the event loop.
    qbit_res, sonarr_res, radarr_res = await asyncio.gather(
        check_connection_async(qbit),
        check_connection_async(sonarr),
        check_connection_async(radarr),
    )
    qbit_connected = bool(qbit_res)
    sonarr_status = None if sonarr is None else ("connected" if sonarr_res else "disconnected")
    radarr_status = None if radarr is None else ("connected" if radarr_res else "disconnected")

    rr = config.rules
    return StatusView(
        status="ok" if qbit_connected else "degraded",
        version=__version__,
        qbittorrent="connected" if qbit_connected else "disconnected",
        sonarr=sonarr_status,
        radarr=radarr_status,
        dry_run=config.settings.dry_run,
        score_validation=rr.validate_custom_format_score,
        config_found=rr.config_found,
        config_error=rr.config_error,
        readonly=config.settings.config_readonly,
    )
