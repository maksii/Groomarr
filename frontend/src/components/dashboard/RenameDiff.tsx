import { ArrowDown } from "lucide-react";
import { cn } from "@/lib/utils";

/** A clear before → after visualization for a single rename (torrent / folder / file).
 *
 *  Names are long, so the pair is stacked vertically with a connector arrow:
 *  the original is muted, the result is emphasized with a success tint. When the
 *  two are equal (nothing changed) it collapses to a single muted line. */
export function RenameDiff({
  from,
  to,
  fromLabel = "From",
  toLabel = "To",
  className,
}: {
  from: string;
  to: string;
  fromLabel?: string;
  toLabel?: string;
  className?: string;
}) {
  const changed = from !== to;

  if (!changed) {
    return (
      <div
        className={cn(
          "rounded-md border border-border bg-muted/30 px-3 py-2 font-mono text-xs break-all text-muted-foreground",
          className,
        )}
      >
        {from || "—"} <span className="not-italic text-muted-foreground/70">(unchanged)</span>
      </div>
    );
  }

  return (
    <div className={cn("overflow-hidden rounded-md border border-border", className)}>
      <Line label={fromLabel} value={from} tone="from" />
      <div className="relative h-0 border-t border-dashed border-border">
        <span className="absolute left-3 -top-2 flex h-4 w-4 items-center justify-center rounded-full border border-border bg-background text-muted-foreground">
          <ArrowDown className="h-3 w-3" />
        </span>
      </div>
      <Line label={toLabel} value={to} tone="to" />
    </div>
  );
}

function Line({ label, value, tone }: { label: string; value: string; tone: "from" | "to" }) {
  return (
    <div
      className={cn(
        "flex items-start gap-2.5 px-3 py-2",
        tone === "from" ? "bg-muted/30" : "bg-success/10",
      )}
    >
      <span
        className={cn(
          "w-9 shrink-0 pt-0.5 text-[10px] font-medium uppercase tracking-wide",
          tone === "from" ? "text-muted-foreground" : "text-success",
        )}
      >
        {label}
      </span>
      <span
        className={cn(
          "min-w-0 break-all font-mono text-xs",
          tone === "from" ? "text-muted-foreground" : "font-medium text-foreground",
        )}
      >
        {value || "—"}
      </span>
    </div>
  );
}
