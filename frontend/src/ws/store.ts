import { create } from "zustand";

export interface WsStore {
  status: "connecting" | "connected" | "disconnected";
  setStatus: (status: WsStore["status"]) => void;
}

export const useWsStore = create<WsStore>((set) => ({
  status: "disconnected",
  setStatus: (status) => set({ status }),
}));
