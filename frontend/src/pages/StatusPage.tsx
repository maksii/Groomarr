import { Activity, Database, Film, ShieldAlert, Tv } from "lucide-react";
import type { ReactNode } from "react";
import { PageHeader } from "@/components/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { useSettings, useStatus } from "@/hooks/queries";
import { cn } from "@/lib/utils";

function ConnectionCard({
  icon,
  name,
  state,
}: {
  icon: ReactNode;
  name: string;
  state: "connected" | "disconnected" | "not configured" | "unknown";
}) {
  const tone =
    state === "connected" ? "success" : state === "disconnected" ? "destructive" : "muted";
  const dot =
    state === "connected"
      ? "bg-success"
      : state === "disconnected"
        ? "bg-destructive"
        : "bg-muted-foreground/50";
  return (
    <Card>
      <CardContent className="flex items-center justify-between p-4">
        <div className="flex items-center gap-3">
          <div className="text-muted-foreground">{icon}</div>
          <div>
            <div className="text-sm font-medium">{name}</div>
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className={cn("inline-block h-2 w-2 rounded-full", dot)} />
              {state}
            </div>
          </div>
        </div>
        <Badge tone={tone}>{state === "connected" ? "OK" : state}</Badge>
      </CardContent>
    </Card>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-border py-2.5 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-right text-sm font-medium">{children}</span>
    </div>
  );
}

export function StatusPage() {
  const status = useStatus();
  const settings = useSettings();

  const s = status.data;
  const cfg = settings.data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Status"
        description="Connectivity and the deployment settings Groomarr is running with."
      />

      <Banner tone="warning" icon={<ShieldAlert className="h-5 w-5" />} title="No authentication">
        Groomarr has no built-in login. Restrict access at the network layer or place it behind an
        authenticating reverse proxy.
      </Banner>

      {status.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner /> Checking connections…
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-3">
          <ConnectionCard
            icon={<Database className="h-5 w-5" />}
            name="qBittorrent"
            state={s ? (s.qbittorrent === "connected" ? "connected" : "disconnected") : "unknown"}
          />
          <ConnectionCard
            icon={<Film className="h-5 w-5" />}
            name="Radarr"
            state={s?.radarr ? (s.radarr as "connected" | "disconnected") : "not configured"}
          />
          <ConnectionCard
            icon={<Tv className="h-5 w-5" />}
            name="Sonarr"
            state={s?.sonarr ? (s.sonarr as "connected" | "disconnected") : "not configured"}
          />
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="h-4 w-4" /> Service
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <Row label="Version">{s?.version ?? "—"}</Row>
            <Row label="Overall status">
              <Badge tone={s?.status === "ok" ? "success" : "warning"}>{s?.status ?? "—"}</Badge>
            </Row>
            <Row label="Dry run">
              {s?.dry_run ? <Badge tone="warning">enabled</Badge> : <span>off</span>}
            </Row>
            <Row label="Score validation">{s?.score_validation ? "enabled" : "off"}</Row>
            <Row label="Config file">
              {s?.config_found ? (
                <Badge tone="success">loaded</Badge>
              ) : (
                <Badge tone="muted">missing</Badge>
              )}
            </Row>
            <Row label="Read-only mode">{s?.readonly ? "yes" : "no"}</Row>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Runtime settings</CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <Row label="Rename mode">
              <span className="font-mono text-xs">{cfg?.rename_mode ?? "—"}</span>
            </Row>
            <Row label="qBittorrent URL">
              <span className="font-mono text-xs">{cfg?.qbittorrent_url ?? "—"}</span>
            </Row>
            <Row label="Rules file">
              <span className="font-mono text-xs">{cfg?.rules_file ?? "—"}</span>
            </Row>
            <Row label="Log level">{cfg?.log_level ?? "—"}</Row>
            <Row label="Retries / delay">
              {cfg ? `${cfg.max_retries} / ${cfg.retry_delay}s` : "—"}
            </Row>
            <Row label="Sonarr / Radarr API">
              {cfg ? (
                <span className="flex items-center justify-end gap-1.5">
                  <Badge tone={cfg.sonarr_configured ? "success" : "muted"}>
                    Sonarr {cfg.sonarr_configured ? "✓" : "—"}
                  </Badge>
                  <Badge tone={cfg.radarr_configured ? "success" : "muted"}>
                    Radarr {cfg.radarr_configured ? "✓" : "—"}
                  </Badge>
                </span>
              ) : (
                "—"
              )}
            </Row>
          </CardContent>
        </Card>
      </div>
      <p className="text-xs text-muted-foreground">
        Runtime settings are configured via environment variables (docker-compose) and are not
        editable from the UI.
      </p>
    </div>
  );
}
