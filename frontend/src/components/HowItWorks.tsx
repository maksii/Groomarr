import { ChevronRight, Lightbulb } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

const KEY = "groomarr-howitworks-open";

export function HowItWorks() {
  const [open, setOpen] = useState(() => localStorage.getItem(KEY) !== "0");
  function toggle() {
    setOpen((o) => {
      try {
        localStorage.setItem(KEY, o ? "0" : "1");
      } catch {
        /* ignore */
      }
      return !o;
    });
  }
  return (
    <div className="rounded-lg border border-primary/25 bg-primary/5">
      <button
        type="button"
        onClick={toggle}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
      >
        <Lightbulb className="h-4 w-4 text-primary" />
        <span className="text-sm font-medium">How rules work</span>
        <ChevronRight
          className={cn("ml-auto h-4 w-4 text-muted-foreground transition-transform", open && "rotate-90")}
        />
      </button>
      {open ? (
        <div className="space-y-2.5 border-t border-primary/15 px-4 py-3 text-sm text-muted-foreground">
          <p>
            For each grabbed release, Groomarr picks <strong>one</strong> rule set: the first{" "}
            <strong>tracker</strong> whose match patterns fit the release&apos;s indexer wins and{" "}
            <em>fully replaces</em> the global rules; if none match, the <strong>global</strong>{" "}
            rules apply.
          </p>
          <p>
            <strong className="text-foreground">Triggers</strong> decide <em>whether</em> to rename
            (filters on indexer, quality, custom formats, score, etc.).{" "}
            <strong className="text-foreground">Rename</strong> rules decide <em>how</em> the title
            becomes the new torrent / folder / file name.
          </p>
          <p>
            Edit anything and the <strong className="text-foreground">live preview</strong> on the
            right shows, for your test release, exactly which filters pass, why it&apos;s processed or
            skipped, and the resulting names — all before you save.
          </p>
          <p className="text-xs">
            Pattern types: <code className="font-mono">exact</code> (case-insensitive),{" "}
            <code className="font-mono">wildcard*</code>, or <code className="font-mono">/regex/</code>{" "}
            for indexer matches; filter lists use case-insensitive regular expressions.
          </p>
        </div>
      ) : null}
    </div>
  );
}
