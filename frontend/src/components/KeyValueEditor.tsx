import { ArrowRight, Plus, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useValidatedPatterns } from "@/hooks/useValidatedPatterns";
import { cn } from "@/lib/utils";
import { stableStringify } from "@/lib/utils";
import { Button } from "./ui/button";
import { Input } from "./ui/input";

interface Props {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
  disabled?: boolean;
}

/** Editor for regex → replacement maps.
 *
 * The backing value is a plain object, which cannot hold duplicate keys. To avoid
 * silently discarding a row when two keys momentarily match, we keep an ordered
 * list of rows as the editing source of truth and only collapse to an object
 * (last-wins) when emitting. Duplicate keys are surfaced as invalid so the user
 * can fix them rather than losing data.
 */
export function KeyValueEditor({ value, onChange, disabled }: Props) {
  const [rows, setRows] = useState<[string, string][]>(() => Object.entries(value));

  // Re-sync from the parent only when the external value genuinely differs from
  // what our rows represent (e.g. on initial load or Revert) — not for changes we
  // ourselves emitted, so transient duplicate keys survive while editing.
  useEffect(() => {
    if (stableStringify(Object.fromEntries(rows)) !== stableStringify(value)) {
      setRows(Object.entries(value));
    }
    // `rows` is intentionally excluded — we only re-sync on external value changes.
  }, [value]);

  const keys = rows.map(([k]) => k);
  const validity = useValidatedPatterns(keys, "regex");
  const keyCounts = keys.reduce<Record<string, number>>((acc, k) => {
    acc[k] = (acc[k] ?? 0) + 1;
    return acc;
  }, {});

  function update(next: [string, string][]) {
    setRows(next);
    onChange(Object.fromEntries(next));
  }

  const hasEmptyKey = keys.includes("");

  return (
    <div className="space-y-2">
      {rows.map(([k, v], i) => {
        const duplicate = k !== "" && keyCounts[k] > 1;
        const badRegex = k !== "" && validity[k]?.valid === false;
        const invalid = duplicate || badRegex;
        const title = duplicate
          ? "Duplicate pattern — only the last row with this pattern is kept"
          : badRegex
            ? (validity[k]?.error ?? "")
            : undefined;
        return (
          <div key={i} className="flex items-center gap-2">
            <Input
              value={k}
              invalid={invalid}
              disabled={disabled}
              placeholder="regex pattern"
              title={title}
              onChange={(e) => {
                const next = [...rows] as [string, string][];
                next[i] = [e.target.value, v];
                update(next);
              }}
              className="font-mono text-xs"
            />
            <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" />
            <Input
              value={v}
              disabled={disabled}
              placeholder="replacement"
              onChange={(e) => {
                const next = [...rows] as [string, string][];
                next[i] = [k, e.target.value];
                update(next);
              }}
              className="font-mono text-xs"
            />
            <Button
              variant="ghost"
              size="icon"
              disabled={disabled}
              onClick={() => update(rows.filter((_, idx) => idx !== i) as [string, string][])}
              aria-label="Remove replacement"
              className="shrink-0 text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        );
      })}
      <Button
        variant="outline"
        size="sm"
        disabled={disabled || hasEmptyKey}
        onClick={() => update([...rows, ["", ""]] as [string, string][])}
        className={cn(rows.length === 0 && "w-full")}
      >
        <Plus className="h-3.5 w-3.5" />
        Add replacement
      </Button>
    </div>
  );
}
