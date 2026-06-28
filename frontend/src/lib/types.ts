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
  files: string[];
}

export interface SimulateStep {
  rule: string;
  before: string;
  after: string;
  error?: string | null;
}

export interface FilterCheck {
  label: string;
  tested: string;
  passed: boolean;
  detail: string;
  blocking: boolean;
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
  trigger_checks: FilterCheck[];
  file_renames: FileRenamePreview[];
  file_warnings: string[];
  root_folder: string | null;
}

export interface TorrentSampleResponse {
  status: string;
  title: string | null;
  files: string[];
  reason: string | null;
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

// ---- Operation history (dashboard) ----

export interface OperationSummary {
  id: number;
  created_at: string;
  updated_at: string;
  source: string;
  event_type: string;
  status: string;
  decision: string;
  skip_reason: string;
  media_title: string;
  release_title: string;
  indexer: string;
  tracker_name: string | null;
  used_global: boolean;
  torrent_hash: string;
  old_name: string;
  new_name: string;
  layout_kind: string;
  files_renamed: number;
  files_total: number;
  dry_run: boolean;
  rolled_back: boolean;
  rollback_of: number | null;
}

export interface OperationListResponse {
  items: OperationSummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface OperationStats {
  total: number;
  last_24h: number;
  renamed: number;
  skipped: number;
  failed: number;
  rolled_back: number;
  by_status: Record<string, number>;
  by_source: Record<string, number>;
  last_operation_at: string | null;
}

export interface OperationFileChange {
  old_path: string;
  new_path: string;
  changed: boolean;
}

export interface LiveTorrentState {
  checked: boolean;
  torrent_exists: boolean;
  torrent_name: string | null;
  root_folder: string | null;
  files: string[];
  matches_rename: boolean;
  note: string;
}

export interface OperationDetail extends OperationSummary {
  download_client: string;
  quality: string;
  release_group: string;
  rename_mode: string;
  folder_old: string | null;
  folder_new: string | null;
  error: string;
  rolled_back_at: string | null;
  rollback_op: number | null;
  rule_steps: SimulateStep[];
  trigger_checks: FilterCheck[];
  file_changes: OperationFileChange[];
  live: LiveTorrentState | null;
  can_rollback: boolean;
  rollback_unavailable_reason: string;
}

export interface RollbackStepView {
  kind: string;
  frm: string;
  to: string;
}

export interface RollbackSkip {
  kind: string;
  frm: string;
  to: string;
  reason: string;
}

export interface RollbackPreviewResponse {
  status: string;
  operation_id: number;
  torrent_exists: boolean;
  can_rollback: boolean;
  reason: string;
  torrent_step: RollbackStepView | null;
  folder_step: RollbackStepView | null;
  file_steps: RollbackStepView[];
  skipped: RollbackSkip[];
  warnings: string[];
}

export interface RollbackResponse {
  status: string;
  operation_id: number;
  rollback_operation_id: number | null;
  torrent_reverted: boolean;
  folder_reverted: boolean;
  files_reverted: number;
  files_failed: number;
  files_skipped: number;
  steps: string[];
  errors: string[];
  reason: string;
}

export interface OperationListParams {
  q?: string;
  source?: string;
  status?: string;
  decision?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export function exampleSampleRelease(): SimulateRelease {
  // A realistic, high-value scenario: the tracker gives a clean, complete release
  // title, but the downloaded folder/files are badly named. Groomarr renames the
  // folder + every file to the proper name while preserving episode numbers —
  // which is what keeps Sonarr/Radarr matching correctly.
  return {
    release_title: "Series Name 2026 S01 JAPANESE 1080p CR WEB-DL DD+ 2.0 H.264-ReleaseGroup",
    indexer: "",
    quality: "WEBDL-1080p",
    release_group: "ReleaseGroup",
    custom_formats: [],
    custom_format_score: null,
    download_client: "",
    files: [
      "[ReleaseGroup] Series Name 2026/[releasegroup]series.name.01.mkv",
      "[ReleaseGroup] Series Name 2026/[releasegroup]series.name.02.mkv",
      "[ReleaseGroup] Series Name 2026/[releasegroup]series.name.03.mkv",
    ],
  };
}
