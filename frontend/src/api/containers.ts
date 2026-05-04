import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { ContainerInfo, ContainerListResponse } from "./types";

export const containersKey = (serverId: number) => ["servers", serverId, "containers"] as const;

/** Список docker-контейнеров на target-сервере. Используется в модалке Direction
 * как dropdown для `scope_target` при scope=container — оператор не вводит
 * имя руками, видит реальные контейнеры. */
export function useContainers(serverId: number) {
  return useQuery<ContainerListResponse, Error, ContainerInfo[]>({
    queryKey: containersKey(serverId),
    queryFn: () => api.get<ContainerListResponse>(`/servers/${serverId}/containers`),
    select: (data) => data.containers,
    // Контейнеры могут запускаться/останавливаться часто — короткий staleTime.
    staleTime: 10_000,
  });
}
