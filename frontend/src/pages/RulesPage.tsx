import { AlertTriangle, FileWarning, Info, Plus, RefreshCw, Save, ShieldAlert, Undo2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/PageHeader";
import { RuleSetEditor } from "@/components/RuleSetEditor";
import { TrackerCard } from "@/components/TrackerCard";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { useConfig, useSaveConfig } from "@/hooks/queries";
import { ApiError } from "@/lib/api";
import { type RulesConfig, type TrackerConfig, emptyTracker } from "@/lib/types";
import { clone, stableStringify } from "@/lib/utils";

const DISMISS_KEY = "groomarr-auth-banner-dismissed";

export function RulesPage() {
  const { data, isLoading, isError, error, refetch, isFetching } = useConfig();
  const save = useSaveConfig();

  const [draft, setDraft] = useState<RulesConfig | null>(null);
  const savedRef = useRef<RulesConfig | null>(null);
  const savedStringRef = useRef<string>("");
  const [authDismissed, setAuthDismissed] = useState(
    () => localStorage.getItem(DISMISS_KEY) === "1",
  );

  // Adopt a server config as both the editable draft and the saved baseline.
  const adopt = useCallback((cfg: RulesConfig) => {
    setDraft(clone(cfg));
    savedRef.current = clone(cfg);
    savedStringRef.current = stableStringify(cfg);
  }, []);

  // Initialize the draft once the config first loads.
  useEffect(() => {
    if (data && draft === null) adopt(data.config);
  }, [data, draft, adopt]);

  const readonly = data?.meta.readonly ?? false;
  // One serialization of the draft per render, compared to a memoized baseline.
  const dirty = draft !== null && stableStringify(draft) !== savedStringRef.current;

  // Warn before leaving with unsaved changes.
  useEffect(() => {
    function handler(e: BeforeUnloadEvent) {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    }
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  function handleSave() {
    if (!draft) return;
    save.mutate(draft, {
      onSuccess: (res) => {
        adopt(res.config);
        toast.success("Rules saved & reloaded", {
          description: res.warnings.length
            ? res.warnings.join(" ")
            : "Your rename rules are now live.",
        });
      },
      onError: (e) => {
        const msgs = e instanceof ApiError ? e.validationMessages() : [];
        toast.error("Could not save rules", {
          description: msgs.length ? msgs.join("; ") : String((e as Error).message ?? e),
        });
      },
    });
  }

  function handleRefresh() {
    if (dirty && !window.confirm("Discard unsaved changes and reload from the server?")) return;
    void refetch().then((res) => {
      if (res.data) {
        adopt(res.data.config);
        toast.success("Reloaded from server");
      }
    });
  }

  function handleRevert() {
    if (savedRef.current) setDraft(clone(savedRef.current));
  }

  function updateTracker(i: number, next: TrackerConfig) {
    if (!draft) return;
    const trackers = [...draft.trackers];
    trackers[i] = next;
    setDraft({ ...draft, trackers });
  }

  function moveTracker(i: number, dir: -1 | 1) {
    if (!draft) return;
    const j = i + dir;
    if (j < 0 || j >= draft.trackers.length) return;
    const trackers = [...draft.trackers];
    [trackers[i], trackers[j]] = [trackers[j], trackers[i]];
    setDraft({ ...draft, trackers });
  }

  if (isLoading || draft === null) {
    return (
      <div className="flex items-center gap-2 py-20 text-sm text-muted-foreground">
        <Spinner /> Loading rules…
      </div>
    );
  }

  if (isError) {
    return (
      <Banner
        tone="destructive"
        icon={<AlertTriangle className="h-5 w-5" />}
        title="Failed to load configuration"
      >
        {String((error as Error)?.message ?? error)}
      </Banner>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Rename rules"
        description="Define which releases get renamed and how. The first matching tracker wins; otherwise the global rules apply."
        actions={
          <>
            {dirty ? <Badge tone="warning">Unsaved changes</Badge> : null}
            <Button variant="ghost" size="icon" onClick={handleRefresh} aria-label="Reload from server">
              <RefreshCw className={isFetching ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            </Button>
            {dirty ? (
              <Button variant="outline" onClick={handleRevert} disabled={save.isPending}>
                <Undo2 className="h-4 w-4" /> Revert
              </Button>
            ) : null}
            <Button
              variant="primary"
              onClick={handleSave}
              disabled={!dirty || readonly || save.isPending}
            >
              {save.isPending ? <Spinner className="text-primary-foreground" /> : <Save className="h-4 w-4" />}
              Save changes
            </Button>
          </>
        }
      />

      {!authDismissed ? (
        <Banner
          tone="warning"
          icon={<ShieldAlert className="h-5 w-5" />}
          title="This service has no built-in authentication"
          action={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                localStorage.setItem(DISMISS_KEY, "1");
                setAuthDismissed(true);
              }}
            >
              Dismiss
            </Button>
          }
        >
          Anyone who can reach this port can change rules and trigger renames. Keep it on a trusted
          network or behind an authenticating reverse proxy.
        </Banner>
      ) : null}

      {data?.meta.config_error ? (
        <Banner
          tone="destructive"
          icon={<FileWarning className="h-5 w-5" />}
          title="The rules file could not be parsed"
        >
          {data.meta.config_error}. Saving from here will overwrite it with valid YAML.
        </Banner>
      ) : null}

      {readonly ? (
        <Banner tone="warning" icon={<ShieldAlert className="h-5 w-5" />} title="Read-only mode">
          Editing is disabled because <code>config_readonly</code> is enabled.
        </Banner>
      ) : null}

      {!data?.meta.config_found ? (
        <Banner tone="info" icon={<Info className="h-5 w-5" />} title="No rules file yet">
          Saving will create <code>{data?.meta.config_path}</code>.
        </Banner>
      ) : null}

      {/* Global rules */}
      <Card>
        <CardHeader>
          <CardTitle>Global rules</CardTitle>
          <CardDescription>
            Used when no tracker override matches the release&apos;s indexer.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <RuleSetEditor
            value={draft.global}
            onChange={(global) => setDraft({ ...draft, global })}
            disabled={readonly}
            idPrefix="global"
          />
        </CardContent>
      </Card>

      {/* Tracker overrides */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold tracking-tight">Tracker overrides</h2>
            <p className="text-sm text-muted-foreground">
              Per-indexer rules. A matched tracker fully replaces the global rules.
            </p>
          </div>
          <Button
            variant="outline"
            disabled={readonly}
            onClick={() => setDraft({ ...draft, trackers: [...draft.trackers, emptyTracker()] })}
          >
            <Plus className="h-4 w-4" /> Add tracker
          </Button>
        </div>

        {draft.trackers.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center gap-2 py-10 text-center">
              <p className="text-sm font-medium">No tracker overrides</p>
              <p className="max-w-md text-sm text-muted-foreground">
                Add a tracker to apply different filters and rename rules for a specific indexer.
              </p>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {draft.trackers.map((tracker, i) => (
              <TrackerCard
                // biome-ignore lint: index key is intentional for reorderable rows
                key={i}
                tracker={tracker}
                index={i}
                total={draft.trackers.length}
                onChange={(next) => updateTracker(i, next)}
                onRemove={() =>
                  setDraft({ ...draft, trackers: draft.trackers.filter((_, idx) => idx !== i) })
                }
                onMove={(dir) => moveTracker(i, dir)}
                disabled={readonly}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
