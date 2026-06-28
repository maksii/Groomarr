import { AlertCircle, X } from "lucide-react";
import { useState } from "react";
import { useValidatedPatterns } from "@/hooks/useValidatedPatterns";
import { cn } from "@/lib/utils";

interface Props {
  value: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  validateKind?: "regex" | "match";
  mono?: boolean;
  disabled?: boolean;
}

export function ChipListEditor({
  value,
  onChange,
  placeholder = "Add value, press Enter",
  validateKind,
  mono,
  disabled,
}: Props) {
  const [draft, setDraft] = useState("");
  const validity = useValidatedPatterns(value, validateKind);

  function commit(raw: string) {
    const v = raw.trim();
    if (!v) return;
    if (!value.includes(v)) onChange([...value, v]);
    setDraft("");
  }

  function removeAt(i: number) {
    onChange(value.filter((_, idx) => idx !== i));
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(draft);
    } else if (e.key === "Backspace" && draft === "" && value.length > 0) {
      removeAt(value.length - 1);
    }
  }

  return (
    <div
      className={cn(
        "flex min-h-9 flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2 py-1.5 focus-within:ring-2 focus-within:ring-ring",
        disabled && "opacity-60",
      )}
    >
      {value.map((item, i) => {
        const info = validity[item];
        const invalid = info && info.valid === false;
        return (
          <span
            key={`${item}-${i}`}
            title={invalid ? (info?.error ?? "Invalid pattern") : info?.interpreted ?? undefined}
            className={cn(
              "inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs",
              mono && "font-mono",
              invalid
                ? "bg-destructive/15 text-destructive ring-1 ring-destructive/40"
                : "bg-muted text-foreground",
            )}
          >
            {invalid ? <AlertCircle className="h-3 w-3 shrink-0" /> : null}
            <span className="max-w-[18rem] truncate">{item}</span>
            {!disabled && (
              <button
                type="button"
                onClick={() => removeAt(i)}
                className="rounded-sm text-current/70 hover:text-current"
                aria-label={`Remove ${item}`}
              >
                <X className="h-3 w-3" />
              </button>
            )}
          </span>
        );
      })}
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
        onBlur={() => commit(draft)}
        placeholder={value.length === 0 ? placeholder : ""}
        disabled={disabled}
        className={cn(
          "min-w-[8rem] flex-1 bg-transparent px-1 py-0.5 text-sm outline-none placeholder:text-muted-foreground/60",
          mono && "font-mono",
        )}
      />
    </div>
  );
}
