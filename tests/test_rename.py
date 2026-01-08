"""Tests for rename logic."""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config import RenameRules
from src.rename import (
    apply_rename_rules,
    sanitize_filename,
    matches_any,
    extract_episode_identifier,
    build_episode_identifier,
    insert_episode_into_name,
    build_new_file_path,
)


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_removes_invalid_chars(self):
        assert sanitize_filename('test<>:"/\\|?*.txt') == 'test.txt'

    def test_collapses_multiple_spaces(self):
        assert sanitize_filename('test   file   name') == 'test file name'

    def test_trims_whitespace(self):
        assert sanitize_filename('  test  ') == 'test'

    def test_limits_length(self):
        long_name = 'a' * 300
        result = sanitize_filename(long_name)
        assert len(result) == 250

    def test_removes_control_characters(self):
        assert sanitize_filename('test\x00\x1f\x7ffile') == 'testfile'


class TestMatchesAny:
    """Test regex pattern matching."""

    def test_matches_simple_pattern(self):
        assert matches_any('TrackerA indexer', ['TrackerA.*']) is True

    def test_no_match(self):
        assert matches_any('Other tracker', ['TrackerA.*']) is False

    def test_case_insensitive(self):
        assert matches_any('trackera indexer', ['TrackerA.*']) is True

    def test_empty_patterns(self):
        assert matches_any('anything', []) is False

    def test_multiple_patterns(self):
        assert matches_any('IndexerB', ['TrackerA.*', 'Indexer.*']) is True


class TestApplyRenameRules:
    """Test rename rules application."""

    def test_no_rules(self):
        rules = RenameRules()
        result = apply_rename_rules('Original Name', rules)
        assert result == 'Original Name'

    def test_add_prefix(self):
        rules = RenameRules()
        rules.prefix = '[AUTO] '
        result = apply_rename_rules('Movie Name', rules)
        assert result == '[AUTO] Movie Name'

    def test_add_suffix(self):
        rules = RenameRules()
        rules.suffix = ' [Renamed]'
        result = apply_rename_rules('Movie Name', rules)
        assert result == 'Movie Name [Renamed]'

    def test_remove_pattern(self):
        rules = RenameRules()
        rules.remove_patterns = [r'-\w+$']  # Remove release group
        result = apply_rename_rules('Movie.2024.1080p.WEB-GroupX', rules)
        assert result == 'Movie.2024.1080p.WEB'

    def test_replace_pattern(self):
        rules = RenameRules()
        rules.replace_patterns = {r'\.': ' '}  # Dots to spaces
        result = apply_rename_rules('Movie.Name.2024', rules)
        assert result == 'Movie Name 2024'

    def test_skip_pattern(self):
        rules = RenameRules()
        rules.remove_patterns = [r'-\w+$']
        rules.skip_title_patterns = ['PROPER']
        
        # Should NOT be modified because of skip pattern
        result = apply_rename_rules('Movie.2024.PROPER-GROUP', rules)
        assert result == 'Movie.2024.PROPER-GROUP'

    def test_combined_rules(self):
        rules = RenameRules()
        rules.remove_patterns = [r'\[.*?\]', r'-\w+$']
        rules.replace_patterns = {r'\.': ' ', r'\s+': ' '}
        
        result = apply_rename_rules('[TAG] Movie.Name.2024.1080p-GroupX', rules)
        assert result == 'Movie Name 2024 1080p'


class TestRenameRulesFromSampleData:
    """Test with sample data from logg.txt."""

    def test_radarr_release_title(self):
        """Test with actual release title from a Radarr grab."""
        rules = RenameRules()
        rules.remove_patterns = [r'-\w+$']  # Remove release group
        
        original = 'Example Movie 2020 DE 4K Remaster BluRay 1080p ENG H.265-ReleaseGrp'
        result = apply_rename_rules(original, rules)
        
        assert result == 'Example Movie 2020 DE 4K Remaster BluRay 1080p ENG H.265'
        assert 'ReleaseGrp' not in result

    def test_clean_release_title(self):
        """Test cleaning up a release title with multiple rules."""
        rules = RenameRules()
        rules.remove_patterns = [
            r'\[.*?\]',       # Remove bracketed tags
            r'-\w+$',         # Remove release group
        ]
        rules.replace_patterns = {
            r'\.': ' ',       # Dots to spaces
            r'\s+': ' ',      # Multiple spaces to single
        }
        
        original = '[TAG] Movie.Name.2024.1080p.HEVC.x265-GroupX'
        result = apply_rename_rules(original, rules)
        
        assert result == 'Movie Name 2024 1080p HEVC x265'


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
        
        assert result == "SeriesX S01 IT WEBDL 1080p ReleaseGroup/SeriesX S01E02 IT WEBDL 1080p ReleaseGroup.mkv"

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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
