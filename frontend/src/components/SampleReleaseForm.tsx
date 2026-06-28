import { useMutation } from "@tanstack/react-query";
import { ChevronRight, DownloadCloud } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import type { SimulateRelease } from "@/lib/types";
import { cn, deriveSampleFiles } from "@/lib/utils";
import { ChipListEditor } from "./ChipListEditor";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Label } from "./ui/label";

interface Props {
  value: SimulateRelease;
  onChange: (next: SimulateRelease) => void;
}

export function SampleReleaseForm({ value, onChange }: Props) {
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [loadOpen, setLoadOpen] = useState(false);
  const [query, setQuery] = useState("");

  const set = <K extends keyof SimulateRelease>(key: K, v: SimulateRelease[K]) =>
    onChange({ ...value, [key]: v });

  const load = useMutation({
    mutationFn: () => api.torrentSample(query.trim()),
    onSuccess: (d) => {
      if (d.status === "ok") {
        onChange({ ...value, release_title: d.title ?? "", files: d.files });
        setLoadOpen(false);
        toast.success("Loaded torrent into the sample release");
      } else {
        toast.error(d.reason ?? "Torrent not found");
      }
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });

  return (
    <div className="space-y-3 rounded-lg border border-border bg-surface/60 p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Test release
        </span>
        <button
          type="button"
          onClick={() => setLoadOpen((o) => !o)}
          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
        >
          <DownloadCloud className="h-3.5 w-3.5" /> Load from torrent
        </button>
      </div>

      {loadOpen ? (
        <div className="flex gap-2">
          <Input
            value={query}
            placeholder="torrent hash or tracker ID/URL"
            className="h-8 text-xs"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && query.trim() && load.mutate()}
          />
          <Button size="sm" disabled={!query.trim() || load.isPending} onClick={() => load.mutate()}>
            Load
          </Button>
        </div>
      ) : null}

      <div className="space-y-1">
        <Label className="text-xs">Release title</Label>
        <Input
          value={value.release_title}
          placeholder="Movie.2024.1080p.BluRay.x264-GROUP"
          className="h-8 font-mono text-xs"
          onChange={(e) => set("release_title", e.target.value)}
        />
      </div>

      <button
        type="button"
        onClick={() => setDetailsOpen((o) => !o)}
        className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        <ChevronRight className={cn("h-3.5 w-3.5 transition-transform", detailsOpen && "rotate-90")} />
        Release details
      </button>

      {detailsOpen ? (
        <div className="space-y-2.5">
          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1">
              <Label className="text-xs">Indexer</Label>
              <Input
                value={value.indexer}
                placeholder="e.g. Nyaa"
                className="h-8 text-xs"
                onChange={(e) => set("indexer", e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Quality</Label>
              <Input
                value={value.quality}
                placeholder="Bluray-1080p"
                className="h-8 text-xs"
                onChange={(e) => set("quality", e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Release group</Label>
              <Input
                value={value.release_group}
                className="h-8 text-xs"
                onChange={(e) => set("release_group", e.target.value)}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Download client</Label>
              <Input
                value={value.download_client}
                className="h-8 text-xs"
                onChange={(e) => set("download_client", e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Custom formats</Label>
            <ChipListEditor
              value={value.custom_formats}
              onChange={(v) => set("custom_formats", v)}
              placeholder="e.g. x265"
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Custom format score</Label>
            <Input
              type="number"
              step={1}
              className="h-8 max-w-[10rem] text-xs"
              value={value.custom_format_score ?? ""}
              placeholder="none"
              onChange={(e) => {
                const n = Number.parseInt(e.target.value, 10);
                set("custom_format_score", Number.isNaN(n) ? null : n);
              }}
            />
          </div>
          <div className="space-y-1">
            <div className="flex items-center justify-between">
              <Label className="text-xs">Sample files</Label>
              <button
                type="button"
                className="text-xs text-primary hover:underline"
                onClick={() => set("files", deriveSampleFiles(value.release_title))}
              >
                Regenerate from title
              </button>
            </div>
            <ChipListEditor
              value={value.files}
              onChange={(v) => set("files", v)}
              mono
              placeholder="folder/file.mkv"
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
