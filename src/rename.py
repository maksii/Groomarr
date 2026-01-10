"""Rename logic with trigger filters and rule application."""

import logging
import re
from enum import Enum
from typing import Any

from .config import RenameRules
from .models import RadarrWebhook, SonarrWebhook
from .qbittorrent import QBitClient

logger = logging.getLogger(__name__)


class RenameMode(str, Enum):
    """Available rename modes."""

    TORRENT_ONLY = "torrent_only"
    TORRENT_AND_FOLDER = "torrent_and_folder"
    TORRENT_FOLDER_FILES = "torrent_folder_files"
    FOLDER_ONLY = "folder_only"
    FILES_ONLY = "files_only"


# =============================================================================
# Trigger Filters
# =============================================================================


def matches_any(value: str, patterns: list[str]) -> bool:
    """Check if value matches any regex pattern (case-insensitive).

    Args:
        value: String to check
        patterns: List of regex patterns

    Returns:
        True if value matches any pattern
    """
    if not patterns:
        return False
    return any(re.search(p, value, re.IGNORECASE) for p in patterns)


def should_process(payload: RadarrWebhook | SonarrWebhook, rules: RenameRules) -> tuple[bool, str]:
    """Check if webhook should be processed based on trigger filters.

    Args:
        payload: Webhook payload from Sonarr or Radarr
        rules: Rename rules with filters

    Returns:
        Tuple of (should_process, skip_reason)
    """
    # Download client filter
    download_client = payload.downloadClient or ""
    if rules.download_clients_include:
        if not matches_any(download_client, rules.download_clients_include):
            return False, f"download_client '{download_client}' not in include list"
    if matches_any(download_client, rules.download_clients_exclude):
        return False, f"download_client '{download_client}' in exclude list"

    # Indexer filter
    indexer = payload.release.indexer or ""
    if rules.indexers_include:
        if not matches_any(indexer, rules.indexers_include):
            return False, f"indexer '{indexer}' not in include list"
    if matches_any(indexer, rules.indexers_exclude):
        return False, f"indexer '{indexer}' in exclude list"

    # Quality filter
    quality = payload.release.quality or ""
    if rules.qualities_include:
        if not matches_any(quality, rules.qualities_include):
            return False, f"quality '{quality}' not in include list"
    if matches_any(quality, rules.qualities_exclude):
        return False, f"quality '{quality}' in exclude list"

    # Release group filter
    group = payload.release.releaseGroup or ""
    if rules.release_groups_include:
        if group and not matches_any(group, rules.release_groups_include):
            return False, f"release_group '{group}' not in include list"
    if group and matches_any(group, rules.release_groups_exclude):
        return False, f"release_group '{group}' in exclude list"

    # Custom format filters
    cf_list = payload.release.customFormats or []
    if rules.customformats_require_any:
        if not any(cf in cf_list for cf in rules.customformats_require_any):
            return False, "no required custom formats present"
    if rules.customformats_exclude:
        excluded = [cf for cf in rules.customformats_exclude if cf in cf_list]
        if excluded:
            return False, f"excluded custom format '{excluded[0]}' present"

    # Custom format score
    if rules.min_customformat_score is not None:
        score = payload.release.customFormatScore or 0
        if score < rules.min_customformat_score:
            return False, f"score {score} < min {rules.min_customformat_score}"

    return True, ""


# =============================================================================
# Rename Rules Application
# =============================================================================


def strip_media_extension(name: str) -> str:
    """Remove common video/media file extensions from a name.

    This handles the case when users configure Sonarr/Radarr to use filenames
    instead of release names, which may include file extensions.

    Args:
        name: Name that may contain a file extension

    Returns:
        Name with media extension stripped (if present)
    """
    # Common video/media file extensions (case-insensitive)
    media_extensions = (
        ".mkv",
        ".mp4",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".mpeg",
        ".mpg",
        ".ts",
        ".m2ts",
        ".vob",
        ".divx",
        ".xvid",
        ".3gp",
        ".ogv",
        ".rm",
        ".rmvb",
        ".asf",
    )

    name_lower = name.lower()
    for ext in media_extensions:
        if name_lower.endswith(ext):
            return name[: -len(ext)]
    return name


