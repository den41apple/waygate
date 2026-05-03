import { useState } from "react";

import { useCreateRule, type RoutingRuleCreate } from "../api/rules";
import { Icon } from "../components/Icon";
import { IconTile, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  awgContainers: string[];
  onClose: () => void;
}

const COUNTRY_RE = /^[A-Z]{2}$/;
const IPSET_NAME_RE = /^[a-zA-Z0-9_-]{1,31}$/;

export function AddRuleModal({ serverId, awgContainers, onClose }: Props) {
  const create = useCreateRule(serverId);

  const [country, setCountry] = useState("");
  const [ipsetName, setIpsetName] = useState("");
  const [fwmark, setFwmark] = useState(1);
  const [tableId, setTableId] = useState(100);
  const [viaInterface, setViaInterface] = useState(awgContainers[0] ?? "");
  const [viaGateway, setViaGateway] = useState("10.0.0.1");
  const [enabled, setEnabled] = useState(true);

  const valid =
    COUNTRY_RE.test(country)
    && IPSET_NAME_RE.test(ipsetName)
    && fwmark > 0
    && tableId > 0
    && viaInterface.trim().length > 0
    && viaGateway.trim().length > 0;

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
