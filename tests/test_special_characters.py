"""Tests for handling titles with special characters.

This test suite validates that the application correctly handles titles
with special characters commonly found in anime and other media titles.
"""

import os
import sys

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.config import TrackerRules
from src.rename import (
    apply_rename_rules,
    build_new_file_path,
    sanitize_filename,
    strip_media_extension,
)

# =============================================================================
# Test Data: Titles with Special Characters
# =============================================================================

# Movies (5 titles)
MOVIE_TITLES = [
    "K-ON! The Movie (2011) 1080p BluRay",
    "Re:ZERO -Starting Life in Another World- The Frozen Bond (2019) 1080p",
    "Fate/stay night: Heaven's Feel - III. Spring Song (2020) 4K",
    "Love, Chunibyo & Other Delusions! Take on Me (2018) 1080p",
    "JoJo's Bizarre Adventure: Diamond is Unbreakable Chapter I (2017) BluRay",
]

# TV Shows (5 titles)
TV_TITLES = [
    "K-ON! S01 BluRay 1080p",
    "Re:ZERO -Starting Life in Another World- S02 1080p WEBDL",
    "Fate/stay night: Unlimited Blade Works S01 1080p",
    "Love, Chunibyo & Other Delusions! S01 1080p",
    "D.Gray-man S01 1080p BluRay",
]

# Additional real-world examples
ADDITIONAL_TITLES = [
    "Is the Order a Rabbit?? S01 1080p",
    "JoJo's Bizarre Adventure: Stardust Crusaders S01 1080p",
    "D.Gray-man Hallow S01 1080p",
]


# =============================================================================
# Sanitization Tests
# =============================================================================


