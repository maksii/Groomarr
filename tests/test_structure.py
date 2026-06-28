"""Tests for torrent structure analysis (multi-season / specials / collection).

Fixtures are drawn from REAL toloka.to release structures (harvested file
listings) so the parser is validated against the messy inputs it must handle in
production, not idealised samples.
"""

import os
import re
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.rename import RenameMode, get_root_folder, perform_rename, validate_rename_plan
from src.structure import (
    LayoutKind,
    analyze_torrent,
    assess_plan_safety,
    episode_only,
    full_season_episode,
    order_moves_safely,
    parse_file,
    season_from_segment,
)

_SE = re.compile(r"S(\d+)E(\d+)", re.IGNORECASE)


def _files(*paths):
    return [{"name": p} for p in paths]


def _ident(path):
    m = _SE.search(path.rsplit("/", 1)[-1])
    return f"S{int(m.group(1)):02d}E{int(m.group(2)):02d}" if m else None


class TestFullSeasonEpisode:
    def test_plain_sxxexx(self):
        assert full_season_episode("Show.S02E06.title.mkv") == (2, 6, "06")

    def test_bracketed(self):
        assert full_season_episode("[Grp] Show [S01_E005].mkv") == (1, 5, "005")

    def test_lowercase(self):
        assert full_season_episode("show.s01e01.mkv") == (1, 1, "01")

    def test_none_when_episode_only(self):
        assert full_season_episode("Show - 05.mkv") is None

    def test_none_when_no_episode(self):
        assert full_season_episode("Movie 2024 1080p.mkv") is None


class TestEpisodeOnly:
    def test_anime_dash(self):
        assert episode_only("Show - 05.mkv") == (5, "05")

    def test_episode_word(self):
        assert episode_only("Show Episode 12.mkv") == (12, "12")

    def test_year_not_episode(self):
        # 2024 must not be taken as an episode number
        assert episode_only("Movie 2024.mkv") is None


class TestSeasonFromSegment:
    def test_sxx(self):
        assert season_from_segment("Show.2024.S02.576p.WEB-DL") == 2

    def test_season_word(self):
        assert season_from_segment("Season 3") == 3

    def test_cyrillic(self):
        assert season_from_segment("Хлопці (2 сезон)") == 2

    def test_bare_number_is_not_season(self):
        # a trailing number is too ambiguous to treat as a season
        assert season_from_segment("Some Show 2") is None

    def test_year_is_not_season(self):
        assert season_from_segment("Show (2024)") is None


class TestParseFileSeasonAuthority:
    """The file's own SxxExx is authoritative — the multi-season bug fix."""

    def test_real_s02_file_keeps_season_2(self):
        # the exact shape that today's engine corrupts to S01
        m = parse_file(
            "Pati.S01-S02/Pati.S02.576p.HMAX.WEB-DL.Ukr-Hurtom/"
            "Pati.S02E06.576p.HMAX.WEB-DL.Ukr-Hurtom.mkv"
        )
        assert m.season == 2
        assert m.episode == 6
        assert m.season_source == "filename"
        assert m.kind == "episode"

    def test_real_s01_file(self):
        m = parse_file(
            "Pati.S01-S02/Pati.S01.576p.HMAX.WEB-DL.Ukr-Hurtom/"
            "Pati.S01E01.576p.HMAX.WEB-DL.Ukr-Hurtom.mkv"
        )
        assert (m.season, m.episode) == (1, 1)

    def test_folder_season_fallback_for_anime(self):
        # filename has only an episode number; season comes from the folder
        m = parse_file("Show Season 2/Show - 05.mkv")
        assert m.season == 2
        assert m.episode == 5
        assert m.season_source == "folder"

    def test_no_season_stays_none(self):
        # bare anime episode, no folder season -> season unknown (not invented)
        m = parse_file("Mikakunin de Shinkoukei/Mikakunin de Shinkoukei - 01.mkv")
        assert m.season is None
        assert m.episode == 1


class TestParseFileKinds:
    def test_specials_folder_is_season_zero(self):
        m = parse_file("Show S01/Specials/Show OVA 1.mkv")
        assert m.kind == "special"
        assert m.season == 0
        assert m.season_source == "special_folder"

    def test_inline_special_keeps_its_season(self):
        # real Strike Witches: "S1E13 Special" stays S01E13, NOT fabricated S00
        m = parse_file("Strike Witches 501/Strike Witches 501 S1E13 Special.mp4")
        assert m.kind == "special"
        assert (m.season, m.episode) == (1, 13)

    def test_sample(self):
        assert parse_file("Show/sample.mkv").kind == "sample"
        assert parse_file("Show/Show-sample.mkv").kind == "sample"

    def test_subtitle(self):
        assert parse_file("Show/Show.S01E01.srt").kind == "subtitle"

    def test_movie(self):
        assert parse_file("Some Movie (2024)/Some Movie (2024) 1080p.mkv").kind == "movie"


