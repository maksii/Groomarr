"""Torrent structure analysis for advanced rename scenarios.

The base rename engine (:mod:`src.rename`) assumes a torrent is a *single*
logical unit: one release title becomes one folder name, and every file gets the
same base name with its episode identifier spliced in. That holds for the common
case — a single-season pack or a movie — but it actively corrupts releases that
pack *several* logical units together:

* **multi-season packs** — one torrent containing two or more seasons. The base
  engine stamps every file with the *title's* first season, so ``S02E06`` becomes
  ``S01E06`` and collides with the real ``S01E06`` on import (silent data loss).
* **season + specials** — a regular season plus OVAs/specials, either inline
  (``... S1E13 Special``) or in a dedicated ``Specials/`` / ``OVA/`` subfolder.
* **collections** — several distinct titles (e.g. a bundle of short films) with
  no episode numbering, which the base engine collapses to a single name.

This module is the *pure, side-effect-free* analysis layer. It parses each file
path into a :class:`FileMeta` (its own season/episode/kind, with the file's own
``SxxExx`` treated as authoritative and folder names used only as a fallback),
then classifies the whole torrent into a :class:`LayoutKind`. It deliberately
contains **no** hardcoded, title-specific rules — every decision is derived from
the file/folder names themselves, so it generalises to any release.

Target-path construction (which depends on the chosen on-disk layout) and the
data-loss safety gate live alongside this analysis and consume its output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from .rename import (
    BRACKETED_EPISODE_PATTERN,
    EPISODE_PATTERN,
    extract_episode_identifier,
    insert_episode_into_name,
    sanitize_filename,
)

# ---------------------------------------------------------------------------
# File-kind / extension classification
# ---------------------------------------------------------------------------

VIDEO_EXTS = (
    ".mkv",
    ".mp4",
    ".avi",
    ".m4v",
    ".ts",
    ".m2ts",
    ".mov",
    ".wmv",
    ".flv",
    ".webm",
    ".mpg",
    ".mpeg",
    ".vob",
    ".divx",
    ".ogm",
    ".rmvb",
)
SUBTITLE_EXTS = (".srt", ".ass", ".ssa", ".sub", ".idx", ".vtt", ".smi")

# A path segment that *is* a specials/extras directory (so its files are S00).
# Anchored at the start of the segment: the segment is dominantly the marker,
# not merely a title that happens to contain the word.
_SPECIAL_FOLDER = re.compile(
    r"(?i)^[\s\W]*(?:specials?|extras?|bonus(?:es)?|ova[s]?|ona[s]?|oad[s]?|"
    r"nc(?:ed|op)|creditless|pv[s]?|cm[s]?|sp(?:ecial)?s?|menus?|trailers?|"
    r"спешл[иів]*|спецвипуск[иів]*|додатк[иовіую]*|омаке|бонус[иніую]*)\b"
)

# A specials/extras marker appearing anywhere in a file *name* (inline special).
_SPECIAL_TOKEN = re.compile(
    r"(?i)(?:\bspecials?\b|\bextras?\b|\bova\b|\bona\b|\boad\b|\bnced\b|\bncop\b|"
    r"\bcreditless\b|\bomake\b|омаке|спешл|спецвипуск|\bSP\d|\bNC\b)"
)

_SAMPLE_TOKEN = re.compile(r"(?i)(?:^|[\W_])sample(?:[\W_]|$)")

# Season tokens for a *folder* segment (used only when the filename lacks SxxExx).
_FOLDER_SXX = re.compile(r"(?i)(?<![A-Za-z])S(\d{1,2})(?![\dEe])")
_FOLDER_SEASON_WORD = re.compile(
    r"(?i)\b(?:season|seizoen|saison|temporada|stagione|staffel|sezon)\s*(\d{1,2})\b"
)
# Ukrainian/Russian: "(2 сезон)", "сезон 2", "2-й сезон"
_FOLDER_SEASON_CYR = re.compile(
    r"(?i)(?:(\d{1,2})\s*[-\s]*(?:й|га|ий)?\s*сезон|сезон[аиівйу]*\s*(\d{1,2}))"
)

YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


class LayoutKind(StrEnum):
    """How a torrent's files are organised, as inferred from their paths."""

    SIMPLE_SEASON = "simple_season"  # one season (or single cour) -> base engine
    MOVIE = "movie"  # one (or split-part) movie -> base engine
    MULTI_SEASON = "multi_season"  # >=2 distinct seasons in one torrent
    SEASON_WITH_SPECIALS = "season_with_specials"  # season(s) + dedicated specials
    COLLECTION = "collection"  # several distinct titles, no episode numbering
    UNKNOWN = "unknown"  # could not classify confidently