class TestSanitizeSpecialCharacters:
    """Test sanitization of titles with special characters."""

    def test_preserves_hyphens(self):
        """Hyphens should be preserved."""
        assert sanitize_filename("K-ON! Movie") == "K-ON! Movie"
        assert sanitize_filename("D.Gray-man") == "D.Gray-man"

    def test_preserves_exclamation_marks(self):
        """Exclamation marks should be preserved."""
        assert sanitize_filename("K-ON!") == "K-ON!"
        assert (
            sanitize_filename("Love, Chunibyo & Other Delusions!")
            == "Love, Chunibyo & Other Delusions!"
        )

    def test_preserves_ampersands(self):
        """Ampersands should be preserved."""
        assert sanitize_filename("Love & Peace") == "Love & Peace"
        assert sanitize_filename("A & B Movie") == "A & B Movie"

    def test_preserves_apostrophes(self):
        """Apostrophes should be preserved."""
        assert sanitize_filename("JoJo's Bizarre Adventure") == "JoJo's Bizarre Adventure"
        assert sanitize_filename("It's a Movie") == "It's a Movie"

    def test_preserves_commas(self):
        """Commas should be preserved."""
        assert (
            sanitize_filename("Love, Chunibyo & Other Delusions!")
            == "Love, Chunibyo & Other Delusions!"
        )
        assert sanitize_filename("Movie, The (2024)") == "Movie, The (2024)"

    def test_preserves_periods(self):
        """Periods should be preserved."""
        assert sanitize_filename("D.Gray-man") == "D.Gray-man"
        assert sanitize_filename("Dr. Strangelove") == "Dr. Strangelove"

    def test_replaces_colons(self):
        """Colons become a dash, reusing the following space when there is one."""
        assert (
            sanitize_filename("Fate/stay night: Unlimited Blade Works")
            == "Fate-stay night - Unlimited Blade Works"
        )
        assert sanitize_filename("Movie: Subtitle") == "Movie - Subtitle"
        assert sanitize_filename("Re:ZERO") == "Re-ZERO"

    def test_replaces_forward_slashes(self):
        """Forward slashes become a dash (never glue the words together)."""
        assert sanitize_filename("Fate/stay night") == "Fate-stay night"
        assert sanitize_filename("Path/To/Movie") == "Path-To-Movie"

    def test_removes_question_marks(self):
        """Question marks should be removed (invalid filesystem character)."""
        assert sanitize_filename("Is the Order a Rabbit??") == "Is the Order a Rabbit"
        assert sanitize_filename("What? Movie") == "What Movie"

    def test_removes_asterisks(self):
        """Asterisks should be removed (invalid filesystem character)."""
        assert sanitize_filename("Movie*Name") == "MovieName"
        assert sanitize_filename("Title*") == "Title"

    def test_replaces_pipes(self):
        """Pipes (language separators in release titles) become a dash."""
        assert (
            sanitize_filename("Title S01 2026 WEB-DL Ukr|Eng-Wrden")
            == "Title S01 2026 WEB-DL Ukr-Eng-Wrden"
        )
        assert sanitize_filename("Ukr Jap | Sub Ukr Eng") == "Ukr Jap - Sub Ukr Eng"

    def test_real_world_movie_titles(self):
        """Test sanitization of real-world movie titles."""
        test_cases = [
            ("K-ON! The Movie (2011) 1080p BluRay", "K-ON! The Movie (2011) 1080p BluRay"),
            (
                "Re:ZERO -Starting Life in Another World- The Frozen Bond (2019) 1080p",
                "Re-ZERO -Starting Life in Another World- The Frozen Bond (2019) 1080p",
            ),
            (
                "Fate/stay night: Heaven's Feel - III. Spring Song (2020) 4K",
                "Fate-stay night - Heaven's Feel - III. Spring Song (2020) 4K",
            ),
            (
                "Love, Chunibyo & Other Delusions! Take on Me (2018) 1080p",
                "Love, Chunibyo & Other Delusions! Take on Me (2018) 1080p",
            ),
            (
                "JoJo's Bizarre Adventure: Diamond is Unbreakable Chapter I (2017) BluRay",
                "JoJo's Bizarre Adventure - Diamond is Unbreakable Chapter I (2017) BluRay",
            ),
        ]

        for original, expected in test_cases:
            result = sanitize_filename(original)
            assert result == expected, f"Failed for: {original}"

    def test_real_world_tv_titles(self):
        """Test sanitization of real-world TV show titles."""
        test_cases = [
            ("K-ON! S01 BluRay 1080p", "K-ON! S01 BluRay 1080p"),
            (
                "Re:ZERO -Starting Life in Another World- S02 1080p WEBDL",
                "Re-ZERO -Starting Life in Another World- S02 1080p WEBDL",
            ),
            (
                "Fate/stay night: Unlimited Blade Works S01 1080p",
                "Fate-stay night - Unlimited Blade Works S01 1080p",
            ),
            (
                "Love, Chunibyo & Other Delusions! S01 1080p",
                "Love, Chunibyo & Other Delusions! S01 1080p",
            ),
            ("D.Gray-man S01 1080p BluRay", "D.Gray-man S01 1080p BluRay"),
            ("Is the Order a Rabbit?? S01 1080p", "Is the Order a Rabbit S01 1080p"),
        ]

        for original, expected in test_cases:
            result = sanitize_filename(original)
            assert result == expected, f"Failed for: {original}"

    def test_preserves_valid_special_chars_in_combination(self):
        """Test that valid special characters work together."""
        title = "K-ON! & D.Gray-man: It's a Test, Really!"
        result = sanitize_filename(title)
        # Should remove : but preserve others
        assert "K-ON!" in result
        assert "&" in result
        assert "D.Gray-man" in result
        assert "It's" in result
        assert "," in result
        assert "!" in result
        assert ":" not in result


# =============================================================================
# Rename Rules Application Tests
# =============================================================================


