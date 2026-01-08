"""Tests for rename logic."""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.config import RenameRules
from src.rename import apply_rename_rules, sanitize_filename, matches_any


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


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