@dataclass
class FileMeta:
    """Parsed metadata for one file in a torrent.

    ``season``/``episode`` are the *effective* values: the file's own ``SxxExx``
    wins; only when the filename has no season does ``season`` fall back to a
    season parsed from a containing folder segment. ``season`` is ``None`` when
    no season could be determined (e.g. a bare anime episode number), and ``0``
    for specials that live in a dedicated specials/extras folder.
    """

    path: str
    folder: str
    filename: str
    ext: str
    is_video: bool
    season: int | None = None
    episode: int | None = None
    episode_str: str | None = None
    season_source: str = "none"  # filename | folder | special_folder | none
    kind: str = "other"  # episode | special | movie | subtitle | extra | sample | other

    @property
    def stem(self) -> str:
        """Filename without its extension."""
        return self.filename[: -len(self.ext)] if self.ext else self.filename


@dataclass
class TorrentLayout:
    """Result of analysing a torrent's complete file list."""

    kind: LayoutKind
    files: list[FileMeta] = field(default_factory=list)
    seasons: list[int] = field(default_factory=list)  # distinct regular seasons (>0)
    has_specials: bool = False
    video_count: int = 0
    reason: str = ""

    @property
    def is_complex(self) -> bool:
        """True when the base single-name engine cannot handle this safely."""
        return self.kind in (
            LayoutKind.MULTI_SEASON,
            LayoutKind.SEASON_WITH_SPECIALS,
            LayoutKind.COLLECTION,
        )


# ---------------------------------------------------------------------------
# Low-level extraction
# ---------------------------------------------------------------------------


def _ext_of(filename: str) -> str:
    if "." in filename:
        dot = filename.rfind(".")
        # treat only short, alpha-numeric tails as extensions
        tail = filename[dot:]
        if 2 <= len(tail) <= 6 and tail[1:].isalnum():
            return tail.lower()
    return ""


def full_season_episode(filename: str) -> tuple[int, int, str] | None:
    """Return ``(season, episode, episode_str)`` if the name has a real SxxExx.

    Uses the same patterns as the base engine (bracketed first, then plain), so
    detection is consistent. ``episode_str`` preserves the original digits
    (e.g. ``"006"``) for padding decisions. Returns ``None`` when the name
    carries no explicit season+episode token.
    """
    for pat in (BRACKETED_EPISODE_PATTERN, EPISODE_PATTERN):
        m = pat.search(filename)
        if m:
            return int(m.group(1)), int(m.group(3)), m.group(3)
    return None


def episode_only(filename: str) -> tuple[int, str] | None:
    """Return ``(episode, episode_str)`` for a season-less episode number.

    Delegates to the base engine's :func:`extract_episode_identifier` (which
    applies the anime/alternative patterns *and* the year/resolution guards),
    but discards its default season so the caller can supply the real season
    from folder context. Returns ``None`` when no episode number is found.
    """
    ident = extract_episode_identifier(filename)
    if ident is None:
        return None
    _season, _marker, ep = ident
    return int(ep), ep


def season_from_segment(segment: str) -> int | None:
    """Parse a season number from a single folder segment, or ``None``.

    Only explicit season tokens count (``S02``, ``Season 2``, ``2 сезон``); a
    bare trailing number is *not* treated as a season — that is too ambiguous
    (it is often part of the title) and risks false multi-season splits.
    """
    m = _FOLDER_SXX.search(segment)
    if m:
        return int(m.group(1))
    m = _FOLDER_SEASON_WORD.search(segment)
    if m:
        return int(m.group(1))
    m = _FOLDER_SEASON_CYR.search(segment)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def _is_special_folder(segment: str) -> bool:
    return bool(_SPECIAL_FOLDER.search(segment))


# ---------------------------------------------------------------------------
# Per-file parsing
# ---------------------------------------------------------------------------


