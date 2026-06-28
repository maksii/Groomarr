"""Tests for rename logic."""

import os
import sys

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import RenameRules, TrackerRules, matches_indexer
from src.rename import (
    RenameConflictError,
    apply_rename_rules,
    build_episode_identifier,
    build_new_file_path,
    extract_episode_from_batch,
    extract_episode_identifier,
    insert_episode_into_name,
    matches_any,
    sanitize_filename,
    strip_media_extension,
    validate_rename_plan,
)


class TestStripMediaExtension:
    """Test stripping media file extensions from names."""

    def test_strips_mkv_extension(self):
        assert strip_media_extension("Movie.2024.1080p.mkv") == "Movie.2024.1080p"

    def test_strips_mp4_extension(self):
        assert strip_media_extension("Movie.2024.1080p.mp4") == "Movie.2024.1080p"

    def test_strips_avi_extension(self):
        assert strip_media_extension("Movie.2024.1080p.avi") == "Movie.2024.1080p"

    def test_strips_mov_extension(self):
        assert strip_media_extension("Movie.2024.1080p.mov") == "Movie.2024.1080p"

    def test_strips_ts_extension(self):
        assert strip_media_extension("Movie.2024.1080p.ts") == "Movie.2024.1080p"

    def test_strips_m2ts_extension(self):
        assert strip_media_extension("Movie.2024.1080p.m2ts") == "Movie.2024.1080p"

    def test_case_insensitive_uppercase(self):
        assert strip_media_extension("Movie.2024.1080p.MKV") == "Movie.2024.1080p"

    def test_case_insensitive_mixed(self):
        assert strip_media_extension("Movie.2024.1080p.Mkv") == "Movie.2024.1080p"

    def test_no_extension_unchanged(self):
        assert strip_media_extension("Movie.2024.1080p") == "Movie.2024.1080p"

    def test_non_media_extension_unchanged(self):
        """Non-media extensions should not be stripped."""
        assert strip_media_extension("Movie.2024.1080p.txt") == "Movie.2024.1080p.txt"
        assert strip_media_extension("Movie.2024.1080p.nfo") == "Movie.2024.1080p.nfo"
        assert strip_media_extension("Movie.2024.1080p.srt") == "Movie.2024.1080p.srt"

    def test_release_group_not_stripped(self):
        """Release groups that look like extensions should not be stripped."""
        # Groups like -MKV or .MKV as group name shouldn't be affected if not at end
        assert strip_media_extension("Movie.2024.MKV-Group") == "Movie.2024.MKV-Group"

    def test_webm_extension(self):
        assert strip_media_extension("Video.2024.1080p.webm") == "Video.2024.1080p"


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_removes_invalid_chars(self):
        assert sanitize_filename('test<>:"/\\|?*.txt') == "test.txt"

    def test_collapses_multiple_spaces(self):
        assert sanitize_filename("test   file   name") == "test file name"

    def test_trims_whitespace(self):
        assert sanitize_filename("  test  ") == "test"

    def test_limits_length(self):
        long_name = "a" * 300
        result = sanitize_filename(long_name)
        assert len(result) == 250

    def test_removes_control_characters(self):
        assert sanitize_filename("test\x00\x1f\x7ffile") == "testfile"


class TestMatchesAny:
    """Test regex pattern matching."""

    def test_matches_simple_pattern(self):
        assert matches_any("TrackerA indexer", ["TrackerA.*"]) is True

    def test_no_match(self):
        assert matches_any("Other tracker", ["TrackerA.*"]) is False

    def test_case_insensitive(self):
        assert matches_any("trackera indexer", ["TrackerA.*"]) is True

    def test_empty_patterns(self):
        assert matches_any("anything", []) is False

    def test_multiple_patterns(self):
        assert matches_any("IndexerB", ["TrackerA.*", "Indexer.*"]) is True


class TestApplyRenameRules:
    """Test rename rules application."""

    def test_no_rules(self):
        rules = TrackerRules()
        result = apply_rename_rules("Original Name", rules)
        assert result == "Original Name"

    def test_add_prefix(self):
        rules = TrackerRules()
        rules.prefix = "[AUTO] "
        result = apply_rename_rules("Movie Name", rules)
        assert result == "[AUTO] Movie Name"

    def test_add_suffix(self):
        rules = TrackerRules()
        rules.suffix = " [Renamed]"
        result = apply_rename_rules("Movie Name", rules)
        assert result == "Movie Name [Renamed]"

    def test_remove_pattern(self):
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]  # Remove release group
        result = apply_rename_rules("Movie.2024.1080p.WEB-GroupX", rules)
        assert result == "Movie.2024.1080p.WEB"

    def test_replace_pattern(self):
        rules = TrackerRules()
        rules.replace_patterns = {r"\.": " "}  # Dots to spaces
        result = apply_rename_rules("Movie.Name.2024", rules)
        assert result == "Movie Name 2024"

    def test_skip_pattern(self):
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        rules.skip_title_patterns = ["PROPER"]

        # Should NOT be modified because of skip pattern
        result = apply_rename_rules("Movie.2024.PROPER-GROUP", rules)
        assert result == "Movie.2024.PROPER-GROUP"

    def test_combined_rules(self):
        rules = TrackerRules()
        rules.remove_patterns = [r"\[.*?\]", r"-\w+$"]
        rules.replace_patterns = {r"\.": " ", r"\s+": " "}

        result = apply_rename_rules("[TAG] Movie.Name.2024.1080p-GroupX", rules)
        assert result == "Movie Name 2024 1080p"


class TestRenameRulesFromSampleData:
    """Test with sample data from logg.txt."""

    def test_radarr_release_title(self):
        """Test with actual release title from a Radarr grab."""
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]  # Remove release group

        original = "Example Movie 2020 DE 4K Remaster BluRay 1080p ENG H.265-ReleaseGrp"
        result = apply_rename_rules(original, rules)

        assert result == "Example Movie 2020 DE 4K Remaster BluRay 1080p ENG H.265"
        assert "ReleaseGrp" not in result

    def test_clean_release_title(self):
        """Test cleaning up a release title with multiple rules."""
        rules = TrackerRules()
        rules.remove_patterns = [
            r"\[.*?\]",  # Remove bracketed tags
            r"-\w+$",  # Remove release group
        ]
        rules.replace_patterns = {
            r"\.": " ",  # Dots to spaces
            r"\s+": " ",  # Multiple spaces to single
        }

        original = "[TAG] Movie.Name.2024.1080p.HEVC.x265-GroupX"
        result = apply_rename_rules(original, rules)

        assert result == "Movie Name 2024 1080p HEVC x265"