def sanitize_filename(name: str) -> str:
    """Remove/replace invalid filesystem characters.

    Args:
        name: Original filename

    Returns:
        Sanitized filename safe for filesystem
    """
    # Remove invalid characters: < > : " / \ | ? *
    invalid_chars = r'[<>:"/\\|?*]'
    name = re.sub(invalid_chars, "", name)

    # Remove control characters
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)

    # Collapse multiple spaces
    name = re.sub(r"\s+", " ", name)

    # Trim whitespace and limit length
    name = name.strip()[:250]

    return name


def apply_rename_rules(original_name: str, rules: RenameRules) -> str:
    """Apply rename rules to transform the name.

    Args:
        original_name: Original release title
        rules: Rename rules to apply

    Returns:
        Transformed name
    """
    name = original_name

    # 1. Strip media extension early (handles filename-based release titles)
    # This must happen before replace patterns to avoid ".mkv" becoming " mkv"
    name = strip_media_extension(name)

    # 2. Check skip patterns
    for pattern in rules.skip_title_patterns:
        if re.search(pattern, name, re.IGNORECASE):
            logger.debug(f"Skipping rename due to skip pattern: {pattern}")
            return strip_media_extension(original_name)

    # 3. Remove patterns
    for pattern in rules.remove_patterns:
        try:
            name = re.sub(pattern, "", name)
        except re.error as e:
            logger.warning(f"Invalid remove pattern '{pattern}': {e}")

    # 3. Replace patterns
    for pattern, replacement in rules.replace_patterns.items():
        try:
            name = re.sub(pattern, replacement, name)
        except re.error as e:
            logger.warning(f"Invalid replace pattern '{pattern}': {e}")

    # 4. Add prefix/suffix
    name = f"{rules.prefix}{name.strip()}{rules.suffix}"

    # 5. Sanitize for filesystem
    name = sanitize_filename(name)

    return name


# =============================================================================
# Episode Identifier Handling
# =============================================================================

# Pattern to match episode identifiers: S01E01, S01 E01, S01EP01, S01_E001, etc.
# Captures: season number, separator, episode marker (E or EP), episode number
# Handles: S01E01, S01 E01, S01EP01, S01_E001, [S01_E001], etc.
EPISODE_PATTERN = re.compile(r"S(\d+)[\s_]*(E(?:P)?)\s*(\d+)", re.IGNORECASE)

# Pattern for bracketed season_episode: [S01_E001], [S01 E05]
BRACKETED_EPISODE_PATTERN = re.compile(r"\[S(\d+)[\s_]*(E(?:P)?)\s*(\d+)\]", re.IGNORECASE)

# Pattern to match season-only identifier: S01, S02, etc.
SEASON_ONLY_PATTERN = re.compile(r"S(\d+)(?![\s_]*E)", re.IGNORECASE)

# Unicode dashes: regular hyphen (-), en-dash (–), em-dash (—)
DASHES = r"\-\u2013\u2014"

# Alternative episode patterns for anime and other formats
# These patterns extract just the episode number (no season)
ALTERNATIVE_EPISODE_PATTERNS = [
    # Anime style with any dash: "- 10", "– 01", "— 13" (hyphen/en-dash/em-dash separated)
    re.compile(rf"[\s{DASHES}_][{DASHES}]\s*(\d{{1,4}})(?:\s|\.|\)|$|\[)", re.IGNORECASE),
    # Episode marker: "Episode 10", "Ep 05", "EP10", "Ep.10"
    re.compile(r"\b(?:Episode|Ep)[\.\s]*(\d{1,4})\b", re.IGNORECASE),
    # Hash number: "#10", "# 05"
    re.compile(r"#\s*(\d{1,4})\b"),
    # "X of Y" or "X з Y" (Ukrainian) or "X из Y" (Russian) pattern: [12 з 12], [5 of 10]
    re.compile(r"\[(\d{1,4})\s*(?:з|of|из|von)\s*\d+\]", re.IGNORECASE),
    # Bracketed standalone episode: "[10]" or "(10)" but NOT "[1080p]" or "(2024)"
    re.compile(r"[\[\(](\d{1,3})[\]\)](?!\s*[pi])"),
    # Number immediately before bracket: "Name 15[WEBRip" or "Name 01["
    re.compile(r"\s(\d{1,4})(?=[\[\(])"),
    # Trailing space + number before extension: "Name 01.mkv", "Name 15.mkv"
    re.compile(r"\s(\d{1,4})(?:\.(?:mkv|mp4|avi|mov|wmv|flv|webm|m4v|ts))?$", re.IGNORECASE),
    # Dot or underscore separated standalone number (not year, not resolution)
    # Matches: ".10." or "_10_" but not ".1080." or ".2024."
    re.compile(r"[._](\d{1,3})[._](?!\d)"),
]


