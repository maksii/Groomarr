"""Rename logic with trigger filters and rule application."""

import asyncio
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .config import TrackerRules
from .models import RadarrWebhook, SonarrWebhook
from .qbittorrent import QBitClient

logger = logging.getLogger(__name__)


@dataclass
class RenameResult:
    """Result of a rename operation with detailed status."""

    success: bool
    torrent_renamed: bool = False
    folder_renamed: bool = False
    files_renamed: int = 0
    files_failed: int = 0
    files_skipped: int = 0  # Already correct, no change needed
    already_complete: bool = False  # True if nothing needed to be changed
    verification_passed: bool = True
    verification_errors: list[str] = field(default_factory=list)

    @property
    def total_files_processed(self) -> int:
        return self.files_renamed + self.files_failed + self.files_skipped


class RenameMode(StrEnum):
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
    for p in patterns:
        try:
            if re.search(p, value, re.IGNORECASE):
                return True
        except re.error as e:
            logger.warning(f"Invalid filter pattern '{p}': {e}")
    return False


def evaluate_filters(
    rules: TrackerRules,
    *,
    indexer: str = "",
    quality: str = "",
    release_group: str = "",
    custom_formats: list[str] | None = None,
    custom_format_score: int | None = None,
    download_client: str = "",
) -> tuple[bool, str]:
    """Evaluate trigger filters against release attributes.

    This is the pure core of the filtering logic, decoupled from the webhook
    payload shape so it can be reused by both real webhook handling
    (via ``should_process``) and the rules simulator/preview UI.

    Args:
        rules: TrackerRules with filters (can be global or tracker-specific)
        indexer: Indexer/tracker name
        quality: Release quality string
        release_group: Release group name
        custom_formats: List of custom format names present on the release
        custom_format_score: Custom format score (None treated as 0 for threshold)
        download_client: Download client name

    Returns:
        Tuple of (should_process, skip_reason). skip_reason is empty when passing.
    """
    # Download client filter
    download_client = download_client or ""
    if rules.download_clients_include:
        if not matches_any(download_client, rules.download_clients_include):
            return False, f"download_client '{download_client}' not in include list"
    if matches_any(download_client, rules.download_clients_exclude):
        return False, f"download_client '{download_client}' in exclude list"

    # Indexer filter
    indexer = indexer or ""
    if rules.indexers_include:
        if not matches_any(indexer, rules.indexers_include):
            return False, f"indexer '{indexer}' not in include list"
    if matches_any(indexer, rules.indexers_exclude):
        return False, f"indexer '{indexer}' in exclude list"

    # Quality filter
    quality = quality or ""
    if rules.qualities_include:
        if not matches_any(quality, rules.qualities_include):
            return False, f"quality '{quality}' not in include list"
    if matches_any(quality, rules.qualities_exclude):
        return False, f"quality '{quality}' in exclude list"

    # Release group filter
    group = release_group or ""
    if rules.release_groups_include:
        if group and not matches_any(group, rules.release_groups_include):
            return False, f"release_group '{group}' not in include list"
    if group and matches_any(group, rules.release_groups_exclude):
        return False, f"release_group '{group}' in exclude list"

    # Custom format filters
    cf_list = custom_formats or []
    if rules.customformats_require_any:
        if not any(cf in cf_list for cf in rules.customformats_require_any):
            return False, "no required custom formats present"
    if rules.customformats_exclude:
        excluded = [cf for cf in rules.customformats_exclude if cf in cf_list]
        if excluded:
            return False, f"excluded custom format '{excluded[0]}' present"

    # Custom format score
    if rules.min_customformat_score is not None:
        score = custom_format_score or 0
        if score < rules.min_customformat_score:
            return False, f"score {score} < min {rules.min_customformat_score}"

    return True, ""