class TestApplyRenameRulesSpecialCharacters:
    """Test applying rename rules to titles with special characters."""

    def test_no_rules_preserves_special_chars(self):
        """Without rules, special characters should be preserved (after sanitization)."""
        rules = TrackerRules()
        for title in MOVIE_TITLES + TV_TITLES:
            result = apply_rename_rules(title, rules)
            # Should preserve valid special chars, remove invalid ones
            assert result is not None
            assert len(result) > 0

    def test_prefix_with_special_chars(self):
        """Test adding prefix to titles with special characters."""
        rules = TrackerRules()
        rules.prefix = "[AUTO] "
        result = apply_rename_rules("K-ON! S01 1080p", rules)
        assert result == "[AUTO] K-ON! S01 1080p"

    def test_suffix_with_special_chars(self):
        """Test adding suffix to titles with special characters."""
        rules = TrackerRules()
        rules.suffix = " [Renamed]"
        result = apply_rename_rules("D.Gray-man S01", rules)
        assert result == "D.Gray-man S01 [Renamed]"

    def test_remove_pattern_with_special_chars(self):
        """Test remove patterns work with special characters."""
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]  # Remove release group
        result = apply_rename_rules("K-ON! S01 1080p-Group", rules)
        assert result == "K-ON! S01 1080p"

    def test_replace_pattern_with_special_chars(self):
        """Test replace patterns work with special characters."""
        rules = TrackerRules()
        rules.replace_patterns = {r"\.": " "}  # Dots to spaces
        result = apply_rename_rules("D.Gray-man S01", rules)
        assert result == "D Gray-man S01"

    def test_all_movie_titles_with_rules(self):
        """Test all movie titles with rename rules."""
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]  # Remove release group if present

        for title in MOVIE_TITLES:
            result = apply_rename_rules(title, rules)
            assert result is not None
            assert len(result) > 0
            # Should preserve valid special characters
            if "K-ON!" in title:
                assert "K-ON!" in result
            if "D.Gray-man" in title:
                assert "D.Gray-man" in result or "D Gray-man" in result  # Depending on rules

    def test_all_tv_titles_with_rules(self):
        """Test all TV titles with rename rules."""
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]

        for title in TV_TITLES:
            result = apply_rename_rules(title, rules)
            assert result is not None
            assert len(result) > 0
            # Episode identifiers should be preserved
            if "S01" in title or "S02" in title:
                assert "S01" in result or "S02" in result


# =============================================================================
# File Path Building Tests
# =============================================================================


class TestBuildFilePathSpecialCharacters:
    """Test building file paths with special characters."""

    def test_movie_file_path(self):
        """Test building file path for movie with special characters."""
        old_path = "K-ON! The Movie (2011)/K-ON! The Movie (2011) 1080p.mkv"
        new_name = "K-ON! The Movie (2011) 1080p BluRay"
        root_folder = "K-ON! The Movie (2011)"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "K-ON!" in result
        assert result.endswith(".mkv")

    def test_tv_series_file_path(self):
        """Test building file path for TV series with special characters."""
        old_path = "K-ON! S01/K-ON! S01E01 Episode.mkv"
        new_name = "K-ON! S01 1080p BluRay"
        root_folder = "K-ON! S01"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "K-ON!" in result
        assert "S01E01" in result
        assert result.endswith(".mkv")

    def test_colon_removed_in_path(self):
        """Test that colons are removed in file paths."""
        from src.rename import sanitize_filename

        old_path = "Fate/stay night S01/Fate.stay.night.S01E01.mkv"
        # new_name should be sanitized (as it would be after apply_rename_rules)
        new_name = sanitize_filename("Fate/stay night: Unlimited Blade Works S01 1080p")
        root_folder = "Fate/stay night S01"

        result = build_new_file_path(old_path, new_name, root_folder)

        # Colon should be removed by sanitize
        assert ":" not in result
        assert "S01E01" in result

    def test_apostrophe_preserved_in_path(self):
        """Test that apostrophes are preserved in file paths."""
        old_path = "JoJo's Bizarre Adventure S01/JoJo's.S01E01.mkv"
        new_name = "JoJo's Bizarre Adventure S01 1080p"
        root_folder = "JoJo's Bizarre Adventure S01"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "JoJo's" in result
        assert "S01E01" in result

    def test_ampersand_preserved_in_path(self):
        """Test that ampersands are preserved in file paths."""
        old_path = "Love, Chunibyo & Other Delusions! S01/Love.S01E01.mkv"
        new_name = "Love, Chunibyo & Other Delusions! S01 1080p"
        root_folder = "Love, Chunibyo & Other Delusions! S01"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "&" in result
        assert "S01E01" in result

    def test_period_preserved_in_path(self):
        """Test that periods are preserved in file paths."""
        old_path = "D.Gray-man S01/D.Gray-man.S01E01.mkv"
        new_name = "D.Gray-man S01 1080p"
        root_folder = "D.Gray-man S01"

        result = build_new_file_path(old_path, new_name, root_folder)

        assert "D.Gray-man" in result
        assert "S01E01" in result


# =============================================================================
# Extension Stripping Tests
# =============================================================================


