import { useState } from "react";

import { useDeleteIpsetGroup, useIpsetGroups } from "../api/ipsetGroups";
import type { IpsetGroup } from "../api/types";
import { Icon } from "../components/Icon";
import { AddCustomIpsetModal } from "../modals/AddCustomIpsetModal";
import { Metric, MonoPill, SectionHead } from "../components/primitives";

interface Props {
  serverId: number;
  showSpark: boolean;
}

export function IpsetGroupsTab({ serverId, showSpark }: Props) {
  const { data: groups = [] } = useIpsetGroups(serverId);
  const deleteGroup = useDeleteIpsetGroup(serverId);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<IpsetGroup | null>(null);

  const totalCidrs = groups.reduce((acc, group) => acc + group.cidrs.length, 0);

  return (
    <>
      <div className="metric-row">
        <Metric
          label="IPset-групп"
          value={groups.length}
          sub="свои списки"
          icon="list"
          tileColor="orange"
          sparkSeed={3}
          showSpark={showSpark}
        />
        <Metric
          label="CIDR'ов"
          value={totalCidrs.toLocaleString()}
          sub="всего"
          icon="server"
          tileColor="cyan"
          sparkSeed={5}
          showSpark={showSpark}
        />
      </div>

      <div className="hint">
        Custom IPset — третья сущность для маршрутизации (помимо GeoIP-зон и
        DNS-правил). Когда хочется роутить произвольные IP/подсети, не привязанные
        к стране или домену — заводи список тут.
      </div>

      <SectionHead title="Свои IPset" count={groups.length}>
        <button className="tb-btn" onClick={() => setShowAdd(true)}>
          <Icon name="plus" size={14} /> Добавить IPset
        </button>
      </SectionHead>

      {groups.length === 0 ? (
        <div className="hint">Пока нет ни одного IPset.</div>
      ) : (
        <div className="card">
          <table className="geoip-table">
            <thead>
              <tr>
                <th>имя ipset</th>
                <th className="num">CIDR'ов</th>
                <th>обновлён</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {groups.map((group) => (
                <tr key={group.id}>
                  <td><MonoPill accent>{group.name}</MonoPill></td>
                  <td className="num">{group.cidrs.length}</td>
                  <td className="mono" style={{ color: "var(--text-3)" }}>
                    {new Date(group.updated_at).toLocaleString()}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button
                      className="tb-btn"
                      onClick={() => setEditing(group)}
                      title="Редактировать"
                    >
                      <Icon name="edit" size={12} />
                    </button>
                    <button
                      className="tb-btn"
                      style={{ marginLeft: 4 }}
                      onClick={() => {
                        if (window.confirm(`Удалить ipset «${group.name}»?`)) {
                          deleteGroup.mutate(group.id);
                        }
                      }}
                      disabled={deleteGroup.isPending}
                      title="Удалить"
                    >
                      <Icon name="x" size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showAdd && <AddCustomIpsetModal serverId={serverId} onClose={() => setShowAdd(false)} />}
      {editing && (
        <AddCustomIpsetModal
          serverId={serverId}
          editing={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}
