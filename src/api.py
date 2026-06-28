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
import json
import logging
import multiprocessing as mp
import re
from datetime import UTC, datetime
from urllib.parse import urlparse, urlunparse

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from . import __version__, config, history
from .config import TrackerRules, regex_match_body
from .models import (
    ConfigMeta,
    LiveTorrentState,
    OperationDetail,
    OperationListResponse,
    OperationStats,
    OperationSummary,
    RollbackPreviewResponse,
    RollbackResponse,
    RollbackSkip,
    RollbackStepView,
    RulesConfig,
    RuleSet,
    SettingsView,
    SimulateRequest,
    SimulateResponse,
    StatusView,
    TorrentSampleRequest,
    TorrentSampleResponse,
    TrackerConfigModel,
    ValidatePatternRequest,
    ValidatePatternResponse,
)
from .rename import get_root_folder
from .rollback import RollbackPlan, build_rollback_plan, perform_rollback
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
    body = regex_match_body(pattern)
    if body is not None:
        try:
            re.compile(body)
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


@router.post("/torrent-sample", response_model=TorrentSampleResponse)
async def torrent_sample(req: TorrentSampleRequest) -> TorrentSampleResponse:
    """Load a real torrent's name + files to populate the preview's sample release.

    Accepts a torrent hash (40 hex chars) or a tracker ID / URL (resolved via the
    torrent's comment, like /find/torrent). Read-only.
    """
    from . import main  # lazy import to avoid a circular import at module load

    qbit = main.qbit_client
    if qbit is None:
        return TorrentSampleResponse(status="error", reason="qBittorrent is not connected.")

    query = req.query.strip()
    thash: str | None = None
    if re.fullmatch(r"[0-9a-fA-F]{40}", query):
        thash = query
    else:
        match = re.search(r"/torrents/(\d+)", query)
        tracker_id = match.group(1) if match else (query if query.isdigit() else None)
        if tracker_id:
            found = await run_in_threadpool(qbit.find_torrent_by_comment_id, tracker_id)
            if found is not None:
                thash = found.hash

    if not thash:
        return TorrentSampleResponse(
            status="not_found", reason="No torrent found for that hash or tracker ID."
        )

    torrent = await run_in_threadpool(qbit.get_torrent_info, thash)
    if not torrent:
        return TorrentSampleResponse(status="not_found", reason="Torrent not found.")

    files = await run_in_threadpool(qbit.get_files, thash)
    return TorrentSampleResponse(
        status="ok",
        title=torrent.get("name", "") or "",
        files=[f.get("name", "") for f in files if f.get("name")],
    )


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


# =============================================================================
# Operation history (dashboard) endpoints
# =============================================================================


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_json(value, default):
    """Decode a JSON column, falling back to ``default`` on any error."""
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _extract_applied(
    row: dict,
) -> tuple[tuple[str, str] | None, tuple[str, str] | None, list[tuple[str, str]]]:
    """Pull the executed forward plan out of a record's ``applied_json``."""
    applied = _parse_json(row.get("applied_json"), {}) or {}
    t = applied.get("torrent")
    f = applied.get("folder")
    files = applied.get("files") or []
    applied_torrent = (t[0], t[1]) if isinstance(t, list) and len(t) == 2 else None
    applied_folder = (f[0], f[1]) if isinstance(f, list) and len(f) == 2 else None
    applied_files = [(p[0], p[1]) for p in files if isinstance(p, list) and len(p) == 2]
    return applied_torrent, applied_folder, applied_files


async def _live_state(torrent_hash: str) -> LiveTorrentState:
    """Fetch the torrent's CURRENT state from qBittorrent (full file list, no cap)."""
    from . import main  # lazy import to avoid a circular import at module load

    qbit = main.qbit_client
    if qbit is None:
        return LiveTorrentState(checked=False, note="qBittorrent is not connected.")
    if not torrent_hash:
        return LiveTorrentState(
            checked=True, torrent_exists=False, note="No torrent hash recorded."
        )

    torrent = await run_in_threadpool(qbit.get_torrent_info, torrent_hash)
    if not torrent:
        return LiveTorrentState(
            checked=True, torrent_exists=False, note="Torrent is no longer in qBittorrent."
        )
    files = await run_in_threadpool(qbit.get_files, torrent_hash)
    names = [f.get("name", "") for f in files if f.get("name")]
    root = get_root_folder(files)
    name = torrent.get("name", "") if hasattr(torrent, "get") else getattr(torrent, "name", "")
    return LiveTorrentState(
        checked=True,
        torrent_exists=True,
        torrent_name=name or None,
        root_folder=root,
        files=names,
    )


