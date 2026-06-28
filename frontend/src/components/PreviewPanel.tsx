import {
  AlertTriangle,
  ArrowRight,
  Ban,
  CheckCircle2,
  ChevronRight,
  Files,
  ListChecks,
  Target,
  XCircle,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import { SampleReleaseForm } from "@/components/SampleReleaseForm";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import type { SimulateRelease, SimulateResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  sample: SimulateRelease;
  onSampleChange: (next: SimulateRelease) => void;
  result?: SimulateResponse;
  isFetching: boolean;
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
        <ChevronRight className={cn("h-3.5 w-3.5 text-muted-foreground transition-transform", open && "rotate-90")} />
        <span className="text-muted-foreground">{icon}</span>
        <span className="text-sm font-medium">{title}</span>
        {count != null ? <span className="ml-auto">{count}</span> : null}
      </button>
      {open ? <div className="border-t border-border p-3">{children}</div> : null}
    </div>
  );
}

function Decision({ result }: { result: SimulateResponse }) {
  const pass = result.would_process;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm">
        <Target className="h-4 w-4 text-muted-foreground" />
        <span className="text-muted-foreground">Matched:</span>
        {result.used_global ? (
          <Badge tone="muted">Global rules</Badge>
        ) : (
          <Badge tone="primary">tracker: {result.matched_tracker}</Badge>
        )}
      </div>
      <div
        className={cn(
          "flex items-start gap-2 rounded-md border p-3",
          pass ? "border-success/30 bg-success/10" : "border-destructive/30 bg-destructive/10",
        )}
      >
        {pass ? (
          <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" />
        ) : (
          <Ban className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
        )}
        <div className="text-sm">
          <div className="font-medium">{pass ? "Will be renamed" : "Would be skipped"}</div>
          {!pass && result.skip_reason ? (
            <div className="text-muted-foreground">{result.skip_reason}</div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function TriggerBreakdown({ result }: { result: SimulateResponse }) {
  if (result.trigger_checks.length === 0) {
    return (
      <p className="rounded-md border border-border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        No trigger filters configured — every release handled by this ruleset is processed.
      </p>
    );
  }
  const passed = result.trigger_checks.filter((c) => c.passed).length;
  return (
    <Section
      icon={<ListChecks className="h-4 w-4" />}
      title="Trigger filters"
      count={
        <Badge tone={passed === result.trigger_checks.length ? "success" : "warning"}>
          {passed}/{result.trigger_checks.length} pass
        </Badge>
      }
    >
      <ul className="space-y-1.5">
        {result.trigger_checks.map((c, i) => (
          // biome-ignore lint: positional list
          <li key={i} className="flex items-start gap-2 text-xs">
            {c.passed ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
            ) : (
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
            )}
            <div className="min-w-0">
              <span className={cn("font-medium", c.blocking && "text-destructive")}>{c.label}</span>
              {c.blocking ? <span className="ml-1 text-destructive">(blocks)</span> : null}
              <div className="text-muted-foreground">
                <span className="font-mono">{c.tested}</span> — {c.detail}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}

function RenameResult({ result }: { result: SimulateResponse }) {
  const changed = result.changed;
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium">Resulting name</div>
      <div className="space-y-1.5 rounded-md border border-border p-3 font-mono text-xs">
        <div className="break-all text-muted-foreground line-through decoration-destructive/40">
          {result.original_title || <span className="italic">(empty)</span>}
        </div>
        <div className="flex items-start gap-1.5 break-all text-foreground">
          <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
          <span>{result.new_title || <span className="italic">(empty)</span>}</span>
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        {!result.would_process
          ? "Hypothetical — this release is skipped, so no rename actually occurs."
          : changed
            ? "This becomes the torrent name, folder name, and file base name."
            : "No change to the name."}
      </p>
      {result.steps.length > 0 ? (
        <Section
          icon={<ListChecks className="h-4 w-4" />}
          title="Transformation steps"
          count={<Badge tone="muted">{result.steps.length}</Badge>}
          defaultOpen={false}
        >
          <ol className="space-y-1.5">
            {result.steps.map((s, i) => (
              // biome-ignore lint: positional list
              <li key={i} className="text-xs">
                <div className={cn("font-medium", s.error ? "text-destructive" : "text-foreground")}>
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
    </div>
  );
}

function FileRenames({ result }: { result: SimulateResponse }) {
  if (result.file_renames.length === 0 && result.file_warnings.length === 0) return null;
  const changed = result.file_renames.filter((f) => f.will_change).length;
  return (
    <Section
      icon={<Files className="h-4 w-4" />}
      title="File renames"
      count={
        result.file_renames.length > 0 ? (
          <Badge tone="muted">
            {changed}/{result.file_renames.length} change
          </Badge>
        ) : null
      }
    >
      {result.file_warnings.map((w, i) => (
        // biome-ignore lint: positional list
        <div key={i} className="mb-2 flex items-start gap-1.5 text-xs text-warning">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>{w}</span>
        </div>
      ))}
      <ul className="space-y-1.5 font-mono text-[11px]">
        {result.file_renames.map((f, i) => (
          // biome-ignore lint: positional list
          <li key={i} className={cn("space-y-0.5", !f.will_change && "text-muted-foreground")}>
            <div className="break-all text-muted-foreground">{f.old_path}</div>
            <div className="flex items-start gap-1 break-all">
              <ArrowRight className="mt-0.5 h-3 w-3 shrink-0 text-success" />
              <span>{f.new_path}</span>
            </div>
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function PreviewPanel({ sample, onSampleChange, result, isFetching }: Props) {
  const errored = result?.status === "error";
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0 py-4">
        <CardTitle className="text-sm">Live preview</CardTitle>
        {isFetching ? <Spinner className="text-muted-foreground" /> : null}
      </CardHeader>
      <CardContent className="space-y-4">
        <SampleReleaseForm value={sample} onChange={onSampleChange} />

        {/* Reserve space and keep the previous result mounted while refreshing so
            the panel height never collapses between edits (no layout jump). */}
        <div
          className={cn(
            "min-h-[20rem] space-y-4 transition-opacity duration-150",
            isFetching && result ? "opacity-60" : "opacity-100",
          )}
        >
          {!result ? (
            <p className="text-sm text-muted-foreground">Enter a release to see the result.</p>
          ) : errored ? (
            <Banner tone="destructive" title="Simulation error">
              {result.errors.join(" ")}
            </Banner>
          ) : (
            <>
              <Decision result={result} />
              <TriggerBreakdown result={result} />
              <RenameResult result={result} />
              <FileRenames result={result} />
            </>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
