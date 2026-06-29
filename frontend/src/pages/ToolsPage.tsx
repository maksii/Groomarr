import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Eye, Pencil, Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api } from "@/lib/api";
import type { RenameMode } from "@/lib/types";

const MODES: { value: RenameMode; label: string }[] = [
  { value: "torrent_only", label: "Torrent only" },
  { value: "torrent_and_folder", label: "Torrent + folder" },
  { value: "torrent_folder_files", label: "Torrent + folder + files" },
  { value: "folder_only", label: "Folder only" },
  { value: "files_only", label: "Files only" },
];

const DEFAULT_MODE: RenameMode = "torrent_and_folder";

/** The rename target shared across the Preview and Manual-rename tabs so that
 *  switching tabs (or arriving from the dashboard) never loses the input. */
interface SharedProps {
  hash: string;
  setHash: (v: string) => void;
  name: string;
  setName: (v: string) => void;
  mode: RenameMode;
  setMode: (v: RenameMode) => void;
}

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "success" || status === "ok" || status === "found"
      ? "success"
      : status === "not_found"
        ? "warning"
        : "destructive";
  return <Badge tone={tone}>{status}</Badge>;
}

/** Hash / name / mode inputs, bound to the shared state. */
function RenameFields({ hash, setHash, name, setName, mode, setMode }: SharedProps) {
  return (
    <>
      <div className="space-y-1.5">
        <Label>Torrent hash</Label>
        <Input
          value={hash}
          onChange={(e) => setHash(e.target.value)}
          className="font-mono text-xs"
        />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label>New name</Label>
          <Input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label>Mode</Label>
          <Select
            value={mode}
            onChange={(e) => setMode(e.target.value as RenameMode)}
            options={MODES}
          />
        </div>
      </div>
    </>
  );
}

