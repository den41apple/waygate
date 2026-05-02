import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface AuthUser {
  username: string;
  is_admin: boolean;
}

export interface AuthStore {
  token: string | null;
  user: AuthUser | null;
  expiresAt: number | null; // unix-секунды
  setSession: (token: string, user: AuthUser, ttlSeconds: number) => void;
  clear: () => void;
  isExpired: () => boolean;
}

export const useAuthStore = create<AuthStore>()(
  persist(
    (set, get) => ({
      token: null,
      user: null,
      expiresAt: null,
      setSession: (token, user, ttlSeconds) =>
        set({
          token,
          user,
          expiresAt: Math.floor(Date.now() / 1000) + ttlSeconds,
        }),
      clear: () => set({ token: null, user: null, expiresAt: null }),
      isExpired: () => {
        const expiresAt = get().expiresAt;
        if (expiresAt === null) return true;
        return expiresAt <= Math.floor(Date.now() / 1000);
      },
    }),
    {
      name: "waygate-auth",
      partialize: (state) => ({
        token: state.token,
        user: state.user,
        expiresAt: state.expiresAt,
      }),
    },
  ),
);