def extract_episode_identifier(filename: str) -> tuple[str, str, str] | None:
    """Extract episode identifier from a filename.

    Matches patterns like: S01E01, S01 E01, S01EP01, s01e01, S01 EP01, [S01_E001], etc.
    Also handles anime patterns like: "- 10", "– 10", "Episode 10", "#10", "[12 з 12]"

    Args:
        filename: Original filename to extract from

    Returns:
        Tuple of (season_num, episode_marker, episode_num) if found, None otherwise
        Example: ("01", "E", "02") for S01E02 or S01 E02
        For anime patterns without season: ("01", "E", "10") using season 01 as default
    """
    # First try bracketed pattern [S01_E001] - common in some release groups
    match = BRACKETED_EPISODE_PATTERN.search(filename)
    if match:
        season_num = match.group(1)
        ep_marker = match.group(2).upper()
        episode_num = match.group(3)
        return (season_num, ep_marker, episode_num)

    # Try standard S01E01 pattern
    match = EPISODE_PATTERN.search(filename)
    if match:
        season_num = match.group(1)
        ep_marker = match.group(2).upper()  # Normalize to uppercase
        episode_num = match.group(3)
        return (season_num, ep_marker, episode_num)

    # Try alternative patterns (anime, etc.) - these only have episode number
    for pattern in ALTERNATIVE_EPISODE_PATTERNS:
        match = pattern.search(filename)
        if match:
            episode_num = match.group(1)
            # Skip if this looks like a year (1900-2099) or resolution (480, 720, 1080, etc.)
            num_int = int(episode_num)
            if 1900 <= num_int <= 2099:
                continue  # Likely a year
            if num_int in (480, 576, 720, 1080, 2160, 4320):
                continue  # Likely a resolution
            # Preserve original padding if 3+ digits (e.g., 001, 450)
            # Otherwise zero-pad to 2 digits for consistency
            episode_num_padded = episode_num if len(episode_num) >= 3 else episode_num.zfill(2)
            # Default to season 01 for anime-style patterns
            return ("01", "E", episode_num_padded)

    return None


def _is_valid_episode_sequence(numbers: list[int], file_count: int) -> tuple[bool, float]:
    """Check if a list of numbers forms a valid episode sequence.

    A valid episode sequence should:
    - Be unique numbers (no duplicates)
    - Be in ascending order (when sorted)
    - Not have excessively large gaps
    - Start from a reasonable episode number

    Args:
        numbers: List of candidate episode numbers
        file_count: Number of files being analyzed

    Returns:
        Tuple of (is_valid, score) where score indicates sequence quality (higher is better)
    """
    if len(numbers) != file_count:
        return False, 0.0

    sorted_nums = sorted(numbers)
    min_num, max_num = sorted_nums[0], sorted_nums[-1]

    # Calculate range statistics
    actual_range = max_num - min_num + 1
    expected_range = file_count

    # Reject if minimum is unreasonably high (episodes usually start low)
    # Allow up to 999 for long-running series like anime (Naruto has 700+)
    if min_num > 999:
        return False, 0.0

    # Reject if max is unreasonably high relative to file count
    # For example, 5 files shouldn't have numbers like 500, 600, 700, 800, 900
    # Allow max to be reasonable for episode counts
    max_reasonable = max(file_count * 50, 100)  # At least 100, or 50x file count
    if max_num > max_reasonable:
        return False, 0.0

    # Check for ascending sequence with reasonable gaps
    # Calculate gap statistics
    gaps = [sorted_nums[i + 1] - sorted_nums[i] for i in range(len(sorted_nums) - 1)]
    max_gap = max(gaps) if gaps else 1
    avg_gap = sum(gaps) / len(gaps) if gaps else 1

    # Reject if there are huge gaps between consecutive episodes
    # Allow gaps up to 10 for anime (missing episodes, specials, etc.)
    if max_gap > 10:
        return False, 0.0

    # Calculate quality score (higher is better)
    # Perfect sequence (1,2,3...) scores 1.0
    # Sequences with gaps score lower
    # Sequences starting from 1 or 0 get a bonus
    contiguity_score = expected_range / actual_range  # 1.0 for perfect, lower for gaps
    start_bonus = 0.2 if min_num <= 1 else 0.0
    gap_penalty = (avg_gap - 1) * 0.05  # Small penalty for average gap > 1

    score = contiguity_score + start_bonus - gap_penalty

    return True, score