def parse_file(path: str) -> FileMeta:
    """Parse one torrent file path into a :class:`FileMeta`.

    Season precedence: the file's own ``SxxExx`` > a dedicated specials folder
    (-> season 0) > a season parsed from a containing folder segment. The result
    never invents a season from thin air — ``season`` stays ``None`` when truly
    unknown, leaving the decision to the higher-level planner.
    """
    norm = path.replace("\\", "/")
    segments = [s for s in norm.split("/") if s]
    filename = segments[-1] if segments else norm
    folder = "/".join(segments[:-1])
    folder_segments = segments[:-1]
    ext = _ext_of(filename)
    is_video = ext in VIDEO_EXTS

    meta = FileMeta(
        path=path,
        folder=folder,
        filename=filename,
        ext=ext,
        is_video=is_video,
    )

    # Non-video files: classify but never treat as episodes.
    if not is_video:
        if ext in SUBTITLE_EXTS:
            meta.kind = "subtitle"
        else:
            meta.kind = "extra"
        return meta

    if _SAMPLE_TOKEN.search(filename):
        meta.kind = "sample"
        return meta

    in_special_folder = any(_is_special_folder(seg) for seg in folder_segments)
    has_special_token = bool(_SPECIAL_TOKEN.search(filename))

    # 1. File's own SxxExx is authoritative for season + episode.
    full = full_season_episode(filename)
    if full is not None:
        meta.season, meta.episode, meta.episode_str = full
        meta.season_source = "filename"
    else:
        # 2. Episode number without a season -> fill season from folder context.
        ep = episode_only(filename)
        if ep is not None:
            meta.episode, meta.episode_str = ep
        # nearest (deepest) folder segment that names a season wins
        for seg in reversed(folder_segments):
            s = season_from_segment(seg)
            if s is not None:
                meta.season = s
                meta.season_source = "folder"
                break

    # 3. Specials: a *dedicated* specials/extras folder forces season 0.
    #    An inline special token keeps the file's own SxxExx (do not invent S00).
    if in_special_folder:
        meta.kind = "special"
        meta.season = 0
        meta.season_source = "special_folder"
    elif has_special_token:
        meta.kind = "special"
    elif meta.episode is not None:
        meta.kind = "episode"
    elif YEAR_RE.search(meta.stem):
        meta.kind = "movie"
    else:
        meta.kind = "other"

    return meta


# ---------------------------------------------------------------------------
# Torrent-level classification
# ---------------------------------------------------------------------------


def _title_stem(meta: FileMeta) -> str:
    """A rough title key for a file, used to spot distinct titles in a folder.

    Multi-part markers of a *single* work (``part1``/``CD2``/``Disc 1``) are
    stripped so a movie split across files collapses to one stem and is *not*
    mistaken for a collection of distinct titles.
    """
    name = meta.stem.lower()
    # drop bracketed groups and common quality/source noise to compare titles
    name = re.sub(r"[\[(].*?[\])]", " ", name)
    name = re.sub(
        r"(?i)\b(1080p|720p|480p|576p|2160p|4k|web-?dl|web-?rip|bdrip|bluray|"
        r"hdtv|x264|x265|h\.?26[45]|hevc|avc|aac|flac|dts|ddp?5\.1|ukr|eng|jap|sub)\b",
        " ",
        name,
    )
    # collapse multi-part / disc markers of one work
    name = re.sub(r"(?i)\b(?:part|pt|cd|disc|disk|vol|том|частина|диск)\s*\.?\s*\d+\b", " ", name)
    name = YEAR_RE.sub(" ", name)
    return re.sub(r"[\W_]+", " ", name).strip()


