import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import type { AwgClient, AwgClientCreate, AwgClientListResponse } from "./types";
import { api } from "./client";
import { useAuthStore } from "../store/auth";

export const awgClientsKey = (serverId: number) => ["awg-clients", serverId] as const;

export function useAwgClients(serverId: number | null) {
  return useQuery({
    queryKey: serverId != null ? awgClientsKey(serverId) : ["awg-clients", "noop"],
    queryFn: () => api.get<AwgClientListResponse>(`/servers/${serverId}/clients`),
    enabled: serverId != null,
    select: (data) => data.clients,
  });
}

export function useCreateAwgClient(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: AwgClientCreate) =>
      api.post<AwgClient>(`/servers/${serverId}/clients`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: awgClientsKey(serverId) }),
  });
}

export function useDeleteAwgClient(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (clientId: number) =>
      api.delete<void>(`/servers/${serverId}/clients/${clientId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: awgClientsKey(serverId) }),
  });
}

export function useStartAwgClient(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (clientId: number) =>
      api.post<AwgClient>(`/servers/${serverId}/clients/${clientId}/start`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: awgClientsKey(serverId) }),
  });
}

export function useStopAwgClient(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (clientId: number) =>
      api.post<AwgClient>(`/servers/${serverId}/clients/${clientId}/stop`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: awgClientsKey(serverId) }),
  });
}

/**
 * Скачивает .conf-файл клиента — добавляет JWT в query-param, потому что
 * `<a href>` не передаёт Authorization-заголовок.
 */
export function downloadConfigUrl(serverId: number, clientId: number): string {
  const token = useAuthStore.getState().token ?? "";
  return `/api/v1/servers/${serverId}/clients/${clientId}/config?access_token=${encodeURIComponent(token)}`;
}

/**
 * URL для `<img src>` с QR-кодом — JWT в query-param по той же причине.
 */
export function qrUrl(serverId: number, clientId: number): string {
  const token = useAuthStore.getState().token ?? "";
  return `/api/v1/servers/${serverId}/clients/${clientId}/qr?access_token=${encodeURIComponent(token)}`;
}
