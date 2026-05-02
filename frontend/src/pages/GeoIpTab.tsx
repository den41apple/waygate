import { useDeleteGeoList, useGeoIpLists, useSyncGeoIp } from "../api/geoip";
import { Icon } from "../components/Icon";
import { Badge, IconTile, Metric, MonoPill, SectionHead } from "../components/primitives";

interface Props {
  serverId: number;
  showSpark: boolean;
}

export function GeoIpTab({ serverId, showSpark }: Props) {
  const { data: lists = [] } = useGeoIpLists();
  const deleteList = useDeleteGeoList();
  const syncList = useSyncGeoIp(serverId);

  const totalV4 = lists.reduce((acc, item) => acc + item.ipv4_count, 0);
  const totalV6 = lists.reduce((acc, item) => acc + item.ipv6_count, 0);

  return (
    <>
      <div className="geoip-toolbar">
        <IconTile color="violet" size="lg" icon="globe" />
        <div className="meta-grid">
          <span className="k">Источник</span>
          <a href="https://www.ipdeny.com/ipblocks" style={{ color: "var(--accent)", fontFamily: "var(--mono)" }}>
            www.ipdeny.com/ipblocks
          </a>
          <span className="k">Списков</span>
          <span className="mono">{lists.length}</span>
          <span className="k">Расписание</span>
          <span className="mono">manual · sync now</span>
        </div>
        <button className="btn primary" disabled>
          <Icon name="refresh" size={14} /> Sync all
        </button>
      </div>

      <div className="metric-row">
        <Metric label="Списков" value={lists.length} sub="стран отслеживается" icon="list" tileColor="violet" sparkSeed={3} showSpark={showSpark} />
        <Metric label="IPv4 блоков" value={totalV4.toLocaleString()} sub="все страны" icon="globe" tileColor="cyan" sparkSeed={6} showSpark={showSpark} />
        <Metric label="IPv6 блоков" value={totalV6.toLocaleString()} sub="все страны" icon="globe" tileColor="green" sparkSeed={9} showSpark={showSpark} />
        <Metric label="Custom CIDR" value={lists.reduce((acc, item) => acc + item.custom_count, 0)} sub="вручную" icon="server" tileColor="orange" sparkSeed={11} showSpark={showSpark} />
      </div>

      <SectionHead title="Реестры ipset" count={lists.length}>
        <button className="tb-btn"><Icon name="plus" size={14} /> Добавить страну</button>
      </SectionHead>

      <div className="card">
        <table className="geoip-table">
          <thead>
            <tr>
              <th>cc</th>
              <th>name</th>
              <th>ipset</th>
              <th>last sync</th>
              <th>status</th>
              <th className="num">ipv4</th>
              <th className="num">ipv6</th>
              <th className="num">custom +</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {lists.map((item) => (
              <tr key={item.id}>
                <td><span className="cc-pill">{item.country}</span></td>
                <td>{item.name}</td>
                <td>
                  <MonoPill accent>{`geoip-${item.country.toLowerCase()}-v4`}</MonoPill>
                </td>
                <td className="mono" style={{ color: "var(--text-3)" }}>
                  {item.last_synced_at ? new Date(item.last_synced_at).toLocaleString() : "—"}
                </td>
                <td>
                  <Badge kind={item.status === "synced" ? "online" : item.status === "stale" ? "amber" : "degraded"}>
                    {item.status}
                  </Badge>
                </td>
                <td className="num">{item.ipv4_count.toLocaleString()}</td>
                <td className="num">{item.ipv6_count.toLocaleString()}</td>
                <td className="num">
                  {item.custom_count > 0
                    ? <span style={{ color: "var(--accent)" }}>+{item.custom_count}</span>
                    : <span style={{ color: "var(--text-4)" }}>—</span>}
                </td>
                <td style={{ textAlign: "right" }}>
                  <button
                    className="tb-btn"
                    onClick={() => syncList.mutate({
                      geo_list_id: item.id,
                      ipset_name: `geoip-${item.country.toLowerCase()}-v4`,
                    })}
                    disabled={syncList.isPending}
                  >
                    <Icon name="refresh" size={12} /> sync
                  </button>
                  <button
                    className="tb-btn"
                    style={{ marginLeft: 4 }}
                    onClick={() => deleteList.mutate(item.id)}
                    disabled={deleteList.isPending}
                  >
                    <Icon name="x" size={12} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
