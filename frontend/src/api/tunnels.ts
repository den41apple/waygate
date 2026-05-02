import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { TunnelsResponse } from "./types";

export const tunnelsKey = (serverId: number) => ["servers", serverId, "tunnels"] as const;

export function useTunnels(serverId: number | null) {
  return useQuery({
    queryKey: serverId != null ? tunnelsKey(serverId) : ["servers", "noop", "tunnels"],
    queryFn: () => api.get<TunnelsResponse>(`/servers/${serverId}/tunnels`),
    enabled: serverId != null,
    refetchInterval: 30_000,
  });
}
