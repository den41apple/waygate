import { useState } from "react";

import {
  downloadConfigUrl,
  qrUrl,
  useAwgClients,
  useDeleteAwgClient,
  useStartAwgClient,
  useStopAwgClient,
} from "../api/awgClients";
import { useTunnels } from "../api/tunnels";
import type { AwgClient, AwgClientStatus, PeerInfo, TunnelInfo, TunnelStatus } from "../api/types";
import { Icon } from "../components/Icon";
import { Badge, IconTile, Metric, SectionHead } from "../components/primitives";
import { AddAwgClientModal } from "../modals/AddAwgClientModal";

interface Props {
  serverId: number;
  showSpark: boolean;
}

function formatBytes(bytes: number): string {
  if (bytes >= 1e12) return `${(bytes / 1e12).toFixed(2)} TB`;
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(2)} GB`;
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`;
  if (bytes >= 1e3) return `${(bytes / 1e3).toFixed(1)} KB`;
  return `${bytes} B`;
}

function formatHandshakeAge(iso: string | null): string {
  if (iso === null) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return `${seconds}с назад`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}м назад`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}ч назад`;
  return `${Math.floor(seconds / 86400)}д назад`;
}

function badgeKindForTunnel(status: TunnelStatus): "online" | "degraded" | "offline" {
  if (status === "up") return "online";
  if (status === "degraded") return "degraded";
  return "offline";
}

function shortKey(publicKey: string): string {
  return `${publicKey.slice(0, 8)}…${publicKey.slice(-4)}`;
}

function clientStatusBadge(status: AwgClientStatus): "online" | "degraded" | "offline" | "amber" | "error" {
  if (status === "running") return "online";
  if (status === "stopped") return "offline";
  if (status === "error") return "error";
  return "amber";
}

interface ManagedClientCardProps {
  serverId: number;
  client: AwgClient;
  onShowQr: (client: AwgClient) => void;
}

function ManagedClientCard({ serverId, client, onShowQr }: ManagedClientCardProps) {
  const startMutation = useStartAwgClient(serverId);
  const stopMutation = useStopAwgClient(serverId);
  const deleteMutation = useDeleteAwgClient(serverId);

  const isRunning = client.status === "running";

  return (
    <div className="card">
      <div className="tunnel-head">
        <IconTile color={isRunning ? "green" : "amber"} icon="tunnel" />
        <div className="name-block">
          <div className="name">
            {client.name}
            {client.country && <span className="mono" style={{ marginLeft: 8, color: "var(--text-3)" }}>[{client.country}]</span>}
          </div>
          <div className="container">{client.container_name}</div>
        </div>
        <Badge kind={clientStatusBadge(client.status)}>{client.status}</Badge>
      </div>
      <div className="tunnel-meta">
        <span>endpoint <b className="mono">{client.peer_endpoint ?? "—"}</b></span>
        <span>address <b className="mono">{client.interface_address ?? "—"}</b></span>
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
        {isRunning ? (
          <button
            className="tb-btn"
            onClick={() => stopMutation.mutate(client.id)}
            disabled={stopMutation.isPending}
          >
            <Icon name="lock" size={14} /> Stop
          </button>
        ) : (
          <button
            className="tb-btn"
            onClick={() => startMutation.mutate(client.id)}
            disabled={startMutation.isPending}
          >
            <Icon name="play" size={14} /> Start
          </button>
        )}
        <button className="tb-btn" onClick={() => onShowQr(client)}>
          <Icon name="globe" size={14} /> QR
        </button>
        <a
          className="tb-btn"
          href={downloadConfigUrl(serverId, client.id)}
          download={`${client.name}.conf`}
          style={{ textDecoration: "none" }}
        >
          <Icon name="download" size={14} /> .conf
        </a>
        <button
          className="tb-btn"
          onClick={() => {
            if (window.confirm(`Удалить клиента ${client.name}? Контейнер будет снесён.`)) {
              deleteMutation.mutate(client.id);
            }
          }}
          disabled={deleteMutation.isPending}
          style={{ color: "var(--red, #ef4444)" }}
        >
          <Icon name="x" size={14} /> Удалить
        </button>
      </div>
    </div>
  );
}

