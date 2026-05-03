import { useState } from "react";

import { useApplyRules, useDeleteRule, useRules, useUpdateRule } from "../api/rules";
import { Icon } from "../components/Icon";
import { AddRuleModal } from "../modals/AddRuleModal";
import { Badge, MonoPill, SectionHead, ViaPill, Toggle, Metric } from "../components/primitives";

interface Props {
  serverId: number;
  awgContainers: string[];
  showSpark: boolean;
}

export function RoutingTab({ serverId, awgContainers, showSpark }: Props) {
  const { data: rules = [], isLoading } = useRules(serverId);
  const updateRule = useUpdateRule(serverId);
  const deleteRule = useDeleteRule(serverId);
  const applyRules = useApplyRules(serverId);
  const [showAdd, setShowAdd] = useState(false);

  const enabledCount = rules.filter((rule) => rule.enabled).length;

  return (
    <>
      <div className="metric-row">
        <Metric label="Активных" value={`${enabledCount}/${rules.length}`} sub="включено" icon="route" tileColor="violet" sparkSeed={3} showSpark={showSpark} />
        <Metric label="Правил всего" value={rules.length} sub="на сервере" icon="list" tileColor="cyan" sparkSeed={7} showSpark={showSpark} />
        <Metric label="Уникальных таблиц" value={new Set(rules.map((rule) => rule.table_id)).size} sub="ip rule lookup" icon="activity" tileColor="green" sparkSeed={11} showSpark={showSpark} />
        <Metric label="fwmark диапазон" value={rules.length === 0 ? "—" : `${Math.min(...rules.map((rule) => rule.fwmark))}–${Math.max(...rules.map((rule) => rule.fwmark))}`} sub="метки пакетов" icon="trending-up" tileColor="orange" sparkSeed={2} showSpark={showSpark} />
      </div>

      <SectionHead title="Правила маршрутизации" count={rules.length}>
        <button
          className="tb-btn primary"
          onClick={() => applyRules.mutate()}
          disabled={applyRules.isPending || rules.length === 0}
        >
          <Icon name="play" size={14} /> {applyRules.isPending ? "Применяю…" : "Применить на агенте"}
        </button>
      </SectionHead>

      {applyRules.data && (
        <div className="hint" style={{ background: "var(--green-tint)", color: "var(--green)" }}>
          применено: {applyRules.data.applied} · skipped: {applyRules.data.skipped}
          {applyRules.data.errors.length > 0 && <> · ошибок: {applyRules.data.errors.length}</>}
        </div>
      )}

      {isLoading && <div className="hint">Загружаю правила…</div>}

      {rules.map((rule) => (
        <div key={rule.id} className="card">
          <div className="rule-head">
            <span className="flag-tile">{flagFor(rule.country)}</span>
            <div className="name-block">
              <div className="name">
                {rule.country} → {rule.via_interface}
                <span className="country mono">[{rule.country}]</span>
              </div>
              <div className="desc">
                ipset <span className="mono">{rule.ipset_name}</span> · fwmark{" "}
                <span className="mono">0x{rule.fwmark.toString(16)}</span>
              </div>
            </div>
            <Badge kind={rule.enabled ? "online" : "offline"}>{rule.enabled ? "active" : "paused"}</Badge>
            <span className="mono-pill">table {rule.table_id}</span>
            <span
              className="mono-pill"
              title={rule.scope === "container" ? `Применяется внутри netns ${rule.scope_target}` : "На уровне хоста"}
              style={rule.scope === "container" ? { background: "var(--accent-tint-2, #2a1a4a)", color: "var(--accent)" } : undefined}
            >
              {rule.scope === "container" ? `🐳 ${rule.scope_target}` : "host"}
            </span>
            <Toggle
              on={rule.enabled}
              onClick={() => updateRule.mutate({ ruleId: rule.id, patch: { enabled: !rule.enabled } })}
            />
            <button
              className="tb-btn"
              style={{ marginLeft: 6 }}
              onClick={() => deleteRule.mutate(rule.id)}
              disabled={deleteRule.isPending}
            >
              <Icon name="x" size={14} />
            </button>
          </div>
          <div className="rule-body">
            <div className="k">match</div>
            <div className="v"><MonoPill accent>ipset:{rule.ipset_name}</MonoPill></div>
            <div className="k">mark</div>
            <div className="v"><MonoPill cyan>fwmark 0x{rule.fwmark.toString(16)}</MonoPill></div>
            <div className="k">via</div>
            <div className="v"><ViaPill>{rule.via_interface} → {rule.via_gateway}</ViaPill></div>
          </div>
        </div>
      ))}

      <button className="add-card" onClick={() => setShowAdd(true)}>
        <Icon name="plus" size={16} /> Добавить правило маршрутизации
      </button>

      {showAdd && (
        <AddRuleModal
          serverId={serverId}
          awgContainers={awgContainers}
          onClose={() => setShowAdd(false)}
        />
      )}
    </>
  );
}

function flagFor(country: string): string {
  if (country.length !== 2) return "🌍";
  const codePoints = [...country.toUpperCase()].map((char) => 0x1f1a5 + char.charCodeAt(0));
  return String.fromCodePoint(...codePoints);
}
