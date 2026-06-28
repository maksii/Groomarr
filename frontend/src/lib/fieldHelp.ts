// Inline guidance shown via the (i) popovers next to fields. Keep each concise.

export interface HelpEntry {
  title: string;
  body: string;
  example?: string;
}

export const FIELD_HELP: Record<string, HelpEntry> = {
  // ---- Trigger filters ----
  indexers_include: {
    title: "Indexers — include",
    body: "Only process releases whose indexer matches one of these patterns. Leave empty to allow all indexers. Case-insensitive regular expressions.",
    example: "TrackerA.*",
  },
  indexers_exclude: {
    title: "Indexers — exclude",
    body: "Skip releases whose indexer matches any of these patterns.",
    example: ".*Public.*",
  },
  qualities_include: {
    title: "Qualities — include",
    body: "Only process releases whose quality matches one of these. Empty = all qualities. Regex, matched against the Sonarr/Radarr quality name.",
    example: "Bluray.*",
  },
  qualities_exclude: {
    title: "Qualities — exclude",
    body: "Skip releases whose quality matches any of these.",
    example: "CAM",
  },
  release_groups_include: {
    title: "Release groups — include",
    body: "Only process releases from these groups. Empty = all. A release with no group still passes the include check.",
    example: "FraMeSToR",
  },
  release_groups_exclude: {
    title: "Release groups — exclude",
    body: "Skip releases from these groups.",
    example: "YIFY",
  },
  download_clients_include: {
    title: "Download clients — include",
    body: "Only process releases grabbed by these download clients. Useful when you run more than one qBittorrent instance. Empty = all.",
    example: "movies_qBit",
  },
  download_clients_exclude: {
    title: "Download clients — exclude",
    body: "Skip releases grabbed by these download clients.",
    example: "seed_qBit",
  },
  customformats_require_any: {
    title: "Custom formats — require any",
    body: "Process only if the release has at least one of these custom formats. Exact names (not regex), matched against the webhook's custom-format list.",
    example: "x265",
  },
  customformats_exclude: {
    title: "Custom formats — exclude",
    body: "Skip the release if it has any of these custom formats. Exact names.",
    example: "3D",
  },
  min_customformat_score: {
    title: "Minimum custom format score",
    body: "Skip releases whose Sonarr/Radarr custom-format score is below this number. Leave empty to disable the threshold.",
    example: "1000",
  },
  // ---- Rename rules ----
  prefix: {
    title: "Prefix",
    body: "Text added to the start of the renamed title.",
    example: "[AUTO] ",
  },
  suffix: {
    title: "Suffix",
    body: "Text added to the end of the renamed title.",
    example: " [Renamed]",
  },
  remove_patterns: {
    title: "Remove patterns",
    body: "Regex matches are removed from the title, in order. Runs before replace patterns.",
    example: "\\[.*?\\]  (removes [bracketed] tags)",
  },
  replace_patterns: {
    title: "Replace patterns",
    body: "Each regex (left) is replaced by the text on the right. Runs after removals.",
    example: "\\.  →  ' '   (turns dots into spaces)",
  },
  skip_title_patterns: {
    title: "Skip title patterns",
    body: "If the title matches any of these, it is left completely unchanged (no rename).",
    example: "PROPER",
  },
  // ---- Score validation ----
  validate_custom_format_score: {
    title: "Validate custom format score",
    body: "Before renaming, ask the Sonarr/Radarr API to score both the old and new name, and act on the result. Requires the matching API URL + key to be configured (see Status).",
  },
  score_validation_policy: {
    title: "Policy when the score would drop",
    body: "Block: skip the rename if the new name scores lower. Warn: rename anyway but log a warning.",
  },
  // ---- Tracker ----
  tracker_name: {
    title: "Tracker name",
    body: "A label for this override. Shown in logs so you can tell which config handled a release.",
    example: "anime-tracker",
  },
  tracker_match: {
    title: "Match patterns",
    body: "Which indexers this override applies to. The first tracker whose patterns match wins, and its rules fully replace the global rules.",
    example: "Nyaa*  ·  /.*anime.*/i  ·  TrackerName",
  },
};

// Pattern-syntax primer, reused where match/regex patterns are entered.
export const PATTERN_HELP = {
  regex: "Case-insensitive regular expression. e.g. Bluray.* matches Bluray-1080p and Bluray-2160p.",
  match:
    "Three pattern types: exact ('TrackerName', case-insensitive), wildcard ('Tracker*', '*Cinema*'), or regex wrapped in slashes ('/Tracker.*API/').",
};
