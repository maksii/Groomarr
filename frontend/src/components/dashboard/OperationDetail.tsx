import { useMutation } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  ChevronRight,
  Database,
  FileStack,
  Info,
  ListChecks,
  RotateCcw,
  Undo2,
  XCircle,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { useOperation, useRefreshOperations } from "@/hooks/queries";
import { api } from "@/lib/api";
import {
  absoluteTime,
  decisionTone,
  layoutLabel,
  relativeTime,
  sourceMeta,
  statusMeta,
} from "@/lib/operations";
import type { OperationDetail, RollbackPreviewResponse } from "@/lib/types";
import { cn } from "@/lib/utils";
import { RenameDiff } from "./RenameDiff";

export function OperationDetailDialog({
  id,
  onOpenChange,
}: {
  id: number | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={id != null} onOpenChange={onOpenChange}>
      {id != null ? <DetailBody id={id} /> : null}
    </Dialog>
  );
}

function Section({
  icon,
  title,
  count,
  defaultOpen = true,
  children,
}: {
  icon: ReactNode;
  title: string;
  count?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-md border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <span className="text-muted-foreground">{icon}</span>
        <span className="text-sm font-medium">{title}</span>
        {count != null ? <span className="ml-auto">{count}</span> : null}
      </button>
      {open ? <div className="border-t border-border p-3">{children}</div> : null}
    </div>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</span>
      <span className="text-sm">{children}</span>
    </div>
  );
}

function DetailBody({ id }: { id: number }) {
  const { data: op, isLoading, isError } = useOperation(id);

  return (
    <DialogContent
      className="max-h-[88vh] w-[min(56rem,95vw)] max-w-none overflow-y-auto scrollbar-thin"
      title={op ? op.media_title || `Operation #${op.id}` : "Operation"}
    >
      {isLoading ? (
        <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
          <Spinner /> Loading operation…
        </div>
      ) : isError || !op ? (
        <Banner tone="destructive" title="Could not load operation">
          The operation could not be loaded. It may have been pruned from the history.
        </Banner>
      ) : (
        <OperationView op={op} />
      )}
    </DialogContent>
  );
}