class TestStripExtensionSpecialCharacters:
    """Test extension stripping with special characters."""

    def test_strips_extension_with_special_chars(self):
        """Test that extension stripping works with special characters."""
        assert strip_media_extension("K-ON! S01 1080p.mkv") == "K-ON! S01 1080p"
        assert strip_media_extension("D.Gray-man S01.mkv") == "D.Gray-man S01"
        assert strip_media_extension("JoJo's Adventure.mkv") == "JoJo's Adventure"

    def test_preserves_special_chars_after_stripping(self):
        """Test that special characters are preserved after extension stripping."""
        result = strip_media_extension("Love, Chunibyo & Other Delusions! S01.mkv")
        assert "Love, Chunibyo & Other Delusions!" in result
        assert "&" in result
        assert "," in result
        assert "!" in result


# =============================================================================
# Full Integration Tests
# =============================================================================


class TestFullRenameFlowSpecialCharacters:
    """Test the full rename flow with special characters."""

    def test_movie_rename_flow(self):
        """Test complete rename flow for a movie with special characters."""
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]

        # Simulate release title from webhook
        release_title = "K-ON! The Movie (2011) 1080p BluRay-Group"
        new_name = apply_rename_rules(release_title, rules)

        # Should have special characters preserved
        assert "K-ON!" in new_name
        assert "1080p" in new_name
        assert "BluRay" in new_name
        assert "-Group" not in new_name

        # Build file path
        old_path = "K-ON! The Movie (2011)/K-ON! The Movie (2011) 1080p.mkv"
        root_folder = "K-ON! The Movie (2011)"
        file_path = build_new_file_path(old_path, new_name, root_folder)

        assert "K-ON!" in file_path
        assert file_path.endswith(".mkv")

    def test_tv_series_rename_flow(self):
        """Test complete rename flow for TV series with special characters."""
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]

        # Simulate release title from webhook
        release_title = "K-ON! S01 1080p BluRay-Group"
        new_name = apply_rename_rules(release_title, rules)

        # Should have special characters preserved
        assert "K-ON!" in new_name
        assert "S01" in new_name

        # Build file path with episode
        old_path = "K-ON! S01/K-ON! S01E01 Episode.mkv"
        root_folder = "K-ON! S01"
        file_path = build_new_file_path(old_path, new_name, root_folder)

        assert "K-ON!" in file_path
        assert "S01E01" in file_path
        assert file_path.endswith(".mkv")

    def test_all_movie_titles_full_flow(self):
        """Test full rename flow for all movie titles."""
        rules = TrackerRules()

        for title in MOVIE_TITLES:
            # Apply rename rules
            new_name = apply_rename_rules(title, rules)
            assert new_name is not None
            assert len(new_name) > 0

            # Build file path
            old_path = f"{title}/movie.mkv"
            root_folder = title
            file_path = build_new_file_path(old_path, new_name, root_folder)

            assert file_path.endswith(".mkv")
            # Valid special characters should be preserved
            if "K-ON!" in title:
                assert "K-ON!" in file_path
            if "D.Gray-man" in title:
                assert "D.Gray-man" in file_path or "D Gray-man" in file_path

    def test_all_tv_titles_full_flow(self):
        """Test full rename flow for all TV titles."""
        rules = TrackerRules()

        for title in TV_TITLES:
            # Apply rename rules
            new_name = apply_rename_rules(title, rules)
            assert new_name is not None
            assert len(new_name) > 0

            # Build file path with episode
            old_path = f"{title}/series.S01E01.mkv"
            root_folder = title
            file_path = build_new_file_path(old_path, new_name, root_folder)

            assert file_path.endswith(".mkv")
            assert "S01E01" in file_path or "S02E01" in file_path


# =============================================================================
# Episode Extraction from Filenames with Special Characters
# =============================================================================