def analyze_torrent(files: list[dict]) -> TorrentLayout:
    """Classify a torrent's file list into a :class:`LayoutKind`.

    ``files`` is the qBittorrent file-list shape (each item a dict with a
    ``"name"`` path). Classification is conservative: it only declares a complex
    layout (multi-season / specials / collection) when the evidence is clear,
    otherwise it falls back to the simple/movie kinds that the base engine
    already handles correctly — so existing behaviour is preserved.
    """
    metas = [parse_file(f.get("name", "")) for f in files]
    # Samples are junk content (Sonarr's import filters them too) — exclude them
    # from classification so a lone movie + sample isn't seen as two "titles".
    videos = [m for m in metas if m.is_video and m.kind != "sample"]
    video_count = len(videos)

    regular_seasons = sorted({m.season for m in videos if m.season is not None and m.season > 0})
    has_specials = any(m.kind == "special" and m.season == 0 for m in metas)
    layout = TorrentLayout(
        kind=LayoutKind.UNKNOWN,
        files=metas,
        seasons=regular_seasons,
        has_specials=has_specials,
        video_count=video_count,
    )

    if video_count == 0:
        layout.kind = LayoutKind.UNKNOWN
        layout.reason = "no video files"
        return layout

    if video_count == 1:
        only = videos[0]
        if only.episode is not None and only.season not in (None, 0):
            layout.kind = LayoutKind.SIMPLE_SEASON
            layout.reason = "single episode file"
        else:
            layout.kind = LayoutKind.MOVIE
            layout.reason = "single video file"
        return layout

    # >=2 distinct regular seasons -> multi-season pack
    if len(regular_seasons) >= 2:
        layout.kind = LayoutKind.MULTI_SEASON
        layout.reason = f"distinct seasons {regular_seasons}"
        return layout

    # one regular season + a dedicated specials folder -> season + specials
    if has_specials and len(regular_seasons) >= 1:
        layout.kind = LayoutKind.SEASON_WITH_SPECIALS
        layout.reason = f"season {regular_seasons} + specials folder"
        return layout

    episode_videos = [m for m in videos if m.episode is not None]

    # Collection of distinct titles (e.g. a bundle of short films): several
    # videos, NONE with an episode number, no season anywhere, and the file
    # stems are mutually distinct with NO shared leading title token. The
    # shared-prefix guard is what separates a real collection (Shishigari /
    # Onikiri / Afternoon Class) from a single series whose episode numbers we
    # merely failed to parse (which all repeat the series name) — the latter
    # must stay a simple case and flow through the base engine unchanged.
    if not regular_seasons and not episode_videos and video_count >= 2:
        stems = [_title_stem(m) for m in videos]
        first_tokens = {s.split()[0] for s in stems if s.split()}
        distinct = len(set(stems))
        if distinct >= 2 and len(first_tokens) > 1:
            layout.kind = LayoutKind.COLLECTION
            layout.reason = "multiple distinct titles, no episode numbering"
            return layout

    # Everything else with episodes in a single season (or season-less single
    # cour) is the simple case the base engine handles.
    if episode_videos:
        layout.kind = LayoutKind.SIMPLE_SEASON
        layout.reason = "single-season episodes"
    else:
        layout.kind = LayoutKind.MOVIE
        layout.reason = "videos without episode numbering"
    return layout


# ---------------------------------------------------------------------------
# Data-loss safety gate
# ---------------------------------------------------------------------------


@dataclass
class PlanSafety:
    """Verdict on whether a rename plan can be executed without losing a file."""

    safe: bool
    conflicts: list[str] = field(default_factory=list)  # data-loss reasons (block)
    overlaps: list[str] = field(default_factory=list)  # targets == another source

    @property
    def needs_ordering(self) -> bool:
        """A move targets a path that is still another pending source."""
        return bool(self.overlaps)


def assess_plan_safety(plan: list[tuple[str, str]], all_paths: list[str] | set[str]) -> PlanSafety:
    """Check a rename plan for data-loss conditions BEFORE any mutation.

    A plan is the list of ``(old_path, new_path)`` the engine intends to apply;
    ``all_paths`` is every file currently in the torrent. The plan is rejected
    (``safe=False``) if executing it could destroy a file:

    * **collapse** — two different files map to the same target (one would
      overwrite the other);
    * **clobber** — a target equals an existing file that is *not* itself being
      renamed away (the bystander would be overwritten).

    A non-fatal **overlap** (a target equals another file that *is* being
    renamed) is reported separately: it is not data loss, but the moves must be
    ordered (or staged through temporary names) so the source is moved before it
    is written over. This is the hard invariant behind "never lose a file":
    callers MUST refuse to execute a plan whose ``safe`` is ``False``.
    """
    moves = [(o, n) for o, n in plan if o != n]
    sources = {o for o, _ in moves}
    targets = [n for _, n in moves]
    conflicts: list[str] = []

    # collapse: multiple sources -> one target
    by_target: dict[str, list[str]] = {}
    for o, n in moves:
        by_target.setdefault(n, []).append(o)
    for target, srcs in by_target.items():
        if len(srcs) > 1:
            sample = [s.rsplit("/", 1)[-1] for s in srcs[:3]]
            conflicts.append(f"{len(srcs)} files would collapse onto '{target}': {sample}")

    # clobber: target equals a file that stays put (not a source of any move)
    untouched = set(all_paths) - sources
    for target in set(targets):
        if target in untouched:
            conflicts.append(
                f"target '{target}' would overwrite an existing file that is not being renamed"
            )

    # overlap: a source is also someone's target -> ordering/temp needed
    target_set = set(targets)
    overlaps = sorted({o for o, n in moves if o in target_set and o != n})

    return PlanSafety(safe=not conflicts, conflicts=conflicts, overlaps=overlaps)


