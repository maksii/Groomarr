"""Rename logic with trigger filters and rule application."""

import logging
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

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


def matches_any(value: str, patterns: List[str]) -> bool:
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


def should_process(
    payload: Union[RadarrWebhook, SonarrWebhook], rules: RenameRules
) -> Tuple[bool, str]:
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

    # 1. Check skip patterns first
    for pattern in rules.skip_title_patterns:
        if re.search(pattern, name, re.IGNORECASE):
            logger.debug(f"Skipping rename due to skip pattern: {pattern}")
            return original_name

    # 2. Remove patterns
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
# Rename Operations
# =============================================================================


def get_root_folder(files: List[Dict[str, Any]]) -> Optional[str]:
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
    old_path: str, new_name: str, root_folder: Optional[str]
) -> str:
    """Build new file path based on rename.

    Args:
        old_path: Original file path
        new_name: New name to use
        root_folder: Root folder if exists

    Returns:
        New file path
    """
    # Get file extension
    ext = ""
    if "." in old_path:
        ext = "." + old_path.rsplit(".", 1)[-1]

    # If file is in root folder, preserve structure
    if root_folder and old_path.startswith(root_folder + "/"):
        # Get relative path after root folder
        relative = old_path[len(root_folder) + 1 :]
        if "/" in relative:
            # Keep subdirectory structure
            subdir = relative.rsplit("/", 1)[0]
            return f"{new_name}/{subdir}/{new_name}{ext}"
        else:
            return f"{new_name}/{new_name}{ext}"

    # Single file or flat structure
    return f"{new_name}{ext}"


async def perform_rename(
    qbit: QBitClient,
    torrent_hash: str,
    new_name: str,
    mode: RenameMode,
) -> bool:
    """Perform the actual rename operations based on mode.

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

    success = True

    # Rename torrent display name
    if mode in [
        RenameMode.TORRENT_ONLY,
        RenameMode.TORRENT_AND_FOLDER,
        RenameMode.TORRENT_FOLDER_FILES,
    ]:
        if not qbit.rename_torrent(torrent_hash, new_name):
            success = False

    # Rename root folder
    if mode in [
        RenameMode.TORRENT_AND_FOLDER,
        RenameMode.TORRENT_FOLDER_FILES,
        RenameMode.FOLDER_ONLY,
    ]:
        if root_folder and root_folder != new_name:
            if not qbit.rename_folder(torrent_hash, root_folder, new_name):
                success = False
        elif not root_folder:
            logger.debug(f"Torrent {hash_short}... has no root folder to rename")

    # Rename individual files
    if mode in [RenameMode.TORRENT_FOLDER_FILES, RenameMode.FILES_ONLY]:
        for file_info in files:
            old_path = file_info.get("name", "")
            new_path = build_new_file_path(old_path, new_name, root_folder)
            if old_path != new_path:
                if not qbit.rename_file(torrent_hash, old_path, new_path):
                    success = False

    return success