class TestEpisodeExtractionSpecialCharacters:
    """Test episode extraction from filenames with special characters."""

    def test_extract_from_k_on_filename(self):
        """Test extracting episode from K-ON! filename."""
        from src.rename import extract_episode_identifier

        # Standard format
        result = extract_episode_identifier("K-ON! S01E01 Disband the Club!.mkv")
        assert result == ("01", "E", "01")

        # Space format
        result = extract_episode_identifier("K-ON! S01 E02 Episode.mkv")
        assert result == ("01", "E", "02")

        # With special characters in episode title
        result = extract_episode_identifier("K-ON! S01E03 Let's Have a Party!.mkv")
        assert result == ("01", "E", "03")

    def test_extract_from_re_zero_filename(self):
        """Test extracting episode from Re:ZERO filename."""
        from src.rename import extract_episode_identifier

        # Standard format with colon and hyphen
        result = extract_episode_identifier(
            "Re:ZERO -Starting Life in Another World- S02E01 Episode.mkv"
        )
        assert result == ("02", "E", "01")

        # Space format
        result = extract_episode_identifier(
            "Re:ZERO -Starting Life in Another World- S02 E05 Episode.mkv"
        )
        assert result == ("02", "E", "05")

    def test_extract_from_fate_stay_night_filename(self):
        """Test extracting episode from Fate/stay night filename."""
        from src.rename import extract_episode_identifier

        # With slash and colon
        result = extract_episode_identifier(
            "Fate/stay night: Unlimited Blade Works S01E01 Episode.mkv"
        )
        assert result == ("01", "E", "01")

        # EP format
        result = extract_episode_identifier(
            "Fate/stay night: Unlimited Blade Works S01EP05 Episode.mkv"
        )
        assert result == ("01", "EP", "05")

    def test_extract_from_love_chunibyo_filename(self):
        """Test extracting episode from Love, Chunibyo & Other Delusions! filename."""
        from src.rename import extract_episode_identifier

        # With comma, ampersand, and exclamation
        result = extract_episode_identifier("Love, Chunibyo & Other Delusions! S01E01 Episode.mkv")
        assert result == ("01", "E", "01")

        # Space format
        result = extract_episode_identifier("Love, Chunibyo & Other Delusions! S01 E12 Episode.mkv")
        assert result == ("01", "E", "12")

    def test_extract_from_d_gray_man_filename(self):
        """Test extracting episode from D.Gray-man filename."""
        from src.rename import extract_episode_identifier

        # With period
        result = extract_episode_identifier("D.Gray-man S01E01 Episode.mkv")
        assert result == ("01", "E", "01")

        # Underscore format
        result = extract_episode_identifier("D.Gray-man S01_E05 Episode.mkv")
        assert result == ("01", "E", "05")

    def test_extract_from_jojo_filename(self):
        """Test extracting episode from JoJo's Bizarre Adventure filename."""
        from src.rename import extract_episode_identifier

        # With apostrophe and colon
        result = extract_episode_identifier(
            "JoJo's Bizarre Adventure: Stardust Crusaders S01E01 Episode.mkv"
        )
        assert result == ("01", "E", "01")

    def test_extract_from_anime_style_filename(self):
        """Test extracting episode from anime-style filenames with special characters."""
        from src.rename import extract_episode_identifier

        # K-ON! anime style
        result = extract_episode_identifier("[GM] K-ON! - 01 (BDRip 1080p).mkv")
        assert result is not None
        assert result[2] == "01"

        # Re:ZERO anime style
        result = extract_episode_identifier(
            "[Group] Re:ZERO -Starting Life in Another World- - 13 (1080p).mkv"
        )
        assert result is not None
        assert result[2] == "13"

        # Love, Chunibyo anime style
        result = extract_episode_identifier(
            "[Fansub] Love, Chunibyo & Other Delusions! - 05 (720p).mkv"
        )
        assert result is not None
        assert result[2] == "05"

    def test_extract_from_bracketed_format(self):
        """Test extracting episode from bracketed format with special characters."""
        from src.rename import extract_episode_identifier

        # Bracketed format
        result = extract_episode_identifier("[TV-1] [S01_E001] K-ON! BDRemux 1080p.mkv")
        assert result == ("01", "E", "001")

        result = extract_episode_identifier("[S01_E05] D.Gray-man 1080p.mkv")
        assert result == ("01", "E", "05")


# =============================================================================
# Episode Insertion into Titles with Special Characters
# =============================================================================


