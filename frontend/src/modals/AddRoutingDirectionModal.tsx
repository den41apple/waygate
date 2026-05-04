import { useEffect, useMemo, useState } from "react";

import { useAwgClients } from "../api/awgClients";
import { useCreateDirection, useUpdateDirection } from "../api/directions";
import { useDnsRules } from "../api/dns";
import { useGeoIpLists } from "../api/geoip";
import { useIpsetGroups } from "../api/ipsetGroups";
import type { Direction, DirectionCreate, DirectionUpdate } from "../api/types";
import { flagFor } from "../components/CountrySelect";
import { Icon } from "../components/Icon";
import { IconTile, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  /** Если задано — модалка работает в edit-режиме: pre-fill полей и PATCH вместо POST. */
  editing?: Direction;
  onClose: () => void;
}

type Scope = "host" | "container";

/** Эвристика gateway из CIDR-адреса клиента: `10.66.66.2/24` → `10.66.66.1`. */
function deriveGateway(interfaceAddress: string | null): string | null {
  if (!interfaceAddress) return null;
  const ipPart = interfaceAddress.split("/")[0];
  const octets = ipPart.split(".");
  if (octets.length !== 4) return null;
  return `${octets[0]}.${octets[1]}.${octets[2]}.1`;
}

export function AddRoutingDirectionModal({ serverId, editing, onClose }: Props) {
  const create = useCreateDirection(serverId);
  const update = useUpdateDirection(serverId);
  const { data: awgClients = [] } = useAwgClients(serverId);
  const { data: geoLists = [] } = useGeoIpLists();
  const { data: dnsRules = [] } = useDnsRules(serverId);
  const { data: ipsetGroups = [] } = useIpsetGroups(serverId);

  const isEdit = editing !== undefined;
  const mutation = isEdit ? update : create;

  const [name, setName] = useState(editing?.name ?? "");
  const [awgClientId, setAwgClientId] = useState<number | null>(editing?.awg_client_id ?? null);
  const [viaInterface, setViaInterface] = useState(editing?.via_interface ?? "");
  const [viaGateway, setViaGateway] = useState(editing?.via_gateway ?? "10.66.66.1");
  const [scope, setScope] = useState<Scope>((editing?.scope ?? "host") as Scope);
  const [scopeTarget, setScopeTarget] = useState(editing?.scope_target ?? "");
  // При редактировании — расширенные параметры сразу раскрыты (юзер мог зайти ровно ради них).
  const [showAdvanced, setShowAdvanced] = useState(isEdit);
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);

  // Чекбоксы — Set'ами для O(1) toggle
  const [geoIds, setGeoIds] = useState<Set<number>>(new Set(editing?.geo_list_ids ?? []));
  const [dnsIds, setDnsIds] = useState<Set<number>>(new Set(editing?.dns_rule_ids ?? []));
  const [ipsetIds, setIpsetIds] = useState<Set<number>>(new Set(editing?.ipset_group_ids ?? []));

  // Default AWG-клиент — первый running, затем auto-fill via_interface/via_gateway.
  // В edit-режиме пропускаем (значения уже из existing direction).
  useEffect(() => {
    if (isEdit) return;
    if (awgClientId !== null || awgClients.length === 0) return;
    const running = awgClients.find((awgClient) => awgClient.status === "running") ?? awgClients[0];
    if (running) {
      setAwgClientId(running.id);
    }
  }, [awgClients, awgClientId, isEdit]);

  // При изменении awg_client_id — подтягиваем netdev и gateway.
  // В edit-режиме — только если пользователь СМЕНИЛ клиента (не на initial set'е).
  useEffect(() => {
    if (awgClientId === null) return;
    if (isEdit && awgClientId === editing?.awg_client_id) return;
    const awgClient = awgClients.find((item) => item.id === awgClientId);
    if (!awgClient) return;
    // Имя netdev'а: `awg-<name>[:11]` (см. agent/awg_clients.py::_iface_name).
    const expectedIface = `awg-${awgClient.name.slice(0, 11)}`;
    setViaInterface(expectedIface);
    const derived = deriveGateway(awgClient.interface_address);
    if (derived) setViaGateway(derived);
  }, [awgClientId, awgClients, isEdit, editing?.awg_client_id]);

  const valid = name.trim().length > 0 && viaInterface.trim().length > 0 && viaGateway.trim().length > 0;
  const totalSelected = geoIds.size + dnsIds.size + ipsetIds.size;

  const submit = async () => {
    if (!valid) return;
    try {
      if (isEdit && editing) {
        const patch: DirectionUpdate = {
          name: name.trim(),
          awg_client_id: awgClientId,
          via_interface: viaInterface.trim(),
          via_gateway: viaGateway.trim(),
          geo_list_ids: [...geoIds],
          dns_rule_ids: [...dnsIds],
          ipset_group_ids: [...ipsetIds],
          scope,
          scope_target: scope === "container" ? scopeTarget.trim() : null,
          enabled,
        };
        await update.mutateAsync({ directionId: editing.id, patch });
      } else {
        const payload: DirectionCreate = {
          name: name.trim(),
          awg_client_id: awgClientId,
          via_interface: viaInterface.trim(),
          via_gateway: viaGateway.trim(),
          geo_list_ids: [...geoIds],
          dns_rule_ids: [...dnsIds],
          ipset_group_ids: [...ipsetIds],
          scope,
          scope_target: scope === "container" ? scopeTarget.trim() : null,
          enabled,
        };
        await create.mutateAsync(payload);
      }
      onClose();
    } catch {
      // ошибка покажется ниже
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div
        className="modal"
        onClick={(event) => event.stopPropagation()}
        style={{ maxWidth: 720 }}
      >
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="route" size="sm" />{" "}
            {isEdit ? "Редактировать направление" : "Новое направление маршрутизации"}
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>

        <div className="modal-body">
          <div className="field-row">
            <div className="field">
              <label>Название</label>
              <input
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Streaming-NL"
              />
            </div>
            <div className="field">
              <label>Через клиента (VPN-туннель)</label>
              <select
                className="select"
                value={awgClientId ?? ""}
                onChange={(event) =>
                  setAwgClientId(event.target.value === "" ? null : Number(event.target.value))
                }
              >
                <option value="">— без клиента (host route) —</option>
                {awgClients.map((awgClient) => (
                  <option key={awgClient.id} value={awgClient.id}>
                    {awgClient.country ? `${flagFor(awgClient.country)} ` : ""}
                    {awgClient.name}
                    {awgClient.peer_endpoint ? ` · ${awgClient.peer_endpoint}` : ""}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <CheckGroup
            title="GeoIP-зоны"
            icon="globe"
            color="cyan"
            empty="Сначала добавь GeoIP-список на табе «Списки»"
            items={geoLists.map((list) => ({
              id: list.id,
              label: `${flagFor(list.country)} ${list.country} · ${list.name}`,
              ipset_name: `geoip-${list.country.toLowerCase()}`,
              meta: `${list.ipv4_count.toLocaleString()} IPv4`,
            }))}
            selected={geoIds}
            onToggle={(id) =>
              setGeoIds((current) => {
                const next = new Set(current);
                next.has(id) ? next.delete(id) : next.add(id);
                return next;
              })
            }
          />

          <CheckGroup
            title="DNS-правила"
            icon="send"
            color="violet"
            empty="Сначала добавь DNS-правило на табе «Списки»"
            items={dnsRules.map((rule) => ({
              id: rule.id,
              label: rule.name,
              ipset_name: rule.ipset_name,
              meta: `${rule.domains.length} доменов`,
            }))}
            selected={dnsIds}
            onToggle={(id) =>
              setDnsIds((current) => {
                const next = new Set(current);
                next.has(id) ? next.delete(id) : next.add(id);
                return next;
              })
            }
          />

          <CheckGroup
            title="Custom IPset"
            icon="list"
            color="orange"
            empty="Сначала добавь Custom IPset на табе «Списки»"
            items={ipsetGroups.map((group) => ({
              id: group.id,
              label: group.name,
              ipset_name: group.name,
              meta: `${group.cidrs.length} CIDR'ов`,
            }))}
            selected={ipsetIds}
            onToggle={(id) =>
              setIpsetIds((current) => {
                const next = new Set(current);
                next.has(id) ? next.delete(id) : next.add(id);
                return next;
              })
            }
          />

          <div className="hint" style={{ fontSize: 12, marginTop: 8 }}>
            Выбрано <b>{totalSelected}</b> {totalSelected === 1 ? "источник" : "источников"} трафика.
            Все они помечаются одной fwmark и направляются через один и тот же
            VPN-туннель.
          </div>

          <div style={{ marginTop: 8 }}>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              style={{
                cursor: "pointer",
                fontSize: 12,
                color: "var(--text-3)",
                background: "none",
                border: "none",
                padding: "4px 0",
              }}
            >
              {showAdvanced ? "▼" : "▶"} Расширенные параметры (scope, via_interface, via_gateway)
            </button>
            {showAdvanced && (
              <>
                <div className="field" style={{ marginTop: 10 }}>
                  <label>Где применять</label>
                  <div className="tab-switcher">
                    <button
                      className={scope === "host" ? "active" : ""}
                      onClick={() => setScope("host")}
                      type="button"
                    >
                      Хост
                    </button>
                    <button
                      className={scope === "container" ? "active" : ""}
                      onClick={() => setScope("container")}
                      type="button"
                    >
                      Контейнер
                    </button>
                  </div>
                </div>

                {scope === "container" && (
                  <div className="field">
                    <label>Имя контейнера</label>
                    <input
                      className="input"
                      value={scopeTarget}
                      onChange={(event) => setScopeTarget(event.target.value)}
                      placeholder="amnezia-awg2"
                    />
                  </div>
                )}

                <div className="field-row">
                  <div className="field">
                    <label>via_interface</label>
                    <input
                      className="input"
                      value={viaInterface}
                      onChange={(event) => setViaInterface(event.target.value)}
                      placeholder="awg0"
                    />
                  </div>
                  <div className="field">
                    <label>via_gateway</label>
                    <input
                      className="input"
                      value={viaGateway}
                      onChange={(event) => setViaGateway(event.target.value)}
                      placeholder="10.66.66.1"
                    />
                  </div>
                </div>
              </>
            )}
          </div>

          <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <Toggle on={enabled} onClick={() => setEnabled(!enabled)} />
            <label>Включить сразу после создания</label>
          </div>

          {mutation.error && (
            <div
              className="hint"
              style={{ background: "var(--red-tint, #4a1f1f)", color: "var(--red, #ef4444)" }}
            >
              {String(mutation.error)}
            </div>
          )}
        </div>

        <div className="modal-foot">
          <button className="btn ghost" onClick={onClose}>Отмена</button>
          <button
            className="btn primary"
            onClick={submit}
            disabled={!valid || mutation.isPending}
          >
            {mutation.isPending
              ? isEdit ? "Сохраняю…" : "Создаю…"
              : isEdit ? "Сохранить" : "Создать направление"}
          </button>
        </div>
      </div>
    </div>
  );
}

interface CheckItem {
  id: number;
  label: string;
  ipset_name: string;
  meta: string;
}

interface CheckGroupProps {
  title: string;
  icon: "globe" | "send" | "list";
  color: "cyan" | "violet" | "orange";
  empty: string;
  items: CheckItem[];
  selected: Set<number>;
  onToggle: (id: number) => void;
}

function CheckGroup({ title, icon, color, empty, items, selected, onToggle }: CheckGroupProps) {
  const selectedCount = useMemo(
    () => items.filter((item) => selected.has(item.id)).length,
    [items, selected],
  );
  return (
    <div className="field">
      <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <IconTile color={color} icon={icon} size="sm" />
        <span>{title}</span>
        <span className="mono-pill" style={{ marginLeft: "auto" }}>
          {selectedCount} / {items.length}
        </span>
      </label>
      {items.length === 0 ? (
        <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>{empty}</div>
      ) : (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
            gap: 6,
            maxHeight: 180,
            overflowY: "auto",
            padding: 6,
            background: "var(--bg-2)",
            borderRadius: 8,
          }}
        >
          {items.map((item) => {
            const isSelected = selected.has(item.id);
            return (
              <label
                key={item.id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "4px 8px",
                  borderRadius: 6,
                  background: isSelected ? "var(--accent-tint, #2a1a4a)" : "transparent",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => onToggle(item.id)}
                />
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {item.label}
                </span>
                <span className="mono" style={{ fontSize: 10, color: "var(--text-3)" }}>
                  {item.meta}
                </span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}
