import { ChevronRight, Inbox, Search, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { useOperations } from "@/hooks/queries";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import {
  absoluteTime,
  relativeTime,
  SOURCE_OPTIONS,
  sourceMeta,
  STATUS_OPTIONS,
  statusMeta,
} from "@/lib/operations";
import type { OperationSummary } from "@/lib/types";
import { OperationDetailDialog } from "./OperationDetail";

const PAGE_SIZE = 25;

/** One-line description of what an operation did, for the row's secondary text. */
function summarize(op: OperationSummary): string {
  if (op.decision === "skipped") return op.skip_reason || "Skipped";
  if (["renamed", "no_change", "dry_run", "rolled_back"].includes(op.status)) {
    if (op.old_name && op.new_name && op.old_name !== op.new_name) {
      return `${op.old_name}  →  ${op.new_name}`;
    }
    return op.new_name || op.release_title || "—";
  }
  return op.release_title || op.skip_reason || "—";
}

function Row({ op, onClick }: { op: OperationSummary; onClick: () => void }) {
  const src = sourceMeta(op.source);
  const SrcIcon = src.icon;
  const sm = statusMeta(op.status);
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
        <SrcIcon className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{op.media_title || "(no title)"}</span>
          {op.dry_run ? (
            <Badge tone="warning" className="shrink-0">
              dry run
            </Badge>
          ) : null}
        </div>
        <div className="truncate font-mono text-xs text-muted-foreground">{summarize(op)}</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {op.tracker_name ? (
          <Badge tone="muted" className="hidden lg:inline-flex">
            {op.tracker_name}
          </Badge>
        ) : null}
        <Badge tone={sm.tone}>{sm.label}</Badge>
        <span
          className="hidden w-16 text-right text-xs text-muted-foreground sm:inline"
          title={absoluteTime(op.created_at)}
        >
          {relativeTime(op.created_at)}
        </span>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
      </div>
    </button>
  );
}

export function OperationsLog({
  status,
  onStatusChange,
}: {
  status: string;
  onStatusChange: (status: string) => void;
}) {
  const [q, setQ] = useState("");
  const debouncedQ = useDebouncedValue(q, 300);
  const [source, setSource] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);

  // Reset to the first page whenever a filter changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset on filter change
  useEffect(() => setOffset(0), [debouncedQ, source, status]);

  const { data, isLoading, isFetching, isError } = useOperations({
    q: debouncedQ || undefined,
    source: source || undefined,
    status: status || undefined,
    limit: PAGE_SIZE,
    offset,
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const hasFilters = Boolean(debouncedQ || source || status);
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);

  return (
    <Card className="overflow-hidden">
      {/* Toolbar */}
      <div className="flex flex-col gap-2 border-b border-border p-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search title, release, hash, indexer…"
            className="pl-9"
          />
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            options={SOURCE_OPTIONS}
            className="w-36"
          />
          <Select
            value={status}
            onChange={(e) => onStatusChange(e.target.value)}
            options={STATUS_OPTIONS}
            className="w-40"
          />
          {hasFilters ? (
            <Button
              variant="ghost"
              size="icon"
              title="Clear filters"
              onClick={() => {
                setQ("");
                setSource("");
                onStatusChange("");
              }}
            >
              <X className="h-4 w-4" />
            </Button>
          ) : null}
        </div>
      </div>

      {/* Rows */}
      {isLoading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <Spinner /> Loading operations…
        </div>
      ) : isError ? (
        <div className="py-16 text-center text-sm text-destructive">
          Failed to load operations.
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-16 text-center text-sm text-muted-foreground">
          <Inbox className="h-8 w-8 opacity-50" />
          {hasFilters ? "No operations match your filters." : "No operations recorded yet."}
          {!hasFilters ? (
            <p className="max-w-sm text-xs">
              When Sonarr or Radarr grabs a release, the webhook, decision, and rename will appear
              here.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="divide-y divide-border">
          {items.map((op) => (
            <Row key={op.id} op={op} onClick={() => setSelected(op.id)} />
          ))}
        </div>
      )}

      {/* Footer / pagination */}
      {total > 0 ? (
        <div className="flex items-center justify-between border-t border-border px-4 py-2.5 text-xs text-muted-foreground">
          <span className="flex items-center gap-2">
            {from}–{to} of {total}
            {isFetching ? <Spinner className="h-3 w-3" /> : null}
          </span>
          <span className="flex items-center gap-1">
            <Button
              variant="outline"
              size="sm"
              disabled={offset === 0}
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
            >
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              disabled={offset + PAGE_SIZE >= total}
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
            >
              Next
            </Button>
          </span>
        </div>
      ) : null}

      <OperationDetailDialog
        id={selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
      />
    </Card>
  );
}
