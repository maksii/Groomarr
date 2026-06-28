import { HelpCircle } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { FIELD_HELP, type HelpEntry } from "@/lib/fieldHelp";

/** An (i) help icon that opens a popover with guidance for a field. */
export function FieldInfo({ field, entry }: { field?: string; entry?: HelpEntry }) {
  const help = entry ?? (field ? FIELD_HELP[field] : undefined);
  if (!help) return null;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`Help: ${help.title}`}
          className="inline-flex text-muted-foreground/70 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-full"
        >
          <HelpCircle className="h-3.5 w-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent>
        <div className="space-y-1.5">
          <div className="text-sm font-semibold">{help.title}</div>
          <p className="text-xs leading-relaxed text-muted-foreground">{help.body}</p>
          {help.example ? (
            <div className="rounded bg-muted px-2 py-1 font-mono text-[11px] text-foreground">
              {help.example}
            </div>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}