def extract_episode_from_batch(filenames: list[str]) -> dict[str, str]:
    """Analyze a batch of filenames to find unique episode identifiers.

    When standard episode patterns fail, this function analyzes all filenames
    together to find numbers that uniquely identify each file in a proper
    ascending sequence.

    The function validates that:
    - Each file has a unique number
    - Numbers form an ascending sequence
    - Sequence is reasonably contiguous (no huge gaps)
    - Numbers are plausible episode numbers (not years, resolutions, etc.)

    Args:
        filenames: List of filenames (without path) to analyze

    Returns:
        Dict mapping filename to episode number (zero-padded), or empty if analysis fails
    """
    if len(filenames) <= 1:
        return {}

    # Extract all potential episode numbers from each filename
    # Pattern to find standalone numbers (not years, not resolutions)
    number_pattern = re.compile(r"(?<![0-9])(\d{1,4})(?![0-9])")

    # Collect all numbers found in each filename with their positions
    file_numbers: dict[str, list[tuple[int, int]]] = {}  # filename -> [(position, number), ...]

    for fname in filenames:
        # Remove extension for analysis
        name_no_ext = fname.rsplit(".", 1)[0] if "." in fname else fname
        numbers = []
        for match in number_pattern.finditer(name_no_ext):
            num = int(match.group(1))
            # Skip years and resolutions
            if 1900 <= num <= 2099:
                continue
            if num in (480, 576, 720, 1080, 2160, 4320):
                continue
            numbers.append((match.start(), num))
        file_numbers[fname] = numbers

    if not file_numbers:
        return {}

    # Find which number position has unique values that form a valid sequence
    # Group numbers by their position index within each filename
    max_positions = max(len(nums) for nums in file_numbers.values()) if file_numbers else 0

    best_result: dict[str, str] | None = None
    best_score = 0.0

    for pos_idx in range(max_positions):
        # Get the number at this position from each file, preserving original string
        pos_numbers: dict[str, tuple[int, str]] = {}  # fname -> (int_value, original_str)
        for fname, nums in file_numbers.items():
            if pos_idx < len(nums):
                pos, num_int = nums[pos_idx]
                # Extract the original number string from filename to preserve padding
                name_no_ext = fname.rsplit(".", 1)[0] if "." in fname else fname
                # Find the original string representation
                match = re.search(r"(?<![0-9])(\d{1,4})(?![0-9])", name_no_ext[pos:])
                original_str = match.group(1) if match else str(num_int)
                pos_numbers[fname] = (num_int, original_str)

        # Check if we have a number from every file at this position
        if len(pos_numbers) != len(filenames):
            continue

        # Check if all numbers are unique
        unique_nums = [v[0] for v in pos_numbers.values()]
        if len(set(unique_nums)) != len(filenames):
            continue

        # Validate that numbers form a proper ascending sequence
        is_valid, score = _is_valid_episode_sequence(unique_nums, len(filenames))
        if not is_valid:
            continue

        # Track the best sequence found (highest score)
        if score > best_score:
            best_score = score
            sorted_nums = sorted(set(unique_nums))
            min_num, max_num = sorted_nums[0], sorted_nums[-1]

            # Determine padding width:
            # 1. Check if source files have consistent padding (e.g., 001, 002)
            # 2. Otherwise use max number width, minimum 2
            original_lengths = [len(v[1]) for v in pos_numbers.values()]
            if len(set(original_lengths)) == 1 and original_lengths[0] >= 2:
                # All files have same padding, preserve it
                pad_width = original_lengths[0]
            else:
                # Mixed or no padding, use sensible default
                pad_width = max(2, len(str(max_num)))

            best_result = {}
            for fname, (num_int, _) in pos_numbers.items():
                best_result[fname] = str(num_int).zfill(pad_width)

            logger.debug(
                f"Batch analysis found episode sequence at position {pos_idx}: "
                f"{min_num}-{max_num} for {len(filenames)} files (score: {score:.2f})"
            )

    return best_result or {}


