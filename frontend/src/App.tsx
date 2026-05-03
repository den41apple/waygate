import { useEffect } from "react";

import { useCurrentUser } from "./api/auth";
import { ApiError } from "./api/client";
import { useDnsRules } from "./api/dns";
import { useGeoIpLists } from "./api/geoip";
import { useRules } from "./api/rules";
import { useDeleteServer, useServers } from "./api/servers";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { type TabItem, Tabs } from "./components/Tabs";
import { Topbar } from "./components/Topbar";
import { AddServerModal } from "./modals/AddServerModal";
import { TlsModal } from "./modals/TlsModal";
import { UpdateAgentModal } from "./modals/UpdateAgentModal";
import { DnsTab } from "./pages/DnsTab";
import { GeoIpTab } from "./pages/GeoIpTab";
import { LoginPage } from "./pages/LoginPage";
import { MetricsTab } from "./pages/MetricsTab";
import { RoutingTab } from "./pages/RoutingTab";
import { TunnelsTab } from "./pages/TunnelsTab";
import { useAuthStore } from "./store/auth";
import { type TabId, useUiStore } from "./store/ui";
import { useWebSocket } from "./ws/useWS";

const TAB_ITEMS: TabItem[] = [
  { id: "routing", label: "Routing",   icon: "route"    },
  { id: "tunnels", label: "Tunnels",   icon: "tunnel"   },
  { id: "geoip",   label: "GeoIP",     icon: "globe"    },
  { id: "dns",     label: "DNS",       icon: "send"     },
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
  const activeServerId = useUiStore((state) => state.activeServerId);
  const setActiveServerId = useUiStore((state) => state.setActiveServerId);
  const activeTab = useUiStore((state) => state.activeTab);
  const setActiveTab = useUiStore((state) => state.setActiveTab);
  const showAddServer = useUiStore((state) => state.showAddServer);
  const setShowAddServer = useUiStore((state) => state.setShowAddServer);
  const showTls = useUiStore((state) => state.showTls);
  const setShowTls = useUiStore((state) => state.setShowTls);
  const showUpdate = useUiStore((state) => state.showUpdate);
  const setShowUpdate = useUiStore((state) => state.setShowUpdate);
  const showSparklines = useUiStore((state) => state.showSparklines);

  useEffect(() => {
    if (activeServerId == null && servers.length > 0) {
      setActiveServerId(servers[0].id);
    }
  }, [servers, activeServerId, setActiveServerId]);

  const activeServer = servers.find((server) => server.id === activeServerId) ?? null;

  const { data: rules = [] } = useRules(activeServerId);
  const { data: dnsRules = [] } = useDnsRules(activeServerId);
  const { data: geoLists = [] } = useGeoIpLists();

  const tabsWithCounts: TabItem[] = TAB_ITEMS.map((item) => ({
    ...item,
    count:
      item.id === "routing" ? rules.length :
      item.id === "tunnels" ? activeServer?.awg_containers.length ?? 0 :
      item.id === "dns" ? dnsRules.length :
      item.id === "geoip" ? geoLists.length :
      null,
  }));

  return (
    <div className="app">
      <Sidebar
        servers={servers}
        activeId={activeServerId}
        onSelect={(id) => setActiveServerId(id)}
        onAdd={() => setShowAddServer(true)}
        onDelete={(server) => {
          if (server.id === activeServerId) setActiveServerId(null);
          deleteServer.mutate(server.id);
        }}
      />
      <div
        className="main"
        data-screen-label={activeServer ? `${activeTab} · ${activeServer.name}` : activeTab}
      >
        <Topbar
          server={activeServer}
          onTLS={() => setShowTls(true)}
          onUpdate={() => setShowUpdate(true)}
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
          {activeServer && activeTab === "dns" && <DnsTab serverId={activeServer.id} showSpark={showSparklines} />}
          {activeServer && activeTab === "geoip" && <GeoIpTab serverId={activeServer.id} showSpark={showSparklines} />}
          {activeServer && activeTab === "metrics" && <MetricsTab serverId={activeServer.id} showSpark={showSparklines} />}
        </div>
        <StatusBar server={activeServer} />
      </div>

      {showAddServer && <AddServerModal onClose={() => setShowAddServer(false)} />}
      {showTls && activeServer && <TlsModal serverId={activeServer.id} onClose={() => setShowTls(false)} />}
      {showUpdate && activeServer && (
        <UpdateAgentModal
          serverId={activeServer.id}
          currentVersion={activeServer.version}
          onClose={() => setShowUpdate(false)}
        />
      )}
    </div>
  );
}