class TestReleaseNameWithExtension:
    """Test handling of release names that include file extensions.

    This handles the case when users configure Sonarr/Radarr to use
    filenames instead of release names, resulting in extensions being
    included in the release title.
    """

    def test_release_with_mkv_extension(self):
        """Release title with .mkv extension should have it stripped."""
        rules = TrackerRules()
        original = "Movie.2024.1080p.WEB-DL.x264-Group.mkv"
        result = apply_rename_rules(original, rules)

        assert not result.endswith(".mkv")
        assert result == "Movie.2024.1080p.WEB-DL.x264-Group"

    def test_release_with_mp4_extension(self):
        """Release title with .mp4 extension should have it stripped."""
        rules = TrackerRules()
        original = "Movie.2024.1080p.WEB-DL.x264-Group.mp4"
        result = apply_rename_rules(original, rules)

        assert not result.endswith(".mp4")
        assert result == "Movie.2024.1080p.WEB-DL.x264-Group"

    def test_release_with_extension_and_rules(self):
        """Extension stripping works with other rename rules."""
        rules = TrackerRules()
        rules.replace_patterns = {r"\.": " ", r"\s+": " "}

        original = "Movie.2024.1080p.WEB-DL.mkv"
        result = apply_rename_rules(original, rules)

        assert not result.endswith(".mkv")
        assert result == "Movie 2024 1080p WEB-DL"

    def test_release_with_uppercase_extension(self):
        """Uppercase extensions should also be stripped."""
        rules = TrackerRules()
        original = "Movie.2024.1080p.WEB-DL.x264-Group.MKV"
        result = apply_rename_rules(original, rules)

        assert not result.upper().endswith(".MKV")
        assert result == "Movie.2024.1080p.WEB-DL.x264-Group"

    def test_sonarr_single_file_scenario(self):
        """Test Sonarr scenario where single file is grabbed by filename.

        When user configures Sonarr to grab by filename instead of release name,
        the release title may include the extension.
        """
        rules = TrackerRules()
        rules.replace_patterns = {r"\.": " ", r"\s+": " "}

        # Simulates: User grabs "Series.S01E05.1080p.WEB.mkv" as filename
        original = "Series.S01E05.1080p.WEB.mkv"
        result = apply_rename_rules(original, rules)

        assert not result.endswith(".mkv")
        assert result == "Series S01E05 1080p WEB"

    def test_no_duplicate_extension_in_file_path(self):
        """Ensure no duplicate extension when building file path."""
        # Scenario: Release title has extension, we rename file
        old_path = "SeriesFolder/Series.S01E05.1080p.WEB.mkv"
        new_name = "Series S01 1080p WEB"  # Already processed (no extension)
        root_folder = "SeriesFolder"

        result = build_new_file_path(old_path, new_name, root_folder)

        # Should have exactly one .mkv extension
        assert result.endswith(".mkv")
        assert not result.endswith(".mkv.mkv")
        assert result.count(".mkv") == 1


class TestExtractEpisodeIdentifier:
    """Test episode identifier extraction from filenames."""

    def test_standard_format_S01E01(self):
        """Test standard S01E01 format."""
        result = extract_episode_identifier("SeriesX S01E02 webdl.mkv")
        assert result == ("01", "E", "02")

    def test_space_format_S01_E01(self):
        """Test S01 E01 format with space."""
        result = extract_episode_identifier("SeriesX 2025 S01 E02 webdl.mkv")
        assert result == ("01", "E", "02")

    def test_ep_format_S01EP01(self):
        """Test S01EP01 format."""
        result = extract_episode_identifier("SeriesX S01EP03 1080p.mkv")
        assert result == ("01", "EP", "03")

    def test_ep_with_space_S01_EP01(self):
        """Test S01 EP01 format with space."""
        result = extract_episode_identifier("SeriesX S01 EP04 HDRip.mkv")
        assert result == ("01", "EP", "04")

    def test_lowercase_s01e01(self):
        """Test lowercase s01e01 format."""
        result = extract_episode_identifier("seriesx s02e05 webdl.mkv")
        assert result == ("02", "E", "05")

    def test_mixed_case_S01e01(self):
        """Test mixed case S01e01 format."""
        result = extract_episode_identifier("SeriesX S03e06 1080p.mkv")
        assert result == ("03", "E", "06")

    def test_double_digit_season_episode(self):
        """Test double digit season and episode numbers."""
        result = extract_episode_identifier("SeriesX S12E25 webdl.mkv")
        assert result == ("12", "E", "25")

    def test_no_episode_identifier(self):
        """Test file without episode identifier (movie)."""
        result = extract_episode_identifier("Movie.Name.2024.1080p.mkv")
        assert result is None

    def test_season_only_no_match(self):
        """Test that season-only pattern doesn't match."""
        result = extract_episode_identifier("SeriesX S01 Complete Season.mkv")
        assert result is None

    # Anime-style episode patterns
    def test_anime_hyphen_format(self):
        """Test anime style '- 10' format (from user's actual case)."""
        result = extract_episode_identifier(
            "[GM] Aku no Hana - 10 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
        )
        assert result is not None
        assert result[2] == "10"  # Episode number

    def test_anime_hyphen_single_digit(self):
        """Test anime style '- 1' format."""
        result = extract_episode_identifier("[Group] Series Name - 1 (720p).mkv")
        assert result is not None
        assert result[2] == "01"  # Should be zero-padded

    def test_anime_hyphen_double_digit(self):
        """Test anime style '- 13' format."""
        result = extract_episode_identifier("[Fansub] Anime Title - 13 [BDRip 1080p].mkv")
        assert result is not None
        assert result[2] == "13"

    def test_episode_word_format(self):
        """Test 'Episode 10' format."""
        result = extract_episode_identifier("Series Name Episode 10 [1080p].mkv")
        assert result is not None
        assert result[2] == "10"

    def test_ep_abbreviation_format(self):
        """Test 'Ep 05' format."""
        result = extract_episode_identifier("Series Name Ep 05 720p.mkv")
        assert result is not None
        assert result[2] == "05"

    def test_ep_no_space_format(self):
        """Test 'Ep05' format."""
        result = extract_episode_identifier("Series Name Ep05 [720p].mkv")
        assert result is not None
        assert result[2] == "05"

    def test_hash_number_format(self):
        """Test '#10' format."""
        result = extract_episode_identifier("Series Name #10 (1080p).mkv")
        assert result is not None
        assert result[2] == "10"

    def test_bracketed_number_format(self):
        """Test '[10]' format."""
        result = extract_episode_identifier("Series Name [10] 720p.mkv")
        assert result is not None
        assert result[2] == "10"

    def test_parenthesis_number_format(self):
        """Test '(10)' format."""
        result = extract_episode_identifier("Series Name (10) 1080p.mkv")
        assert result is not None
        assert result[2] == "10"

    def test_dot_separated_number(self):
        """Test '.10.' format.

        Note: This pattern is tricky because '.10.720p' has a digit after the separator.
        The batch analysis handles this case better for multiple files.
        """
        # Pattern works when followed by non-digit
        result = extract_episode_identifier("Series.Name.10.HDRip.mkv")
        assert result is not None
        assert result[2] == "10"

    def test_underscore_separated_number(self):
        """Test '_10_' format.

        Note: Similar to dot-separated, works best when followed by non-digit.
        """
        result = extract_episode_identifier("Series_Name_10_HDRip.mkv")
        assert result is not None
        assert result[2] == "10"

    def test_ignores_year(self):
        """Test that years (1900-2099) are not treated as episodes."""
        result = extract_episode_identifier("Movie Name 2024 1080p.mkv")
        assert result is None

    def test_ignores_resolution(self):
        """Test that resolutions are not treated as episodes."""
        result = extract_episode_identifier("Movie Name 1080p BluRay.mkv")
        assert result is None
        result2 = extract_episode_identifier("Movie Name 720p WEB.mkv")
        assert result2 is None

    def test_standard_pattern_takes_priority(self):
        """Test that S01E01 pattern takes priority over alternative patterns."""
        result = extract_episode_identifier("[Group] Series - 10 S01E05 720p.mkv")
        assert result == ("01", "E", "05")  # Standard pattern wins

    # New patterns from user's real-world examples
    def test_en_dash_format(self):
        """Test en-dash (–) separator: 'Блукач Кеншін – 01'."""
        result = extract_episode_identifier("Блукач Кеншін – 01 (Сезон 2)[1080p][Clan Kaizoku].mkv")
        assert result is not None
        assert result[2] == "01"

    def test_em_dash_format(self):
        """Test em-dash (—) separator."""
        result = extract_episode_identifier("Series Name — 05 [1080p].mkv")
        assert result is not None
        assert result[2] == "05"

    def test_trailing_space_number(self):
        """Test trailing space + number: '[РГ] Магічна Битва 3 01.mkv'."""
        result = extract_episode_identifier("[РГ] Магічна Битва 3 01.mkv")
        assert result is not None
        assert result[2] == "01"

    def test_trailing_space_number_double_digit(self):
        """Test trailing space + number: '[РГ] Алхімічна крамничка Сараси 12.mkv'."""
        result = extract_episode_identifier("[РГ] Алхімічна крамничка Сараси 12.mkv")
        assert result is not None
        assert result[2] == "12"

    def test_number_before_bracket(self):
        """Test number immediately before bracket: 'принцеси 15[WEBRip'."""
        result = extract_episode_identifier(
            "[РГ] Маленькі клопоти магічної принцеси 15[WEBRip Ai Rem 1080p x264 AAC].mkv"
        )
        assert result is not None
        assert result[2] == "15"

    def test_number_before_bracket_no_space(self):
        """Test number before bracket without space: 'Name 13['."""
        result = extract_episode_identifier("[РГ] Маленькі клопоти магічної принцеси 13[WEBRip.mkv")
        assert result is not None
        assert result[2] == "13"

    def test_ukrainian_of_pattern(self):
        """Test Ukrainian 'of' pattern: '[12 з 12]'."""
        result = extract_episode_identifier(
            "[РГ] Алхімічна крамничка Сараси [12 з 12] [WEBRip Ai Rem 1080p].mkv"
        )
        assert result is not None
        assert result[2] == "12"

    def test_english_of_pattern(self):
        """Test English 'of' pattern: '[5 of 10]'."""
        result = extract_episode_identifier("Series Name [5 of 10] 1080p.mkv")
        assert result is not None
        assert result[2] == "05"

    def test_russian_of_pattern(self):
        """Test Russian 'of' pattern: '[8 из 12]'."""
        result = extract_episode_identifier("Сериал [8 из 12] 720p.mkv")
        assert result is not None
        assert result[2] == "08"

    def test_bracketed_season_episode_underscore(self):
        """Test bracketed [S01_E001] pattern from Naruto releases."""
        result = extract_episode_identifier("[TV-1] [S01_E001] Naruto BDRemux 1080 [UKR_JAP].mkv")
        assert result is not None
        assert result[0] == "01"  # Season
        assert result[2] == "001"  # Episode (3 digits preserved)

    def test_bracketed_season_episode_three_digit(self):
        """Test 3-digit episode: [S01_E450]."""
        result = extract_episode_identifier("[TV-1] [S01_E450] Naruto BDRemux 1080.mkv")
        assert result is not None
        assert result[0] == "01"
        assert result[2] == "450"

    def test_three_digit_episode_standard(self):
        """Test 3-digit episode in standard format: S01E001."""
        result = extract_episode_identifier("Long Series S01E001 1080p.mkv")
        assert result is not None
        assert result[2] == "001"

    def test_three_digit_episode_preserved(self):
        """Test that 3-digit episode numbers are preserved (not truncated)."""
        result = extract_episode_identifier("Series S02E220 HDTV.mkv")
        assert result is not None
        assert result[0] == "02"
        assert result[2] == "220"

    def test_underscore_season_episode(self):
        """Test S01_E01 format with underscore separator."""
        result = extract_episode_identifier("Series_Name_S01_E05_720p.mkv")
        assert result is not None
        assert result[0] == "01"
        assert result[2] == "05"