def explain_filters(
    rules: TrackerRules,
    *,
    indexer: str = "",
    quality: str = "",
    release_group: str = "",
    custom_formats: list[str] | None = None,
    custom_format_score: int | None = None,
    download_client: str = "",
) -> list[dict[str, Any]]:
    """Produce a per-filter breakdown explaining the trigger decision.

    Reports every *configured* filter and whether the release passes it, in the
    same order ``evaluate_filters`` checks them (no short-circuit). Used by the UI
    to show how each rule affects the decision — including non-title filters like
    download client, custom formats, score, and exclude lists. The first failing
    check is flagged as ``blocking`` (it matches the short-circuit skip reason).

    Returns a list of dicts: {label, tested, passed, detail, blocking}.
    """
    cfs = custom_formats or []
    checks: list[dict[str, Any]] = []

    def add(label: str, tested: str, passed: bool, detail: str) -> None:
        checks.append({"label": label, "tested": tested, "passed": passed, "detail": detail})

    dc = download_client or ""
    if rules.download_clients_include:
        ok = matches_any(dc, rules.download_clients_include)
        add(
            "Download client — include",
            dc or "(none)",
            ok,
            "matches an allowed pattern" if ok else "not in the include list",
        )
    if rules.download_clients_exclude:
        hit = matches_any(dc, rules.download_clients_exclude)
        add(
            "Download client — exclude",
            dc or "(none)",
            not hit,
            "matches an exclude pattern" if hit else "not excluded",
        )

    ix = indexer or ""
    if rules.indexers_include:
        ok = matches_any(ix, rules.indexers_include)
        add(
            "Indexer — include",
            ix or "(none)",
            ok,
            "matches an allowed pattern" if ok else "not in the include list",
        )
    if rules.indexers_exclude:
        hit = matches_any(ix, rules.indexers_exclude)
        add(
            "Indexer — exclude",
            ix or "(none)",
            not hit,
            "matches an exclude pattern" if hit else "not excluded",
        )

    q = quality or ""
    if rules.qualities_include:
        ok = matches_any(q, rules.qualities_include)
        add(
            "Quality — include",
            q or "(none)",
            ok,
            "matches an allowed pattern" if ok else "not in the include list",
        )
    if rules.qualities_exclude:
        hit = matches_any(q, rules.qualities_exclude)
        add(
            "Quality — exclude",
            q or "(none)",
            not hit,
            "matches an exclude pattern" if hit else "not excluded",
        )

    grp = release_group or ""
    if rules.release_groups_include:
        # Mirror evaluate_filters: an empty release group passes the include check.
        ok = (not grp) or matches_any(grp, rules.release_groups_include)
        add(
            "Release group — include",
            grp or "(none)",
            ok,
            "matches an allowed pattern" if ok else "not in the include list",
        )
    if rules.release_groups_exclude:
        hit = bool(grp) and matches_any(grp, rules.release_groups_exclude)
        add(
            "Release group — exclude",
            grp or "(none)",
            not hit,
            "matches an exclude pattern" if hit else "not excluded",
        )

    if rules.customformats_require_any:
        present = [cf for cf in rules.customformats_require_any if cf in cfs]
        ok = bool(present)
        add(
            "Custom formats — require any",
            ", ".join(cfs) or "(none)",
            ok,
            ("has " + ", ".join(present)) if ok else "none of the required formats are present",
        )
    if rules.customformats_exclude:
        excluded = [cf for cf in rules.customformats_exclude if cf in cfs]
        ok = not excluded
        add(
            "Custom formats — exclude",
            ", ".join(cfs) or "(none)",
            ok,
            ("present: " + ", ".join(excluded)) if excluded else "no excluded formats present",
        )

    if rules.min_customformat_score is not None:
        score = custom_format_score or 0
        ok = score >= rules.min_customformat_score
        add(
            "Minimum custom format score",
            str(score),
            ok,
            f"{score} ≥ {rules.min_customformat_score}"
            if ok
            else f"{score} < {rules.min_customformat_score}",
        )

    for check in checks:
        if not check["passed"]:
            check["blocking"] = True
            break

    return checks


def should_process(payload: RadarrWebhook | SonarrWebhook, rules: TrackerRules) -> tuple[bool, str]:
    """Check if webhook should be processed based on trigger filters.

    Args:
        payload: Webhook payload from Sonarr or Radarr
        rules: TrackerRules with filters (can be global or tracker-specific)

    Returns:
        Tuple of (should_process, skip_reason)
    """
    return evaluate_filters(
        rules,
        indexer=payload.release.indexer or "",
        quality=payload.release.quality or "",
        release_group=payload.release.releaseGroup or "",
        custom_formats=payload.release.customFormats or [],
        custom_format_score=payload.release.customFormatScore,
        download_client=payload.downloadClient or "",
    )


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


