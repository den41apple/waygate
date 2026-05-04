import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { AgentReleasesResponse } from "./types";

export const AGENT_RELEASES_KEY = ["agent-releases"] as const;

export function useAgentReleases() {
  return useQuery({
    queryKey: AGENT_RELEASES_KEY,
    queryFn: () => api.get<AgentReleasesResponse>("/agent-releases"),
    // Сервер кеширует 60 сек. На клиенте 30 сек — каждое открытие модалки старее
    // 30 сек триггерит refetch (юзер ожидает свежий список после нового release'а).
    staleTime: 30_000,
    select: (data) => data.releases,
  });
}

/** Принудительно сбрасывает кеш списка релизов и запускает повторный запрос.
 * Вызывать при открытии модалки UpdateAgent — тогда свежий релиз появится
 * сразу, без ожидания TTL. */
export function useRefreshAgentReleases() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: AGENT_RELEASES_KEY });
}
