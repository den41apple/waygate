import { useMemo, useState } from "react";

import { useAwgClients } from "../api/awgClients";
import {
  useDeleteDirection,
  useDirections,
  useUpdateDirection,
} from "../api/directions";
import { useDnsRules } from "../api/dns";
import { useGeoIpLists } from "../api/geoip";
import { useIpsetGroups } from "../api/ipsetGroups";
import { useApplyRules } from "../api/rules";
import type { AwgClient, Direction, DnsRule, GeoList, IpsetGroup } from "../api/types";
import { flagFor } from "../components/CountrySelect";
import { Icon } from "../components/Icon";
import { AddRoutingDirectionModal } from "../modals/AddRoutingDirectionModal";
import { Badge, IconTile, Metric, MonoPill, SectionHead, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  awgContainers: string[];
  showSpark: boolean;
}

export function RoutingTab({ serverId, showSpark }: Props) {
  const { data: directions = [], isLoading } = useDirections(serverId);
  const { data: awgClients = [] } = useAwgClients(serverId);
  // Бейджи на DirectionCard'ах резолвят geo/dns/ipset/awgClient'ы по id —
  // подгружаем все списки один раз на уровне RoutingTab и прокидываем в виде
  // Map'ов. Раньше каждая карточка дёргала эти 4 хука сама → 80 hook calls
  // на 20 direction'ов (BACKLOG B4).
  const { data: geoLists = [] } = useGeoIpLists();
  const { data: dnsRules = [] } = useDnsRules(serverId);
  const { data: ipsetGroups = [] } = useIpsetGroups(serverId);
  const updateDirection = useUpdateDirection(serverId);
  const deleteDirection = useDeleteDirection(serverId);
  const applyRules = useApplyRules(serverId);

  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<Direction | null>(null);

  // Pre-computed lookups для карточек.
  const geoMap = useMemo(() => new Map(geoLists.map((g) => [g.id, g])), [geoLists]);
  const dnsMap = useMemo(() => new Map(dnsRules.map((d) => [d.id, d])), [dnsRules]);
  const ipsetMap = useMemo(() => new Map(ipsetGroups.map((g) => [g.id, g])), [ipsetGroups]);
  const awgClientMap = useMemo(() => new Map(awgClients.map((c) => [c.id, c])), [awgClients]);

  // Группировка direction'ов по awg_client
  const grouped = useMemo(() => {
    const map = new Map<number | "host", Direction[]>();
    for (const direction of directions) {
      const key = direction.awg_client_id ?? "host";
      const list = map.get(key) ?? [];
      list.push(direction);
      map.set(key, list);
    }
    return map;
  }, [directions]);

  const enabledCount = directions.filter((direction) => direction.enabled).length;
  const totalSources = directions.reduce(
    (acc, direction) =>
      acc + direction.geo_list_ids.length + direction.dns_rule_ids.length + direction.ipset_group_ids.length,
    0,
  );

  return (
    <>
      <div className="metric-row">
        <Metric
          label="Активных"
          value={`${enabledCount}/${directions.length}`}
          sub="включено"
          icon="route"
          tileColor="violet"
          sparkSeed={3}
          showSpark={showSpark}
        />
        <Metric
          label="Направлений"
          value={directions.length}
          sub="всего на сервере"
          icon="list"
          tileColor="cyan"
          sparkSeed={7}
          showSpark={showSpark}
        />
        <Metric
          label="Источников трафика"
          value={totalSources}
          sub="GeoIP + DNS + IPset"
          icon="activity"
          tileColor="green"
          sparkSeed={11}
          showSpark={showSpark}
        />
        <Metric
          label="VPN-клиентов"
          value={awgClients.filter((awgClient) => awgClient.status === "running").length}
          sub="доступно для маршрутизации"
          icon="tunnel"
          tileColor="orange"
          sparkSeed={2}
          showSpark={showSpark}
        />
      </div>

      <SectionHead title="Маршрутные направления" count={directions.length}>
        <button
          className="tb-btn"
          onClick={() => setShowAdd(true)}
        >
          <Icon name="plus" size={14} /> Добавить направление
        </button>
        <button
          className="tb-btn primary"
          onClick={() => applyRules.mutate()}
          disabled={applyRules.isPending || directions.length === 0}
          style={{ marginLeft: 8 }}
        >
          <Icon name="play" size={14} /> {applyRules.isPending ? "Применяю…" : "Применить на агенте"}
        </button>
      </SectionHead>

      {applyRules.data && (
        <div
          className="hint"
          style={
            applyRules.data.errors.length > 0
              ? { background: "var(--red-tint, #4a1f1f)", color: "var(--red, #ef4444)" }
              : { background: "var(--green-tint)", color: "var(--green)" }
          }
        >
          применено: {applyRules.data.applied} · skipped: {applyRules.data.skipped}
          {applyRules.data.errors.length > 0 && (
            <>
              {" · "}ошибок: {applyRules.data.errors.length}
              <ul style={{ margin: "6px 0 0", paddingLeft: 20, fontFamily: "var(--mono)", fontSize: 11 }}>
                {applyRules.data.errors.map((error, idx) => (
                  <li key={idx}>{error}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}

      {isLoading && <div className="hint">Загружаю направления…</div>}

      {!isLoading && directions.length === 0 && (
        <div className="hint">
          Направлений пока нет. Создай первое — выбери VPN-клиента, поставь
          галочки на нужные GeoIP-зоны / DNS-правила / IPset-группы, и весь
          этот трафик пойдёт через выбранный туннель.
        </div>
      )}

      {[...grouped.entries()].map(([key, list]) => {
        const awgClient = key === "host" ? null : awgClientMap.get(key) ?? null;
        return (
          <DirectionGroup
            key={String(key)}
            awgClient={awgClient}
            directions={list}
            geoMap={geoMap}
            dnsMap={dnsMap}
            ipsetMap={ipsetMap}
            awgClientMap={awgClientMap}
            onToggle={(direction) =>
              updateDirection.mutate({
                directionId: direction.id,
                patch: { enabled: !direction.enabled },
              })
            }
            onEdit={(direction) => setEditing(direction)}
            onDelete={(direction) => {
              if (window.confirm(`Удалить направление «${direction.name}»?`)) {
                deleteDirection.mutate(direction.id);
              }
            }}
          />
        );
      })}

      {showAdd && (
        <AddRoutingDirectionModal serverId={serverId} onClose={() => setShowAdd(false)} />
      )}
      {editing && (
        <AddRoutingDirectionModal
          serverId={serverId}
          editing={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}

interface GroupProps {
  awgClient: AwgClient | null;
  directions: Direction[];
  geoMap: Map<number, GeoList>;
  dnsMap: Map<number, DnsRule>;
  ipsetMap: Map<number, IpsetGroup>;
  awgClientMap: Map<number, AwgClient>;
  onToggle: (direction: Direction) => void;
  onEdit: (direction: Direction) => void;
  onDelete: (direction: Direction) => void;
}

function DirectionGroup({
  awgClient,
  directions,
  geoMap,
  dnsMap,
  ipsetMap,
  awgClientMap,
  onToggle,
  onEdit,
  onDelete,
}: GroupProps) {
  return (
    <div style={{ marginBottom: 16 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
          fontSize: 13,
          color: "var(--text-2)",
        }}
      >
        {awgClient ? (
          <>
            <IconTile color="green" icon="tunnel" size="sm" />
            <b>
              {awgClient.country ? `${flagFor(awgClient.country)} ` : ""}
              {awgClient.name}
            </b>
            <span className="mono" style={{ color: "var(--text-3)" }}>
              {awgClient.peer_endpoint ?? "—"}
            </span>
          </>
        ) : (
          <>
            <IconTile color="bg" icon="server" size="sm" />
            <b>Без VPN-клиента</b>
            <span style={{ color: "var(--text-3)" }}>(host-route без туннеля)</span>
          </>
        )}
      </div>
      {directions.map((direction) => (
        <DirectionCard
          key={direction.id}
          direction={direction}
          geoMap={geoMap}
          dnsMap={dnsMap}
          ipsetMap={ipsetMap}
          awgClientMap={awgClientMap}
          onToggle={() => onToggle(direction)}
          onEdit={() => onEdit(direction)}
          onDelete={() => onDelete(direction)}
        />
      ))}
    </div>
  );
}

interface CardProps {
  direction: Direction;
  geoMap: Map<number, GeoList>;
  dnsMap: Map<number, DnsRule>;
  ipsetMap: Map<number, IpsetGroup>;
  awgClientMap: Map<number, AwgClient>;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
}

function DirectionCard({
  direction,
  geoMap,
  dnsMap,
  ipsetMap,
  awgClientMap,
  onToggle,
  onEdit,
  onDelete,
}: CardProps) {
  const geoBadges = direction.geo_list_ids.map((id) => geoMap.get(id)).filter(Boolean);
  const dnsBadges = direction.dns_rule_ids.map((id) => dnsMap.get(id)).filter(Boolean);
  const ipsetBadges = direction.ipset_group_ids.map((id) => ipsetMap.get(id)).filter(Boolean);

  // Integrity-check: awg_client_id ссылается на существующий running клиент?
  // Иначе при apply'е агент вернёт `Cannot find device awg-X` (юзер уже ловил
  // это после удаления firstbyte клиента, direction остался ссылаться).
  const awgClient = direction.awg_client_id !== null ? awgClientMap.get(direction.awg_client_id) : null;
  const clientMissing = direction.awg_client_id !== null && awgClient === undefined;
  const clientNotRunning = awgClient !== undefined && awgClient !== null && awgClient.status !== "running";

  return (
    <div className="card" style={{ marginBottom: 8 }}>
      <div className="rule-head">
        <IconTile color={direction.enabled ? "violet" : "bg"} icon="route" />
        <div className="name-block">
          <div className="name">{direction.name}</div>
          <div className="desc">
            via <span className="mono">{direction.via_interface}</span> →{" "}
            <span className="mono">{direction.via_gateway}</span>
            {direction.scope === "container" && direction.scope_target && (
              <> · 🐳 <span className="mono">{direction.scope_target}</span></>
            )}
          </div>
          {clientMissing && (
            <div
              style={{
                fontSize: 11,
                color: "var(--red, #ef4444)",
                background: "var(--red-tint, #4a1f1f)",
                padding: "4px 6px",
                borderRadius: 4,
                marginTop: 4,
              }}
            >
              ✗ AWG-клиент удалён или не существует. Apply вернёт "Cannot find device".
              Отредактируй direction → выбери существующего клиента.
            </div>
          )}
          {clientNotRunning && (
            <div
              style={{
                fontSize: 11,
                color: "var(--amber, #f59e0b)",
                background: "var(--amber-tint, #3a2a1a)",
                padding: "4px 6px",
                borderRadius: 4,
                marginTop: 4,
              }}
            >
              ⚠️ AWG-клиент в статусе "{awgClient?.status}". Запусти его перед apply.
            </div>
          )}
        </div>
        <Badge kind={direction.enabled ? "online" : "offline"}>
          {direction.enabled ? "active" : "paused"}
        </Badge>
        <Toggle on={direction.enabled} onClick={onToggle} />
        <button
          className="tb-btn"
          style={{ marginLeft: 6 }}
          onClick={onEdit}
          title="Редактировать"
        >
          <Icon name="edit" size={14} />
        </button>
        <button
          className="tb-btn"
          onClick={onDelete}
          title="Удалить"
        >
          <Icon name="x" size={14} />
        </button>
      </div>

      <div className="rule-body">
        <div className="k">GeoIP</div>
        <div className="v cidrs">
          {geoBadges.length === 0 ? (
            <span style={{ color: "var(--text-4)" }}>—</span>
          ) : (
            geoBadges.map((geo) =>
              geo ? (
                <MonoPill key={geo.id} cyan>
                  {flagFor(geo.country)} {geo.country}
                </MonoPill>
              ) : null,
            )
          )}
        </div>
        <div className="k">DNS</div>
        <div className="v cidrs">
          {dnsBadges.length === 0 ? (
            <span style={{ color: "var(--text-4)" }}>—</span>
          ) : (
            dnsBadges.map((dns) =>
              dns ? <MonoPill key={dns.id} accent>{dns.name}</MonoPill> : null,
            )
          )}
        </div>
        <div className="k">IPset</div>
        <div className="v cidrs">
          {ipsetBadges.length === 0 ? (
            <span style={{ color: "var(--text-4)" }}>—</span>
          ) : (
            ipsetBadges.map((group) =>
              group ? (
                <MonoPill key={group.id} green>
                  {group.name} ({group.cidrs.length})
                </MonoPill>
              ) : null,
            )
          )}
        </div>
      </div>
    </div>
  );
}
