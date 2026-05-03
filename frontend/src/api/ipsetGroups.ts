import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type { IpsetGroup, IpsetGroupCreate, IpsetGroupListResponse } from "./types";

export const ipsetGroupsKey = (serverId: number) => ["ipset-groups", serverId] as const;

export function useIpsetGroups(serverId: number | null) {
  return useQuery({
    queryKey: serverId != null ? ipsetGroupsKey(serverId) : ["ipset-groups", "noop"],
    queryFn: () => api.get<IpsetGroupListResponse>(`/servers/${serverId}/ipset-groups`),
    enabled: serverId != null,
    select: (data) => data.groups,
  });
}

export function useCreateIpsetGroup(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: IpsetGroupCreate) =>
      api.post<IpsetGroup>(`/servers/${serverId}/ipset-groups`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ipsetGroupsKey(serverId) }),
  });
}

export interface IpsetGroupPatch {
  name?: string;
  cidrs?: string[];
}

export function useUpdateIpsetGroup(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, patch }: { groupId: number; patch: IpsetGroupPatch }) =>
      api.patch<IpsetGroup>(`/servers/${serverId}/ipset-groups/${groupId}`, patch),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ipsetGroupsKey(serverId) }),
  });
}

export function useDeleteIpsetGroup(serverId: number) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (groupId: number) =>
      api.delete<void>(`/servers/${serverId}/ipset-groups/${groupId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ipsetGroupsKey(serverId) }),
  });
}
