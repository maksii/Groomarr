import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ConfigResponse, OperationListParams, RulesConfig } from "@/lib/types";

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

export function useOperations(params: OperationListParams) {
  return useQuery({
    queryKey: ["operations", params],
    queryFn: () => api.listOperations(params),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
}

export function useOperationStats() {
  return useQuery({
    queryKey: ["operationStats"],
    queryFn: api.getOperationStats,
    refetchInterval: 30_000,
  });
}

export function useOperation(id: number | null) {
  return useQuery({
    queryKey: ["operation", id],
    queryFn: () => api.getOperation(id as number),
    enabled: id != null,
    refetchInterval: 15_000,
  });
}

/** Invalidate every operations-related query (after a rollback changes state). */
export function useRefreshOperations() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["operations"] });
    qc.invalidateQueries({ queryKey: ["operationStats"] });
    qc.invalidateQueries({ queryKey: ["operation"] });
  };
}
