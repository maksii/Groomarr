import { ArrowDown, ArrowUp, ChevronRight, Trash2 } from "lucide-react";
import { useState } from "react";
import type { TrackerConfig } from "@/lib/types";
import { cn } from "@/lib/utils";
import { ChipListEditor } from "./ChipListEditor";
import { RuleSetEditor } from "./RuleSetEditor";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Card } from "./ui/card";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Tooltip } from "./ui/tooltip";

interface Props {
  tracker: TrackerConfig;
  index: number;
  total: number;
  onChange: (next: TrackerConfig) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  disabled?: boolean;
}

export function TrackerCard({ tracker, index, total, onChange, onRemove, onMove, disabled }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 p-3">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex min-w-0 flex-1 items-center gap-2 text-left"
          aria-expanded={open}
        >
          <ChevronRight
            className={cn("h-4 w-4 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
          />
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">#{index + 1}</span>
          <span className="truncate font-medium">{tracker.name || "Unnamed tracker"}</span>
          <Badge tone="muted">{tracker.match.length} match</Badge>
          {tracker.match.length === 0 ? <Badge tone="warning">no match — ignored</Badge> : null}
        </button>
        <div className="flex shrink-0 items-center gap-1">
          <Tooltip content="Move up">
            <Button
              variant="ghost"
              size="icon"
              disabled={disabled || index === 0}
              onClick={() => onMove(-1)}
              aria-label="Move tracker up"
            >
              <ArrowUp className="h-4 w-4" />
            </Button>
          </Tooltip>
          <Tooltip content="Move down">
            <Button
              variant="ghost"
              size="icon"
              disabled={disabled || index === total - 1}
              onClick={() => onMove(1)}
              aria-label="Move tracker down"
            >
              <ArrowDown className="h-4 w-4" />
            </Button>
          </Tooltip>
          <Tooltip content="Remove tracker">
            <Button
              variant="ghost"
              size="icon"
              disabled={disabled}
              onClick={onRemove}
              aria-label="Remove tracker"
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </Tooltip>
        </div>
      </div>

      {open ? (
        <div className="space-y-5 border-t border-border p-5">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Name</Label>
              <Input
                value={tracker.name}
                disabled={disabled}
                placeholder="e.g. anime-tracker"
                onChange={(e) => onChange({ ...tracker, name: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Match patterns</Label>
              <p className="text-xs text-muted-foreground">
                Exact, <code>wildcard*</code>, or <code>/regex/</code>. First matching tracker wins.
              </p>
              <ChipListEditor
                value={tracker.match}
                onChange={(v) => onChange({ ...tracker, match: v })}
                validateKind="match"
                mono
                disabled={disabled}
                placeholder="e.g. Nyaa*"
              />
            </div>
          </div>
          <div className="border-t border-border pt-4">
            <RuleSetEditor
              value={tracker.rules}
              onChange={(rules) => onChange({ ...tracker, rules })}
              disabled={disabled}
              idPrefix={`tracker-${index}`}
            />
          </div>
        </div>
      ) : null}
    </Card>
  );
}