function OperationView({ op }: { op: OperationDetail }) {
  const sm = statusMeta(op.status);
  const src = sourceMeta(op.source);
  const SrcIcon = src.icon;
  const StatusIcon = sm.icon;

  return (
    <div className="space-y-4">
      {/* Header badges + timestamp */}
      <div className="-mt-1 flex flex-wrap items-center gap-2 text-xs">
        <Badge tone="default" className="gap-1">
          <SrcIcon className="h-3 w-3" /> {src.label}
        </Badge>
        <Badge tone={sm.tone} className="gap-1">
          <StatusIcon className="h-3 w-3" /> {sm.label}
        </Badge>
        {op.dry_run ? <Badge tone="warning">dry run</Badge> : null}
        {op.layout_kind ? <Badge tone="muted">{layoutLabel(op.layout_kind)}</Badge> : null}
        {op.rollback_of ? <Badge tone="muted">rollback of #{op.rollback_of}</Badge> : null}
        <span className="ml-auto text-muted-foreground" title={absoluteTime(op.created_at)}>
          {relativeTime(op.created_at)}
        </span>
      </div>

      {/* Metadata grid */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-md border border-border p-3 sm:grid-cols-3">
        <Meta label="Event">{op.event_type || "—"}</Meta>
        <Meta label="Decision">
          <Badge tone={decisionTone(op.decision)}>{op.decision || "—"}</Badge>
        </Meta>
        <Meta label="Ruleset">
          {op.used_global ? (
            <Badge tone="muted">Global</Badge>
          ) : (
            <Badge tone="primary">{op.tracker_name || "tracker"}</Badge>
          )}
        </Meta>
        <Meta label="Indexer">{op.indexer || "—"}</Meta>
        <Meta label="Quality">{op.quality || "—"}</Meta>
        <Meta label="Release group">{op.release_group || "—"}</Meta>
        <Meta label="Download client">{op.download_client || "—"}</Meta>
        <Meta label="Rename mode">
          <span className="font-mono text-xs">{op.rename_mode || "—"}</span>
        </Meta>
        <Meta label="Files">
          {op.files_renamed > 0 || op.files_total > 0
            ? `${op.files_renamed}/${op.files_total} renamed`
            : "—"}
        </Meta>
        {op.torrent_hash ? (
          <div className="col-span-2 flex flex-col gap-0.5 sm:col-span-3">
            <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
              Torrent hash
            </span>
            <span className="break-all font-mono text-xs">{op.torrent_hash}</span>
          </div>
        ) : null}
      </div>

      {/* Decision + skip reason */}
      {op.decision === "skipped" && op.skip_reason ? (
        <Banner tone="warning" icon={<Ban className="h-4 w-4" />} title="Skipped">
          {op.skip_reason}
        </Banner>
      ) : null}
      {op.status === "failed" && op.error ? (
        <Banner tone="destructive" icon={<XCircle className="h-4 w-4" />} title="Failed">
          {op.error}
        </Banner>
      ) : null}

      {/* Release title */}
      {op.release_title ? (
        <div className="space-y-1">
          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
            Release title
          </div>
          <div className="break-all rounded-md border border-border bg-muted/30 px-3 py-2 font-mono text-xs">
            {op.release_title}
          </div>
        </div>
      ) : null}

      {/* Trigger filter breakdown */}
      {op.trigger_checks.length > 0 ? (
        <Section
          icon={<ListChecks className="h-4 w-4" />}
          title="Trigger filters"
          count={
            <Badge
              tone={op.trigger_checks.every((c) => c.passed) ? "success" : "warning"}
            >
              {op.trigger_checks.filter((c) => c.passed).length}/{op.trigger_checks.length} pass
            </Badge>
          }
          defaultOpen={op.decision === "skipped"}
        >
          <ul className="space-y-1.5">
            {op.trigger_checks.map((c, i) => (
              // biome-ignore lint: positional list
              <li key={i} className="flex items-start gap-2 text-xs">
                {c.passed ? (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
                ) : (
                  <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                )}
                <div className="min-w-0">
                  <span className={cn("font-medium", c.blocking && "text-destructive")}>
                    {c.label}
                  </span>
                  {c.blocking ? <span className="ml-1 text-destructive">(blocks)</span> : null}
                  <div className="text-muted-foreground">
                    <span className="font-mono">{c.tested}</span> — {c.detail}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {/* Rule transformation trace */}
      {op.rule_steps.length > 0 ? (
        <Section
          icon={<ListChecks className="h-4 w-4" />}
          title="Rename rule trace"
          count={<Badge tone="muted">{op.rule_steps.length}</Badge>}
          defaultOpen={false}
        >
          <ol className="space-y-1.5">
            {op.rule_steps.map((s, i) => (
              // biome-ignore lint: positional list
              <li key={i} className="text-xs">
                <div
                  className={cn("font-medium", s.error ? "text-destructive" : "text-foreground")}
                >
                  {s.rule}
                </div>
                {s.error ? (
                  <div className="text-destructive">{s.error}</div>
                ) : (
                  <div className="break-all font-mono text-muted-foreground">
                    {s.before} <span className="text-success">→</span> {s.after}
                  </div>
                )}
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {/* Rename visualization */}
      {op.old_name || op.new_name || op.folder_old || op.file_changes.length > 0 ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <ArrowRight className="h-4 w-4 text-muted-foreground" />
            {op.status === "dry_run" ? "Planned rename (dry run)" : "Rename"}
          </div>
          {op.old_name || op.new_name ? (
            <div className="space-y-1">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Torrent name
              </div>
              <RenameDiff from={op.old_name} to={op.new_name} />
            </div>
          ) : null}
          {op.folder_old || op.folder_new ? (
            <div className="space-y-1">
              <div className="text-[11px] uppercase tracking-wide text-muted-foreground">
                Root folder
              </div>
              <RenameDiff from={op.folder_old ?? ""} to={op.folder_new ?? ""} />
            </div>
          ) : null}
          {op.file_changes.length > 0 ? (
            <Section
              icon={<FileStack className="h-4 w-4" />}
              title="Files"
              count={<Badge tone="muted">{op.file_changes.length}</Badge>}
              defaultOpen={op.file_changes.length <= 12}
            >
              <ul className="space-y-2">
                {op.file_changes.map((f, i) => (
                  // biome-ignore lint: positional list
                  <li key={i}>
                    <RenameDiff from={f.old_path} to={f.new_path} />
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}
        </div>
      ) : null}

      {/* Live state */}
      <LiveState op={op} />

      {/* Rollback */}
      <RollbackPanel op={op} />
    </div>
  );
}

function LiveState({ op }: { op: OperationDetail }) {
  const live = op.live;
  if (!live) return null;
  if (!live.checked) {
    return (
      <Banner tone="info" icon={<Info className="h-4 w-4" />} title="Live state unavailable">
        {live.note || "qBittorrent is not connected."}
      </Banner>
    );
  }
  return (
    <Section
      icon={<Database className="h-4 w-4" />}
      title="Current state in qBittorrent"
      count={
        live.torrent_exists ? (
          <Badge tone={live.matches_rename ? "success" : "warning"}>
            {live.matches_rename ? "matches" : "drifted"}
          </Badge>
        ) : (
          <Badge tone="destructive">gone</Badge>
        )
      }
      defaultOpen={!live.torrent_exists || !live.matches_rename}
    >
      {!live.torrent_exists ? (
        <p className="text-xs text-muted-foreground">
          {live.note || "This torrent is no longer present in qBittorrent."}
        </p>
      ) : (
        <div className="space-y-2 text-xs">
          <div>
            <span className="text-muted-foreground">Current name: </span>
            <span className="break-all font-mono">{live.torrent_name}</span>
          </div>
          {!live.matches_rename ? (
            <div className="flex items-start gap-1.5 text-warning">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                The torrent no longer carries the name Groomarr set — it was changed elsewhere.
                Rollback will only revert files that still match.
              </span>
            </div>
          ) : null}
          {live.files.length > 0 ? (
            <details className="group">
              <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                {live.files.length} file{live.files.length === 1 ? "" : "s"} on disk
              </summary>
              <ul className="mt-1.5 max-h-48 space-y-0.5 overflow-y-auto scrollbar-thin font-mono text-[11px] text-muted-foreground">
                {live.files.map((f, i) => (
                  // biome-ignore lint: positional list
                  <li key={i} className="break-all">
                    {f}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </div>
      )}
    </Section>
  );
}

function RollbackPanel({ op }: { op: OperationDetail }) {
  const refresh = useRefreshOperations();
  const [preview, setPreview] = useState<RollbackPreviewResponse | null>(null);

  const previewMut = useMutation({
    mutationFn: () => api.rollbackPreview(op.id),
    onSuccess: (data) => setPreview(data),
    onError: (e) => toast.error("Could not prepare rollback", { description: String((e as Error).message) }),
  });

  const executeMut = useMutation({
    mutationFn: () => api.rollback(op.id),
    onSuccess: (d) => {
      if (d.status === "success") {
        toast.success("Rolled back", {
          description: "The original names were restored in qBittorrent.",
        });
      } else if (d.status === "partial") {
        toast.warning("Partially rolled back", {
          description: `${d.files_reverted} restored, ${d.files_failed} failed, ${d.files_skipped} skipped.`,
        });
      } else {
        toast.error("Rollback failed", { description: d.reason || d.errors.join("; ") });
      }
      setPreview(null);
      refresh();
    },
    onError: (e) => toast.error("Rollback failed", { description: String((e as Error).message) }),
  });

  if (op.rolled_back) {
    return (
      <Banner tone="info" icon={<RotateCcw className="h-4 w-4" />} title="Rolled back">
        This rename was rolled back
        {op.rolled_back_at ? ` ${relativeTime(op.rolled_back_at)}` : ""}
        {op.rollback_op ? ` (operation #${op.rollback_op}).` : "."}
      </Banner>
    );
  }

  if (!op.can_rollback) {
    return (
      <div className="rounded-md border border-dashed border-border px-3 py-2 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">Rollback unavailable.</span>{" "}
        {op.rollback_unavailable_reason || "There is no executed rename to reverse."}
      </div>
    );
  }

  // Confirmation view (after a preview has been fetched)
  if (preview) {
    if (!preview.can_rollback) {
      return (
        <Banner tone="warning" icon={<AlertTriangle className="h-4 w-4" />} title="Cannot roll back">
          {preview.reason}
          <div className="mt-3">
            <Button size="sm" variant="outline" onClick={() => setPreview(null)}>
              Close
            </Button>
          </div>
        </Banner>
      );
    }
    return (
      <div className="space-y-3 rounded-md border border-warning/40 bg-warning/5 p-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Undo2 className="h-4 w-4 text-warning" /> Confirm rollback
        </div>
        <p className="text-xs text-muted-foreground">
          This will reverse the rename in qBittorrent, restoring the original names. Verified
          against current state — anything that has changed since is skipped, never overwritten.
        </p>
        <ul className="space-y-1.5 font-mono text-[11px]">
          {preview.torrent_step ? (
            <li className="break-all">
              <span className="text-muted-foreground">torrent: </span>
              {preview.torrent_step.frm} <span className="text-success">→</span>{" "}
              {preview.torrent_step.to}
            </li>
          ) : null}
          {preview.folder_step ? (
            <li className="break-all">
              <span className="text-muted-foreground">folder: </span>
              {preview.folder_step.frm} <span className="text-success">→</span>{" "}
              {preview.folder_step.to}
            </li>
          ) : null}
          {preview.file_steps.map((s, i) => (
            // biome-ignore lint: positional list
            <li key={i} className="break-all">
              <span className="text-muted-foreground">file: </span>
              {s.frm} <span className="text-success">→</span> {s.to}
            </li>
          ))}
        </ul>
        {preview.skipped.length > 0 ? (
          <div className="space-y-1 text-[11px] text-warning">
            <div className="font-medium">{preview.skipped.length} will be skipped:</div>
            {preview.skipped.map((s, i) => (
              // biome-ignore lint: positional list
              <div key={i} className="break-all">
                {s.frm.split("/").pop()} — {s.reason}
              </div>
            ))}
          </div>
        ) : null}
        {preview.warnings.map((w, i) => (
          // biome-ignore lint: positional list
          <p key={i} className="text-[11px] text-warning">
            {w}
          </p>
        ))}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="destructive"
            disabled={executeMut.isPending}
            onClick={() => executeMut.mutate()}
          >
            {executeMut.isPending ? <Spinner /> : <Undo2 className="h-4 w-4" />} Confirm rollback
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={executeMut.isPending}
            onClick={() => setPreview(null)}
          >
            Cancel
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between rounded-md border border-border px-3 py-2.5">
      <div className="text-xs text-muted-foreground">
        Reverse this rename and restore the original names (safe & verified).
      </div>
      <Button
        size="sm"
        variant="outline"
        disabled={previewMut.isPending}
        onClick={() => previewMut.mutate()}
      >
        {previewMut.isPending ? <Spinner /> : <Undo2 className="h-4 w-4" />} Roll back…
      </Button>
    </div>
  );
}