def apply_rename_rules(original_name: str, rules: TrackerRules) -> str:
    """Apply rename rules to transform the name.

    Args:
        original_name: Original release title
        rules: TrackerRules with rename rules (can be global or tracker-specific)

    Returns:
        Transformed name
    """
    name = original_name

    # 1. Strip media extension early (handles filename-based release titles)
    # This must happen before replace patterns to avoid ".mkv" becoming " mkv"
    name = strip_media_extension(name)

    # 2. Check skip patterns
    for pattern in rules.skip_title_patterns:
        try:
            if re.search(pattern, name, re.IGNORECASE):
                logger.debug(f"Skipping rename due to skip pattern: {pattern}")
                return strip_media_extension(original_name)
        except re.error as e:
            logger.warning(f"Invalid skip pattern '{pattern}': {e}")

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


def apply_rename_rules_traced(
    original_name: str, rules: TrackerRules
) -> tuple[str, list[dict[str, str]]]:
    """Apply rename rules while recording a step-by-step trace.

    Mirrors :func:`apply_rename_rules` exactly (enforced by tests) but records a
    human-readable trace of every transformation that changed the name. Used by
    the simulator UI to explain *why* a release becomes a given name.

    Args:
        original_name: Original release title
        rules: TrackerRules with rename rules

    Returns:
        Tuple of (final_name, steps). Each step is a dict with keys
        ``rule``, ``before``, ``after`` and optionally ``error``.
    """
    steps: list[dict[str, str]] = []
    name = original_name

    # 1. Strip media extension
    after = strip_media_extension(name)
    if after != name:
        steps.append({"rule": "strip media extension", "before": name, "after": after})
    name = after

    # 2. Skip patterns (short-circuit: revert to the original, extension stripped)
    for pattern in rules.skip_title_patterns:
        try:
            if re.search(pattern, name, re.IGNORECASE):
                final = strip_media_extension(original_name)
                steps.append(
                    {
                        "rule": f"skip_title_patterns matched '{pattern}' (rename skipped)",
                        "before": name,
                        "after": final,
                    }
                )
                return final, steps
        except re.error as e:
            steps.append(
                {
                    "rule": f"skip_title_patterns: invalid regex '{pattern}'",
                    "before": name,
                    "after": name,
                    "error": str(e),
                }
            )

    # 3. Remove patterns
    for pattern in rules.remove_patterns:
        try:
            after = re.sub(pattern, "", name)
            if after != name:
                steps.append(
                    {"rule": f"remove_patterns: '{pattern}'", "before": name, "after": after}
                )
            name = after
        except re.error as e:
            steps.append(
                {
                    "rule": f"remove_patterns: invalid regex '{pattern}'",
                    "before": name,
                    "after": name,
                    "error": str(e),
                }
            )

    # 4. Replace patterns
    for pattern, replacement in rules.replace_patterns.items():
        try:
            after = re.sub(pattern, replacement, name)
            if after != name:
                steps.append(
                    {
                        "rule": f"replace_patterns: '{pattern}' → '{replacement}'",
                        "before": name,
                        "after": after,
                    }
                )
            name = after
        except re.error as e:
            steps.append(
                {
                    "rule": f"replace_patterns: invalid regex '{pattern}'",
                    "before": name,
                    "after": name,
                    "error": str(e),
                }
            )

    # 5. Add prefix/suffix
    before = name
    name = f"{rules.prefix}{name.strip()}{rules.suffix}"
    if name != before:
        labels = []
        if rules.prefix:
            labels.append(f"prefix '{rules.prefix}'")
        if rules.suffix:
            labels.append(f"suffix '{rules.suffix}'")
        steps.append(
            {"rule": " + ".join(labels) or "trim whitespace", "before": before, "after": name}
        )

    # 6. Sanitize for filesystem
    after = sanitize_filename(name)
    if after != name:
        steps.append({"rule": "sanitize for filesystem", "before": name, "after": after})
    name = after

    return name, steps


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

