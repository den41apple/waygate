import { useTunnels } from "../api/tunnels";
import type { PeerInfo, TunnelInfo, TunnelStatus } from "../api/types";
import { Icon } from "../components/Icon";
import { Badge, IconTile, Metric, SectionHead } from "../components/primitives";

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

export function TunnelsTab({ serverId, showSpark }: Props) {
  const { data, isLoading, isError, error } = useTunnels(serverId);
  const tunnels: TunnelInfo[] = data?.tunnels ?? [];
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

      <SectionHead title="AWG-интерфейсы" count={tunnels.length}>
        <span style={{ fontSize: 11, color: "var(--text-3)" }}>обновляется каждые 30с</span>
      </SectionHead>

      {isLoading && <div className="hint">Загружаю туннели…</div>}
      {isError && <div className="hint">Не удалось получить туннели: {String(error)}</div>}

      {!isLoading && !isError && tunnels.length === 0 && (
        <div className="hint">
          На сервере не найдено AmneziaWG-контейнеров. Запустите контейнер с image,
          содержащим «amnezia» / «awg» в имени, и убедитесь что у агента есть доступ
          к docker-сокету.
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
            <button className="tb-btn"><Icon name="more" size={14} /></button>
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
  );
}
