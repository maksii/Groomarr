"""Safe, verified rollback of a recorded rename.

A rollback reverses a rename that Groomarr previously applied to a torrent in
qBittorrent, restoring the original torrent display name, root folder, and file
names. The hard requirement (shared with the forward engine) is **never lose a
file**: a rollback must not overwrite a bystander, collapse two files onto one
name, or act on a path that no longer holds what Groomarr put there.

The design is "safe & verified":

* The reverse plan is computed from the *executed* forward plan recorded at rename
  time (``RenameResult.applied_*`` → ``applied_json``), then validated against the
  torrent's **current live state**. Any element whose live state no longer matches
  what Groomarr set (the user moved/renamed it, re-downloaded, etc.) is **skipped**
  with a reason rather than forced.
* File moves pass the same data-loss gate as the forward path
  (:func:`src.structure.partition_safe_moves` + :func:`order_moves_safely`), so a
  rollback can never destroy a file.
* Reversal mirrors the forward order in reverse: folder (new→old) → files
  (new→original, inside the restored folder) → torrent display name. Folder revert
  runs first so the file moves operate on the restored folder paths.

:func:`build_rollback_plan` is pure (no I/O) and is the unit-tested core;
:func:`perform_rollback` performs the qBittorrent calls.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from .qbittorrent import QBitClient
from .structure import assess_plan_safety, order_moves_safely, partition_safe_moves

logger = logging.getLogger(__name__)


@dataclass
class RollbackStep:
    """A single reverse rename: rename ``frm`` back to ``to``."""

    kind: str  # "torrent" | "folder" | "file"
    frm: str
    to: str


@dataclass
class RollbackPlan:
    """A validated reverse plan for one torrent (pure data; no I/O performed yet)."""

    torrent_hash: str
    torrent_exists: bool = True
    can_rollback: bool = False
    reason: str = ""
    torrent_step: RollbackStep | None = None
    folder_step: RollbackStep | None = None
    file_steps: list[RollbackStep] = field(default_factory=list)
    # Elements not reverted because live state drifted or a move was unsafe.
    skipped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def total_steps(self) -> int:
        return (
            (1 if self.torrent_step else 0) + (1 if self.folder_step else 0) + len(self.file_steps)
        )


@dataclass
class RollbackResult:
    """Outcome of executing a :class:`RollbackPlan`."""

    success: bool = True
    torrent_reverted: bool = False
    folder_reverted: bool = False
    files_reverted: int = 0
    files_failed: int = 0
    files_skipped: int = 0
    steps: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _strip_folder_prefix(path: str, folder: str) -> str:
    """Return ``path`` with a leading ``folder/`` replaced — used for folder revert.

    If the file currently sits under ``folder`` (``folder/...``) the prefix segment
    is returned without it (i.e. the part after ``folder``), else ``None``-like ''.
    """
    if path == folder:
        return ""
    if path.startswith(folder + "/"):
        return path[len(folder) :]  # keeps the leading "/"
    return path


def build_rollback_plan(
    *,
    torrent_hash: str,
    applied_torrent: tuple[str, str] | None,
    applied_folder: tuple[str, str] | None,
    applied_files: list[tuple[str, str]],
    live_torrent_name: str | None,
    live_files: list[str],
    live_root_folder: str | None,
) -> RollbackPlan:
    """Compute a validated reverse plan from the recorded forward plan + live state.

    Args:
        torrent_hash: The torrent the rollback targets.
        applied_torrent: ``(old_name, new_name)`` the forward rename applied, or None.
        applied_folder: ``(folder_old, folder_new)`` applied, or None.
        applied_files: list of ``(original_path, final_new_path)`` file moves applied
            (``final_new_path`` already reflects any folder rename).
        live_torrent_name: current torrent display name, or None if the torrent is
            no longer present in qBittorrent.
        live_files: current file paths in the torrent.
        live_root_folder: current root folder name (or None for a flat torrent).

    Returns:
        A :class:`RollbackPlan`. ``can_rollback`` is True only when at least one
        element can be safely reverted. Drift and unsafe moves populate ``skipped``.
    """
    plan = RollbackPlan(torrent_hash=torrent_hash)

    # The torrent must still exist to roll anything back.
    if live_torrent_name is None:
        plan.torrent_exists = False
        plan.can_rollback = False
        plan.reason = "Torrent is no longer present in qBittorrent."
        return plan

    live_set = set(live_files)

    # --- Folder revert (executed first) ---------------------------------------
    will_revert_folder = False
    folder_old = folder_new = None
    if applied_folder:
        folder_old, folder_new = applied_folder
        if folder_old == folder_new:
            pass  # no-op forward rename; nothing to revert
        elif live_root_folder == folder_new:
            plan.folder_step = RollbackStep("folder", folder_new, folder_old)
            will_revert_folder = True
        elif live_root_folder == folder_old:
            plan.warnings.append("Folder already at its original name.")
        else:
            plan.skipped.append(
                {
                    "kind": "folder",
                    "frm": folder_new,
                    "to": folder_old,
                    "reason": (
                        f"current root folder '{live_root_folder}' no longer matches the "
                        f"renamed folder — left as-is"
                    ),
                }
            )

    # --- File reverts ----------------------------------------------------------
    # Build the set of paths AS THEY WILL BE after the folder revert, so the
    # data-loss gate evaluates the moves in the order they actually execute.
    if will_revert_folder and folder_new is not None and folder_old is not None:
        post_folder_paths = {
            (folder_old + _strip_folder_prefix(p, folder_new))
            if (p == folder_new or p.startswith(folder_new + "/"))
            else p
            for p in live_files
        }
    else:
        post_folder_paths = set(live_set)

    candidate_moves: list[tuple[str, str]] = []
    for original_path, final_new_path in applied_files:
        if original_path == final_new_path:
            continue  # this file's name never changed
        # Verify Groomarr's rename is still in place at its recorded final path.
        if final_new_path not in live_set:
            plan.skipped.append(
                {
                    "kind": "file",
                    "frm": final_new_path,
                    "to": original_path,
                    "reason": "expected renamed file not found (moved, removed, or re-renamed)",
                }
            )
            continue
        # The file's path at the moment its reverse move runs (after folder revert).
        if will_revert_folder and folder_new is not None and folder_old is not None:
            src = (
                folder_old + _strip_folder_prefix(final_new_path, folder_new)
                if (final_new_path == folder_new or final_new_path.startswith(folder_new + "/"))
                else final_new_path
            )
        else:
            src = final_new_path
        if src == original_path:
            continue  # already at the original name
        candidate_moves.append((src, original_path))

    # Data-loss gate: drop any move that would collapse onto another target or
    # clobber a file staying put; keep every provably-safe move.
    safe_moves, dropped = partition_safe_moves(candidate_moves, post_folder_paths)
    for frm, to, reason in dropped:
        plan.skipped.append({"kind": "file", "frm": frm, "to": to, "reason": reason})

    # Defensive re-assertion: partition_safe_moves already guarantees this, but a
    # rollback that could lose a file must never proceed — withhold all file
    # reverts if the safe set somehow still fails the gate.
    if safe_moves and not assess_plan_safety(safe_moves, post_folder_paths).safe:
        plan.warnings.append("File reverts withheld by the data-loss safety check.")
        safe_moves = []

    # Order so no move overwrites a path still pending as a source (cycles staged).
    ordered, _staged = order_moves_safely(safe_moves)
    plan.file_steps = [RollbackStep("file", frm, to) for frm, to in ordered]

    # --- Torrent display name revert (executed last) --------------------------
    if applied_torrent:
        old_name, new_name = applied_torrent
        if old_name == new_name:
            pass
        elif live_torrent_name == new_name:
            plan.torrent_step = RollbackStep("torrent", new_name, old_name)
        elif live_torrent_name == old_name:
            plan.warnings.append("Torrent name already at its original value.")
        else:
            plan.skipped.append(
                {
                    "kind": "torrent",
                    "frm": new_name,
                    "to": old_name,
                    "reason": (
                        f"current torrent name '{live_torrent_name}' no longer matches the "
                        f"renamed value — left as-is"
                    ),
                }
            )

    plan.can_rollback = plan.total_steps > 0
    if not plan.can_rollback and not plan.reason:
        if plan.skipped:
            plan.reason = "Nothing could be safely reverted (live state has drifted)."
        else:
            plan.reason = "Already at the original names — nothing to roll back."
    return plan


async def perform_rollback(qbit: QBitClient, plan: RollbackPlan) -> RollbackResult:
    """Execute a validated :class:`RollbackPlan` against qBittorrent.

    Steps run in the reverse of the forward order — folder, then files (inside the
    restored folder), then the torrent display name — so file moves act on the
    already-restored folder paths. Failures are counted and reported; because the
    plan passed the data-loss gate, a failed step never destroys a file.
    """
    result = RollbackResult()
    th = plan.torrent_hash
    result.files_skipped = sum(1 for s in plan.skipped if s.get("kind") == "file")

    if not plan.can_rollback:
        result.success = False
        result.errors.append(plan.reason or "Nothing to roll back.")
        return result

    # 1) Folder revert (new -> old). File reverts below assume this ran.
    if plan.folder_step:
        ok = await qbit.rename_folder(th, plan.folder_step.frm, plan.folder_step.to)
        if ok:
            result.folder_reverted = True
            result.steps.append(f"folder '{plan.folder_step.frm}' → '{plan.folder_step.to}'")
            await asyncio.sleep(0.2)  # let qBittorrent propagate nested path updates
        else:
            result.success = False
            result.errors.append("Failed to revert the folder name.")
            # File reverts depend on the folder having been restored; abort them.
            result.files_failed += len(plan.file_steps)
            plan.file_steps = []

    # 2) File reverts (renamed name -> original), in the safe order.
    for step in plan.file_steps:
        if await qbit.rename_file(th, step.frm, step.to):
            result.files_reverted += 1
        else:
            result.files_failed += 1
            result.success = False
    if plan.file_steps:
        result.steps.append(f"{result.files_reverted}/{len(plan.file_steps)} files restored")

    # 3) Torrent display name revert (cosmetic; independent of the above).
    if plan.torrent_step:
        if await qbit.rename_torrent(th, plan.torrent_step.to):
            result.torrent_reverted = True
            result.steps.append(f"torrent '{plan.torrent_step.frm}' → '{plan.torrent_step.to}'")
        else:
            result.success = False
            result.errors.append("Failed to revert the torrent name.")

    return result
