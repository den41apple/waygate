import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  ProvisionPayload,
  ServerListResponse,
  ServerSummary,
  UpdateResponse,
  UpdateServerPayload,
} from "./types";

export const SERVERS_KEY = ["servers"] as const;

export function useServers() {
  return useQuery({
    queryKey: SERVERS_KEY,
    queryFn: () => api.get<ServerListResponse>("/servers"),
    select: (data) => data.servers,
  });
}

export function useServer(serverId: number | null) {
  return useQuery({
    queryKey: ["servers", serverId],
    queryFn: () => api.get<ServerSummary>(`/servers/${serverId}`),
    enabled: serverId != null,
  });
}

export function useDeleteServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (serverId: number) => api.delete<void>(`/servers/${serverId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SERVERS_KEY }),
  });
}

export function useRefreshServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (serverId: number) => api.post<ServerSummary>(`/servers/${serverId}/refresh`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SERVERS_KEY }),
  });
}

export function useProvisionServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ProvisionPayload) => api.post<ServerSummary>("/servers/provision", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SERVERS_KEY }),
  });
}

export function useUpdateServer() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ serverId, payload }: { serverId: number; payload: UpdateServerPayload }) =>
      api.post<UpdateResponse>(`/servers/${serverId}/update`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SERVERS_KEY }),
  });
}

interface RotateTokenResponse {
  rotated: boolean;
}

export function useRotateToken() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (serverId: number) =>
      api.post<RotateTokenResponse>(`/servers/${serverId}/token/rotate`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SERVERS_KEY }),
  });
}