# Pattern for an episode-only RANGE envelope with no season token, e.g.
# "E110-E293", "E01-E32", "E00-E37" — how the indexer names absolute-numbered
# anime / episode packs. Used to splice in a file's own absolute episode.
EP_RANGE_PATTERN = re.compile(r"E\d{1,4}\s*[-–—]\s*E?\d{1,4}", re.IGNORECASE)

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
    file_season_num, ep_marker, episode_num = episode_info

    # Check if new_name already has an episode pattern FIRST
    # This must be checked before season-only pattern to avoid partial matches
    title_episode_match = EPISODE_PATTERN.search(new_name)
    if title_episode_match:
        # Extract season from the title (authoritative source)
        title_season = title_episode_match.group(1)
        # Build identifier using title's season + file's episode number
        full_identifier = build_episode_identifier(title_season, ep_marker, episode_num)
        # Replace the episode pattern in the title
        return EPISODE_PATTERN.sub(full_identifier, new_name, count=1)

    # Check if new_name has season-only pattern (no episode)
    season_match = SEASON_ONLY_PATTERN.search(new_name)
    if season_match:
        # Extract season from the title (authoritative source)
        title_season = season_match.group(1)
        # Build identifier using title's season + file's episode number
        full_identifier = build_episode_identifier(title_season, ep_marker, episode_num)
        # Replace season-only with full season+episode
        return SEASON_ONLY_PATTERN.sub(full_identifier, new_name, count=1)

    # Episode-range envelope but no season token (absolute-numbered anime, e.g.
    # "Bleach E110-E293"): replace the range with THIS file's own episode number,
    # preserving the absolute E-style. Fabricating "SxxExx" here would both
    # duplicate the envelope AND assert a season the release does not have —
    # Sonarr maps anime by absolute number, so we keep the absolute number.
    ep_range_match = EP_RANGE_PATTERN.search(new_name)
    if ep_range_match:
        try:
            abs_ep = f"E{int(episode_num):02d}"
        except (TypeError, ValueError):
            abs_ep = f"E{episode_num}"
        return EP_RANGE_PATTERN.sub(abs_ep, new_name, count=1)

    # No season pattern found in title, use season from file's episode_info
    # Build identifier using file's season + episode number
    full_identifier = build_episode_identifier(file_season_num, ep_marker, episode_num)

    # Try to insert after series name
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
            # If the anchor (typically the year) sits inside parentheses, e.g.
            # "Series (2017) 1080p", insert BEFORE the opening paren so the id is
            # not spliced inside them ("Series ( S01E01 2017)" -> wrong).
            if insert_pos > 0 and new_name[insert_pos - 1] == "(":
                insert_pos -= 1
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

    # Structure-aware handling for complex packs (multi-season / season+specials
    # / collection). Imported lazily to avoid a circular import (structure
    # builds on this module's episode helpers). Simple seasons and movies fall
    # through to the unchanged logic below, so existing behaviour is preserved.
    from .structure import LayoutKind, analyze_torrent, build_complex_plan

    layout = analyze_torrent(files)
    if layout.is_complex:
        return build_complex_plan(files, new_name, root_folder, preserve_folder, layout)

    # A movie is a SINGLE feature file; fabricating an "SxxExx" for it (e.g. from
    # a stray number in "100.Meters.2025...AAC2.0...") mislabels it as an episode.
    # Radarr expects no episode token, so the file just takes the clean release
    # name. The single-file guard is essential: a multi-file release that merely
    # *classified* as movie (e.g. episodes the analyzer could not parse) must
    # still get per-file episodes from the base extractor, or every file would
    # collapse onto one name.
    is_movie = layout.kind == LayoutKind.MOVIE and layout.video_count == 1

    # Samples never participate in the rename plan: renaming a sample to the
    # content's name would collapse it onto the real file (data loss), and
    # Sonarr/Radarr ignore samples on import anyway. Exclude them up front so the
    # simple path below plans only real content.
    sample_paths = {m.path for m in layout.files if m.kind == "sample"}
    if sample_paths:
        files = [f for f in files if f.get("name", "") not in sample_paths]
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

        # Try to get episode info from various sources (never for movies).
        episode_info = None if is_movie else extract_episode_identifier(filename)

        # If no standard episode, try batch analysis result
        if not is_movie and not episode_info and filename in batch_episodes:
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
            skip_episode=is_movie,
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
    skip_episode: bool = False,
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

    # Use override if provided, otherwise extract from original filename. A movie
    # (skip_episode) never gets an episode token, even if its filename carries a
    # stray number that would otherwise parse as one.
    if skip_episode:
        episode_info = None
    else:
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


