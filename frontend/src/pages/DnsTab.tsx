import { useState } from "react";

import { useApplyDns, useDeleteDnsRule, useDnsRules, useUpdateDnsRule } from "../api/dns";
import type { DnsRule } from "../api/types";
import { Icon } from "../components/Icon";
import { AddDnsModal } from "../modals/AddDnsModal";
import { Badge, IconTile, Metric, MonoPill, SectionHead, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  showSpark: boolean;
}

export function DnsTab({ serverId, showSpark }: Props) {
  const { data: rules = [], isLoading } = useDnsRules(serverId);
  const updateRule = useUpdateDnsRule(serverId);
  const deleteRule = useDeleteDnsRule(serverId);
  const applyDns = useApplyDns(serverId);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState<DnsRule | null>(null);

  const totalDomains = rules.reduce((acc, rule) => acc + rule.domains.length, 0);

  return (
    <>
      <div className="hint">
        <div className="h-title"><Icon name="globe" size={14} /> Как работает DNS-маршрутизация</div>
        <ol>
          <li>dnsmasq сопоставляет запрос с правилами доменов</li>
          <li>результаты A/AAAA пишутся в ipset правила</li>
          <li>iptables помечает трафик к этим IP через fwmark</li>
          <li>ip rule направляет помеченный трафик в выбранный туннель</li>
        </ol>
      </div>

      <div className="metric-row">
        <Metric label="DNS-правил" value={rules.length} sub="активных групп" icon="globe" tileColor="violet" sparkSeed={4} showSpark={showSpark} />
        <Metric label="Домены" value={totalDomains} sub="паттернов всего" icon="list" tileColor="cyan" sparkSeed={8} showSpark={showSpark} />
        <Metric label="Активных" value={rules.filter((rule) => rule.enabled).length} sub="из всего" icon="check" tileColor="green" sparkSeed={12} showSpark={showSpark} />
        <Metric label="Сервер" value={isLoading ? "…" : "ready"} sub={`server_id=${serverId}`} icon="server" tileColor="orange" sparkSeed={16} showSpark={showSpark} />
      </div>

      <SectionHead title="Группы DNS-маршрутов" count={rules.length}>
        <button
          className="tb-btn primary"
          onClick={() => applyDns.mutate()}
          disabled={applyDns.isPending || rules.length === 0}
        >
          <Icon name="play" size={14} /> {applyDns.isPending ? "Применяю…" : "Применить"}
        </button>
      </SectionHead>

      {applyDns.data && (
        <div className="hint" style={{ background: "var(--green-tint)", color: "var(--green)" }}>
          применено: {applyDns.data.applied}
          {applyDns.data.errors.length > 0 && <> · ошибок: {applyDns.data.errors.length}</>}
        </div>
      )}

      {rules.map((rule) => (
        <div key={rule.id} className="card">
          <div className="dns-head">
            <IconTile color={rule.enabled ? "violet" : "bg"} icon="globe" />
            <span className="name">{rule.name}</span>
            <Badge kind={rule.enabled ? "online" : "offline"}>{rule.enabled ? "active" : "paused"}</Badge>
            <span className="mono-pill">{rule.domains.length} доменов</span>
            <Toggle
              on={rule.enabled}
              onClick={() => updateRule.mutate({ ruleId: rule.id, patch: { enabled: !rule.enabled } })}
            />
            <button
              className="tb-btn"
              style={{ marginLeft: 6 }}
              onClick={() => setEditing(rule)}
              title="Редактировать"
            >
              <Icon name="edit" size={14} />
            </button>
            <button
              className="tb-btn"
              onClick={() => deleteRule.mutate(rule.id)}
              disabled={deleteRule.isPending}
              title="Удалить"
            >
              <Icon name="x" size={14} />
            </button>
          </div>
          <div className="rule-body">
            <div className="k">domains</div>
            <div className="v cidrs">
              {rule.domains.map((domain) => (
                <span key={domain} className="domain-pill">{domain}</span>
              ))}
            </div>
            <div className="k">ipset</div>
            <div className="v"><MonoPill accent>{rule.ipset_name}</MonoPill></div>
          </div>
        </div>
      ))}

      <button className="add-card" onClick={() => setShowAdd(true)}>
        <Icon name="plus" size={16} /> Добавить DNS-правило
      </button>

      {showAdd && <AddDnsModal serverId={serverId} onClose={() => setShowAdd(false)} />}
      {editing && (
        <AddDnsModal
          serverId={serverId}
          editing={editing}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}