interface QrModalProps {
  serverId: number;
  client: AwgClient;
  onClose: () => void;
}

function QrModal({ serverId, client, onClose }: QrModalProps) {
  return (
    <div className="modal-veil" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()} style={{ maxWidth: 420 }}>
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="globe" size="sm" /> QR-код · {client.name}
          </div>
          <button className="close" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
        <div className="modal-body" style={{ textAlign: "center" }}>
          <img
            src={qrUrl(serverId, client.id)}
            alt="QR-код .conf"
            style={{ maxWidth: "100%", background: "#fff", padding: 16, borderRadius: 8 }}
          />
          <div className="hint" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 12 }}>
            Отсканируйте в мобильном AmneziaWG-приложении для импорта.
          </div>
        </div>
        <div className="modal-foot">
          <button className="btn primary" onClick={onClose}>Закрыть</button>
        </div>
      </div>
    </div>
  );
}

export function TunnelsTab({ serverId, showSpark }: Props) {
  const { data, isLoading, isError, error } = useTunnels(serverId);
  const allTunnels: TunnelInfo[] = data?.tunnels ?? [];
  const { data: awgClients = [] } = useAwgClients(serverId);

  // Tunnels из /v1/tunnels включают и наших клиентов, и внешние контейнеры.
  // Наши уже отображаются в верхней секции «Управляемые клиенты» — здесь
  // оставляем только внешние, чтобы не дублировать.
  const clientContainerNames = new Set(awgClients.map((awgClient) => awgClient.container_name));
  const tunnels = allTunnels.filter((tunnel) => !clientContainerNames.has(tunnel.container_name));

  const [showAdd, setShowAdd] = useState(false);
  const [qrTarget, setQrTarget] = useState<AwgClient | null>(null);
  const [view, setView] = useState<"clients" | "servers">("clients");
  const totalPeers = tunnels.reduce((acc, tunnel) => acc + tunnel.peers.length, 0);
  const upPeers = tunnels.flatMap((tunnel) => tunnel.peers).filter((peer) => {
    if (peer.last_handshake === null) return false;
    const ageSec = (Date.now() - new Date(peer.last_handshake).getTime()) / 1000;
    return ageSec < 180;
  }).length;
  const totalRx = tunnels.flatMap((tunnel) => tunnel.peers).reduce((acc, peer) => acc + peer.rx_bytes, 0);
  const totalTx = tunnels.flatMap((tunnel) => tunnel.peers).reduce((acc, peer) => acc + peer.tx_bytes, 0);

  return (
    <>
      <div className="metric-row">
        <Metric label="Туннели" value={tunnels.length} sub="awg-интерфейсы" icon="tunnel" tileColor="violet" sparkSeed={5} showSpark={showSpark} />
        <Metric label="Пиры" value={`${upPeers}/${totalPeers}`} sub="свежий handshake / всего" icon="server" tileColor="green" sparkSeed={9} showSpark={showSpark} />
        <Metric label="RX всего" value={formatBytes(totalRx)} sub="по всем пирам" icon="trending-down" tileColor="cyan" sparkSeed={13} showSpark={showSpark} />
        <Metric label="TX всего" value={formatBytes(totalTx)} sub="по всем пирам" icon="trending-up" tileColor="pink" sparkSeed={17} showSpark={showSpark} />
      </div>

      <div className="tab-switcher" style={{ marginBottom: 16 }}>
        <button
          className={view === "clients" ? "active" : ""}
          onClick={() => setView("clients")}
          type="button"
        >
          Клиенты ({awgClients.length})
        </button>
        <button
          className={view === "servers" ? "active" : ""}
          onClick={() => setView("servers")}
          type="button"
        >
          Серверные ({tunnels.length})
        </button>
      </div>

      {view === "clients" && (
        <>
          <SectionHead title="Управляемые клиенты" count={awgClients.length}>
            <button className="tb-btn primary" onClick={() => setShowAdd(true)}>
              <Icon name="plus" size={14} /> Добавить клиента
            </button>
          </SectionHead>

          {awgClients.length === 0 && (
            <div className="hint">
              Нет развёрнутых AmneziaWG-клиентов. Импортируйте `.conf` через «Добавить
              клиента» — Waygate поднимет docker-контейнер и подцепит туннель к
              доступным маршрутизирующим правилам.
            </div>
          )}

          {awgClients.map((awgClient) => (
            <ManagedClientCard
              key={awgClient.id}
              serverId={serverId}
              client={awgClient}
              onShowQr={setQrTarget}
            />
          ))}
        </>
      )}

      {view === "servers" && (
        <>
          <SectionHead title="Серверные туннели (внешние контейнеры)" count={tunnels.length}>
            <span style={{ fontSize: 11, color: "var(--text-3)" }}>обновляется каждые 30с</span>
          </SectionHead>

          {isLoading && <div className="hint">Загружаю туннели…</div>}
          {isError && <div className="hint">Не удалось получить туннели: {String(error)}</div>}

          {!isLoading && !isError && tunnels.length === 0 && (
            <div className="hint">
              Внешних AmneziaWG-контейнеров не найдено. Это контейнеры, которые
              ты поднимал сам (через AmneziaWG-Server и т.п.) — их Waygate не
              разворачивает, только показывает статус и пиров.
            </div>
          )}

          {tunnels.map((tunnel) => (
        <div key={tunnel.container_name} className="card">
          <div className="tunnel-head">
            <IconTile color="violet" icon="tunnel" />
            <div className="name-block">
              <div className="name">{tunnel.interface}</div>
              <div className="container">{tunnel.container_name}</div>
            </div>
            <Badge kind={badgeKindForTunnel(tunnel.status)}>{tunnel.status}</Badge>
          </div>
          <div className="tunnel-meta">
            <span>type <b>amneziawg</b></span>
            <span>peers <b>{tunnel.peers.length}</b></span>
          </div>
          {tunnel.peers.length > 0 && (
            <table className="peer-table">
              <thead>
                <tr>
                  <th>peer</th>
                  <th>endpoint</th>
                  <th>last handshake</th>
                  <th style={{ textAlign: "right" }}>rx</th>
                  <th style={{ textAlign: "right" }}>tx</th>
                </tr>
              </thead>
              <tbody>
                {tunnel.peers.map((peer: PeerInfo) => {
                  const stale =
                    peer.last_handshake === null
                    || (Date.now() - new Date(peer.last_handshake).getTime()) / 1000 > 180;
                  return (
                    <tr key={peer.public_key}>
                      <td>
                        <div className="col-name">
                          <span
                            className="dot"
                            style={{
                              width: 8,
                              height: 8,
                              borderRadius: 999,
                              background: stale ? "var(--amber)" : "var(--green)",
                              boxShadow: stale ? "0 0 0 3px var(--amber-tint)" : "0 0 0 3px var(--green-tint)",
                            }}
                          />
                          <span className="mono">{shortKey(peer.public_key)}</span>
                        </div>
                      </td>
                      <td className="col-ep">{peer.endpoint ?? "—"}</td>
                      <td className={`col-hs ${stale ? "stale" : ""}`}>
                        {formatHandshakeAge(peer.last_handshake)}
                      </td>
                      <td className="col-rx" style={{ textAlign: "right" }}>{formatBytes(peer.rx_bytes)}</td>
                      <td className="col-tx" style={{ textAlign: "right" }}>{formatBytes(peer.tx_bytes)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      ))}
        </>
      )}

      {showAdd && <AddAwgClientModal serverId={serverId} onClose={() => setShowAdd(false)} />}
      {qrTarget && (
        <QrModal serverId={serverId} client={qrTarget} onClose={() => setQrTarget(null)} />
      )}
    </>
  );
}
