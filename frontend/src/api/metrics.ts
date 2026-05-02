import { useQuery } from "@tanstack/react-query";

import { api } from "./client";
import type { MetricsRange, MetricsRangeResponse } from "./types";

export const metricsKey = (serverId: number, range: MetricsRange) =>
  ["servers", serverId, "metrics", range] as const;

export function useMetrics(serverId: number | null, range: MetricsRange) {
  return useQuery({
    queryKey: serverId != null ? metricsKey(serverId, range) : ["servers", "noop", "metrics", range],
    queryFn: () => api.get<MetricsRangeResponse>(`/servers/${serverId}/metrics?range=${range}`),
    enabled: serverId != null,
    refetchInterval: 30_000,
  });
}
