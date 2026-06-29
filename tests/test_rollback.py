"""Tests for the safe rollback engine (src/rollback.py).

The plan builder is pure, so most safety guarantees are asserted directly on its
output (drift detection, collapse/clobber refusal, post-folder path math).
``perform_rollback`` is exercised against a stateful in-memory mock.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.rollback import build_rollback_plan, perform_rollback  # noqa: E402


class TestBuildRollbackPlan:
    def test_torrent_and_folder_default_mode(self):
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=("old-GRP", "New Name 2026"),
            applied_folder=("old-GRP", "New Name 2026"),
            applied_files=[],
            live_torrent_name="New Name 2026",
            live_files=["New Name 2026/a.mkv"],
            live_root_folder="New Name 2026",
        )
        assert plan.can_rollback
        assert plan.torrent_step and plan.torrent_step.to == "old-GRP"
        assert plan.folder_step and plan.folder_step.to == "old-GRP"
        assert plan.file_steps == []

    def test_files_and_folder_uses_post_folder_paths(self):
        applied = [
            ("oldF/e1.mkv", "New 2026/New 2026 S01E01.mkv"),
            ("oldF/e2.mkv", "New 2026/New 2026 S02E01.mkv"),
        ]
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=("oldT", "New 2026"),
            applied_folder=("oldF", "New 2026"),
            applied_files=applied,
            live_torrent_name="New 2026",
            live_files=["New 2026/New 2026 S01E01.mkv", "New 2026/New 2026 S02E01.mkv"],
            live_root_folder="New 2026",
        )
        assert plan.folder_step is not None
        # File reverts run AFTER the folder revert, so their source is under oldF.
        srcs = {s.frm for s in plan.file_steps}
        tgts = {s.to for s in plan.file_steps}
        assert srcs == {"oldF/New 2026 S01E01.mkv", "oldF/New 2026 S02E01.mkv"}
        assert tgts == {"oldF/e1.mkv", "oldF/e2.mkv"}

    def test_torrent_missing_blocks_rollback(self):
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=("o", "n"),
            applied_folder=None,
            applied_files=[],
            live_torrent_name=None,
            live_files=[],
            live_root_folder=None,
        )
        assert not plan.can_rollback
        assert not plan.torrent_exists

    def test_torrent_name_drift_is_skipped(self):
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=("o", "n"),
            applied_folder=None,
            applied_files=[],
            live_torrent_name="something else entirely",
            live_files=[],
            live_root_folder=None,
        )
        assert plan.torrent_step is None
        assert any(s["kind"] == "torrent" for s in plan.skipped)

    def test_already_at_original_names(self):
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=("orig", "new"),
            applied_folder=("origF", "newF"),
            applied_files=[],
            live_torrent_name="orig",
            live_files=["origF/a.mkv"],
            live_root_folder="origF",
        )
        assert not plan.can_rollback
        assert "original" in plan.reason.lower()

    def test_collapse_drops_both_files(self):
        # Two renamed files that would both revert to the SAME original name.
        applied = [("oldF/same.mkv", "newF/A.mkv"), ("oldF/same.mkv", "newF/B.mkv")]
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=None,
            applied_folder=("oldF", "newF"),
            applied_files=applied,
            live_torrent_name="t",
            live_files=["newF/A.mkv", "newF/B.mkv"],
            live_root_folder="newF",
        )
        assert plan.file_steps == []
        assert sum(1 for s in plan.skipped if s["kind"] == "file") == 2

    def test_missing_renamed_file_is_skipped(self):
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=None,
            applied_folder=("oldF", "newF"),
            applied_files=[("oldF/e1.mkv", "newF/X.mkv")],
            live_torrent_name="t",
            live_files=["newF/SOMETHING-ELSE.mkv"],
            live_root_folder="newF",
        )
        assert plan.file_steps == []
        assert any(s["kind"] == "file" for s in plan.skipped)

    def test_flat_files_only(self):
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=None,
            applied_folder=None,
            applied_files=[("Movie.2024.mkv", "New Movie 2024.mkv")],
            live_torrent_name="t",
            live_files=["New Movie 2024.mkv"],
            live_root_folder=None,
        )
        assert len(plan.file_steps) == 1
        assert plan.file_steps[0].frm == "New Movie 2024.mkv"
        assert plan.file_steps[0].to == "Movie.2024.mkv"


class _MockQbit:
    """Stateful mock that mutates an in-memory torrent on rename."""

    def __init__(self, name, files):
        self.name = name
        self.files = list(files)

    async def rename_torrent(self, h, new_name):
        self.name = new_name
        return True

    async def rename_folder(self, h, old, new):
        self.files = [
            new + f[len(old) :] if (f == old or f.startswith(old + "/")) else f for f in self.files
        ]
        return True

    async def rename_file(self, h, old, new):
        self.files = [new if f == old else f for f in self.files]
        return True


class TestPerformRollback:
    @pytest.mark.asyncio
    async def test_executes_and_reverts_state(self):
        qbit = _MockQbit(
            "New 2026",
            ["New 2026/New 2026 S01E01.mkv", "New 2026/New 2026 S02E01.mkv"],
        )

        # Wrap so get_files-like access isn't needed; build the plan directly.
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=("oldT", "New 2026"),
            applied_folder=("oldF", "New 2026"),
            applied_files=[
                ("oldF/e1.mkv", "New 2026/New 2026 S01E01.mkv"),
                ("oldF/e2.mkv", "New 2026/New 2026 S02E01.mkv"),
            ],
            live_torrent_name="New 2026",
            live_files=qbit.files,
            live_root_folder="New 2026",
        )
        result = await perform_rollback(qbit, plan)
        assert result.success
        assert result.torrent_reverted
        assert result.folder_reverted
        assert result.files_reverted == 2
        # State fully restored to the originals
        assert qbit.name == "oldT"
        assert sorted(qbit.files) == ["oldF/e1.mkv", "oldF/e2.mkv"]

    @pytest.mark.asyncio
    async def test_nothing_to_do_returns_error(self):
        qbit = _MockQbit("x", [])
        plan = build_rollback_plan(
            torrent_hash="h",
            applied_torrent=("orig", "orig"),
            applied_folder=None,
            applied_files=[],
            live_torrent_name="orig",
            live_files=[],
            live_root_folder=None,
        )
        result = await perform_rollback(qbit, plan)
        assert not result.success