def order_moves_safely(
    plan: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Order moves so no move writes to a path that is still a pending source.

    Returns ``(ordered, staged)`` where ``ordered`` is a sequence of
    ``(old, new)`` moves safe to apply left-to-right, and ``staged`` is a list
    of ``(old, temp)`` first-phase moves required to break rename cycles (empty
    when the plan is acyclic). A unique ``.groomarr-tmp-N`` suffix is used for
    staging so temporaries never collide with real files.

    Precondition: the plan passed :func:`assess_plan_safety` (``safe=True``),
    i.e. targets are unique and never clobber a bystander. Under that
    precondition this ordering is always loss-free.
    """
    moves = [(o, n) for o, n in plan if o != n]
    remaining = dict(moves)  # old -> new (targets unique by precondition)
    pending_sources = set(remaining)
    ordered: list[tuple[str, str]] = []

    progressed = True
    while remaining and progressed:
        progressed = False
        for old in list(remaining):
            new = remaining[old]
            # safe to emit now if its target isn't still waiting to be moved
            if new not in pending_sources or new == old:
                ordered.append((old, new))
                del remaining[old]
                pending_sources.discard(old)
                progressed = True

    # whatever is left forms cycles -> stage through unique temp names
    staged: list[tuple[str, str]] = []
    for i, (old, new) in enumerate(remaining.items()):
        tmp = f"{old}.groomarr-tmp-{i}"
        staged.append((old, tmp))
        ordered.append((tmp, new))
    return ordered, staged


def partition_safe_moves(
    plan: list[tuple[str, str]], all_paths: list[str] | set[str]
) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
    """Split a plan into the moves that are safe to apply and those to drop.

    Rather than abandoning an entire rename because a few files are ambiguous,
    this keeps every move that is provably loss-free and drops only the
    individual moves that would destroy data:

    * **collapse** — two or more sources targeting the same path: ALL of them are
      dropped (we cannot know which file deserves the name), so every involved
      file keeps its original, still-importable name.
    * **clobber** — a target that equals a file staying put: that move is dropped.

    Returns ``(safe, dropped)`` where ``safe`` is a list of ``(old, new)`` and
    ``dropped`` is a list of ``(old, new, reason)``. Applying ``safe`` (in an
    order from :func:`order_moves_safely`) can never lose a file.
    """
    moves = [(o, n) for o, n in plan if o != n]
    by_target: dict[str, list[str]] = {}
    for o, n in moves:
        by_target.setdefault(n, []).append(o)
    sources = {o for o, _ in moves}
    untouched = set(all_paths) - sources

    safe: list[tuple[str, str]] = []
    dropped: list[tuple[str, str, str]] = []
    for o, n in moves:
        if len(by_target[n]) > 1:
            dropped.append((o, n, "would collapse onto the same name as another file"))
        elif n in untouched:
            dropped.append((o, n, "would overwrite an existing file that is not being renamed"))
        else:
            safe.append((o, n))
    return safe, dropped


# ---------------------------------------------------------------------------
# Structure-aware target planner
# ---------------------------------------------------------------------------

# A season / season-range / season+episode envelope in a release title, e.g.
# "S01", "S01-S02", "S01E05", "S01E01-E11". Used to splice in a file's real id.
_SEASON_ENVELOPE = re.compile(
    r"(?i)\bS\d{1,2}(?:\s*[-–—]\s*S?\d{1,2})?"
    r"(?:\s*E\d{1,3}(?:\s*[-–—]\s*E?\d{1,3})?)?\b"
)
_EP_RANGE = re.compile(r"(?i)\bE\d{1,4}\s*[-–—]\s*E?\d{1,4}\b")


def place_episode_in_title(title: str, season: int, episode: int) -> str:
    """Splice a file's TRUE ``SxxExx`` into the release title, cleanly.

    The release title for a multi-season pack carries a *range* envelope
    (``S01-S02``) or, due to a known indexer limitation, only the *first* season
    (``S01``). Both are replaced by the calling file's own ``S{season}E{episode}``
    so every file gets a consistent, clean name with its CORRECT season — this is
    the fix for the season-collapse data loss. When the title has no season token
    at all, the identifier is inserted via the base engine's placement logic.
    """
    ident = f"S{season:02d}E{episode:02d}"
    m = _SEASON_ENVELOPE.search(title)
    if m is None:
        m = _EP_RANGE.search(title)
    if m is not None:
        spliced = title[: m.start()] + ident + title[m.end() :]
        return re.sub(r"\s{2,}", " ", spliced).strip()
    # no season/episode token in the title -> use the base engine's insertion
    return insert_episode_into_name(title, (f"{season:02d}", "E", f"{episode:02d}"))


def _target_path(
    old_path: str,
    base_name: str,
    root_folder: str | None,
    preserve_folder: bool,
    new_name: str,
) -> str:
    """Build a file's target path, preserving its subfolder (Sonarr ignores it).

    Mirrors the base engine's folder handling (:func:`src.rename.build_new_file_path`)
    so downstream folder-rename and verification behave identically — only the
    *base name* differs (it is supplied by the structure-aware planner).
    """
    segment = old_path.rsplit("/", 1)[-1]
    ext = "." + segment.rsplit(".", 1)[-1] if "." in segment else ""
    base = sanitize_filename(base_name)
    if root_folder and old_path.startswith(root_folder + "/"):
        folder_name = root_folder if preserve_folder else new_name
        relative = old_path[len(root_folder) + 1 :]
        if "/" in relative:
            subdir = relative.rsplit("/", 1)[0]
            return f"{folder_name}/{subdir}/{base}{ext}"
        return f"{folder_name}/{base}{ext}"
    return f"{base}{ext}"


def build_complex_plan(
    files: list[dict],
    new_name: str,
    root_folder: str | None,
    preserve_folder: bool,
    layout: TorrentLayout,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Build a loss-free rename plan for a complex (multi-season/specials/collection) torrent.

    Returns ``(plan, warnings)`` in the same shape as
    :func:`src.rename.validate_rename_plan`. The plan renames only the files it
    can name safely:

    * **multi-season / season+specials** — each regular-season video gets the
      release title with its OWN ``SxxExx`` spliced in (subfolders preserved).
    * **specials** (dedicated specials folder / season 0) and **absolute-numbered
      or season-less** files are left untouched — fabricating ``S00``/``SxxExx``
      for them makes Sonarr reject or mis-map them.
    * **collection** — distinct titles are left untouched (never collapsed).

    If the plan would lose data — two files sharing one target path, or two
    files resolving to the SAME ``SxxExx`` identifier (which makes Sonarr import
    only one and silently drop the other) — the whole file plan is abandoned
    (empty list) with an explanatory warning, so the caller leaves the torrent's
    files as-is rather than risk a destructive rename.
    """
    warnings: list[str] = []

    if layout.kind == LayoutKind.COLLECTION:
        return [], [
            f"collection of {layout.video_count} distinct titles: files left unchanged "
            f"(no safe single naming)"
        ]

    plan: list[tuple[str, str]] = []
    by_identifier: dict[tuple[int, int], list[str]] = {}

    for m in layout.files:
        if not m.is_video or m.kind == "sample":
            continue
        # preserve specials (season 0 / dedicated specials folder): never invent
        # an S00 episode number — Sonarr won't map it and may reject the file.
        if m.kind == "special" or m.season == 0:
            warnings.append(f"left special '{m.filename}' unchanged")
            continue
        if m.season is None or m.episode is None:
            warnings.append(f"left '{m.filename}' unchanged: no reliable season/episode to place")
            continue
        base = place_episode_in_title(new_name, m.season, m.episode)
        new_path = _target_path(m.path, base, root_folder, preserve_folder, new_name)
        plan.append((m.path, new_path))
        by_identifier.setdefault((m.season, m.episode), []).append(m.path)

    # Sonarr loss condition: two files resolving to the same (season, episode).
    dupes = {k: v for k, v in by_identifier.items() if len(v) > 1}
    if dupes:
        sample = "; ".join(f"S{s:02d}E{e:02d}×{len(v)}" for (s, e), v in list(dupes.items())[:3])
        return [], [
            f"unsafe: source has duplicate episode identifiers ({sample}); "
            f"files left unchanged to avoid Sonarr dropping episodes"
        ]

    # Path-level data-loss gate.
    safety = assess_plan_safety(plan, [f.get("name", "") for f in files])
    if not safety.safe:
        return [], [f"unsafe rename plan ({safety.conflicts[0]}); files left unchanged"]

    return plan, warnings
