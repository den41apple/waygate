import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { ApplyDnsResponse, DnsRule, DnsRuleListResponse } from "./types";

export const dnsKey = (serverId: number) => ["servers", serverId, "dns"] as const;

export function useDnsRules(serverId: number | null) {
  return useQuery({
    queryKey: serverId != null ? dnsKey(serverId) : ["servers", "noop", "dns"],
    queryFn: () => api.get<DnsRuleListResponse>(`/servers/${serverId}/dns`),
    enabled: serverId != null,
    select: (data) => data.rules,
  });
}

export interface DnsRuleCreate {
  name: string;
  domains: string[];
  ipset_name: string;
  enabled?: boolean;
}

export function useCreateDnsRule(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: DnsRuleCreate) => api.post<DnsRule>(`/servers/${serverId}/dns`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: dnsKey(serverId) }),
  });
}

export function useUpdateDnsRule(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ ruleId, patch }: { ruleId: number; patch: Partial<DnsRule> }) =>
      api.patch<DnsRule>(`/servers/${serverId}/dns/${ruleId}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: dnsKey(serverId) }),
  });
}

export function useDeleteDnsRule(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (ruleId: number) => api.delete<void>(`/servers/${serverId}/dns/${ruleId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: dnsKey(serverId) }),
  });
}

export function useApplyDns(serverId: number) {
  return useMutation({
    mutationFn: () => api.post<ApplyDnsResponse>(`/servers/${serverId}/dns/apply`),
  });
}
