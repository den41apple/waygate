import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { ApplyRulesResponse, RoutingRule, RoutingRuleListResponse } from "./types";

export const rulesKey = (serverId: number) => ["servers", serverId, "rules"] as const;

export function useRules(serverId: number | null) {
  return useQuery({
    queryKey: serverId != null ? rulesKey(serverId) : ["servers", "noop", "rules"],
    queryFn: () => api.get<RoutingRuleListResponse>(`/servers/${serverId}/rules`),
    enabled: serverId != null,
    select: (data) => data.rules,
  });
}

export interface RoutingRuleCreate {
  country: string;
  ipset_name: string;
  fwmark: number;
  table_id: number;
  via_interface: string;
  via_gateway: string;
  enabled?: boolean;
  scope?: "host" | "container";
  scope_target?: string | null;
}

export function useCreateRule(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: RoutingRuleCreate) =>
      api.post<RoutingRule>(`/servers/${serverId}/rules`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: rulesKey(serverId) }),
  });
}

export function useUpdateRule(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ruleId, patch }: { ruleId: number; patch: Partial<RoutingRule> }) =>
      api.patch<RoutingRule>(`/servers/${serverId}/rules/${ruleId}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: rulesKey(serverId) }),
  });
}

export function useDeleteRule(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: number) => api.delete<void>(`/servers/${serverId}/rules/${ruleId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: rulesKey(serverId) }),
  });
}

export function useApplyRules(serverId: number) {
  return useMutation({
    mutationFn: () => api.post<ApplyRulesResponse>(`/servers/${serverId}/rules/apply`),
  });
}