class TestAnalyzeTorrentRealStructures:
    def test_multi_season_nested(self):
        layout = analyze_torrent(
            _files(
                "The.Jinx.S01-S02/S01/The.Jinx.S01E01.mkv",
                "The.Jinx.S01-S02/S01/The.Jinx.S01E02.mkv",
                "The.Jinx.S01-S02/S02/The.Jinx.S02E01.mkv",
                "The.Jinx.S01-S02/S02/The.Jinx.S02E02.mkv",
            )
        )
        assert layout.kind == LayoutKind.MULTI_SEASON
        assert layout.seasons == [1, 2]

    def test_multi_season_deep(self):
        # The Rookie S1-S8 style: many seasons in one pack
        paths = []
        for s in range(1, 9):
            for e in range(1, 4):
                paths.append(f"The.Rookie/The.Rookie.s{s:02d}e{e:02d}.mkv")
        layout = analyze_torrent(_files(*paths))
        assert layout.kind == LayoutKind.MULTI_SEASON
        assert layout.seasons == list(range(1, 9))

    def test_season_with_specials_folder(self):
        layout = analyze_torrent(
            _files(
                "Show S01/Show.S01E01.mkv",
                "Show S01/Show.S01E02.mkv",
                "Show S01/Specials/Show.OVA.1.mkv",
            )
        )
        assert layout.kind == LayoutKind.SEASON_WITH_SPECIALS
        assert layout.has_specials is True
        assert layout.seasons == [1]

    def test_collection_of_distinct_shorts(self):
        # real "Instead of a thousand words" short-film bundle
        layout = analyze_torrent(
            _files(
                "Instead of a thousand words/Shishigari 1080p.mkv",
                "Instead of a thousand words/Onikiri Musume Saisen 1080p.mkv",
                "Instead of a thousand words/Afternoon Class 1080p.mkv",
                "Instead of a thousand words/Suna no Akari 1080p.mkv",
            )
        )
        assert layout.kind == LayoutKind.COLLECTION

    def test_single_season_anime_is_simple_not_collection(self):
        # real Вежа Бога 2-NN shape: episode parse may fail, MUST NOT be a collection
        layout = analyze_torrent(
            _files(
                "Вежа Бога 2/Вежа Бога 2-01.mp4",
                "Вежа Бога 2/Вежа Бога 2-10.mp4",
                "Вежа Бога 2/Вежа Бога 2-11.mp4",
            )
        )
        assert layout.kind != LayoutKind.COLLECTION

    def test_simple_single_season(self):
        layout = analyze_torrent(
            _files(
                "Show S01/Show.S01E01.mkv",
                "Show S01/Show.S01E02.mkv",
                "Show S01/Show.S01E03.mkv",
            )
        )
        assert layout.kind == LayoutKind.SIMPLE_SEASON
        assert layout.is_complex is False

    def test_single_movie(self):
        layout = analyze_torrent(_files("Movie (2024)/Movie (2024) 1080p.mkv"))
        assert layout.kind == LayoutKind.MOVIE
        assert layout.is_complex is False

    def test_split_movie_parts_not_collection(self):
        layout = analyze_torrent(
            _files(
                "Only Love (1998)/Only Love. Part01 (1998) DVDRip.avi",
                "Only Love (1998)/Only Love. Part02 (1998) DVDRip.avi",
            )
        )
        assert layout.kind != LayoutKind.COLLECTION


class TestPlanSafety:
    def test_collapse_is_unsafe(self):
        s = assess_plan_safety([("a.mkv", "X.mkv"), ("b.mkv", "X.mkv")], ["a.mkv", "b.mkv"])
        assert s.safe is False
        assert s.conflicts

    def test_clobber_bystander_is_unsafe(self):
        # renaming a->b would overwrite c-less file b which stays put
        s = assess_plan_safety([("a.mkv", "b.mkv")], ["a.mkv", "b.mkv", "c.mkv"])
        assert s.safe is False

    def test_clean_plan_is_safe(self):
        s = assess_plan_safety([("a.mkv", "X.mkv"), ("b.mkv", "Y.mkv")], ["a.mkv", "b.mkv"])
        assert s.safe is True
        assert s.overlaps == []

    def test_overlap_is_safe_but_flagged(self):
        s = assess_plan_safety([("a", "b"), ("b", "c")], ["a", "b"])
        assert s.safe is True
        assert s.overlaps == ["b"]

    def test_noop_moves_ignored(self):
        s = assess_plan_safety([("a", "a"), ("b", "b")], ["a", "b"])
        assert s.safe is True


