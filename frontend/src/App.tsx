import { useEffect } from "react";

import { useCurrentUser } from "./api/auth";
import { ApiError } from "./api/client";
import { useDirections } from "./api/directions";
import { useDnsRules } from "./api/dns";
import { useGeoIpLists } from "./api/geoip";
import { useIpsetGroups } from "./api/ipsetGroups";
import { useDeleteServer, useServers, useUninstallServer } from "./api/servers";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { type TabItem, Tabs } from "./components/Tabs";
import { Topbar } from "./components/Topbar";
import { AddServerModal } from "./modals/AddServerModal";
import { EditServerModal } from "./modals/EditServerModal";
import { TlsModal } from "./modals/TlsModal";
import { UpdateAgentModal } from "./modals/UpdateAgentModal";
import { ListsTab } from "./pages/ListsTab";
import { LoginPage } from "./pages/LoginPage";
import { MetricsTab } from "./pages/MetricsTab";
import { RoutingTab } from "./pages/RoutingTab";
import { TunnelsTab } from "./pages/TunnelsTab";
import { useAuthStore } from "./store/auth";
import { useModalsStore } from "./store/modals";
import { type TabId, useUiStore } from "./store/ui";
import { useWebSocket } from "./ws/useWS";

const TAB_ITEMS: TabItem[] = [
  { id: "routing", label: "Routing",   icon: "route"    },
  { id: "tunnels", label: "Tunnels",   icon: "tunnel"   },
  { id: "lists",   label: "Lists",     icon: "list"     },
  { id: "metrics", label: "Metrics",   icon: "activity" },
];

export function App() {
  const theme = useUiStore((state) => state.theme);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  const token = useAuthStore((state) => state.token);
  const isExpired = useAuthStore((state) => state.isExpired);
  const meQuery = useCurrentUser();

  const sessionInvalid =
    token === null
    || isExpired()
    || (meQuery.error instanceof ApiError && meQuery.error.status === 401);

  if (sessionInvalid) {
    return <LoginPage />;
  }

  // meQuery ещё крутится, но токен формально валидный — рендерим основной UI.
  return <Dashboard />;
}

function Dashboard() {
  useWebSocket();

  const { data: servers = [], isLoading } = useServers();
  const deleteServer = useDeleteServer();
  const uninstallServer = useUninstallServer();
  const activeServerId = useUiStore((state) => state.activeServerId);
  const setActiveServerId = useUiStore((state) => state.setActiveServerId);
  const activeTab = useUiStore((state) => state.activeTab);
  const setActiveTab = useUiStore((state) => state.setActiveTab);
  // Все эфемерные модалки управляются единым store. Вся логика «открыта/нет» —
  // через `useModalsStore` (не размазывается по useUiStore + useState).
  const openModals = useModalsStore((state) => state.open);
  const showModal = useModalsStore((state) => state.show);
  const hideModal = useModalsStore((state) => state.hide);
  const showSparklines = useUiStore((state) => state.showSparklines);

  // На случай если активный сервер удалён — закрываем зависящие от него модалки,
  // иначе они останутся в open-state и при следующем выборе сервера снова покажутся.
  useEffect(() => {
    if (activeServerId === null) {
      hideModal("tls");
      hideModal("updateAgent");
      hideModal("editServer");
    }
  }, [activeServerId, hideModal]);

  useEffect(() => {
    if (activeServerId == null && servers.length > 0) {
      setActiveServerId(servers[0].id);
    }
  }, [servers, activeServerId, setActiveServerId]);

  const activeServer = servers.find((server) => server.id === activeServerId) ?? null;

  const { data: directions = [] } = useDirections(activeServerId);
  const { data: dnsRules = [] } = useDnsRules(activeServerId);
  const { data: geoLists = [] } = useGeoIpLists();
  const { data: ipsetGroups = [] } = useIpsetGroups(activeServerId);

  const tabsWithCounts: TabItem[] = TAB_ITEMS.map((item) => ({
    ...item,
    count:
      item.id === "routing" ? directions.length :
      item.id === "tunnels" ? activeServer?.awg_containers.length ?? 0 :
      item.id === "lists" ? geoLists.length + dnsRules.length + ipsetGroups.length :
      null,
  }));

  return (
    <div className="app">
      <Sidebar
        servers={servers}
        activeId={activeServerId}
        onSelect={(id) => setActiveServerId(id)}
        onAdd={() => showModal("addServer")}
        onDelete={(server) => {
          if (server.id === activeServerId) setActiveServerId(null);
          deleteServer.mutate(server.id);
        }}
        onUninstall={(server) => {
          if (server.id === activeServerId) setActiveServerId(null);
          uninstallServer.mutate(server.id, {
            onSuccess: (data) => {
              alert(
                `Сервер ${server.name} удалён.\n\nЛог cleanup'а:\n${(data?.log ?? []).join("\n")}`,
              );
            },
            onError: (error) => alert(`Ошибка uninstall: ${String(error)}`),
          });
        }}
      />
      <div
        className="main"
        data-screen-label={activeServer ? `${activeTab} · ${activeServer.name}` : activeTab}
      >
        <Topbar
          server={activeServer}
          onTLS={() => showModal("tls")}
          onUpdate={() => showModal("updateAgent")}
          onEditSettings={() => showModal("editServer")}
        />
        <Tabs tab={activeTab} onTab={(tab: TabId) => setActiveTab(tab)} tabs={tabsWithCounts} />
        <div className="content" key={`${activeTab}-${activeServerId ?? "none"}`}>
          {!activeServer && !isLoading && (
            <div className="hint">
              Серверы ещё не добавлены — нажмите «Добавить сервер» в левом нижнем углу,
              чтобы запустить онбординг через SSH.
            </div>
          )}
          {activeServer && activeTab === "routing" && (
            <RoutingTab
              serverId={activeServer.id}
              awgContainers={activeServer.awg_containers}
              showSpark={showSparklines}
            />
          )}
          {activeServer && activeTab === "tunnels" && <TunnelsTab serverId={activeServer.id} showSpark={showSparklines} />}
          {activeServer && activeTab === "lists" && <ListsTab serverId={activeServer.id} showSpark={showSparklines} />}
          {activeServer && activeTab === "metrics" && <MetricsTab serverId={activeServer.id} showSpark={showSparklines} />}
        </div>
        <StatusBar server={activeServer} />
      </div>

      {openModals.has("addServer") && <AddServerModal onClose={() => hideModal("addServer")} />}
      {openModals.has("tls") && activeServer && (
        <TlsModal serverId={activeServer.id} onClose={() => hideModal("tls")} />
      )}
      {openModals.has("updateAgent") && activeServer && (
        <UpdateAgentModal
          serverId={activeServer.id}
          currentVersion={activeServer.version}
          onClose={() => hideModal("updateAgent")}
        />
      )}
      {openModals.has("editServer") && activeServer && (
        <EditServerModal server={activeServer} onClose={() => hideModal("editServer")} />
      )}
    </div>
  );
}
