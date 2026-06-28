import type { ReactNode } from "react";
import type { RuleSet, ScorePolicy } from "@/lib/types";
import { ChipListEditor } from "./ChipListEditor";
import { FieldInfo } from "./FieldInfo";
import { KeyValueEditor } from "./KeyValueEditor";
import { Input } from "./ui/input";
import { Label } from "./ui/label";
import { Select } from "./ui/select";
import { Switch } from "./ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./ui/tabs";

interface Props {
  value: RuleSet;
  onChange: (next: RuleSet) => void;
  disabled?: boolean;
  idPrefix: string;
}

function Field({
  label,
  help,
  hint,
  children,
}: {
  label: string;
  help?: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <Label>{label}</Label>
        {help ? <FieldInfo field={help} /> : null}
      </div>
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
      {children}
    </div>
  );
}

function Pair({ children }: { children: ReactNode }) {
  return <div className="grid gap-4 md:grid-cols-2">{children}</div>;
}

export function RuleSetEditor({ value, onChange, disabled, idPrefix }: Props) {
  function set<K extends keyof RuleSet>(key: K, val: RuleSet[K]) {
    onChange({ ...value, [key]: val });
  }

  return (
    <Tabs defaultValue="triggers" className="w-full">
      <TabsList className="mb-4">
        <TabsTrigger value="triggers">Triggers</TabsTrigger>
        <TabsTrigger value="rename">Rename</TabsTrigger>
        <TabsTrigger value="validation">Validation</TabsTrigger>
      </TabsList>

      {/* ---- Trigger filters ---- */}
      <TabsContent value="triggers" className="space-y-5 focus-visible:outline-none">
        <p className="text-xs text-muted-foreground">
          <strong className="text-foreground">Triggers</strong> decide <em>when</em> a release is
          renamed. Empty include lists allow everything; exclude lists skip matches. Hover the{" "}
          <span className="font-medium">(?)</span> on any field for help and examples.
        </p>
        <Pair>
          <Field label="Indexers — include" help="indexers_include">
            <ChipListEditor
              value={value.indexers_include}
              onChange={(v) => set("indexers_include", v)}
              validateKind="regex"
              mono
              disabled={disabled}
              placeholder="e.g. TrackerA.*"
            />
          </Field>
          <Field label="Indexers — exclude" help="indexers_exclude">
            <ChipListEditor
              value={value.indexers_exclude}
              onChange={(v) => set("indexers_exclude", v)}
              validateKind="regex"
              mono
              disabled={disabled}
              placeholder="e.g. .*Public.*"
            />
          </Field>
        </Pair>
        <Pair>
          <Field label="Qualities — include" help="qualities_include">
            <ChipListEditor
              value={value.qualities_include}
              onChange={(v) => set("qualities_include", v)}
              validateKind="regex"
              mono
              disabled={disabled}
              placeholder="e.g. Bluray.*"
            />
          </Field>
          <Field label="Qualities — exclude" help="qualities_exclude">
            <ChipListEditor
              value={value.qualities_exclude}
              onChange={(v) => set("qualities_exclude", v)}
              validateKind="regex"
              mono
              disabled={disabled}
              placeholder="e.g. CAM"
            />
          </Field>
        </Pair>
        <Pair>
          <Field label="Release groups — include" help="release_groups_include">
            <ChipListEditor
              value={value.release_groups_include}
              onChange={(v) => set("release_groups_include", v)}
              validateKind="regex"
              mono
              disabled={disabled}
            />
          </Field>
          <Field label="Release groups — exclude" help="release_groups_exclude">
            <ChipListEditor
              value={value.release_groups_exclude}
              onChange={(v) => set("release_groups_exclude", v)}
              validateKind="regex"
              mono
              disabled={disabled}
            />
          </Field>
        </Pair>
        <Pair>
          <Field label="Download clients — include" help="download_clients_include">
            <ChipListEditor
              value={value.download_clients_include}
              onChange={(v) => set("download_clients_include", v)}
              validateKind="regex"
              mono
              disabled={disabled}
            />
          </Field>
          <Field label="Download clients — exclude" help="download_clients_exclude">
            <ChipListEditor
              value={value.download_clients_exclude}
              onChange={(v) => set("download_clients_exclude", v)}
              validateKind="regex"
              mono
              disabled={disabled}
            />
          </Field>
        </Pair>
        <Pair>
          <Field label="Custom formats — require any" help="customformats_require_any" hint="Exact names, not regex">
            <ChipListEditor
              value={value.customformats_require_any}
              onChange={(v) => set("customformats_require_any", v)}
              disabled={disabled}
              placeholder="e.g. x265"
            />
          </Field>
          <Field label="Custom formats — exclude" help="customformats_exclude" hint="Exact names, not regex">
            <ChipListEditor
              value={value.customformats_exclude}
              onChange={(v) => set("customformats_exclude", v)}
              disabled={disabled}
              placeholder="e.g. 3D"
            />
          </Field>
        </Pair>
        <Field label="Minimum custom format score" help="min_customformat_score">
          <Input
            type="number"
            step={1}
            className="max-w-[12rem]"
            disabled={disabled}
            value={value.min_customformat_score ?? ""}
            onChange={(e) => {
              const n = Number.parseInt(e.target.value, 10);
              set("min_customformat_score", Number.isNaN(n) ? null : n);
            }}
            placeholder="disabled"
          />
        </Field>
      </TabsContent>

      {/* ---- Rename rules ---- */}
      <TabsContent value="rename" className="space-y-5 focus-visible:outline-none">
        <p className="text-xs text-muted-foreground">
          <strong className="text-foreground">Rename</strong> rules decide <em>how</em> the title is
          transformed. Steps run in order: strip extension → skip check → remove → replace →
          prefix/suffix → sanitize.
        </p>
        <Pair>
          <Field label="Prefix" help="prefix">
            <Input
              value={value.prefix}
              disabled={disabled}
              onChange={(e) => set("prefix", e.target.value)}
              placeholder="e.g. [AUTO] "
            />
          </Field>
          <Field label="Suffix" help="suffix">
            <Input
              value={value.suffix}
              disabled={disabled}
              onChange={(e) => set("suffix", e.target.value)}
              placeholder="e.g.  [Renamed]"
            />
          </Field>
        </Pair>
        <Field label="Remove patterns" help="remove_patterns">
          <ChipListEditor
            value={value.remove_patterns}
            onChange={(v) => set("remove_patterns", v)}
            validateKind="regex"
            mono
            disabled={disabled}
            placeholder="e.g. \[.*?\]"
          />
        </Field>
        <Field label="Replace patterns" help="replace_patterns">
          <KeyValueEditor
            value={value.replace_patterns}
            onChange={(v) => set("replace_patterns", v)}
            disabled={disabled}
          />
        </Field>
        <Field label="Skip title patterns" help="skip_title_patterns">
          <ChipListEditor
            value={value.skip_title_patterns}
            onChange={(v) => set("skip_title_patterns", v)}
            validateKind="regex"
            mono
            disabled={disabled}
            placeholder="e.g. PROPER"
          />
        </Field>
      </TabsContent>

      {/* ---- Score validation ---- */}
      <TabsContent value="validation" className="space-y-5 focus-visible:outline-none">
        <p className="text-xs text-muted-foreground">
          Optionally compare custom-format scores before/after the rename via the Sonarr/Radarr API.
          Requires the corresponding API URL + key (see Status).
        </p>
        <div className="flex items-center justify-between rounded-md border border-border p-4">
          <div className="flex items-center gap-1.5">
            <div>
              <Label htmlFor={`${idPrefix}-validate`}>Validate custom format score</Label>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Check that the new name does not lower the score
              </p>
            </div>
            <FieldInfo field="validate_custom_format_score" />
          </div>
          <Switch
            id={`${idPrefix}-validate`}
            checked={value.validate_custom_format_score}
            disabled={disabled}
            onCheckedChange={(c) => set("validate_custom_format_score", c)}
          />
        </div>
        <Field label="Policy when score would decrease" help="score_validation_policy">
          <Select
            className="max-w-[16rem]"
            disabled={disabled || !value.validate_custom_format_score}
            value={value.score_validation_policy}
            onChange={(e) => set("score_validation_policy", e.target.value as ScorePolicy)}
            options={[
              { value: "block", label: "Block — skip the rename" },
              { value: "warn", label: "Warn — rename anyway" },
            ]}
          />
        </Field>
      </TabsContent>
    </Tabs>
  );
}