class TestBuildEpisodeIdentifier:
    """Test episode identifier string building."""

    def test_standard_format(self):
        assert build_episode_identifier("01", "E", "02") == "S01E02"

    def test_ep_normalized_to_e(self):
        """EP marker should be normalized to E."""
        assert build_episode_identifier("01", "EP", "03") == "S01E03"

    def test_preserves_numbers(self):
        assert build_episode_identifier("12", "E", "25") == "S12E25"


class TestInsertEpisodeIntoName:
    """Test inserting episode identifier into new names."""

    def test_replace_season_only(self):
        """Test replacing season-only pattern with full episode."""
        episode_info = ("01", "E", "02")
        result = insert_episode_into_name("SeriesX S01 IT WEBDL 1080p ReleaseGroup", episode_info)
        assert result == "SeriesX S01E02 IT WEBDL 1080p ReleaseGroup"

    def test_replace_season_only_lowercase(self):
        """Test replacing lowercase season-only pattern."""
        episode_info = ("02", "E", "05")
        result = insert_episode_into_name("seriesx s02 webdl 720p", episode_info)
        assert result == "seriesx S02E05 webdl 720p"

    def test_replace_existing_episode(self):
        """Test replacing existing episode pattern with correct one."""
        episode_info = ("01", "E", "03")
        result = insert_episode_into_name("SeriesX S01E01 1080p WEBDL", episode_info)
        assert result == "SeriesX S01E03 1080p WEBDL"

    def test_season_from_title_takes_priority(self):
        """Test that season from title takes priority over season from file.

        Bug fix: When file extraction returns wrong season (e.g., "01" default
        from anime-style files), the season from the release title should be used.

        Example: Title has S02E05, file extraction returns ("01", "E", "10")
        Result should be S02E10, not S01E10
        """
        # Title has season 2, file extraction wrongly detected season 1
        episode_info = ("01", "E", "10")  # Wrong season from anime-style file
        result = insert_episode_into_name("SeriesX S02E05 1080p WEBDL", episode_info)
        assert result == "SeriesX S02E10 1080p WEBDL"

    def test_season_from_title_priority_different_seasons(self):
        """Test season priority with various season numbers."""
        # Season 5 in title, file extraction says season 1
        episode_info = ("01", "E", "07")
        result = insert_episode_into_name("Anime.Series.S05E01.1080p.WEB-DL", episode_info)
        assert result == "Anime.Series.S05E07.1080p.WEB-DL"

    def test_season_only_pattern_uses_title_season(self):
        """Test that season-only pattern in title is preserved."""
        # Title has S03 (no episode), file has episode 12 with wrong season 01
        episode_info = ("01", "E", "12")
        result = insert_episode_into_name("SeriesX S03 1080p WEBDL", episode_info)
        assert result == "SeriesX S03E12 1080p WEBDL"

    def test_insert_before_year(self):
        """Test inserting episode before year when no season pattern."""
        episode_info = ("01", "E", "05")
        result = insert_episode_into_name("SeriesX 2024 1080p WEBDL", episode_info)
        assert "S01E05" in result

    def test_insert_before_resolution(self):
        """Test inserting episode before resolution when no season pattern."""
        episode_info = ("02", "E", "10")
        result = insert_episode_into_name("SeriesX 1080p WEBDL Group", episode_info)
        assert "S02E10" in result

    def test_absolute_episode_range_replaced_not_spliced_into_year(self):
        """Absolute-numbered anime (episode-range envelope, no season token).

        Regression: the year-insertion fallback used to splice the identifier
        inside the year parens AND leave the range, producing the malformed
        ``Bleach E110-E293 ( S01E110 2004-2012)``. The range must instead be
        replaced by the file's own absolute episode, with no season fabricated.
        """
        episode_info = ("01", "E", "110")
        result = insert_episode_into_name(
            "Bleach E110-E293 (2004-2012) BluRay 1080p Ukrainian-fanat22012", episode_info
        )
        assert result == "Bleach E110 (2004-2012) BluRay 1080p Ukrainian-fanat22012"
        assert "E110-E293" not in result  # envelope consumed
        assert "( S01E110" not in result  # not spliced into the year parens
        assert "S01E110" not in result  # no fabricated season for absolute anime

    def test_absolute_episode_range_two_digit(self):
        """Two-digit absolute range narrows to the file's own padded episode."""
        episode_info = ("01", "E", "5")
        result = insert_episode_into_name(
            "Candy Candy E01-E32 (1976-1979) DVDRip 720p Ukrainian-fanat22012", episode_info
        )
        assert result == "Candy Candy E05 (1976-1979) DVDRip 720p Ukrainian-fanat22012"

    def test_absolute_episode_range_zero_episode(self):
        """Episode 0 (e.g. a prologue) is preserved, not dropped."""
        episode_info = ("01", "E", "0")
        result = insert_episode_into_name(
            "Mobile Suit Gundam SEED E00-E37 (2002) BluRay 1080p Ukrainian-Gwean", episode_info
        )
        assert result == "Mobile Suit Gundam SEED E00 (2002) BluRay 1080p Ukrainian-Gwean"

    def test_season_token_still_wins_over_episode_range(self):
        """A real season token must still take priority over a bare episode range."""
        # Title carries S02 plus a stray range; the season branch should win.
        episode_info = ("01", "E", "07")
        result = insert_episode_into_name("SeriesX S02 E01-E12 1080p WEBDL", episode_info)
        assert "S02E07" in result


