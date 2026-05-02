import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { type AuthUser, useAuthStore } from "../store/auth";
import { api, ApiError } from "./client";

export interface WsTokenResponse {
  token: string;
  ttl_seconds: number;
}

export async function fetchWsToken(): Promise<WsTokenResponse> {
  return api.post<WsTokenResponse>("/auth/ws-token");
}

interface LoginRequest {
  username: string;
  password: string;
}

interface LoginResponse {
  token: string;
  ttl_seconds: number;
  user: AuthUser;
}

export const ME_KEY = ["auth", "me"] as const;

export function useLogin() {
  const queryClient = useQueryClient();
  const setSession = useAuthStore((state) => state.setSession);
  return useMutation({
    mutationFn: (request: LoginRequest) => api.post<LoginResponse>("/auth/login", request),
    onSuccess: (data) => {
      setSession(data.token, data.user, data.ttl_seconds);
      queryClient.invalidateQueries({ queryKey: ME_KEY });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  const clear = useAuthStore((state) => state.clear);
  return useMutation({
    mutationFn: async () => {
      try {
        await api.post<void>("/auth/logout");
      } catch {
        // logout best-effort: серверный no-op, главное — почистить локальный стор
      }
    },
    onSuccess: () => {
      clear();
      queryClient.clear();
    },
  });
}

export function useCurrentUser() {
  const token = useAuthStore((state) => state.token);
  return useQuery({
    queryKey: ME_KEY,
    queryFn: () => api.get<AuthUser>("/auth/me"),
    enabled: token !== null,
    retry: (count, error) => {
      if (error instanceof ApiError && error.status === 401) return false;
      return count < 1;
    },
    staleTime: 60_000,
  });
}
