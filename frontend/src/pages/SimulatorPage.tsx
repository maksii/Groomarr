import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Ban, CheckCircle2, FlaskConical, Target } from "lucide-react";
import { useState } from "react";
import { ChipListEditor } from "@/components/ChipListEditor";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Spinner } from "@/components/ui/spinner";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { api } from "@/lib/api";
import type { SimulateRelease } from "@/lib/types";

const EXAMPLE: SimulateRelease = {
  release_title: "Some.Movie.2024.1080p.BluRay.x264-GROUP",
  indexer: "Nyaa",
  quality: "Bluray-1080p",
  release_group: "GROUP",
  custom_formats: [],
  custom_format_score: null,
  download_client: "",
};

function LabeledInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label>{label}</Label>
      <Input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

export function SimulatorPage() {
  const [release, setRelease] = useState<SimulateRelease>(EXAMPLE);
  const debounced = useDebouncedValue(release, 350);

  const set = <K extends keyof SimulateRelease>(key: K, val: SimulateRelease[K]) =>
    setRelease((r) => ({ ...r, [key]: val }));

  const { data: result, isFetching } = useQuery({
    queryKey: ["simulate", debounced],
    queryFn: () => api.simulate(debounced),
  });

  const errored = result?.status === "error";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Simulator"
        description="Paste a release and see, live, whether your saved rules would rename it — and what the result would be."
      />

      <Banner tone="info" icon={<FlaskConical className="h-5 w-5" />}>
        The simulator runs against your <strong>saved</strong> rules using the exact production
        engine. Save changes on the Rules page to test them here.
      </Banner>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* Input */}
        <Card>
          <CardHeader>
            <CardTitle>Sample release</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1.5">
              <Label>Release title</Label>
              <Input
                value={release.release_title}
                placeholder="Movie.2024.1080p.BluRay.x264-GROUP"
                onChange={(e) => set("release_title", e.target.value)}
                className="font-mono text-xs"
              />
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <LabeledInput
                label="Indexer"
                value={release.indexer}
                onChange={(v) => set("indexer", v)}
                placeholder="e.g. Nyaa"
              />
              <LabeledInput
                label="Quality"
                value={release.quality}
                onChange={(v) => set("quality", v)}
                placeholder="e.g. Bluray-1080p"
              />
              <LabeledInput
                label="Release group"
                value={release.release_group}
                onChange={(v) => set("release_group", v)}
              />
              <LabeledInput
                label="Download client"
                value={release.download_client}
                onChange={(v) => set("download_client", v)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Custom formats</Label>
              <ChipListEditor
                value={release.custom_formats}
                onChange={(v) => set("custom_formats", v)}
                placeholder="e.g. x265"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Custom format score</Label>
              <Input
                type="number"
                step={1}
                className="max-w-[12rem]"
                value={release.custom_format_score ?? ""}
                placeholder="none"
                onChange={(e) => {
                  const n = Number.parseInt(e.target.value, 10);
                  set("custom_format_score", Number.isNaN(n) ? null : n);
                }}
              />
            </div>
          </CardContent>
        </Card>

        {/* Result */}
        <Card className="lg:sticky lg:top-24 lg:self-start">
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Result</CardTitle>
            {isFetching ? <Spinner className="text-muted-foreground" /> : null}
          </CardHeader>
          <CardContent className="space-y-5">
            {!result ? (
              <p className="text-sm text-muted-foreground">Enter a release to see the result.</p>
            ) : errored ? (
              <Banner tone="destructive" title="Simulation error">
                {result.errors.join(" ")}
              </Banner>
            ) : (
              <>
                {/* Rule resolution */}
                <div className="flex items-center gap-2 text-sm">
                  <Target className="h-4 w-4 text-muted-foreground" />
                  <span className="text-muted-foreground">Matched:</span>
                  {result.used_global ? (
                    <Badge tone="muted">Global rules</Badge>
                  ) : (
                    <Badge tone="primary">tracker: {result.matched_tracker}</Badge>
                  )}
                </div>

                {/* Decision */}
                <div
                  className={
                    result.would_process
                      ? "flex items-start gap-2 rounded-md border border-success/30 bg-success/10 p-3"
                      : "flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-3"
                  }
                >
                  {result.would_process ? (
                    <CheckCircle2 className="mt-0.5 h-5 w-5 shrink-0 text-success" />
                  ) : (
                    <Ban className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
                  )}
                  <div className="text-sm">
                    <div className="font-medium">
                      {result.would_process ? "Would be renamed" : "Would be skipped"}
                    </div>
                    {!result.would_process && result.skip_reason ? (
                      <div className="text-muted-foreground">{result.skip_reason}</div>
                    ) : null}
                  </div>
                </div>

                {/* Title transformation */}
                <div className="space-y-2">
                  <Label>Title</Label>
                  <div className="space-y-1.5 rounded-md border border-border p-3 font-mono text-xs">
                    <div className="break-all text-muted-foreground line-through decoration-destructive/40">
                      {result.original_title || <span className="italic">（empty）</span>}
                    </div>
                    <div className="flex items-start gap-1.5 break-all text-foreground">
                      <ArrowRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" />
                      <span>{result.new_title || <span className="italic">（empty）</span>}</span>
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {!result.would_process
                      ? "Hypothetical — this release is skipped, so no rename actually occurs."
                      : result.changed
                        ? "The title is transformed."
                        : "No change to the title."}
                  </p>
                </div>

                {/* Steps */}
                {result.steps.length > 0 ? (
                  <div className="space-y-2">
                    <Label>Transformation steps</Label>
                    <ol className="space-y-1.5">
                      {result.steps.map((s, i) => (
                        <li
                          // biome-ignore lint: trace steps are positional
                          key={i}
                          className="rounded-md border border-border p-2 text-xs"
                        >
                          <div
                            className={
                              s.error ? "font-medium text-destructive" : "font-medium text-foreground"
                            }
                          >
                            {s.rule}
                          </div>
                          {s.error ? (
                            <div className="mt-0.5 text-destructive">{s.error}</div>
                          ) : (
                            <div className="mt-0.5 break-all font-mono text-muted-foreground">
                              {s.before} <span className="text-success">→</span> {s.after}
                            </div>
                          )}
                        </li>
                      ))}
                    </ol>
                  </div>
                ) : null}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
