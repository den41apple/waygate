import { create } from "zustand";

/**
 * Centralised store для эфемерного состояния «открытые модалки».
 *
 * Перед B5 (BACKLOG) каждая модалка либо жила в `useUiStore` (showAddServer/
 * showTls/showUpdate), либо в локальном `useState` соседнего компонента
 * (showEditServer в App.tsx). Добавление модалки = два-три edit'а.
 *
 * Теперь — единый Set активных id'шек: `open(id)`, `close(id)`, `isOpen(id)`.
 * id — короткая строка-литерал, опечатки ловит TypeScript благодаря `ModalId`.
 */
export type ModalId =
  | "addServer"
  | "tls"
  | "updateAgent"
  | "editServer";

interface ModalsStore {
  open: Set<ModalId>;
  show: (id: ModalId) => void;
  hide: (id: ModalId) => void;
  isOpen: (id: ModalId) => boolean;
}

export const useModalsStore = create<ModalsStore>((set, get) => ({
  open: new Set(),
  show: (id) =>
    set((state) => {
      const next = new Set(state.open);
      next.add(id);
      return { open: next };
    }),
  hide: (id) =>
    set((state) => {
      const next = new Set(state.open);
      next.delete(id);
      return { open: next };
    }),
  isOpen: (id) => get().open.has(id),
}));