def build_episode_identifier(season: str, ep_marker: str, episode: str) -> str:
    """Build a normalized episode identifier string.

    Args:
        season: Season number (e.g., "01")
        ep_marker: Episode marker (e.g., "E" or "EP")
        episode: Episode number (e.g., "02")

    Returns:
        Normalized identifier like "S01E02"
    """
    # Normalize EP to E for consistency
    marker = "E" if ep_marker.upper() in ("E", "EP") else ep_marker.upper()
    return f"S{season}{marker}{episode}"


def insert_episode_into_name(new_name: str, episode_info: tuple[str, str, str]) -> str:
    """Insert episode identifier into the new name.

    If the new name has a season-only pattern (e.g., "S01"), replace it
    with the full season+episode identifier (e.g., "S01E02").

    Args:
        new_name: The new name (typically from release title)
        episode_info: Tuple of (season_num, ep_marker, episode_num)

    Returns:
        New name with episode identifier inserted
    """
    season_num, ep_marker, episode_num = episode_info
    full_identifier = build_episode_identifier(season_num, ep_marker, episode_num)

    # Check if new_name already has an episode pattern FIRST
    # This must be checked before season-only pattern to avoid partial matches
    if EPISODE_PATTERN.search(new_name):
        # Already has episode info, replace it with the correct one
        return EPISODE_PATTERN.sub(full_identifier, new_name, count=1)

    # Check if new_name has season-only pattern (no episode)
    season_match = SEASON_ONLY_PATTERN.search(new_name)
    if season_match:
        # Replace season-only with full season+episode
        return SEASON_ONLY_PATTERN.sub(full_identifier, new_name, count=1)

    # No season pattern found, try to insert after series name
    # Look for a good insertion point (before quality markers, year, etc.)
    # Common patterns to insert before: year (4 digits), quality (1080p, 720p, etc.)
    insertion_patterns = [
        r"\b(19|20)\d{2}\b",  # Year
        r"\b\d{3,4}p\b",  # Resolution like 1080p, 720p
        r"\b(HDTV|WEBDL|WEB-DL|BLURAY|BDRIP|WEBRIP)\b",  # Source
    ]

    for pattern in insertion_patterns:
        match = re.search(pattern, new_name, re.IGNORECASE)
        if match:
            insert_pos = match.start()
            # Insert episode identifier before this match
            prefix = new_name[:insert_pos].rstrip()
            suffix = new_name[insert_pos:]
            return f"{prefix} {full_identifier} {suffix}"

    # Fallback: append before the last word (often release group)
    parts = new_name.rsplit(" ", 1)
    if len(parts) == 2:
        return f"{parts[0]} {full_identifier} {parts[1]}"

    # Last resort: just append
    return f"{new_name} {full_identifier}"


# =============================================================================
# Safety Checks
# =============================================================================


class RenameConflictError(Exception):
    """Raised when rename would result in file conflicts."""

    pass