async def _verify_rename_state(
    qbit: QBitClient,
    torrent_hash: str,
    expected_torrent_name: str | None,
    expected_folder: str | None,
    expected_files: dict[str, str] | None,
    mode: RenameMode,
    max_retries: int = 5,
    retry_delay: float = 0.5,
) -> tuple[bool, list[str]]:
    """Verify the actual state matches expected after rename operations.

    Uses retry logic because qBittorrent may take time to propagate changes.
    Re-fetches data from qBit on each retry to get the latest state.

    Args:
        qbit: qBittorrent client
        torrent_hash: Torrent info hash
        expected_torrent_name: Expected torrent name (None to skip check)
        expected_folder: Expected root folder name (None to skip check)
        expected_files: Dict mapping expected new paths to original paths (None to skip)
        mode: Rename mode
        max_retries: Maximum number of verification attempts
        retry_delay: Delay in seconds between retries

    Returns:
        Tuple of (all_passed, list of error messages)
    """
    hash_short = torrent_hash[:8]

    for attempt in range(max_retries):
        errors: list[str] = []

        # Re-fetch current state from qBittorrent
        torrent = await asyncio.to_thread(qbit.get_torrent_info, torrent_hash)
        if not torrent:
            errors.append(f"Torrent {hash_short}... not found during verification")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            return False, errors

        # Check torrent name
        if expected_torrent_name is not None:
            current_name = torrent.get("name", "")
            if current_name != expected_torrent_name:
                errors.append(
                    f"Torrent name mismatch: expected '{expected_torrent_name}', "
                    f"got '{current_name}'"
                )

        # Fetch files once for both file and folder checks
        current_files = await asyncio.to_thread(qbit.get_files, torrent_hash)
        current_paths = {f.get("name", "") for f in current_files}

        # Check files
        if expected_files is not None:
            for expected_path in expected_files:
                if expected_path not in current_paths:
                    errors.append(f"File not renamed: expected '{expected_path}'")

        # Check folder (after folder rename, file paths change)
        if expected_folder is not None and mode in [
            RenameMode.TORRENT_AND_FOLDER,
            RenameMode.TORRENT_FOLDER_FILES,
            RenameMode.FOLDER_ONLY,
        ]:
            current_root = get_root_folder(current_files)
            if current_root != expected_folder:
                errors.append(
                    f"Folder mismatch: expected '{expected_folder}', got '{current_root}'"
                )

        # If no errors, verification passed
        if not errors:
            if attempt > 0:
                logger.debug(
                    f"Torrent {hash_short}... verification passed after {attempt + 1} attempts"
                )
            return True, []

        # If this isn't the last attempt, wait and retry
        if attempt < max_retries - 1:
            logger.debug(
                f"Torrent {hash_short}... verification attempt {attempt + 1}/{max_retries} "
                f"failed, retrying in {retry_delay}s: {errors}"
            )
            await asyncio.sleep(retry_delay)

    # All retries exhausted, return final errors
    return False, errors