class TestBuildNewFilePathTVSeries:
    """Test build_new_file_path for TV series scenarios."""

    def test_user_scenario_preserve_episode(self):
        """Test the user's specific scenario.

        release name: SeriesX S01 IT WEBDL 1080p ReleaseGroup
        original file: SeriesX 2025 S01 E02 webdl.mkv
        expected: SeriesX S01E02 IT WEBDL 1080p ReleaseGroup.mkv
        """
        old_path = "SeriesX S01 2025/SeriesX 2025 S01 E02 webdl.mkv"
        new_name = "SeriesX S01 IT WEBDL 1080p ReleaseGroup"
        root_folder = "SeriesX S01 2025"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert (
            result
            == "SeriesX S01 IT WEBDL 1080p ReleaseGroup/SeriesX S01E02 IT WEBDL 1080p ReleaseGroup.mkv"
        )

    def test_standard_episode_format(self):
        """Test with standard S01E01 format in original file."""
        old_path = "Series.Folder/Series.S01E05.Episode.Title.mkv"
        new_name = "SeriesName S01 1080p WEBDL"
        root_folder = "Series.Folder"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "S01E05" in result
        assert result.endswith(".mkv")

    def test_space_episode_format(self):
        """Test with S01 E01 format (space) in original file."""
        old_path = "SeriesFolder/Series S02 E10 720p.mkv"
        new_name = "SeriesName S02 720p WEB"
        root_folder = "SeriesFolder"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "S02E10" in result

    def test_ep_episode_format(self):
        """Test with S01EP01 format in original file."""
        old_path = "SeriesFolder/Series S01EP08 HDRip.mkv"
        new_name = "SeriesName S01 HDRip 720p"
        root_folder = "SeriesFolder"

        result = build_new_file_path(old_path, new_name, root_folder)

        # EP should be normalized to E
        assert "S01E08" in result

    def test_movie_no_episode_preserved(self):
        """Test that movies without episode info work correctly."""
        old_path = "Movie.Folder/Movie.2024.1080p.BluRay.mkv"
        new_name = "Movie Name 2024 1080p BluRay"
        root_folder = "Movie.Folder"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert result == "Movie Name 2024 1080p BluRay/Movie Name 2024 1080p BluRay.mkv"
        assert "S0" not in result  # No season/episode added

    def test_single_file_no_folder(self):
        """Test single file without root folder."""
        old_path = "Series.S01E03.Episode.mkv"
        new_name = "SeriesName S01 1080p"
        root_folder = None

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "S01E03" in result
        assert result.endswith(".mkv")

    def test_preserves_subdirectory_structure(self):
        """Test that subdirectory structure is preserved."""
        old_path = "SeriesFolder/Season 1/Series S01E05.mkv"
        new_name = "SeriesName S01 1080p"
        root_folder = "SeriesFolder"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "SeriesName S01 1080p/Season 1/" in result
        assert "S01E05" in result

    def test_multiple_episodes_same_folder(self):
        """Test that different episodes get different names."""
        new_name = "SeriesX S01 IT WEBDL 1080p"
        root_folder = "SeriesFolder"

        result_ep1 = build_new_file_path("SeriesFolder/Series S01E01.mkv", new_name, root_folder)
        result_ep2 = build_new_file_path("SeriesFolder/Series S01E02.mkv", new_name, root_folder)
        result_ep3 = build_new_file_path("SeriesFolder/Series S01 E03.mkv", new_name, root_folder)

        assert "S01E01" in result_ep1
        assert "S01E02" in result_ep2
        assert "S01E03" in result_ep3
        # All should be different
        assert result_ep1 != result_ep2 != result_ep3


