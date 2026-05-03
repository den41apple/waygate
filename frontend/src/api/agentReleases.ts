import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { AgentReleasesResponse } from "./types";

export const AGENT_RELEASES_KEY = ["agent-releases"] as const;

export function useAgentReleases() {
  return useQuery({
    queryKey: AGENT_RELEASES_KEY,
    queryFn: () => api.get<AgentReleasesResponse>("/agent-releases"),
    // Сервер кеширует 5 мин, на клиенте достаточно 1 мин чтобы свежие релизы
    // подтянулись через UI без ручного reload.
    staleTime: 60_000,
    select: (data) => data.releases,
  });
}
