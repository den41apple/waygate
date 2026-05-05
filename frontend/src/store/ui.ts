import { create } from "zustand";
import { persist } from "zustand/middleware";

export type TabId = "routing" | "tunnels" | "lists" | "metrics";
export type Theme = "dark" | "light";

export interface UiStore {
  activeServerId: number | null;
  activeTab: TabId;
  theme: Theme;
  showSparklines: boolean;
  setActiveServerId: (id: number | null) => void;
  setActiveTab: (tab: TabId) => void;
  setTheme: (theme: Theme) => void;
  setShowSparklines: (show: boolean) => void;
}

// Эфемерные флаги «модалка открыта» вынесены в `store/modals.ts` —
// чтобы добавление новой модалки не требовало правок в этом store.

export const useUiStore = create<UiStore>()(
  persist(
    (set) => ({
      activeServerId: null,
      activeTab: "routing",
      theme: "dark",
      showSparklines: true,
      setActiveServerId: (id) => set({ activeServerId: id }),
      setActiveTab: (tab) => set({ activeTab: tab }),
      setTheme: (theme) => set({ theme }),
      setShowSparklines: (show) => set({ showSparklines: show }),
    }),
    {
      name: "waygate-ui",
      // Не персистим эфемерное (модалки, выбранный сервер) — сохраняем только tweaks
      partialize: (state) => ({
        theme: state.theme,
        showSparklines: state.showSparklines,
        activeTab: state.activeTab,
      }),
      // Старые сессии хранят activeTab="geoip"/"dns" — schлёпываем в "lists"
      // чтобы UI не падал на невалидном TabId.
      migrate: (persisted: unknown) => {
        const state = persisted as { activeTab?: string } | null;
        if (state && (state.activeTab === "geoip" || state.activeTab === "dns")) {
          return { ...state, activeTab: "lists" } as Partial<UiStore>;
        }
        return state as Partial<UiStore>;
      },
      version: 2,
    },
  ),
);
