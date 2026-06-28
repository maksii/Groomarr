import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, Eye, Pencil, Search } from "lucide-react";
import { useState } from "react";
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

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "success" || status === "ok" || status === "found"
      ? "success"
      : status === "not_found"
        ? "warning"
        : "destructive";
  return <Badge tone={tone}>{status}</Badge>;
}

function PreviewTool() {
  const [hash, setHash] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<RenameMode>("torrent_and_folder");
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
        <div className="space-y-1.5">
          <Label>Torrent hash</Label>
          <Input value={hash} onChange={(e) => setHash(e.target.value)} className="font-mono text-xs" />
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

function RenameTool() {
  const [hash, setHash] = useState("");
  const [name, setName] = useState("");
  const [mode, setMode] = useState<RenameMode>("torrent_and_folder");
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
        <div className="space-y-1.5">
          <Label>Torrent hash</Label>
          <Input value={hash} onChange={(e) => setHash(e.target.value)} className="font-mono text-xs" />
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

function FindTool() {
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
          <div className="space-y-1 text-sm">
            <div className="flex items-center gap-2">
              <StatusBadge status={m.data.status} />
              {m.data.reason ? <span className="text-muted-foreground">{m.data.reason}</span> : null}
            </div>
            {m.data.torrent_hash ? (
              <div className="break-all font-mono text-xs">{m.data.torrent_hash}</div>
            ) : null}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function ToolsPage() {
  return (
    <div className="space-y-6">
      <PageHeader
        title="Tools"
        description="One-off torrent operations: preview a rename, apply one manually, or find a torrent by its tracker ID."
      />
      <Tabs defaultValue="preview">
        <TabsList>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="rename">Manual rename</TabsTrigger>
          <TabsTrigger value="find">Find by ID</TabsTrigger>
        </TabsList>
        <TabsContent value="preview" className="mt-4 focus-visible:outline-none">
          <PreviewTool />
        </TabsContent>
        <TabsContent value="rename" className="mt-4 focus-visible:outline-none">
          <RenameTool />
        </TabsContent>
        <TabsContent value="find" className="mt-4 focus-visible:outline-none">
          <FindTool />
        </TabsContent>
      </Tabs>
    </div>
  );
}