class TestExtractEpisodeFromBatch:
    """Test batch episode detection for files without standard patterns."""

    def test_anime_batch_detection(self):
        """Test batch detection for anime files with '- XX' pattern."""
        filenames = [
            "[GM] Aku no Hana - 01 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv",
            "[GM] Aku no Hana - 02 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv",
            "[GM] Aku no Hana - 03 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv",
            "[GM] Aku no Hana - 10 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv",
            "[GM] Aku no Hana - 13 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 5
        assert result[filenames[0]] == "01"
        assert result[filenames[1]] == "02"
        assert result[filenames[3]] == "10"
        assert result[filenames[4]] == "13"

    def test_simple_numbered_files(self):
        """Test batch detection for simple numbered files."""
        filenames = [
            "Series 1.mkv",
            "Series 2.mkv",
            "Series 3.mkv",
            "Series 4.mkv",
            "Series 5.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 5
        # Numbers are zero-padded to consistent width
        assert result["Series 1.mkv"] in ("1", "01")
        assert result["Series 5.mkv"] in ("5", "05")

    def test_dot_separated_numbers(self):
        """Test batch detection for dot-separated numbered files."""
        filenames = [
            "Series.Name.01.720p.mkv",
            "Series.Name.02.720p.mkv",
            "Series.Name.03.720p.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 3
        assert result["Series.Name.01.720p.mkv"] == "01"
        assert result["Series.Name.03.720p.mkv"] == "03"

    def test_single_file_returns_empty(self):
        """Test that single file returns empty (no batch needed)."""
        result = extract_episode_from_batch(["Single.File.mkv"])
        assert result == {}

    def test_empty_list_returns_empty(self):
        """Test that empty list returns empty."""
        result = extract_episode_from_batch([])
        assert result == {}

    def test_ignores_years_in_batch(self):
        """Test that years are ignored during batch analysis."""
        filenames = [
            "Movie 2024 Part 1.mkv",
            "Movie 2024 Part 2.mkv",
            "Movie 2024 Part 3.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 3
        # Should detect 1, 2, 3 - not 2024
        assert "1" in result.values() or "01" in result.values()

    def test_ignores_resolution_in_batch(self):
        """Test that resolutions are ignored during batch analysis."""
        filenames = [
            "Series 01 1080p.mkv",
            "Series 02 1080p.mkv",
            "Series 03 1080p.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 3
        # Should detect 01, 02, 03 - not 1080
        values = list(result.values())
        assert "1080" not in values

    def test_ascending_sequence_required(self):
        """Test that batch detection requires ascending sequence."""
        # Perfect ascending sequence 1-5 should work
        filenames = [
            "Episode 1.mkv",
            "Episode 2.mkv",
            "Episode 3.mkv",
            "Episode 4.mkv",
            "Episode 5.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 5
        # Verify order is preserved
        values = [int(result[f]) for f in filenames]
        assert values == [1, 2, 3, 4, 5]

    def test_sequence_with_small_gaps(self):
        """Test that sequences with small gaps (missing episodes) work."""
        # 1, 2, 4, 5, 6 - gap of 1 episode (3 is missing)
        filenames = [
            "Episode 1.mkv",
            "Episode 2.mkv",
            "Episode 4.mkv",
            "Episode 5.mkv",
            "Episode 6.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 5

    def test_sequence_with_huge_gaps_rejected(self):
        """Test that sequences with huge gaps are rejected."""
        # 1, 10, 20, 30, 40 - gaps of 10, not a proper episode sequence
        filenames = [
            "File 1.mkv",
            "File 10.mkv",
            "File 20.mkv",
            "File 30.mkv",
            "File 40.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        # Should fail because gaps are too large
        assert result == {}

    def test_random_numbers_rejected(self):
        """Test that random non-sequential numbers are rejected."""
        # Random numbers that don't form a sequence
        filenames = [
            "File 7.mkv",
            "File 23.mkv",
            "File 45.mkv",
            "File 89.mkv",
            "File 156.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        # Should fail because numbers don't form a reasonable sequence
        assert result == {}

    def test_prefers_better_sequence(self):
        """Test that batch detection prefers sequences starting from 1."""
        # Files have multiple numbers, should prefer the episode-like sequence
        filenames = [
            "S01 Episode 1 720p.mkv",
            "S01 Episode 2 720p.mkv",
            "S01 Episode 3 720p.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 3
        # Should detect 1,2,3 not 01,01,01 (season) or 720,720,720 (resolution)
        values = sorted([int(result[f]) for f in filenames])
        assert values == [1, 2, 3]

    def test_full_season_sequence(self):
        """Test detection of full season (13 episodes typical for anime)."""
        filenames = [f"Anime - {i:02d}.mkv" for i in range(1, 14)]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 13
        # Verify all episodes detected in order
        for i, fname in enumerate(filenames, start=1):
            assert int(result[fname]) == i

    def test_three_digit_sequence(self):
        """Test detection of 3-digit episode sequence (long series)."""
        # Simulates Naruto-style with 220 episodes (subset)
        filenames = [
            "Series 001.mkv",
            "Series 002.mkv",
            "Series 003.mkv",
            "Series 004.mkv",
            "Series 005.mkv",
        ]
        result = extract_episode_from_batch(filenames)
        assert len(result) == 5
        assert result["Series 001.mkv"] == "001"
        assert result["Series 005.mkv"] == "005"

    def test_no_unique_numbers_returns_empty(self):
        """Test that files without unique identifying numbers return empty."""
        filenames = [
            "Series 720p.mkv",
            "Series 720p Part2.mkv",
            "Series 720p Part3.mkv",
        ]
        # All have 720 at same position, so it's not unique
        # Part2/Part3 numbers are at different positions
        # Just verify this doesn't crash - the key is robustness
        extract_episode_from_batch(filenames)


class TestValidateRenamePlan:
    """Test rename plan validation and conflict detection."""

    def test_no_conflicts_with_standard_episodes(self):
        """Test validation passes with standard episode patterns."""
        files = [
            {"name": "SeriesFolder/Series S01E01.mkv"},
            {"name": "SeriesFolder/Series S01E02.mkv"},
            {"name": "SeriesFolder/Series S01E03.mkv"},
        ]
        plan, warnings = validate_rename_plan(files, "New Series S01 1080p", "SeriesFolder")
        assert len(plan) == 3
        assert len(warnings) == 0
        # All paths should be different
        new_paths = [p[1] for p in plan]
        assert len(set(new_paths)) == 3

    def test_no_conflicts_with_anime_episodes(self):
        """Test validation passes with anime episode patterns."""
        files = [
            {"name": "AnimeFolder/[GM] Anime - 01 (1080p).mkv"},
            {"name": "AnimeFolder/[GM] Anime - 02 (1080p).mkv"},
            {"name": "AnimeFolder/[GM] Anime - 03 (1080p).mkv"},
        ]
        plan, warnings = validate_rename_plan(files, "Anime S01 1080p", "AnimeFolder")
        assert len(plan) == 3
        assert len(warnings) == 0
        new_paths = [p[1] for p in plan]
        assert len(set(new_paths)) == 3

    def test_conflict_detection_all_same_name(self):
        """Test detection when all files would get the same name."""
        # Files with no detectable episode identifiers
        files = [
            {"name": "Folder/File A.mkv"},
            {"name": "Folder/File B.mkv"},
            {"name": "Folder/File C.mkv"},
        ]
        with pytest.raises(RenameConflictError) as exc_info:
            validate_rename_plan(files, "New Name", "Folder")
        assert "CRITICAL" in str(exc_info.value)
        assert "data loss" in str(exc_info.value).lower()

    def test_batch_analysis_prevents_conflict(self):
        """Test that batch analysis is used to prevent false conflicts."""
        # Files that look like they have no pattern, but batch analysis finds one
        files = [
            {"name": "Folder/[GM] Aku no Hana - 01 (1080p).mkv"},
            {"name": "Folder/[GM] Aku no Hana - 02 (1080p).mkv"},
            {"name": "Folder/[GM] Aku no Hana - 03 (1080p).mkv"},
        ]
        # Should not raise because anime pattern is detected
        plan, warnings = validate_rename_plan(files, "Aku no Hana S01 1080p", "Folder")
        assert len(plan) == 3
        new_paths = [p[1] for p in plan]
        assert len(set(new_paths)) == 3  # All unique

    def test_single_file_no_conflict(self):
        """Test that single file never has conflicts."""
        files = [{"name": "Folder/Movie 2024 1080p.mkv"}]
        plan, warnings = validate_rename_plan(files, "New Movie Name", "Folder")
        assert len(plan) == 1
        assert len(warnings) == 0

    def test_empty_files_no_conflict(self):
        """Test that empty file list returns empty plan."""
        plan, warnings = validate_rename_plan([], "New Name", "Folder")
        assert len(plan) == 0
        assert len(warnings) == 0

    def test_movie_with_stray_number_gets_no_episode(self):
        """A single-file movie whose filename has a stray number (e.g. "AAC2.0")
        must NOT get a fabricated SxxExx — Radarr expects no episode token."""
        files = [{"name": "100.Meters.2025.1080p.BluRay.AAC2.0.x264-SPiLNO.mkv"}]
        plan, _ = validate_rename_plan(
            files, "Hyakuemu (2025) BluRay 1080p Ukrainian-Anonymous", None
        )
        assert len(plan) == 1
        new_base = plan[0][1].rsplit("/", 1)[-1]
        assert new_base == "Hyakuemu (2025) BluRay 1080p Ukrainian-Anonymous.mkv"
        assert "S01E" not in new_base

    def test_multifile_release_never_collapsed_as_movie(self):
        """A multi-file episode release the analyzer cannot fully parse (so it may
        fall to the MOVIE kind) must still get per-file episodes from the base
        extractor — never have its episodes suppressed and collapse to one name."""
        files = [
            {"name": f"Golgo 13 - E0{i} [Ukr] WEB-DLRip [Anime Classic].mkv"} for i in range(1, 6)
        ]
        plan, _ = validate_rename_plan(
            files, "Golgo 13 E01-E05 (2008) WEBRip 1080p Ukrainian-Anonymous", None
        )
        new_bases = [p[1].rsplit("/", 1)[-1] for p in plan]
        assert len(set(new_bases)) == 5  # all distinct — no collapse
        assert all("E0" in b for b in new_bases)

    def test_user_scenario_aku_no_hana(self):
        """Test the exact user scenario that caused the bug.

        This is the actual case from the bug report where 13 anime files
        were all renamed to the same name.
        """
        files = [
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 01 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 02 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 03 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 04 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 05 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 06 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 07 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 08 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 09 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 10 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 11 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 12 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
            {
                "name": "Aku no Hana [BDRip 1080p HEVC AAC]/[GM] Aku no Hana - 13 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
            },
        ]
        new_name = "Aku no Hana S01 BluRay 1080p [Ukrainian+Japanese] H.265-antik_2008"
        root_folder = "Aku no Hana [BDRip 1080p HEVC AAC]"

        # This should NOT raise an error and should produce 13 unique filenames
        plan, warnings = validate_rename_plan(files, new_name, root_folder)
        assert len(plan) == 13
        assert len(warnings) == 0

        # All new paths should be unique
        new_paths = [p[1] for p in plan]
        assert len(set(new_paths)) == 13, f"Expected 13 unique paths, got {len(set(new_paths))}"

        # Each path should contain the episode number
        for _, new_path in plan:
            # The episode number should be preserved in the new path
            assert "S01E" in new_path, f"Missing episode identifier in {new_path}"


class TestBuildNewFilePathWithOverride:
    """Test build_new_file_path with episode_override parameter."""

    def test_episode_override_used(self):
        """Test that episode_override is used when provided."""
        old_path = "Folder/Some File.mkv"
        new_name = "Series S01 1080p"
        episode_override = ("01", "E", "05")

        result = build_new_file_path(old_path, new_name, "Folder", episode_override)
        assert "S01E05" in result

    def test_override_takes_priority(self):
        """Test that override takes priority over extracted episode."""
        # File has S01E01, but override says E05
        old_path = "Folder/Series S01E01 720p.mkv"
        new_name = "Series S01 1080p"
        episode_override = ("01", "E", "05")

        result = build_new_file_path(old_path, new_name, "Folder", episode_override)
        assert "S01E05" in result
        assert "S01E01" not in result

    def test_no_override_extracts_from_filename(self):
        """Test that without override, episode is extracted from filename."""
        old_path = "Folder/Series S01E03 720p.mkv"
        new_name = "Series S01 1080p"

        result = build_new_file_path(old_path, new_name, "Folder", episode_override=None)
        assert "S01E03" in result


class TestMatchesIndexer:
    """Test indexer pattern matching for tracker-specific rules."""

    def test_exact_match(self):
        """Test exact string matching (case-insensitive)."""
        assert matches_indexer("TrackerA", ["TrackerA"]) is True
        assert matches_indexer("trackera", ["TrackerA"]) is True
        assert matches_indexer("TRACKERA", ["TrackerA"]) is True
        assert matches_indexer("TrackerB", ["TrackerA"]) is False

    def test_exact_match_complex_names(self):
        """Test exact matching with complex indexer names."""
        assert matches_indexer("UTOPIA (API)-experimental", ["UTOPIA (API)-experimental"]) is True
        assert matches_indexer("Toloka.to", ["Toloka.to"]) is True
        assert matches_indexer("upload.cx (API)", ["upload.cx (API)"]) is True
        assert matches_indexer("Secret Cinema", ["Secret Cinema"]) is True
        assert matches_indexer("seedpool (API)", ["seedpool (API)"]) is True
        assert matches_indexer("CinemaMovieS_ZT", ["CinemaMovieS_ZT"]) is True
        assert matches_indexer("Free Farm (自由农场)", ["Free Farm (自由农场)"]) is True

    def test_wildcard_match(self):
        """Test wildcard pattern matching."""
        assert matches_indexer("TrackerA (API)", ["TrackerA*"]) is True
        assert matches_indexer("TrackerA (Prowlarr)", ["TrackerA*"]) is True
        assert matches_indexer("TrackerB", ["TrackerA*"]) is False
        assert matches_indexer("Secret Cinema", ["*Cinema*"]) is True
        assert matches_indexer("CinemaMovieS_ZT", ["*Cinema*"]) is True
        assert matches_indexer("No Match", ["*Cinema*"]) is False

    def test_wildcard_single_char(self):
        """Test single character wildcard (?)."""
        assert matches_indexer("Tracker1", ["Tracker?"]) is True
        assert matches_indexer("TrackerA", ["Tracker?"]) is True
        assert matches_indexer("Tracker10", ["Tracker?"]) is False  # Two chars don't match ?

    def test_regex_match(self):
        """Test regex pattern matching (wrapped in slashes)."""
        assert matches_indexer("TrackerA (API)", ["/TrackerA.*API/"]) is True
        assert matches_indexer("TrackerA-Prowlarr", ["/TrackerA.*API/"]) is False
        assert matches_indexer("UTOPIA (API)-experimental", ["/UTOPIA.*experimental/"]) is True

    def test_regex_match_with_trailing_i_flag(self):
        """A trailing /i flag is accepted (matching is always case-insensitive).

        The UI documents the ``/.*anime.*/i`` form, so a pattern ending in ``/i``
        must be treated as a regex — not silently fall through to exact match.
        """
        assert matches_indexer("Nyaa (Prowlarr)", ["/.*anime.*/i"]) is False
        assert matches_indexer("AniDex (Prowlarr)", ["/.*anidex.*/i"]) is True
        assert matches_indexer("FreshIndexer (Prowlarr)", ["/freshindexer/i"]) is True
        # Same expression without the flag still works and stays case-insensitive.
        assert matches_indexer("freshindexer", ["/FreshIndexer/"]) is True

    def test_multiple_patterns(self):
        """Test matching against multiple patterns."""
        patterns = ["TrackerA*", "TrackerB*", "/Public.*/"]
        assert matches_indexer("TrackerA (API)", patterns) is True
        assert matches_indexer("TrackerB", patterns) is True
        assert matches_indexer("PublicTracker", patterns) is True
        assert matches_indexer("PrivateTracker", patterns) is False

    def test_empty_patterns(self):
        """Test with empty pattern list."""
        assert matches_indexer("AnyTracker", []) is False

    def test_invalid_regex_handled(self):
        """Test that invalid regex patterns don't crash."""
        # Invalid regex should be handled gracefully
        assert matches_indexer("Test", ["/[invalid/"]) is False
        assert matches_indexer("TrackerA", ["/(invalid[regex/"]) is False

    def test_invalid_wildcard(self):
        """Test that wildcard patterns work correctly."""
        # Wildcard * should match anything
        result = matches_indexer("TrackerA", ["*"])
        assert result is True


class TestTrackerRulesFromDict:
    """Test TrackerRules creation from dictionary."""

    def test_from_dict_full(self):
        """Test loading all fields from dict."""
        data = {
            "indexers_include": ["TrackerA"],
            "indexers_exclude": ["BadTracker"],
            "qualities_include": ["Bluray-1080p"],
            "qualities_exclude": ["CAM"],
            "prefix": "[TEST] ",
            "suffix": " [Auto]",
            "remove_patterns": [r"\[.*?\]"],
            "replace_patterns": {r"\.": " "},
            "validate_custom_format_score": True,
            "score_validation_policy": "warn",
        }
        rules = TrackerRules.from_dict(data)

        assert rules.indexers_include == ["TrackerA"]
        assert rules.indexers_exclude == ["BadTracker"]
        assert rules.qualities_include == ["Bluray-1080p"]
        assert rules.qualities_exclude == ["CAM"]
        assert rules.prefix == "[TEST] "
        assert rules.suffix == " [Auto]"
        assert rules.remove_patterns == [r"\[.*?\]"]
        assert rules.replace_patterns == {r"\.": " "}
        assert rules.validate_custom_format_score is True
        assert rules.score_validation_policy == "warn"

    def test_from_dict_empty(self):
        """Test loading from empty dict uses defaults."""
        rules = TrackerRules.from_dict({})

        assert rules.indexers_include == []
        assert rules.prefix == ""
        assert rules.validate_custom_format_score is False
        assert rules.score_validation_policy == "block"

    def test_from_dict_partial(self):
        """Test loading only some fields."""
        data = {"prefix": "[MyTracker] ", "qualities_exclude": ["CAM", "TS"]}
        rules = TrackerRules.from_dict(data)

        assert rules.prefix == "[MyTracker] "
        assert rules.qualities_exclude == ["CAM", "TS"]
        assert rules.indexers_include == []  # Default
        assert rules.suffix == ""  # Default


class TestRenameRulesHierarchical:
    """Test hierarchical RenameRules loading and rule resolution."""

    def test_legacy_flat_format(self, tmp_path):
        """Test loading legacy flat format (no global/trackers keys)."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
indexers_exclude:
  - "BadTracker"
prefix: "[Legacy] "
validate_custom_format_score: true
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.config_found is True
        assert rules.global_rules.indexers_exclude == ["BadTracker"]
        assert rules.global_rules.prefix == "[Legacy] "
        assert rules.global_rules.validate_custom_format_score is True
        assert rules.trackers == []

    def test_hierarchical_format_global_only(self, tmp_path):
        """Test hierarchical format with only global rules."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  indexers_exclude:
    - "PublicTracker"
  prefix: "[Global] "
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.global_rules.indexers_exclude == ["PublicTracker"]
        assert rules.global_rules.prefix == "[Global] "
        assert rules.trackers == []

    def test_hierarchical_format_with_trackers(self, tmp_path):
        """Test hierarchical format with tracker-specific rules."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: "[Global] "

trackers:
  - name: "tracker-a"
    match:
      - "TrackerA*"
    rules:
      prefix: "[TrackerA] "
      qualities_include:
        - "Bluray-1080p"

  - name: "public"
    match:
      - "*Public*"
      - "1337x"
    rules:
      qualities_exclude:
        - "CAM"
        - "TS"
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.global_rules.prefix == "[Global] "
        assert len(rules.trackers) == 2

        assert rules.trackers[0].name == "tracker-a"
        assert rules.trackers[0].match == ["TrackerA*"]
        assert rules.trackers[0].rules.prefix == "[TrackerA] "
        assert rules.trackers[0].rules.qualities_include == ["Bluray-1080p"]

        assert rules.trackers[1].name == "public"
        assert rules.trackers[1].match == ["*Public*", "1337x"]
        assert rules.trackers[1].rules.qualities_exclude == ["CAM", "TS"]

    def test_get_rules_for_indexer_tracker_match(self, tmp_path):
        """Test resolving rules for an indexer that matches a tracker."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: "[Global] "

trackers:
  - name: "my-tracker"
    match:
      - "MyPrivateTracker*"
    rules:
      prefix: "[Private] "
""")
        rules = RenameRules.from_yaml(str(config_file))

        # Should match tracker
        effective_rules, tracker_name = rules.get_rules_for_indexer("MyPrivateTracker (API)")
        assert tracker_name == "my-tracker"
        assert effective_rules.prefix == "[Private] "

    def test_get_rules_for_indexer_fallback_to_global(self, tmp_path):
        """Test falling back to global rules when no tracker matches."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: "[Global] "

trackers:
  - name: "tracker-a"
    match:
      - "TrackerA*"
    rules:
      prefix: "[TrackerA] "
""")
        rules = RenameRules.from_yaml(str(config_file))

        # Should NOT match any tracker, use global
        effective_rules, tracker_name = rules.get_rules_for_indexer("SomeOtherTracker")
        assert tracker_name is None
        assert effective_rules.prefix == "[Global] "

    def test_get_rules_for_indexer_first_match_wins(self, tmp_path):
        """Test that first matching tracker wins."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: "[Global] "

trackers:
  - name: "specific"
    match:
      - "TrackerA (API)"
    rules:
      prefix: "[Specific] "

  - name: "wildcard"
    match:
      - "TrackerA*"
    rules:
      prefix: "[Wildcard] "
""")
        rules = RenameRules.from_yaml(str(config_file))

        # Should match first (specific) tracker
        effective_rules, tracker_name = rules.get_rules_for_indexer("TrackerA (API)")
        assert tracker_name == "specific"
        assert effective_rules.prefix == "[Specific] "

        # This one should match second (wildcard) tracker
        effective_rules, tracker_name = rules.get_rules_for_indexer("TrackerA-Prowlarr")
        assert tracker_name == "wildcard"
        assert effective_rules.prefix == "[Wildcard] "

    def test_tracker_without_match_skipped(self, tmp_path):
        """Test that trackers without match patterns are skipped."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: "[Global] "

trackers:
  - name: "no-match"
    match: []
    rules:
      prefix: "[NoMatch] "

  - name: "valid"
    match:
      - "TrackerA*"
    rules:
      prefix: "[Valid] "
""")
        rules = RenameRules.from_yaml(str(config_file))

        # Should only have 1 tracker (the one without match was skipped)
        assert len(rules.trackers) == 1
        assert rules.trackers[0].name == "valid"

    def test_complex_indexer_names_matching(self, tmp_path):
        """Test matching complex real-world indexer names."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text(
            """
global:
  prefix: "[Global] "

trackers:
  - name: "utopia"
    match:
      - "UTOPIA (API)-experimental"
      - "UTOPIA*"
    rules:
      prefix: "[UTOPIA] "

  - name: "toloka"
    match:
      - "Toloka.to"
    rules:
      prefix: "[Toloka] "

  - name: "free-farm"
    match:
      - "Free Farm (自由农场)"
    rules:
      prefix: "[FreeFarm] "
""",
            encoding="utf-8",
        )
        rules = RenameRules.from_yaml(str(config_file))

        # Test exact match
        effective, name = rules.get_rules_for_indexer("UTOPIA (API)-experimental")
        assert name == "utopia"
        assert effective.prefix == "[UTOPIA] "

        # Test wildcard match
        effective, name = rules.get_rules_for_indexer("UTOPIA (Prowlarr)")
        assert name == "utopia"

        # Test with dots
        effective, name = rules.get_rules_for_indexer("Toloka.to")
        assert name == "toloka"
        assert effective.prefix == "[Toloka] "

        # Test Unicode
        effective, name = rules.get_rules_for_indexer("Free Farm (自由农场)")
        assert name == "free-farm"
        assert effective.prefix == "[FreeFarm] "

    def test_backward_compatibility_properties(self, tmp_path):
        """Test backward compatibility properties on RenameRules."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  validate_custom_format_score: true
  score_validation_policy: "warn"
""")
        rules = RenameRules.from_yaml(str(config_file))

        # These properties should delegate to global_rules
        assert rules.validate_custom_format_score is True
        assert rules.score_validation_policy == "warn"

    def test_has_trigger_filters_global_and_tracker(self, tmp_path):
        """Test has_trigger_filters checks both global and tracker rules."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  indexers_exclude: []

trackers:
  - name: "tracker"
    match:
      - "TrackerA"
    rules:
      qualities_include:
        - "1080p"
""")
        rules = RenameRules.from_yaml(str(config_file))

        # Should return True because tracker has filters
        assert rules.has_trigger_filters() is True

    def test_config_not_found(self, tmp_path):
        """Test handling when config file doesn't exist."""
        rules = RenameRules.from_yaml(str(tmp_path / "nonexistent.yaml"))

        assert rules.config_found is False
        assert rules.trackers == []
        # Global rules should be defaults
        assert rules.global_rules.prefix == ""

    def test_config_yaml_parse_error(self, tmp_path):
        """Test handling when YAML file has parse errors."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: yaml: [unclosed")

        rules = RenameRules.from_yaml(str(config_file))

        assert rules.config_found is True
        assert rules.config_error is not None
        assert "unclosed" in rules.config_error.lower() or "yaml" in rules.config_error.lower()

    def test_get_active_filters_summary(self, tmp_path):
        """Test get_active_filters_summary returns correct summary."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  indexers_include: ["TrackerA", "TrackerB"]
  indexers_exclude: ["BadTracker"]
  qualities_include: ["1080p"]
  min_customformat_score: 100
  release_groups_include: ["GroupA"]
""")
        rules = RenameRules.from_yaml(str(config_file))

        summary = rules.get_active_filters_summary()

        assert len(summary) > 0
        assert any("indexers_include" in s for s in summary)
        assert any("indexers_exclude" in s for s in summary)
        assert any("qualities_include" in s for s in summary)
        assert any("min_customformat_score" in s for s in summary)
        assert any("release_groups_include" in s for s in summary)

    def test_get_active_rules_summary(self, tmp_path):
        """Test get_active_rules_summary returns correct summary."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: "[TEST] "
  suffix: " [Auto]"
  remove_patterns: ["pattern1", "pattern2"]
  replace_patterns:
    ".": " "
  skip_title_patterns: ["PROPER"]

trackers:
  - name: "tracker1"
    match: ["TrackerA"]
    rules:
      prefix: "[Tracker1] "
  - name: "tracker2"
    match: ["TrackerB"]
    rules:
      suffix: " [T2]"
""")
        rules = RenameRules.from_yaml(str(config_file))

        summary = rules.get_active_rules_summary()

        assert len(summary) > 0
        assert any("global" in s and "prefix" in s for s in summary)
        assert any("global" in s and "suffix" in s for s in summary)
        assert any("global" in s and "remove_patterns" in s for s in summary)
        assert any("global" in s and "replace_patterns" in s for s in summary)
        assert any("global" in s and "skip_title_patterns" in s for s in summary)
        assert any("tracker1" in s for s in summary)
        assert any("tracker2" in s for s in summary)


class TestTrackerRulesSummary:
    """Test TrackerRules summary methods."""

    def test_get_active_filters_summary_all_filters(self):
        """get_active_filters_summary should list all active filters."""
        rules = TrackerRules()
        rules.indexers_include = ["A", "B"]
        rules.indexers_exclude = ["C"]
        rules.qualities_include = ["1080p"]
        rules.qualities_exclude = ["CAM"]
        rules.customformats_require_any = ["HDR"]
        rules.customformats_exclude = ["x264"]
        rules.min_customformat_score = 100
        rules.download_clients_include = ["qBit"]
        rules.download_clients_exclude = ["Deluge"]
        rules.release_groups_include = ["GroupA"]
        rules.release_groups_exclude = ["GroupB"]

        summary = rules.get_active_filters_summary()

        assert len(summary) == 11
        assert any("indexers_include" in s for s in summary)
        assert any("indexers_exclude" in s for s in summary)
        assert any("qualities_include" in s for s in summary)
        assert any("qualities_exclude" in s for s in summary)
        assert any("customformats_require_any" in s for s in summary)
        assert any("customformats_exclude" in s for s in summary)
        assert any("min_customformat_score" in s for s in summary)
        assert any("download_clients_include" in s for s in summary)
        assert any("download_clients_exclude" in s for s in summary)
        assert any("release_groups_include" in s for s in summary)
        assert any("release_groups_exclude" in s for s in summary)

    def test_get_active_filters_summary_empty(self):
        """get_active_filters_summary should return empty list when no filters."""
        rules = TrackerRules()
        summary = rules.get_active_filters_summary()
        assert summary == []

    def test_get_active_rules_summary_all_rules(self):
        """get_active_rules_summary should list all active rules."""
        rules = TrackerRules()
        rules.prefix = "[TEST] "
        rules.suffix = " [Auto]"
        rules.remove_patterns = ["pattern1", "pattern2"]
        rules.replace_patterns = {".": " "}
        rules.skip_title_patterns = ["PROPER"]

        summary = rules.get_active_rules_summary()

        assert len(summary) == 5
        assert any("prefix" in s for s in summary)
        assert any("suffix" in s for s in summary)
        assert any("remove_patterns" in s for s in summary)
        assert any("replace_patterns" in s for s in summary)
        assert any("skip_title_patterns" in s for s in summary)

    def test_get_active_rules_summary_empty(self):
        """get_active_rules_summary should return empty list when no rules."""
        rules = TrackerRules()
        summary = rules.get_active_rules_summary()
        assert summary == []


class TestRenameRulesProperties:
    """Test RenameRules convenience properties."""

    def test_validate_custom_format_score_property(self, tmp_path):
        """Test validate_custom_format_score property delegates to global."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  validate_custom_format_score: true
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.validate_custom_format_score is True

    def test_score_validation_policy_property(self, tmp_path):
        """Test score_validation_policy property delegates to global."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  score_validation_policy: "warn"
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.score_validation_policy == "warn"

    def test_has_rename_rules_global_only(self, tmp_path):
        """Test has_rename_rules with only global rules."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: "[TEST] "
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.has_rename_rules() is True

    def test_has_rename_rules_tracker_only(self, tmp_path):
        """Test has_rename_rules with only tracker rules."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global:
  prefix: ""

trackers:
  - name: "tracker"
    match: ["TrackerA"]
    rules:
      prefix: "[T] "
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.has_rename_rules() is True

    def test_has_rename_rules_none(self, tmp_path):
        """Test has_rename_rules when no rules configured."""
        config_file = tmp_path / "rules.yaml"
        config_file.write_text("""
global: {}
""")
        rules = RenameRules.from_yaml(str(config_file))

        assert rules.has_rename_rules() is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
