import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { GeoIpSyncResponse, GeoList, GeoListListResponse } from "./types";

export const GEOIP_KEY = ["geoip", "lists"] as const;

export function useGeoIpLists() {
  return useQuery({
    queryKey: GEOIP_KEY,
    queryFn: () => api.get<GeoListListResponse>("/geoip/lists"),
    select: (data) => data.lists,
  });
}

export interface GeoListCreate {
  country: string;
  name: string;
  source_url: string;
}

export function useCreateGeoList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: GeoListCreate) => api.post<GeoList>("/geoip/lists", payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: GEOIP_KEY }),
  });
}

export function useDeleteGeoList() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/geoip/lists/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: GEOIP_KEY }),
  });
}

export interface GeoIpSyncTrigger {
  geo_list_id: number;
  ipset_name: string;
  custom_cidrs?: string[];
}

export function useSyncGeoIp(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: GeoIpSyncTrigger) =>
      api.post<GeoIpSyncResponse>(`/servers/${serverId}/geoip/sync`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: GEOIP_KEY }),
  });
}
