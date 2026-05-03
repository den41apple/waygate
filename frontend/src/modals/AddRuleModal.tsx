import { useState } from "react";

import { useCreateRule, type RoutingRuleCreate } from "../api/rules";
import { useTunnels } from "../api/tunnels";
import { Icon } from "../components/Icon";
import { IconTile, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  awgContainers: string[];
  onClose: () => void;
}

type Scope = "host" | "container";

const COUNTRY_RE = /^[A-Z]{2}$/;
const IPSET_NAME_RE = /^[a-zA-Z0-9_-]{1,31}$/;

export function AddRuleModal({ serverId, awgContainers, onClose }: Props) {
  const create = useCreateRule(serverId);
  // Список туннелей нужен только для container-scope: какой контейнер выбрать
  // как target. При host-scope этот список не используется.
  const { data: tunnelsData } = useTunnels(serverId);
  const tunnelContainers = tunnelsData?.tunnels.map((tunnel) => tunnel.container_name) ?? [];

  const [country, setCountry] = useState("");
  const [ipsetName, setIpsetName] = useState("");
  const [fwmark, setFwmark] = useState(1);
  const [tableId, setTableId] = useState(100);
  const [viaInterface, setViaInterface] = useState(awgContainers[0] ?? "");
  const [viaGateway, setViaGateway] = useState("10.0.0.1");
  const [enabled, setEnabled] = useState(true);
  const [scope, setScope] = useState<Scope>("host");
  const [scopeTarget, setScopeTarget] = useState<string>(tunnelContainers[0] ?? "");

  const valid =
    COUNTRY_RE.test(country)
    && IPSET_NAME_RE.test(ipsetName)
    && fwmark > 0
    && tableId > 0
    && viaInterface.trim().length > 0
    && viaGateway.trim().length > 0
    && (scope === "host" || scopeTarget.trim().length > 0);

  const submit = async () => {
    if (!valid) return;
    const payload: RoutingRuleCreate = {
      country,
      ipset_name: ipsetName.trim(),
      fwmark,
      table_id: tableId,
      via_interface: viaInterface.trim(),
      via_gateway: viaGateway.trim(),
      enabled,
      scope,
      scope_target: scope === "container" ? scopeTarget.trim() : null,
    };
    try {
      await create.mutateAsync(payload);
      onClose();
    } catch {
      // error отрисуется ниже, модалка остаётся открытой
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="route" size="sm" /> Добавить правило маршрутизации
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field-row">
            <div className="field">
              <label>Страна (ISO-2)</label>
              <input
                className="input"
                value={country}
                onChange={(event) => setCountry(event.target.value.toUpperCase().slice(0, 2))}
                placeholder="RU"
                maxLength={2}
              />
            </div>
            <div className="field">
              <label>ipset</label>
              <input
                className="input"
                value={ipsetName}
                onChange={(event) => setIpsetName(event.target.value)}
                placeholder="geoip-ru-v4"
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>fwmark</label>
              <input
                className="input"
                type="number"
                min={1}
                value={fwmark}
                onChange={(event) => setFwmark(Number(event.target.value))}
              />
            </div>
            <div className="field">
              <label>table_id (ip rule lookup)</label>
              <input
                className="input"
                type="number"
                min={1}
                value={tableId}
                onChange={(event) => setTableId(Number(event.target.value))}
              />
            </div>
          </div>

          <div className="field-row">
            <div className="field">
              <label>via_interface</label>
              {awgContainers.length > 0 ? (
                <select
                  className="select"
                  value={viaInterface}
                  onChange={(event) => setViaInterface(event.target.value)}
                >
                  {awgContainers.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              ) : (
                <input
                  className="input"
                  value={viaInterface}
                  onChange={(event) => setViaInterface(event.target.value)}
                  placeholder="amnezia-awg0"
                />
              )}
            </div>
            <div className="field">
              <label>via_gateway</label>
              <input
                className="input"
                value={viaGateway}
                onChange={(event) => setViaGateway(event.target.value)}
                placeholder="10.0.0.1"
              />
            </div>
          </div>

          <div className="field">
            <label>Где применять правило</label>
            <div className="tab-switcher">
              <button
                className={scope === "host" ? "active" : ""}
                onClick={() => setScope("host")}
                type="button"
              >
                Хост (вся ВМ)
              </button>
              <button
                className={scope === "container" ? "active" : ""}
                onClick={() => setScope("container")}
                type="button"
              >
                Контейнер (внутри netns)
              </button>
            </div>
            <div className="hint" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 6 }}>
              {scope === "host"
                ? "Стандарт: iptables/ip rule на хосте — для исходящего трафика ВМ."
                : "Внутри netns указанного docker-контейнера через nsenter — для двойного VPN, когда трафик клиентов AWG-server-контейнера нужно роутить через свой клиентский туннель."}
            </div>
          </div>

          {scope === "container" && (
            <div className="field">
              <label>Имя контейнера</label>
              {tunnelContainers.length > 0 ? (
                <select
                  className="select"
                  value={scopeTarget}
                  onChange={(event) => setScopeTarget(event.target.value)}
                >
                  {tunnelContainers.map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              ) : (
                <input
                  className="input"
                  value={scopeTarget}
                  onChange={(event) => setScopeTarget(event.target.value)}
                  placeholder="amnezia-awg2"
                />
              )}
            </div>
          )}

          <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <Toggle on={enabled} onClick={() => setEnabled(!enabled)} />
            <label>Включить сразу после создания</label>
          </div>

          {create.error && (
            <div
              className="hint"
              style={{ background: "var(--red-tint, #4a1f1f)", color: "var(--red, #ef4444)" }}
            >
              {String(create.error)}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={onClose}>Отмена</button>
          <button
            className="btn primary"
            onClick={submit}
            disabled={!valid || create.isPending}
          >
            {create.isPending ? "Создаю…" : "Создать"}
          </button>
        </div>
      </div>
    </div>
  );
}
