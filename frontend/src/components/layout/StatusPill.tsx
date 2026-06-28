import { useStatus } from "@/hooks/queries";
import { cn } from "@/lib/utils";

function Dot({ ok }: { ok: boolean | null }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        ok === null ? "bg-muted-foreground/50" : ok ? "bg-success" : "bg-destructive",
      )}
    />
  );
}

export function StatusPill() {
  const { data, isError } = useStatus();
  const qbitOk = isError ? false : data ? data.qbittorrent === "connected" : null;

  return (
    <div className="flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1.5 text-xs">
      <Dot ok={qbitOk} />
      <span className="text-muted-foreground">qBittorrent</span>
      <span className="font-medium">
        {qbitOk === null ? "…" : qbitOk ? "Connected" : "Offline"}
      </span>
      {data?.dry_run ? (
        <span className="ml-1 rounded bg-warning/15 px-1.5 py-0.5 font-medium text-warning">
          DRY RUN
        </span>
      ) : null}
    </div>
  );
}