function PreviewTool(shared: SharedProps) {
  const { hash, name, mode } = shared;
  const m = useMutation({ mutationFn: () => api.previewRename(hash.trim(), name, mode) });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Eye className="h-4 w-4" /> Preview rename
        </CardTitle>
        <CardDescription>
          Read-only. Shows exactly what a rename would change for a torrent — nothing is modified.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <RenameFields {...shared} />
        <Button
          variant="primary"
          disabled={!hash.trim() || !name || m.isPending}
          onClick={() => m.mutate()}
        >
          {m.isPending ? <Spinner className="text-primary-foreground" /> : <Eye className="h-4 w-4" />}
          Preview
        </Button>

        {m.isError ? (
          <Banner tone="destructive">{String((m.error as Error).message)}</Banner>
        ) : null}
        {m.data ? (
          <div className="space-y-3 rounded-md border border-border p-3 text-sm">
            <div className="flex items-center gap-2">
              <StatusBadge status={m.data.status} />
              {m.data.reason ? <span className="text-muted-foreground">{m.data.reason}</span> : null}
            </div>
            {m.data.status === "ok" ? (
              <>
                <div className="grid gap-1 font-mono text-xs">
                  <div>
                    <span className="text-muted-foreground">Torrent: </span>
                    {m.data.current_torrent_name} {m.data.torrent_will_change ? "→ " : "(unchanged) "}
                    {m.data.torrent_will_change ? m.data.new_torrent_name : null}
                  </div>
                  <div>
                    <span className="text-muted-foreground">Files: </span>
                    {m.data.files_will_change}/{m.data.total_files} would change
                  </div>
                </div>
                {m.data.file_renames.length > 0 ? (
                  <ul className="max-h-60 space-y-1 overflow-y-auto scrollbar-thin font-mono text-xs">
                    {m.data.file_renames.map((f, i) => (
                      // biome-ignore lint: positional list
                      <li key={i} className={f.will_change ? "" : "text-muted-foreground"}>
                        {f.old_path} → {f.new_path}
                      </li>
                    ))}
                  </ul>
                ) : null}
                {m.data.warnings.map((w, i) => (
                  // biome-ignore lint: positional list
                  <p key={i} className="text-xs text-warning">
                    {w}
                  </p>
                ))}
              </>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function RenameTool(shared: SharedProps) {
  const { hash, name, mode } = shared;
  const m = useMutation({
    mutationFn: () => api.manualRename(hash.trim(), name, mode),
    onSuccess: (d) =>
      d.status === "success"
        ? toast.success("Rename applied", { description: d.reason ?? undefined })
        : toast.error("Rename failed", { description: d.reason ?? undefined }),
    onError: (e) => toast.error("Rename failed", { description: String((e as Error).message) }),
  });

  function run() {
    if (!window.confirm("Apply this rename to the torrent in qBittorrent now?")) return;
    m.mutate();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Pencil className="h-4 w-4" /> Manual rename
        </CardTitle>
        <CardDescription>Applies a rename directly in qBittorrent.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Banner tone="warning" icon={<AlertTriangle className="h-4 w-4" />}>
          This modifies the torrent immediately. Use Preview first to confirm the result.
        </Banner>
        <RenameFields {...shared} />
        <Button variant="destructive" disabled={!hash.trim() || !name || m.isPending} onClick={run}>
          {m.isPending ? <Spinner /> : <Pencil className="h-4 w-4" />}
          Apply rename
        </Button>
        {m.data ? (
          <div className="flex items-center gap-2 text-sm">
            <StatusBadge status={m.data.status} />
            {m.data.reason ? <span className="text-muted-foreground">{m.data.reason}</span> : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function FindTool({ onUseHash }: { onUseHash: (hash: string) => void }) {
  const [id, setId] = useState("");
  const m = useMutation({ mutationFn: () => api.findTorrent(id.trim()) });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Search className="h-4 w-4" /> Find torrent by tracker ID
        </CardTitle>
        <CardDescription>
          Look up a torrent hash by the tracker ID in its comment (URL or number).
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1.5">
          <Label>Tracker ID or URL</Label>
          <Input
            value={id}
            placeholder="342558 or https://tracker/torrents/342558"
            onChange={(e) => setId(e.target.value)}
          />
        </div>
        <Button variant="primary" disabled={!id.trim() || m.isPending} onClick={() => m.mutate()}>
          {m.isPending ? <Spinner className="text-primary-foreground" /> : <Search className="h-4 w-4" />}
          Find
        </Button>
        {m.isError ? (
          <Banner tone="destructive">{String((m.error as Error).message)}</Banner>
        ) : null}
        {m.data ? (
          <div className="space-y-2 text-sm">
            <div className="flex items-center gap-2">
              <StatusBadge status={m.data.status} />
              {m.data.reason ? <span className="text-muted-foreground">{m.data.reason}</span> : null}
            </div>
            {m.data.torrent_hash ? (
              <div className="flex flex-wrap items-center gap-2">
                <div className="break-all font-mono text-xs">{m.data.torrent_hash}</div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => onUseHash(m.data.torrent_hash as string)}
                >
                  <ArrowRight className="h-4 w-4" /> Use in Preview
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

type ToolTab = "preview" | "rename" | "find";

export function ToolsPage() {
  const [params, setParams] = useSearchParams();

  // Hydrate the shared rename target from the URL so the dashboard can deep-link
  // into Tools with a hash/name pre-filled, and so a page refresh keeps the input.
  const [hash, setHash] = useState(() => params.get("hash") ?? "");
  const [name, setName] = useState(() => params.get("name") ?? "");
  const [mode, setMode] = useState<RenameMode>(() => {
    const m = params.get("mode");
    return MODES.some((x) => x.value === m) ? (m as RenameMode) : DEFAULT_MODE;
  });
  const [tab, setTab] = useState<ToolTab>(() => {
    const t = params.get("tab");
    return t === "rename" || t === "find" ? t : "preview";
  });

  // Mirror the shared state back into the URL (replace, so it doesn't spam
  // history). This keeps Preview/Rename in sync and survives a manual refresh.
  // biome-ignore lint/correctness/useExhaustiveDependencies: setParams identity is unstable; derive from state only
  useEffect(() => {
    const next = new URLSearchParams();
    if (hash) next.set("hash", hash);
    if (name) next.set("name", name);
    if (mode !== DEFAULT_MODE) next.set("mode", mode);
    if (tab !== "preview") next.set("tab", tab);
    setParams(next, { replace: true });
  }, [hash, name, mode, tab]);

  const shared: SharedProps = { hash, setHash, name, setName, mode, setMode };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Tools"
        description="One-off torrent operations: preview a rename, apply one manually, or find a torrent by its tracker ID."
      />
      <Tabs value={tab} onValueChange={(v) => setTab(v as ToolTab)}>
        <TabsList>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="rename">Manual rename</TabsTrigger>
          <TabsTrigger value="find">Find by ID</TabsTrigger>
        </TabsList>
        <TabsContent value="preview" className="mt-4 focus-visible:outline-none">
          <PreviewTool {...shared} />
        </TabsContent>
        <TabsContent value="rename" className="mt-4 focus-visible:outline-none">
          <RenameTool {...shared} />
        </TabsContent>
        <TabsContent value="find" className="mt-4 focus-visible:outline-none">
          <FindTool
            onUseHash={(h) => {
              setHash(h);
              setTab("preview");
            }}
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}
