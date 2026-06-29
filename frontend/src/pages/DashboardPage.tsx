import {
  Activity,
  Ban,
  CheckCircle2,
  Database,
  Film,
  type LucideIcon,
  RotateCcw,
  Tv,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/PageHeader";
import { OperationsLog } from "@/components/dashboard/OperationsLog";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { useOperationStats, useStatus } from "@/hooks/queries";
import { cn } from "@/lib/utils";

type ConnState = "connected" | "disconnected" | "not configured" | "unknown";

function ConnDot({ icon: Icon, name, state }: { icon: LucideIcon; name: string; state: ConnState }) {
  const dot =
    state === "connected"
      ? "bg-success"
      : state === "disconnected"
        ? "bg-destructive"
        : "bg-muted-foreground/40";
  return (
    <div className="flex items-center gap-2">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <span className="text-sm">{name}</span>
      <span className={cn("inline-block h-2 w-2 rounded-full", dot)} title={state} />
    </div>
  );
}

function StatCard({
  label,
  value,
  icon: Icon,
  iconClass,
  active,
  onClick,
}: {
  label: string;
  value: number | string;
  icon: LucideIcon;
  iconClass: string;
  active?: boolean;
  onClick?: () => void;
}) {
  const Comp = onClick ? "button" : "div";
  return (
    <Comp
      type={onClick ? "button" : undefined}
      onClick={onClick}
      className={cn(
        "flex items-center gap-3 rounded-lg border bg-card p-3 text-left shadow-sm transition-colors",
        onClick && "hover:border-primary/40 hover:bg-muted/40",
        active ? "border-primary ring-1 ring-primary/30" : "border-border",
      )}
    >
      <div className={cn("flex h-9 w-9 items-center justify-center rounded-md bg-muted", iconClass)}>
        <Icon className="h-4 w-4" />
      </div>
      <div className="min-w-0">
        <div className="text-lg font-semibold leading-none">{value}</div>
        <div className="mt-1 truncate text-xs text-muted-foreground">{label}</div>
      </div>
    </Comp>
  );
}

export function DashboardPage() {
  const status = useStatus();
  const stats = useOperationStats();
  const [statusFilter, setStatusFilter] = useState("");

  const s = status.data;
  const k = stats.data;

  const qbit: ConnState = s
    ? s.qbittorrent === "connected"
      ? "connected"
      : "disconnected"
    : "unknown";
  const radarr: ConnState = s?.radarr ? (s.radarr as ConnState) : "not configured";
  const sonarr: ConnState = s?.sonarr ? (s.sonarr as ConnState) : "not configured";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        description="Live status and a searchable log of every webhook, decision, and rename — open any entry for the full before/after and to roll it back."
      />

      {/* System overview */}
      <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <ConnDot icon={Database} name="qBittorrent" state={qbit} />
          <ConnDot icon={Film} name="Radarr" state={radarr} />
          <ConnDot icon={Tv} name="Sonarr" state={sonarr} />
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {s?.dry_run ? <Badge tone="warning">DRY RUN</Badge> : null}
          {s?.readonly ? <Badge tone="muted">config read-only</Badge> : null}
          <span>v{s?.version ?? "—"}</span>
          <Link to="/status" className="font-medium text-primary hover:underline">
            Details →
          </Link>
        </div>
      </Card>

      {/* KPI cards (click to filter the log by status) */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <StatCard
          label="Operations"
          value={k?.total ?? (stats.isLoading ? "…" : 0)}
          icon={Activity}
          iconClass="text-foreground"
          active={statusFilter === ""}
          onClick={() => setStatusFilter("")}
        />
        <StatCard
          label="Renamed"
          value={k?.renamed ?? 0}
          icon={CheckCircle2}
          iconClass="text-success"
          active={statusFilter === "renamed"}
          onClick={() => setStatusFilter("renamed")}
        />
        <StatCard
          label="Skipped"
          value={k?.skipped ?? 0}
          icon={Ban}
          iconClass="text-warning"
          active={statusFilter === "skipped"}
          onClick={() => setStatusFilter("skipped")}
        />
        <StatCard
          label="Failed"
          value={k?.failed ?? 0}
          icon={XCircle}
          iconClass="text-destructive"
          active={statusFilter === "failed"}
          onClick={() => setStatusFilter("failed")}
        />
        <StatCard
          label="Rolled back"
          value={k?.rolled_back ?? 0}
          icon={RotateCcw}
          iconClass="text-muted-foreground"
          active={statusFilter === "rolled_back"}
          onClick={() => setStatusFilter("rolled_back")}
        />
        <StatCard
          label="Last 24h"
          value={k?.last_24h ?? 0}
          icon={Activity}
          iconClass="text-primary"
        />
      </div>

      <OperationsLog status={statusFilter} onStatusChange={setStatusFilter} />
    </div>
  );
}
