// Presentation helpers for the operations dashboard: status/source/layout
// mappings and timestamp formatting. Kept separate so the table, detail view,
// and KPI cards stay visually consistent.

import {
  type LucideIcon,
  Ban,
  CheckCircle2,
  CircleSlash,
  Clock,
  FileWarning,
  Film,
  FlaskConical,
  Radar,
  RotateCcw,
  Tv,
  Webhook,
  Wrench,
  XCircle,
} from "lucide-react";

type Tone = "default" | "primary" | "success" | "warning" | "destructive" | "muted";

interface StatusMeta {
  label: string;
  tone: Tone;
  icon: LucideIcon;
}

const STATUS: Record<string, StatusMeta> = {
  renamed: { label: "Renamed", tone: "success", icon: CheckCircle2 },
  no_change: { label: "No change", tone: "muted", icon: CircleSlash },
  queued: { label: "Queued", tone: "primary", icon: Clock },
  processing: { label: "Processing", tone: "primary", icon: Clock },
  skipped: { label: "Skipped", tone: "warning", icon: Ban },
  failed: { label: "Failed", tone: "destructive", icon: XCircle },
  dry_run: { label: "Dry run", tone: "warning", icon: FlaskConical },
  rolled_back: { label: "Rolled back", tone: "muted", icon: RotateCcw },
  test: { label: "Test", tone: "muted", icon: Webhook },
  received: { label: "Received", tone: "muted", icon: Webhook },
};

export function statusMeta(status: string): StatusMeta {
  return STATUS[status] ?? { label: status || "—", tone: "default", icon: FileWarning };
}

interface SourceMeta {
  label: string;
  icon: LucideIcon;
}

const SOURCE: Record<string, SourceMeta> = {
  sonarr: { label: "Sonarr", icon: Tv },
  radarr: { label: "Radarr", icon: Film },
  manual: { label: "Manual", icon: Wrench },
  prowlarr: { label: "Prowlarr", icon: Radar },
};

export function sourceMeta(source: string): SourceMeta {
  return SOURCE[source] ?? { label: source || "—", icon: Webhook };
}

const LAYOUT: Record<string, string> = {
  simple_season: "Season",
  movie: "Movie",
  multi_season: "Multi-season",
  season_with_specials: "Season + specials",
  collection: "Collection",
  unknown: "Unknown",
};

export function layoutLabel(kind: string): string {
  return LAYOUT[kind] ?? kind;
}

/** Compact relative time like "3m ago", "2h ago", "5d ago". */
export function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.round((Date.now() - then) / 1000);
  if (secs < 5) return "just now";
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.round(months / 12)}y ago`;
}

/** Full local timestamp for tooltips. */
export function absoluteTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Decision tone for the decision pill (processed / skipped / error). */
export function decisionTone(decision: string): Tone {
  if (decision === "processed") return "primary";
  if (decision === "skipped") return "warning";
  if (decision === "error") return "destructive";
  return "muted";
}

// Statuses where Groomarr actually executed or planned a rename, so the
// before → after diff and the "files renamed" count are meaningful. A *skipped*
// webhook never touched the torrent (it only records the name it *would* have
// produced), so showing a rename diff for it is misleading.
const RENAME_OUTCOME_STATUSES = new Set([
  "renamed",
  "no_change",
  "dry_run",
  "rolled_back",
  "failed",
]);

/** Whether this operation represents an executed/planned rename (vs. a skip). */
export function isRenameOutcome(status: string): boolean {
  return RENAME_OUTCOME_STATUSES.has(status);
}

/**
 * Whether the live torrent is *expected* to still carry the renamed value, so
 * the "matches / drifted" drift-detection verdict applies. Only true for
 * operations that successfully applied the new name (not dry runs or rollbacks,
 * where the live name intentionally differs from `new_name`).
 */
export function expectsRename(status: string): boolean {
  return status === "renamed" || status === "no_change";
}

// --- Live torrent download status (qBittorrent `state` + `progress`) ---

const DL_STATES = new Set([
  "downloading",
  "metaDL",
  "stalledDL",
  "forcedDL",
  "queuedDL",
  "allocating",
]);
const UP_STATES = new Set(["uploading", "stalledUP", "forcedUP", "queuedUP"]);
const PAUSED_STATES = new Set(["pausedUP", "pausedDL", "stoppedUP", "stoppedDL"]);
const ERROR_STATES = new Set(["error", "missingFiles"]);
const CHECK_STATES = new Set(["checkingUP", "checkingDL", "checkingResumeData"]);

export interface TorrentStateMeta {
  label: string;
  tone: Tone;
  percent: number | null;
}

/** Map a qBittorrent torrent state + progress to a friendly status label/tone. */
export function torrentStateMeta(state: string, progress: number | null): TorrentStateMeta {
  const percent = progress != null ? Math.round(progress * 100) : null;
  const complete = percent != null && percent >= 100;

  if (ERROR_STATES.has(state)) {
    return { label: state === "missingFiles" ? "Missing files" : "Error", tone: "destructive", percent };
  }
  if (CHECK_STATES.has(state)) return { label: "Checking", tone: "muted", percent };
  if (state === "moving") return { label: "Moving", tone: "muted", percent };
  if (PAUSED_STATES.has(state)) {
    return { label: complete ? "Paused · complete" : "Paused", tone: "muted", percent };
  }
  if (UP_STATES.has(state) || complete) {
    return { label: "Completed", tone: "success", percent: percent ?? 100 };
  }
  if (DL_STATES.has(state)) return { label: "Downloading", tone: "primary", percent };
  return { label: state || "Unknown", tone: "default", percent };
}

/** Human-readable byte size (e.g. "1.4 GB"). Returns "—" for missing/zero. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || bytes <= 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export const SOURCE_OPTIONS = [
  { value: "", label: "All sources" },
  { value: "sonarr", label: "Sonarr" },
  { value: "radarr", label: "Radarr" },
  { value: "manual", label: "Manual" },
  { value: "prowlarr", label: "Prowlarr" },
];

export const STATUS_OPTIONS = [
  { value: "", label: "All statuses" },
  { value: "renamed", label: "Renamed" },
  { value: "no_change", label: "No change" },
  { value: "skipped", label: "Skipped" },
  { value: "failed", label: "Failed" },
  { value: "dry_run", label: "Dry run" },
  { value: "rolled_back", label: "Rolled back" },
  { value: "queued", label: "Queued" },
  { value: "test", label: "Test" },
  { value: "received", label: "Received" },
];
