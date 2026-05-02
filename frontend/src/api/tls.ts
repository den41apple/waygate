import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { TlsApplyResponse, TlsConfigPayload, TlsConfigResponse } from "./types";

export const tlsKey = (serverId: number) => ["servers", serverId, "tls"] as const;

export function useTlsConfig(serverId: number | null) {
  return useQuery({
    queryKey: serverId != null ? tlsKey(serverId) : ["servers", "noop", "tls"],
    queryFn: () => api.get<TlsConfigResponse>(`/servers/${serverId}/tls`),
    enabled: serverId != null,
    retry: false,
  });
}

export function useApplyTls(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: TlsConfigPayload) => api.post<TlsApplyResponse>(`/servers/${serverId}/tls`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: tlsKey(serverId) }),
  });
}
