// Тонкая fetch-обёртка с базовым URL, Bearer-auth и обработкой ошибок.
// В dev `/api/*` проксируется на :8000, в проде — на тот же origin.

import { useAuthStore } from "../store/auth";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly body?: unknown,
  ) {
    super(message);
  }
}

const BASE = "/api/v1";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = useAuthStore.getState().token;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (token !== null) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${BASE}${path}`, { ...init, headers });

  // 401 на /auth/me — канонический сигнал «сессия мертва». Очищаем стор.
  // Прочие 401 (race-condition / транзиентные) — просто кидаем ApiError,
  // App.tsx завязан на meQuery.error, реальный logout произойдёт через
  // следующий /auth/me, не моментально по любому фейлу.
  if (response.status === 401 && path === "/auth/me") {
    useAuthStore.getState().clear();
  }

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = await response.text();
    }
    const detail = typeof body === "object" && body && "detail" in body
      ? String((body as { detail: unknown }).detail)
      : String(body);
    throw new ApiError(response.status, `${init.method ?? "GET"} ${path} → ${response.status}: ${detail}`, body);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  patch: <T,>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }),
  delete: <T,>(path: string) => request<T>(path, { method: "DELETE" }),
};