def validate_rename_plan(
    files: list[dict[str, Any]],
    new_name: str,
    root_folder: str | None,
    preserve_folder: bool = False,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Validate rename plan and detect potential conflicts.

    Checks if renaming files would result in duplicate filenames,
    which would cause data loss.

    Args:
        files: List of file info dicts from qBittorrent
        new_name: New name to use for renaming
        root_folder: Root folder if exists
        preserve_folder: If True, keep files in original folder (for renaming
                         files before folder). If False, use new_name as folder.

    Returns:
        Tuple of (rename_plan, warnings) where:
        - rename_plan: List of (old_path, new_path) tuples
        - warnings: List of warning messages (empty if all OK)

    Raises:
        RenameConflictError: If all files would be renamed to the same name
    """
    if not files:
        return [], []

    warnings: list[str] = []
    rename_plan: list[tuple[str, str]] = []

    # Get all original filenames for batch analysis
    original_filenames = []
    for f in files:
        path = f.get("name", "")
        filename = path.rsplit("/", 1)[-1] if "/" in path else path
        original_filenames.append(filename)

    # First pass: try standard episode extraction
    paths_with_episodes = []
    paths_without_episodes = []

    for f in files:
        old_path = f.get("name", "")
        filename = old_path.rsplit("/", 1)[-1] if "/" in old_path else old_path
        episode_info = extract_episode_identifier(filename)
        if episode_info:
            paths_with_episodes.append((old_path, episode_info))
        else:
            paths_without_episodes.append(old_path)

    # If most files don't have standard episode identifiers, try batch analysis
    batch_episodes: dict[str, str] = {}
    if len(paths_without_episodes) > 1 and len(paths_without_episodes) >= len(files) * 0.5:
        # Get filenames without paths for batch analysis
        filenames_for_batch = [
            (p.rsplit("/", 1)[-1] if "/" in p else p) for p in paths_without_episodes
        ]
        batch_episodes = extract_episode_from_batch(filenames_for_batch)

        if batch_episodes:
            logger.info(
                f"Using batch episode detection for {len(batch_episodes)} files "
                f"without standard episode patterns"
            )

    # Build rename plan
    new_paths: dict[str, list[str]] = {}  # new_path -> list of old_paths (for conflict detection)

    for f in files:
        old_path = f.get("name", "")
        filename = old_path.rsplit("/", 1)[-1] if "/" in old_path else old_path

        # Try to get episode info from various sources
        episode_info = extract_episode_identifier(filename)

        # If no standard episode, try batch analysis result
        if not episode_info and filename in batch_episodes:
            episode_num = batch_episodes[filename]
            # Use default season 01 for batch-detected episodes
            episode_info = ("01", "E", episode_num)
            logger.debug(f"Batch detection: {filename} -> E{episode_num}")

        # Build new path
        new_path = build_new_file_path(
            old_path,
            new_name,
            root_folder,
            episode_override=episode_info,
            preserve_folder=preserve_folder,
        )

        rename_plan.append((old_path, new_path))

        # Track for conflict detection
        if new_path not in new_paths:
            new_paths[new_path] = []
        new_paths[new_path].append(old_path)

    # Check for conflicts (multiple files -> same target)
    conflicts = {k: v for k, v in new_paths.items() if len(v) > 1}

    if conflicts:
        conflict_count = sum(len(v) for v in conflicts.values())
        unique_targets = len(conflicts)

        # Critical: all files would get the same name = total data loss
        if unique_targets == 1 and conflict_count == len(files):
            conflict_target = list(conflicts.keys())[0]
            raise RenameConflictError(
                f"CRITICAL: All {len(files)} files would be renamed to the same name "
                f"'{conflict_target}'. This would result in data loss. "
                f"Episode identifiers could not be extracted from original filenames. "
                f"Sample files: {[p.rsplit('/', 1)[-1] for p in list(conflicts.values())[0][:3]]}"
            )

        # Partial conflicts - some files would overwrite each other
        for target, sources in conflicts.items():
            warnings.append(
                f"Conflict: {len(sources)} files would be renamed to '{target}': "
                f"{[p.rsplit('/', 1)[-1] for p in sources[:3]]}"
            )

    return rename_plan, warnings


# =============================================================================
# Rename Operations
# =============================================================================


def get_root_folder(files: list[dict[str, Any]]) -> str | None:
    """Get the root folder name from torrent files.

    Args:
        files: List of file info dicts from qBittorrent

    Returns:
        Root folder name if all files share one, None otherwise
    """
    if not files:
        return None

    # Get all first-level folder names
    root_folders = set()
    for f in files:
        name = f.get("name", "")
        if "/" in name:
            root_folders.add(name.split("/")[0])

    # Return root folder only if there's exactly one
    if len(root_folders) == 1:
        return root_folders.pop()

    return None


def build_new_file_path(
    old_path: str,
    new_name: str,
    root_folder: str | None,
    episode_override: tuple[str, str, str] | None = None,
    preserve_folder: bool = False,
) -> str:
    """Build new file path based on rename.

    For TV series files, preserves the episode identifier (S01E02, etc.)
    from the original filename while applying the new name format.

    Args:
        old_path: Original file path
        new_name: New name to use (typically from release title)
        root_folder: Root folder if exists
        episode_override: Optional episode info tuple (season, marker, episode)
                          to use instead of extracting from filename.
                          Used for batch-detected episodes.
        preserve_folder: If True, keep files in original folder (for renaming
                         files before folder). If False, use new_name as folder.

    Returns:
        New file path
    """
    # Get file extension
    ext = ""
    if "." in old_path:
        ext = "." + old_path.rsplit(".", 1)[-1]

    # Get the original filename without path
    original_filename = old_path.rsplit("/", 1)[-1] if "/" in old_path else old_path

    # Use override if provided, otherwise extract from original filename
    episode_info = episode_override or extract_episode_identifier(original_filename)

    # Build the file's base name
    if episode_info:
        # TV series: insert episode identifier into the new name
        file_base_name = insert_episode_into_name(new_name, episode_info)
        logger.debug(f"Preserved episode info: {original_filename} -> {file_base_name}{ext}")
    else:
        # Movie or no episode info: use new name as-is
        file_base_name = new_name

    # If file is in root folder, preserve structure
    if root_folder and old_path.startswith(root_folder + "/"):
        # Determine folder name: keep original if preserve_folder, otherwise use new_name
        folder_name = root_folder if preserve_folder else new_name

        # Get relative path after root folder
        relative = old_path[len(root_folder) + 1 :]
        if "/" in relative:
            # Keep subdirectory structure
            subdir = relative.rsplit("/", 1)[0]
            return f"{folder_name}/{subdir}/{file_base_name}{ext}"
        else:
            return f"{folder_name}/{file_base_name}{ext}"

    # Single file or flat structure
    return f"{file_base_name}{ext}"


async def perform_rename(
    qbit: QBitClient,
    torrent_hash: str,
    new_name: str,
    mode: RenameMode,
) -> bool:
    """Perform the actual rename operations based on mode.

    Includes safety checks to prevent data loss from duplicate filenames.

    Args:
        qbit: qBittorrent client
        torrent_hash: Torrent info hash
        new_name: New name to apply
        mode: Rename mode

    Returns:
        True if successful, False otherwise
    """
    hash_short = torrent_hash[:8]

    # Get current torrent info
    torrent = qbit.get_torrent_info(torrent_hash)
    if not torrent:
        logger.error(f"Torrent {hash_short}... not found for rename")
        return False

    current_name = torrent.get("name", "")

    # Skip if already renamed
    if current_name == new_name:
        logger.info(f"Torrent {hash_short}... already has correct name, skipping")
        return True

    # Get files
    files = qbit.get_files(torrent_hash)
    root_folder = get_root_folder(files)

    # Determine if we're renaming both files and folder
    # If so, we need to rename files FIRST (keeping them in original folder),
    # then rename the folder to avoid "file not found" errors
    renaming_folder = (
        mode
        in [
            RenameMode.TORRENT_AND_FOLDER,
            RenameMode.TORRENT_FOLDER_FILES,
            RenameMode.FOLDER_ONLY,
        ]
        and root_folder
        and root_folder != new_name
    )

    renaming_files = mode in [RenameMode.TORRENT_FOLDER_FILES, RenameMode.FILES_ONLY]

    # Validate file rename plan if we're renaming files
    rename_plan: list[tuple[str, str]] = []
    if renaming_files:
        # When renaming both files and folder, keep files in original folder first
        # The folder rename will move them to the new location
        preserve_folder = renaming_folder
        try:
            rename_plan, warnings = validate_rename_plan(
                files, new_name, root_folder, preserve_folder=preserve_folder
            )

            # Log warnings for partial conflicts
            for warning in warnings:
                logger.warning(f"Torrent {hash_short}...: {warning}")

        except RenameConflictError as e:
            logger.error(f"Torrent {hash_short}...: {e}")
            logger.error(
                f"Torrent {hash_short}...: Aborting rename to prevent data loss. "
                f"Please check file naming patterns."
            )
            return False

    success = True

    # Rename torrent display name first (this doesn't affect file paths)
    if mode in [
        RenameMode.TORRENT_ONLY,
        RenameMode.TORRENT_AND_FOLDER,
        RenameMode.TORRENT_FOLDER_FILES,
    ]:
        if not qbit.rename_torrent(torrent_hash, new_name):
            success = False

    # Rename individual files BEFORE folder (to avoid path invalidation)
    # Files are renamed in-place within original folder, then folder is renamed
    if renaming_files:
        for old_path, new_path in rename_plan:
            if old_path != new_path:
                if not qbit.rename_file(torrent_hash, old_path, new_path):
                    success = False

    # Rename root folder AFTER files (folder rename updates all file paths)
    if renaming_folder:
        if not qbit.rename_folder(torrent_hash, root_folder, new_name):
            success = False
    elif (
        mode
        in [
            RenameMode.TORRENT_AND_FOLDER,
            RenameMode.TORRENT_FOLDER_FILES,
            RenameMode.FOLDER_ONLY,
        ]
        and not root_folder
    ):
        logger.debug(f"Torrent {hash_short}... has no root folder to rename")

    return success
