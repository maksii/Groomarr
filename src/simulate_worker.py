"""Out-of-process rule simulation.

The rename engine uses Python's ``re`` module, which holds the GIL for the entire
duration of a match. A catastrophic-backtracking pattern (ReDoS) therefore cannot
be interrupted by a thread + timeout — it freezes the whole event loop. Running the
simulation in a separate process lets us force-kill it on timeout, keeping the
service responsive while still using the *exact* production engine (so the preview
matches real behavior).

This module is deliberately lean (no FastAPI imports) so it re-imports quickly
under the ``spawn`` start method.
"""

from __future__ import annotations

from typing import Any

from .config import RenameRules
from .rename import (
    RenameConflictError,
    apply_rename_rules_traced,
    evaluate_filters,
    explain_filters,
    get_root_folder,
    validate_rename_plan,
)


def run_simulation(rules_dict: dict, release: dict) -> dict[str, Any]:
    """Resolve rules, evaluate trigger filters, trace the rename, and preview files.

    Args:
        rules_dict: Hierarchical rules dict (``{'global': ..., 'trackers': [...]}``).
        release: Sample release fields (release_title, indexer, quality, files, ...).

    Returns:
        A plain dict matching the SimulateResponse fields (picklable).
    """
    rules_obj = RenameRules.from_dict(rules_dict)
    indexer = release.get("indexer") or ""
    effective_rules, tracker_name = rules_obj.get_rules_for_indexer(indexer)

    filter_kwargs = {
        "indexer": indexer,
        "quality": release.get("quality") or "",
        "release_group": release.get("release_group") or "",
        "custom_formats": release.get("custom_formats") or [],
        "custom_format_score": release.get("custom_format_score"),
        "download_client": release.get("download_client") or "",
    }
    would_process, skip_reason = evaluate_filters(effective_rules, **filter_kwargs)
    trigger_checks = explain_filters(effective_rules, **filter_kwargs)

    title = release.get("release_title") or ""
    new_title, steps = apply_rename_rules_traced(title, effective_rules)
    errors = [s["error"] for s in steps if s.get("error")]

    # Preview how sample files would be renamed (uses the new title as the base,
    # preserving episode identifiers — exactly like the real rename).
    file_renames: list[dict] = []
    file_warnings: list[str] = []
    root_folder = None
    files = [f for f in (release.get("files") or []) if isinstance(f, str) and f]
    if files:
        file_dicts = [{"name": f} for f in files]
        root_folder = get_root_folder(file_dicts)
        try:
            plan, warnings = validate_rename_plan(file_dicts, new_title, root_folder)
            file_warnings = warnings
            file_renames = [
                {"old_path": old, "new_path": new, "will_change": old != new} for old, new in plan
            ]
        except RenameConflictError as e:
            file_warnings = [str(e)]

    return {
        "matched_tracker": tracker_name,
        "used_global": tracker_name is None,
        "would_process": would_process,
        "skip_reason": skip_reason,
        "original_title": title,
        "new_title": new_title,
        "changed": new_title != title,
        "steps": steps,
        "errors": errors,
        "trigger_checks": trigger_checks,
        "file_renames": file_renames,
        "file_warnings": file_warnings,
        "root_folder": root_folder,
    }


def _worker(conn, rules_dict: dict, release: dict) -> None:
    """Process entry point: run the simulation and send the result over a pipe."""
    try:
        conn.send(("ok", run_simulation(rules_dict, release)))
    except Exception as e:  # pragma: no cover - defensive; reported to parent
        conn.send(("err", str(e)))
    finally:
        conn.close()