class TestEpisodeInsertionSpecialCharacters:
    """Test inserting episode identifiers into titles with special characters."""

    def test_insert_into_k_on_title(self):
        """Test inserting episode into K-ON! title."""
        from src.rename import insert_episode_into_name

        # Season-only pattern
        result = insert_episode_into_name("K-ON! S01 1080p BluRay", ("01", "E", "05"))
        assert result == "K-ON! S01E05 1080p BluRay"

        # Already has episode (should replace)
        result = insert_episode_into_name("K-ON! S01E01 1080p BluRay", ("01", "E", "05"))
        assert result == "K-ON! S01E05 1080p BluRay"

        # No season pattern (should insert)
        result = insert_episode_into_name("K-ON! 1080p BluRay", ("01", "E", "03"))
        assert "S01E03" in result

    def test_insert_into_re_zero_title(self):
        """Test inserting episode into Re:ZERO title."""
        from src.rename import insert_episode_into_name

        # With colon and hyphen (colon will be removed by sanitize, but test insertion logic)
        result = insert_episode_into_name(
            "Re:ZERO -Starting Life in Another World- S02 1080p", ("02", "E", "01")
        )
        assert result == "Re:ZERO -Starting Life in Another World- S02E01 1080p"

        # Already has episode
        result = insert_episode_into_name(
            "Re:ZERO -Starting Life in Another World- S02E05 1080p", ("02", "E", "01")
        )
        assert result == "Re:ZERO -Starting Life in Another World- S02E01 1080p"

    def test_insert_into_fate_stay_night_title(self):
        """Test inserting episode into Fate/stay night title."""
        from src.rename import insert_episode_into_name

        # With slash and colon
        result = insert_episode_into_name(
            "Fate/stay night: Unlimited Blade Works S01 1080p", ("01", "E", "01")
        )
        assert result == "Fate/stay night: Unlimited Blade Works S01E01 1080p"

    def test_insert_into_love_chunibyo_title(self):
        """Test inserting episode into Love, Chunibyo & Other Delusions! title."""
        from src.rename import insert_episode_into_name

        # With comma, ampersand, and exclamation
        result = insert_episode_into_name(
            "Love, Chunibyo & Other Delusions! S01 1080p", ("01", "E", "01")
        )
        assert result == "Love, Chunibyo & Other Delusions! S01E01 1080p"

    def test_insert_into_d_gray_man_title(self):
        """Test inserting episode into D.Gray-man title."""
        from src.rename import insert_episode_into_name

        # With period
        result = insert_episode_into_name("D.Gray-man S01 1080p BluRay", ("01", "E", "01"))
        assert result == "D.Gray-man S01E01 1080p BluRay"

    def test_insert_into_jojo_title(self):
        """Test inserting episode into JoJo's Bizarre Adventure title."""
        from src.rename import insert_episode_into_name

        # With apostrophe and colon
        result = insert_episode_into_name(
            "JoJo's Bizarre Adventure: Stardust Crusaders S01 1080p", ("01", "E", "01")
        )
        assert result == "JoJo's Bizarre Adventure: Stardust Crusaders S01E01 1080p"


# =============================================================================
# Full Flow: Extract from Filename → Apply Rules → Insert Episode
# =============================================================================


