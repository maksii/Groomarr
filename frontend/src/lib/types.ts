// Shared types mirroring the Groomarr backend models (src/models.py).

export type ScorePolicy = "block" | "warn";

export interface RuleSet {
  indexers_include: string[];
  indexers_exclude: string[];
  qualities_include: string[];
  qualities_exclude: string[];
  customformats_require_any: string[];
  customformats_exclude: string[];
  min_customformat_score: number | null;
  download_clients_include: string[];
  download_clients_exclude: string[];
  release_groups_include: string[];
  release_groups_exclude: string[];
  prefix: string;
  suffix: string;
  remove_patterns: string[];
  replace_patterns: Record<string, string>;
  skip_title_patterns: string[];
  validate_custom_format_score: boolean;
  score_validation_policy: ScorePolicy;
}

export interface TrackerConfig {
  name: string;
  match: string[];
  rules: RuleSet;
}

export interface RulesConfig {
  global: RuleSet;
  trackers: TrackerConfig[];
}

export type ConfigFormat = "hierarchical" | "flat" | "empty" | "missing";

export interface ConfigMeta {
  config_path: string;
  config_found: boolean;
  config_error: string | null;
  config_format: ConfigFormat;
  readonly: boolean;
}

export interface ConfigResponse {
  meta: ConfigMeta;
  config: RulesConfig;
}

export interface SaveConfigResponse extends ConfigResponse {
  status: string;
  warnings: string[];
}

export interface SimulateRelease {
  release_title: string;
  indexer: string;
  quality: string;
  release_group: string;
  custom_formats: string[];
  custom_format_score: number | null;
  download_client: string;
}

export interface SimulateStep {
  rule: string;
  before: string;
  after: string;
  error?: string | null;
}

export interface SimulateResponse {
  status: string;
  matched_tracker: string | null;
  used_global: boolean;
  would_process: boolean;
  skip_reason: string;
  original_title: string;
  new_title: string;
  changed: boolean;
  steps: SimulateStep[];
  errors: string[];
}

export interface ValidatePatternResponse {
  valid: boolean;
  kind: "regex" | "match";
  interpreted?: string | null;
  error?: string | null;
}

export interface SettingsView {
  rename_mode: string;
  dry_run: boolean;
  initial_delay: number;
  max_retries: number;
  retry_delay: number;
  api_operation_delay_ms: number;
  log_level: string;
  log_format: string;
  rules_file: string;
  config_readonly: boolean;
  qbittorrent_url: string;
  sonarr_configured: boolean;
  radarr_configured: boolean;
}

export interface StatusView {
  status: string;
  version: string;
  qbittorrent: string;
  sonarr: string | null;
  radarr: string | null;
  dry_run: boolean;
  score_validation: boolean;
  config_found: boolean;
  config_error: string | null;
  readonly: boolean;
}

// ---- Tools (existing endpoints) ----

export type RenameMode =
  | "torrent_only"
  | "torrent_and_folder"
  | "torrent_folder_files"
  | "folder_only"
  | "files_only";

export interface FileRenamePreview {
  old_path: string;
  new_path: string;
  will_change: boolean;
}

export interface PreviewRenameResponse {
  status: string;
  torrent_hash: string;
  mode: string;
  reason: string | null;
  current_torrent_name: string | null;
  current_root_folder: string | null;
  new_torrent_name: string | null;
  new_root_folder: string | null;
  file_renames: FileRenamePreview[];
  torrent_will_change: boolean;
  folder_will_change: boolean;
  files_will_change: number;
  total_files: number;
  warnings: string[];
}

export interface ManualRenameResponse {
  status: string;
  torrent_hash: string;
  new_name: string | null;
  mode: string | null;
  reason: string | null;
}

export interface FindTorrentResponse {
  status: string;
  torrent_id: string;
  torrent_hash: string | null;
  reason: string | null;
}

// ---- Factories (mirror backend defaults) ----

export function emptyRuleSet(): RuleSet {
  return {
    indexers_include: [],
    indexers_exclude: [],
    qualities_include: [],
    qualities_exclude: [],
    customformats_require_any: [],
    customformats_exclude: [],
    min_customformat_score: null,
    download_clients_include: [],
    download_clients_exclude: [],
    release_groups_include: [],
    release_groups_exclude: [],
    prefix: "",
    suffix: "",
    remove_patterns: [],
    replace_patterns: {},
    skip_title_patterns: [],
    validate_custom_format_score: false,
    score_validation_policy: "block",
  };
}

export function emptyTracker(): TrackerConfig {
  return { name: "", match: [], rules: emptyRuleSet() };
}
