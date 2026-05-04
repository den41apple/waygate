import { useMemo, useState } from "react";

import type { ServerSummary } from "../api/types";
import { Icon } from "./Icon";

interface Props {
  servers: ServerSummary[];
  activeId: number | null;
  onSelect: (id: number) => void;
  onAdd: () => void;
  onDelete: (server: ServerSummary) => void;
  onUninstall: (server: ServerSummary) => void;
}

export function Sidebar({ servers, activeId, onSelect, onAdd, onDelete, onUninstall }: Props) {
  const [query, setQuery] = useState("");

  const list = useMemo(() => {
    if (!query) return servers;
    const lowerQuery = query.toLowerCase();
    return servers.filter((server) =>
      server.name.toLowerCase().includes(lowerQuery)
      || server.host.includes(query)
      || (server.region ?? "").toLowerCase().includes(lowerQuery),
    );
  }, [servers, query]);

  const groups = useMemo(() => {
    const acc: Record<string, ServerSummary[]> = {};
    for (const server of list) {
      const region = server.region ?? "—";
      (acc[region] ||= []).push(server);
    }
    return acc;
  }, [list]);

  const onlineCount = servers.filter((server) => server.status === "online").length;

  return (
    <aside className="sidebar">
      <div className="sb-head">
        <div className="sb-logo">
          <span className="mark">W</span>
          <span>Waygate</span>
        </div>
        <div className="sb-meta">v0.1.0</div>
      </div>
      <div className="sb-search">
        <Icon name="search" size={14} />
        <input
          placeholder="Найти сервер…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <span className="kbd">⌘K</span>
      </div>
      <div className="sb-section-label">
        <span>Серверы</span>
        <span style={{ color: "var(--green)" }}>● {onlineCount}/{servers.length}</span>
      </div>
      <div className="sb-list">
        {Object.entries(groups).map(([region, items]) => (
          <div key={region}>
            <div className="sb-region">{region}</div>
            {items.map((server) => (
              <div
                key={server.id}
                className={`sb-item ${server.id === activeId ? "active" : ""}`}
                onClick={() => onSelect(server.id)}
              >
                <span className={`dot ${server.status}`} />
                <div className="info">
                  <div className="name">{server.name}</div>
                  <div className="ip">{server.host}</div>
                </div>
                <span className="region">{server.region ?? "—"}</span>
                <button
                  className="sb-del"
                  title="Снести агента + удалить (cleanup на target по SSH)"
                  onClick={(event) => {
                    event.stopPropagation();
                    if (
                      window.confirm(
                        `СНЕСТИ всё с сервера ${server.name} (${server.host})?\n\n` +
                          "На target по SSH будет:\n" +
                          "• systemctl stop/disable waygate-agent\n" +
                          "• docker rm waygate-amnezia-client-*\n" +
                          "• flush iptables/ipset/route\n" +
                          "• rm -rf /opt/waygate-agent /etc/waygate /var/lib/waygate-agent\n\n" +
                          "Требует сохранённых SSH-кредов в карточке сервера.",
                      )
                    ) {
                      onUninstall(server);
                    }
                  }}
                >
                  <Icon name="server" size={14} />
                </button>
                <button
                  className="sb-del"
                  title="Только удалить из БД (агент на target остаётся жить)"
                  onClick={(event) => {
                    event.stopPropagation();
                    if (
                      window.confirm(
                        `Удалить только запись в БД? Агент на ${server.host} останется работать ` +
                          "(если хочешь полный cleanup — используй кнопку 🗑).",
                      )
                    ) {
                      onDelete(server);
                    }
                  }}
                >
                  <Icon name="x" size={14} />
                </button>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="sb-foot">
        <button className="sb-add" onClick={onAdd}>
          <Icon name="plus" size={16} /> Добавить сервер
        </button>
        <div className="sb-foot-row">
          <span>control-plane</span>
          <span style={{ color: "var(--green)" }}>● live</span>
        </div>
      </div>
    </aside>
  );
}