class TestFullFlowSpecialCharacters:
    """Test the complete flow: extract episode from filename → apply rules → insert into new name."""

    def test_k_on_full_flow(self):
        """Test complete flow for K-ON! series."""
        from src.rename import apply_rename_rules, build_new_file_path, extract_episode_identifier

        # Original filename with special characters
        old_filename = "K-ON! S01E01 Disband the Club!.mkv"
        old_path = "K-ON! S01 Complete/K-ON! S01E01 Disband the Club!.mkv"
        root_folder = "K-ON! S01 Complete"

        # Release title from webhook
        release_title = "K-ON! S01 1080p BluRay-Group"

        # Step 1: Extract episode from filename
        episode_info = extract_episode_identifier(old_filename)
        assert episode_info == ("01", "E", "01")

        # Step 2: Apply rename rules
        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        new_name = apply_rename_rules(release_title, rules)
        assert "K-ON!" in new_name
        assert "S01" in new_name
        assert "-Group" not in new_name

        # Step 3: Build new file path (this internally uses insert_episode_into_name)
        new_path = build_new_file_path(
            old_path, new_name, root_folder, episode_override=episode_info
        )

        # Verify result
        assert "K-ON!" in new_path
        assert "S01E01" in new_path
        assert new_path.endswith(".mkv")
        assert "-Group" not in new_path

    def test_re_zero_full_flow(self):
        """Test complete flow for Re:ZERO series."""
        from src.rename import apply_rename_rules, build_new_file_path, extract_episode_identifier

        old_filename = "Re:ZERO -Starting Life in Another World- S02E05 Episode.mkv"
        old_path = "Re:ZERO S02/Re:ZERO -Starting Life in Another World- S02E05 Episode.mkv"
        root_folder = "Re:ZERO S02"

        release_title = "Re:ZERO -Starting Life in Another World- S02 1080p WEBDL-Group"

        episode_info = extract_episode_identifier(old_filename)
        assert episode_info == ("02", "E", "05")

        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        new_name = apply_rename_rules(release_title, rules)

        new_path = build_new_file_path(
            old_path, new_name, root_folder, episode_override=episode_info
        )

        assert "Re-ZERO" in new_path  # Colon is illegal in a path, replaced with a dash
        assert ":" not in new_path
        assert "S02E05" in new_path
        assert new_path.endswith(".mkv")

    def test_fate_stay_night_full_flow(self):
        """Test complete flow for Fate/stay night series."""
        from src.rename import apply_rename_rules, build_new_file_path, extract_episode_identifier

        old_filename = "Fate/stay night: Unlimited Blade Works S01E01 Episode.mkv"
        old_path = "Fate/stay night S01/Fate/stay night: Unlimited Blade Works S01E01 Episode.mkv"
        root_folder = "Fate/stay night S01"

        release_title = "Fate/stay night: Unlimited Blade Works S01 1080p-Group"

        episode_info = extract_episode_identifier(old_filename)
        assert episode_info == ("01", "E", "01")

        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        new_name = apply_rename_rules(release_title, rules)

        new_path = build_new_file_path(
            old_path, new_name, root_folder, episode_override=episode_info
        )

        assert "S01E01" in new_path
        assert new_path.endswith(".mkv")

    def test_love_chunibyo_full_flow(self):
        """Test complete flow for Love, Chunibyo & Other Delusions! series."""
        from src.rename import apply_rename_rules, build_new_file_path, extract_episode_identifier

        old_filename = "Love, Chunibyo & Other Delusions! S01E01 Episode.mkv"
        old_path = "Love, Chunibyo & Other Delusions! S01/Love, Chunibyo & Other Delusions! S01E01 Episode.mkv"
        root_folder = "Love, Chunibyo & Other Delusions! S01"

        release_title = "Love, Chunibyo & Other Delusions! S01 1080p-Group"

        episode_info = extract_episode_identifier(old_filename)
        assert episode_info == ("01", "E", "01")

        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        new_name = apply_rename_rules(release_title, rules)

        new_path = build_new_file_path(
            old_path, new_name, root_folder, episode_override=episode_info
        )

        assert "Love, Chunibyo & Other Delusions!" in new_path
        assert "&" in new_path
        assert "S01E01" in new_path
        assert new_path.endswith(".mkv")

    def test_d_gray_man_full_flow(self):
        """Test complete flow for D.Gray-man series."""
        from src.rename import apply_rename_rules, build_new_file_path, extract_episode_identifier

        old_filename = "D.Gray-man S01E01 Episode.mkv"
        old_path = "D.Gray-man S01/D.Gray-man S01E01 Episode.mkv"
        root_folder = "D.Gray-man S01"

        release_title = "D.Gray-man S01 1080p BluRay-Group"

        episode_info = extract_episode_identifier(old_filename)
        assert episode_info == ("01", "E", "01")

        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        new_name = apply_rename_rules(release_title, rules)

        new_path = build_new_file_path(
            old_path, new_name, root_folder, episode_override=episode_info
        )

        assert "D.Gray-man" in new_path
        assert "S01E01" in new_path
        assert new_path.endswith(".mkv")

    def test_anime_style_full_flow(self):
        """Test complete flow for anime-style filenames."""
        from src.rename import apply_rename_rules, build_new_file_path, extract_episode_identifier

        # Anime style filename
        old_filename = "[GM] K-ON! - 01 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
        old_path = "K-ON! [BDRip]/[GM] K-ON! - 01 (BDRip 1080p HEVC AAC) Ukr DVO SUB.mkv"
        root_folder = "K-ON! [BDRip]"

        release_title = "K-ON! S01 BluRay 1080p-Group"

        episode_info = extract_episode_identifier(old_filename)
        assert episode_info is not None
        assert episode_info[2] == "01"

        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        new_name = apply_rename_rules(release_title, rules)

        new_path = build_new_file_path(
            old_path, new_name, root_folder, episode_override=episode_info
        )

        assert "K-ON!" in new_path
        assert "S01E01" in new_path
        assert new_path.endswith(".mkv")

    def test_multiple_episodes_same_series(self):
        """Test that multiple episodes from same series work correctly."""
        from src.rename import apply_rename_rules, build_new_file_path, extract_episode_identifier

        rules = TrackerRules()
        rules.remove_patterns = [r"-\w+$"]
        release_title = "K-ON! S01 1080p BluRay-Group"
        new_name = apply_rename_rules(release_title, rules)
        root_folder = "K-ON! S01 Complete"

        # Test multiple episodes
        episodes = [
            ("K-ON! S01E01 Disband the Club!.mkv", "01"),
            ("K-ON! S01E02 Entrance Ceremony!.mkv", "02"),
            ("K-ON! S01E03 After School!.mkv", "03"),
            ("K-ON! S01E12 Light Music!.mkv", "12"),
        ]

        new_paths = []
        for old_filename, expected_ep in episodes:
            old_path = f"{root_folder}/{old_filename}"
            episode_info = extract_episode_identifier(old_filename)
            assert episode_info == ("01", "E", expected_ep)

            new_path = build_new_file_path(
                old_path, new_name, root_folder, episode_override=episode_info
            )
            new_paths.append(new_path)

            # Verify each path is unique and correct
            assert f"S01E{expected_ep}" in new_path
            assert "K-ON!" in new_path

        # All paths should be unique
        assert len(set(new_paths)) == len(episodes)

    def test_regex_special_characters_dont_break_extraction(self):
        """Test that regex special characters in filenames don't break episode extraction."""
        from src.rename import extract_episode_identifier

        # Test various special characters that could interfere with regex
        test_cases = [
            ("K-ON! S01E01 Episode.mkv", ("01", "E", "01")),
            ("Re:ZERO S02E05 Episode.mkv", ("02", "E", "05")),
            ("Fate/stay night S01E01 Episode.mkv", ("01", "E", "01")),
            ("Love, Chunibyo & Other Delusions! S01E01 Episode.mkv", ("01", "E", "01")),
            ("D.Gray-man S01E01 Episode.mkv", ("01", "E", "01")),
            ("JoJo's Bizarre Adventure S01E01 Episode.mkv", ("01", "E", "01")),
            ("Is the Order a Rabbit?? S01E01 Episode.mkv", ("01", "E", "01")),
            # Test with regex special chars in episode title
            ("K-ON! S01E01 Episode (.) [*] {+} ?.mkv", ("01", "E", "01")),
            ("Series S01E05 Episode [Special] (2024).mkv", ("01", "E", "05")),
        ]

        for filename, expected in test_cases:
            result = extract_episode_identifier(filename)
            assert result == expected, f"Failed for: {filename}"

    def test_regex_special_characters_dont_break_insertion(self):
        """Test that regex special characters in titles don't break episode insertion."""
        from src.rename import insert_episode_into_name

        # Test various special characters that could interfere with regex
        test_cases = [
            ("K-ON! S01 1080p", "K-ON! S01E01 1080p"),
            ("Re:ZERO S02 1080p", "Re:ZERO S02E01 1080p"),
            (
                "Fate/stay night: Unlimited Blade Works S01 1080p",
                "Fate/stay night: Unlimited Blade Works S01E01 1080p",
            ),
            (
                "Love, Chunibyo & Other Delusions! S01 1080p",
                "Love, Chunibyo & Other Delusions! S01E01 1080p",
            ),
            ("D.Gray-man S01 1080p", "D.Gray-man S01E01 1080p"),
            ("JoJo's Bizarre Adventure S01 1080p", "JoJo's Bizarre Adventure S01E01 1080p"),
        ]

        for title, expected in test_cases:
            result = insert_episode_into_name(title, ("01", "E", "01"))
            assert result == expected, f"Failed for: {title}, got: {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