@router.get("/operations", response_model=OperationListResponse)
async def list_operations(
    q: str | None = None,
    source: str | None = None,
    status: str | None = None,
    decision: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> OperationListResponse:
    """Search / filter / paginate the operations log (newest first)."""
    page = await run_in_threadpool(
        history.list_operations,
        q=q,
        source=source,
        status=status,
        decision=decision,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return OperationListResponse(
        items=[OperationSummary(**row) for row in page["items"]],
        total=page["total"],
        limit=page["limit"],
        offset=page["offset"],
    )


@router.get("/operations/stats", response_model=OperationStats)
async def operations_stats() -> OperationStats:
    """KPI aggregates for the dashboard header."""
    data = await run_in_threadpool(history.stats)
    return OperationStats(**data)


@router.get("/operations/{op_id}", response_model=OperationDetail)
async def get_operation(op_id: int) -> OperationDetail:
    """Full record for one operation: decision, rule trace, rename plan + LIVE state."""
    row = await run_in_threadpool(history.get_operation, op_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Operation not found.")

    live = await _live_state(row.get("torrent_hash") or "")

    # Does the torrent still carry the name Groomarr gave it?
    if live.torrent_exists and row.get("new_name"):
        live.matches_rename = live.torrent_name == row["new_name"]
    # Truncate the live file list for the response (rollback uses the full list).
    if len(live.files) > 500:
        live.files = live.files[:500]
        live.note = (live.note + " (file list truncated to 500)").strip()

    applied_t, applied_f, applied_files = _extract_applied(row)
    has_applied = bool(applied_t or applied_f or applied_files)
    if bool(row.get("rolled_back")):
        can_rollback, reason = False, "This operation has already been rolled back."
    elif row.get("status") != "renamed" or not has_applied:
        can_rollback, reason = False, "There is no executed rename to roll back."
    elif live.checked and not live.torrent_exists:
        can_rollback, reason = False, "The torrent is no longer in qBittorrent."
    elif not live.checked:
        can_rollback, reason = False, "qBittorrent is not connected."
    else:
        can_rollback, reason = True, ""

    detail = OperationDetail(
        **row,
        rule_steps=_parse_json(row.get("rule_steps_json"), []),
        trigger_checks=_parse_json(row.get("trigger_checks_json"), []),
        file_changes=_parse_json(row.get("file_plan_json"), []),
        live=live,
        can_rollback=can_rollback,
        rollback_unavailable_reason=reason,
    )
    return detail


def _plan_to_preview(op_id: int, plan: RollbackPlan) -> RollbackPreviewResponse:
    """Serialize a RollbackPlan into the preview response model."""

    def step(s):
        return RollbackStepView(kind=s.kind, frm=s.frm, to=s.to) if s else None

    return RollbackPreviewResponse(
        status="ok" if plan.can_rollback else "unavailable",
        operation_id=op_id,
        torrent_exists=plan.torrent_exists,
        can_rollback=plan.can_rollback,
        reason=plan.reason,
        torrent_step=step(plan.torrent_step),
        folder_step=step(plan.folder_step),
        file_steps=[RollbackStepView(kind=s.kind, frm=s.frm, to=s.to) for s in plan.file_steps],
        skipped=[RollbackSkip(**s) for s in plan.skipped],
        warnings=plan.warnings,
    )


async def _plan_rollback(row: dict) -> RollbackPlan:
    """Build a validated rollback plan for a recorded operation against live state."""
    live = await _live_state(row.get("torrent_hash") or "")
    applied_t, applied_f, applied_files = _extract_applied(row)
    return build_rollback_plan(
        torrent_hash=row.get("torrent_hash") or "",
        applied_torrent=applied_t,
        applied_folder=applied_f,
        applied_files=applied_files,
        live_torrent_name=live.torrent_name if live.torrent_exists else None,
        live_files=live.files,
        live_root_folder=live.root_folder,
    )


@router.post("/operations/{op_id}/rollback/preview", response_model=RollbackPreviewResponse)
async def rollback_preview(op_id: int) -> RollbackPreviewResponse:
    """Compute what a rollback would do, validated against current live state. Read-only."""
    from . import main

    row = await run_in_threadpool(history.get_operation, op_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    if bool(row.get("rolled_back")):
        return RollbackPreviewResponse(
            status="unavailable",
            operation_id=op_id,
            can_rollback=False,
            reason="This operation has already been rolled back.",
        )
    if main.qbit_client is None:
        return RollbackPreviewResponse(
            status="error", operation_id=op_id, reason="qBittorrent is not connected."
        )
    plan = await _plan_rollback(row)
    return _plan_to_preview(op_id, plan)


@router.post("/operations/{op_id}/rollback", response_model=RollbackResponse)
async def rollback_operation(op_id: int) -> RollbackResponse:
    """Safely reverse a recorded rename, restoring the original names.

    Validated against live state and the data-loss safety gate (see
    :mod:`src.rollback`). Records the rollback as its own operation and links it
    back to the original. Available whenever the manual-rename tool is (the
    service's existing no-auth, network-trust posture).
    """
    from . import main

    row = await run_in_threadpool(history.get_operation, op_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Operation not found.")
    if bool(row.get("rolled_back")):
        return RollbackResponse(
            status="unavailable",
            operation_id=op_id,
            reason="This operation has already been rolled back.",
        )
    qbit = main.qbit_client
    if qbit is None:
        return RollbackResponse(
            status="error", operation_id=op_id, reason="qBittorrent is not connected."
        )

    plan = await _plan_rollback(row)
    files_skipped = sum(1 for s in plan.skipped if s.get("kind") == "file")
    if not plan.can_rollback:
        return RollbackResponse(
            status="unavailable" if plan.torrent_exists else "error",
            operation_id=op_id,
            reason=plan.reason,
            files_skipped=files_skipped,
        )

    result = await perform_rollback(qbit, plan)
    reverted_any = result.torrent_reverted or result.folder_reverted or result.files_reverted > 0
    if result.success and reverted_any:
        status = "success"
    elif reverted_any:
        status = "partial"
    else:
        status = "error"

    # Record the rollback as its own operation, linked to the original.
    rb_applied = {
        "torrent": [plan.torrent_step.frm, plan.torrent_step.to] if plan.torrent_step else None,
        "folder": [plan.folder_step.frm, plan.folder_step.to] if plan.folder_step else None,
        "files": [[s.frm, s.to] for s in plan.file_steps],
    }
    rb_file_plan = [{"old_path": s.frm, "new_path": s.to, "changed": True} for s in plan.file_steps]
    rb_id = await run_in_threadpool(
        history.record_operation,
        source="manual",
        event_type="rollback",
        status="rolled_back" if status == "success" else "failed",
        decision="processed",
        rollback_of=op_id,
        torrent_hash=row.get("torrent_hash") or "",
        media_title=f"Rollback of #{op_id}: {row.get('media_title') or ''}".strip(),
        old_name=plan.torrent_step.frm if plan.torrent_step else row.get("new_name") or "",
        new_name=plan.torrent_step.to if plan.torrent_step else row.get("old_name") or "",
        folder_old=plan.folder_step.frm if plan.folder_step else None,
        folder_new=plan.folder_step.to if plan.folder_step else None,
        applied_json=rb_applied,
        file_plan_json=rb_file_plan,
        files_renamed=result.files_reverted,
        files_failed=result.files_failed,
        files_skipped=result.files_skipped,
        error="; ".join(result.errors) if result.errors else "",
    )

    # Mark the original: fully reverted -> flagged + status flips so the dashboard
    # shows it as rolled back; a partial revert stays rollback-able for a retry.
    update_fields = {"rollback_op": rb_id}
    if status == "success":
        update_fields.update(rolled_back=True, rolled_back_at=_now_iso(), status="rolled_back")
    await run_in_threadpool(history.update_operation, op_id, **update_fields)

    return RollbackResponse(
        status=status,
        operation_id=op_id,
        rollback_operation_id=rb_id,
        torrent_reverted=result.torrent_reverted,
        folder_reverted=result.folder_reverted,
        files_reverted=result.files_reverted,
        files_failed=result.files_failed,
        files_skipped=result.files_skipped,
        steps=result.steps,
        errors=result.errors,
        reason="" if status == "success" else "; ".join(result.errors) or plan.reason,
    )
