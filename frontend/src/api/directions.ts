import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  Direction,
  DirectionCreate,
  DirectionListResponse,
  DirectionUpdate,
} from "./types";

export const directionsKey = (serverId: number) => ["directions", serverId] as const;

export function useDirections(serverId: number | null) {
  return useQuery({
    queryKey: serverId != null ? directionsKey(serverId) : ["directions", "noop"],
    queryFn: () => api.get<DirectionListResponse>(`/servers/${serverId}/directions`),
    enabled: serverId != null,
    select: (data) => data.directions,
  });
}

export function useCreateDirection(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: DirectionCreate) =>
      api.post<Direction>(`/servers/${serverId}/directions`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: directionsKey(serverId) }),
  });
}

export function useUpdateDirection(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ directionId, patch }: { directionId: number; patch: DirectionUpdate }) =>
      api.patch<Direction>(`/servers/${serverId}/directions/${directionId}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: directionsKey(serverId) }),
  });
}

export function useDeleteDirection(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (directionId: number) =>
      api.delete<void>(`/servers/${serverId}/directions/${directionId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: directionsKey(serverId) }),
  });
}