class TestOrderMovesSafely:
    def test_orders_overlap_before_target(self):
        ordered, staged = order_moves_safely([("a", "b"), ("b", "c")])
        assert staged == []
        # b->c must come before a->b
        assert ordered.index(("b", "c")) < ordered.index(("a", "b"))

    def test_breaks_cycle_with_temp(self):
        ordered, staged = order_moves_safely([("a", "b"), ("b", "a")])
        assert staged  # cycle requires staging through temp names
        # every original source is moved out to a temp first
        assert {o for o, _ in staged} == {"a", "b"}

    def test_independent_moves_pass_through(self):
        ordered, staged = order_moves_safely([("a", "x"), ("b", "y")])
        assert staged == []
        assert set(ordered) == {("a", "x"), ("b", "y")}


class TestValidateRenamePlanComplex:
    """The multi-season fix flows through the real production entry point."""

    def test_multiseason_preserves_true_season(self):
        # this exact shape was corrupted (every file -> S01) before the fix
        files = _files(
            "Pati.S01-S02/Pati.S01/Pati.S01E01.mkv",
            "Pati.S01-S02/Pati.S01/Pati.S01E02.mkv",
            "Pati.S01-S02/Pati.S02/Pati.S02E01.mkv",
            "Pati.S01-S02/Pati.S02/Pati.S02E02.mkv",
        )
        root = get_root_folder(files)
        plan, warnings = validate_rename_plan(
            files, "Pati S01-S02 (2024) WEB-DL x264 Ukr", root, preserve_folder=True
        )
        assert len(plan) == 4
        # every file keeps its OWN season; targets are a collision-free bijection
        for old, new in plan:
            assert _ident(old) == _ident(new), f"season changed: {old} -> {new}"
        assert len({n for _, n in plan}) == 4
        assert len({_ident(n) for _, n in plan}) == 4  # no duplicate SxxExx

    def test_collection_left_unchanged(self):
        files = _files(
            "Shorts/Alpha Tale 1080p.mkv",
            "Shorts/Beta Story 1080p.mkv",
            "Shorts/Gamma Fable 1080p.mkv",
        )
        plan, warnings = validate_rename_plan(
            files, "Shorts Collection (2021)", "Shorts", preserve_folder=True
        )
        assert plan == []
        assert warnings and "collection" in warnings[0].lower()

    def test_simple_season_unchanged_by_delegation(self):
        # a normal single-season pack must still be planned by the base engine
        files = _files(
            "Show.S01/Show.S01E01.mkv",
            "Show.S01/Show.S01E02.mkv",
        )
        plan, _ = validate_rename_plan(files, "Show S01 1080p", "Show.S01", preserve_folder=True)
        assert len(plan) == 2
        assert {_ident(n) for _, n in plan} == {"S01E01", "S01E02"}


class TestPerformRenameSafetyGate:
    """perform_rename must refuse a plan that could lose a file."""

    def _qbit(self, files, name="Pack"):
        qbit = MagicMock()
        qbit.get_torrent_info = MagicMock(return_value={"name": name})
        qbit.get_files = MagicMock(return_value=files)
        qbit.rename_torrent = AsyncMock(return_value=True)
        qbit.rename_file = AsyncMock(return_value=True)
        qbit.rename_folder = AsyncMock(return_value=True)
        return qbit

    @pytest.mark.asyncio
    async def test_drops_colliding_keeps_safe(self):
        # alpha+beta have no episode -> both map to the same name (collapse) and
        # must be left unchanged; Show.S01E01 is distinct and must still rename.
        files = [
            {"name": "Pack/alpha.mkv"},
            {"name": "Pack/beta.mkv"},
            {"name": "Pack/Show.S01E01.mkv"},
        ]
        qbit = self._qbit(files)
        result = await perform_rename(qbit, "A" * 40, "New Pack", RenameMode.TORRENT_FOLDER_FILES)
        # only the one safe, distinct file was renamed
        assert qbit.rename_file.await_count == 1
        renamed_old = qbit.rename_file.await_args_list[0].args[1]
        assert "Show.S01E01" in renamed_old
        assert result.files_skipped >= 2  # alpha + beta left unchanged (no loss)

    @pytest.mark.asyncio
    async def test_safe_simple_rename_proceeds(self):
        # the gate must NOT block a safe plan: both files get renamed.
        # (final verification is covered by the stateful integration suite;
        # here the mock is static, so we assert the renames were issued.)
        files = [
            {"name": "Pack/Show.S01E01.mkv"},
            {"name": "Pack/Show.S01E02.mkv"},
        ]
        qbit = self._qbit(files)
        await perform_rename(qbit, "A" * 40, "Show S01", RenameMode.TORRENT_FOLDER_FILES)
        assert qbit.rename_file.await_count == 2
        qbit.rename_torrent.assert_awaited()