async def perform_rename(
    qbit: QBitClient,
    torrent_hash: str,
    new_name: str,
    mode: RenameMode,
) -> RenameResult:
    """Perform the actual rename operations based on mode.

    Includes safety checks to prevent data loss and verification after operations.
    Supports partial renames - if some files are already renamed, only renames
    the remaining ones.

    Args:
        qbit: qBittorrent client
        torrent_hash: Torrent info hash
        new_name: New name to apply
        mode: Rename mode

    Returns:
        RenameResult with detailed status of what was renamed
    """
    hash_short = torrent_hash[:8]
    result = RenameResult(success=True)

    # Get current torrent info
    torrent = await asyncio.to_thread(qbit.get_torrent_info, torrent_hash)
    if not torrent:
        logger.error(f"Torrent {hash_short}... not found for rename")
        return RenameResult(success=False, verification_errors=["Torrent not found"])

    current_name = torrent.get("name", "")

    # Get files and determine structure
    files = await asyncio.to_thread(qbit.get_files, torrent_hash)
    root_folder = get_root_folder(files)

    # Determine what needs to be renamed based on mode
    should_rename_torrent = mode in [
        RenameMode.TORRENT_ONLY,
        RenameMode.TORRENT_AND_FOLDER,
        RenameMode.TORRENT_FOLDER_FILES,
    ]
    should_rename_folder = (
        mode
        in [
            RenameMode.TORRENT_AND_FOLDER,
            RenameMode.TORRENT_FOLDER_FILES,
            RenameMode.FOLDER_ONLY,
        ]
        and root_folder is not None
    )
    should_rename_files = mode in [RenameMode.TORRENT_FOLDER_FILES, RenameMode.FILES_ONLY]

    # Check current state to determine what actually needs changing
    torrent_needs_rename = should_rename_torrent and current_name != new_name
    folder_needs_rename = should_rename_folder and root_folder != new_name

    # Build file rename plan if needed
    rename_plan: list[tuple[str, str]] = []
    files_needing_rename: list[tuple[str, str]] = []

    if should_rename_files:
        # When renaming both files and folder, keep files in original folder first
        # The folder rename will move them to the new location
        preserve_folder = folder_needs_rename

        try:
            rename_plan, warnings = validate_rename_plan(
                files, new_name, root_folder, preserve_folder=preserve_folder
            )

            # Log warnings for partial conflicts
            for warning in warnings:
                logger.warning(f"Torrent {hash_short}...: {warning}")

            # Filter to only files that actually need renaming
            # This supports partial renames - skip files already at target path
            current_paths = {f.get("name", "") for f in files}
            for old_path, new_path in rename_plan:
                if old_path != new_path:
                    # Check if file is at old path (needs rename) or already at new path
                    if old_path in current_paths:
                        files_needing_rename.append((old_path, new_path))
                    elif new_path in current_paths:
                        # File already at target path
                        result.files_skipped += 1
                        logger.debug(f"File already renamed: '{new_path}'")
                    else:
                        # Neither path found - could be an issue
                        logger.warning(
                            f"File state unclear for {hash_short}...: "
                            f"neither '{old_path}' nor '{new_path}' found"
                        )
                else:
                    result.files_skipped += 1

        except RenameConflictError as e:
            logger.error(f"Torrent {hash_short}...: {e}")
            logger.error(
                f"Torrent {hash_short}...: Aborting rename to prevent data loss. "
                f"Please check file naming patterns."
            )
            return RenameResult(success=False, verification_errors=[str(e)])

    # Check if everything is already in the correct state
    if not torrent_needs_rename and not folder_needs_rename and not files_needing_rename:
        logger.info(f"Torrent {hash_short}... already fully renamed, nothing to do")
        result.already_complete = True
        return result

    # Log what we're about to do
    actions = []
    if torrent_needs_rename:
        actions.append("torrent")
    if folder_needs_rename:
        actions.append("folder")
    if files_needing_rename:
        actions.append(f"{len(files_needing_rename)} files")
    logger.info(f"Torrent {hash_short}...: will rename {', '.join(actions)}")

    # Final data-loss gate: never execute a file-rename plan that could destroy
    # a file. This guards EVERY rename path (simple and complex) — it rejects a
    # plan where two files collapse onto one target or a target would clobber a
    # bystander, and refuses cyclic plans rather than risk a partial swap. The
    # gate runs before any file/folder mutation, so a rejected plan changes
    # nothing on disk (the torrent display-name rename below is cosmetic).
    if files_needing_rename:
        from .structure import order_moves_safely, partition_safe_moves

        current_paths_now = {f.get("name", "") for f in files}
        # Drop only the individual moves that would destroy data (collapse onto
        # a shared name, or clobber a bystander); keep every provably-safe move.
        safe_moves, dropped = partition_safe_moves(files_needing_rename, current_paths_now)
        for old_path, _new, reason in dropped:
            logger.warning(
                f"Torrent {hash_short}...: leaving '{old_path.rsplit('/', 1)[-1]}' "
                f"unchanged to prevent data loss ({reason})"
            )
            result.files_skipped += 1

        # Order the safe moves so none writes over a path still pending as a
        # source. A residual cycle (practically impossible with clean target
        # names) is also left unchanged rather than risk a partial swap.
        ordered, staged = order_moves_safely(safe_moves)
        if staged:
            cyclic = {o for o, _ in staged}
            for old_path in cyclic:
                logger.warning(
                    f"Torrent {hash_short}...: leaving '{old_path.rsplit('/', 1)[-1]}' "
                    f"unchanged (cyclic rename)"
                )
                result.files_skipped += 1
            safe_moves = [(o, n) for o, n in safe_moves if o not in cyclic]
            ordered, _ = order_moves_safely(safe_moves)

        files_needing_rename = ordered
        # Verification must only expect the moves we actually execute.
        rename_plan = ordered

    # Rename torrent display name first (this doesn't affect file paths)
    if torrent_needs_rename:
        if await qbit.rename_torrent(torrent_hash, new_name):
            result.torrent_renamed = True
        else:
            result.success = False

    # Rename individual files BEFORE folder (to avoid path invalidation)
    # Files are renamed in-place within original folder, then folder is renamed
    if files_needing_rename:
        for old_path, new_path in files_needing_rename:
            if await qbit.rename_file(torrent_hash, old_path, new_path):
                result.files_renamed += 1
            else:
                result.files_failed += 1
                result.success = False

    # Rename root folder AFTER files (folder rename updates all file paths)
    if folder_needs_rename:
        if await qbit.rename_folder(torrent_hash, root_folder, new_name):
            result.folder_renamed = True
            # Small delay to allow qBittorrent to update all file paths after folder rename
            await asyncio.sleep(0.2)
        else:
            result.success = False
    elif should_rename_folder and not root_folder:
        logger.debug(f"Torrent {hash_short}... has no root folder to rename")

    # Verification: check final state matches expected
    # Build expected file paths after all operations
    # Note: After folder rename, qBittorrent updates all file paths automatically
    # So paths in rename_plan (which were built with preserve_folder=True) need adjustment
    expected_files: dict[str, str] | None = None
    if should_rename_files and rename_plan:
        expected_files = {}
        # Build expected paths based on rename plan and folder rename
        for old_path, new_path in rename_plan:
            if old_path != new_path:
                # After folder rename, file paths will have new folder name
                if folder_needs_rename and root_folder:
                    # new_path was built with preserve_folder=True, so it has old folder name
                    # We need to replace old folder with new folder in the path
                    if new_path.startswith(root_folder + "/"):
                        # Extract relative path after old folder, then prepend new folder
                        relative_path = new_path[len(root_folder) + 1 :]
                        expected_path = f"{new_name}/{relative_path}"
                    else:
                        # Path doesn't start with root folder - shouldn't happen but handle it
                        expected_path = new_path
                else:
                    # No folder rename, paths should be as-is from rename plan
                    expected_path = new_path
                expected_files[expected_path] = old_path

    # Determine expected folder after rename
    expected_folder_name = new_name if folder_needs_rename else root_folder

    # Run verification with retries (qBit may take time to propagate changes)
    verification_passed, verification_errors = await _verify_rename_state(
        qbit=qbit,
        torrent_hash=torrent_hash,
        expected_torrent_name=new_name if should_rename_torrent else None,
        expected_folder=expected_folder_name if should_rename_folder else None,
        expected_files=expected_files,
        mode=mode,
    )

    result.verification_passed = verification_passed
    result.verification_errors = verification_errors

    if not verification_passed:
        result.success = False
        for error in verification_errors:
            logger.error(f"Torrent {hash_short}... verification failed: {error}")

    # Log summary
    if result.success:
        summary_parts = []
        if result.torrent_renamed:
            summary_parts.append("torrent")
        if result.folder_renamed:
            summary_parts.append("folder")
        if result.files_renamed > 0:
            summary_parts.append(f"{result.files_renamed} files")
        if summary_parts:
            logger.info(f"Torrent {hash_short}...: renamed {', '.join(summary_parts)}")
    else:
        logger.error(
            f"Torrent {hash_short}...: rename partially failed - "
            f"files: {result.files_renamed} ok, {result.files_failed} failed, "
            f"{result.files_skipped} skipped"
        )

    return result
