import { create } from "zustand";
import { persist } from "zustand/middleware";

export type TabId = "routing" | "tunnels" | "geoip" | "dns" | "metrics";
export type Theme = "dark" | "light";

export interface UiStore {
  activeServerId: number | null;
  activeTab: TabId;
  showAddServer: boolean;
  showTls: boolean;
  showUpdate: boolean;
  theme: Theme;
  showSparklines: boolean;
  setActiveServerId: (id: number | null) => void;
  setActiveTab: (tab: TabId) => void;
  setShowAddServer: (open: boolean) => void;
  setShowTls: (open: boolean) => void;
  setShowUpdate: (open: boolean) => void;
  setTheme: (theme: Theme) => void;
  setShowSparklines: (show: boolean) => void;
}

export const useUiStore = create<UiStore>()(
  persist(
    (set) => ({
      activeServerId: null,
      activeTab: "routing",
      showAddServer: false,
      showTls: false,
      showUpdate: false,
      theme: "dark",
      showSparklines: true,
      setActiveServerId: (id) => set({ activeServerId: id }),
      setActiveTab: (tab) => set({ activeTab: tab }),
      setShowAddServer: (open) => set({ showAddServer: open }),
      setShowTls: (open) => set({ showTls: open }),
      setShowUpdate: (open) => set({ showUpdate: open }),
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
    },
  ),
);
