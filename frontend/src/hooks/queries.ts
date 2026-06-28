import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ConfigResponse, RulesConfig } from "@/lib/types";

export function useConfig() {
  return useQuery({ queryKey: ["config"], queryFn: api.getConfig, staleTime: 5_000 });
}

export function useSettings() {
  return useQuery({ queryKey: ["settings"], queryFn: api.getSettings, staleTime: 30_000 });
}

export function useStatus() {
  return useQuery({
    queryKey: ["status"],
    queryFn: api.getStatus,
    refetchInterval: 30_000,
  });
}

export function useSaveConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: RulesConfig) => api.saveConfig(config),
    onSuccess: (data) => {
      const next: ConfigResponse = { meta: data.meta, config: data.config };
      qc.setQueryData(["config"], next);
      qc.invalidateQueries({ queryKey: ["status"] });
    },
  });
}
