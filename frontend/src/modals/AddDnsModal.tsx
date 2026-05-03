import { useState } from "react";

import { useCreateDnsRule, type DnsRuleCreate } from "../api/dns";
import { Icon } from "../components/Icon";
import { IconTile, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  onClose: () => void;
}

const IPSET_NAME_RE = /^[a-zA-Z0-9_-]{1,31}$/;

export function AddDnsModal({ serverId, onClose }: Props) {
  const create = useCreateDnsRule(serverId);

  const [name, setName] = useState("");
  const [domainsRaw, setDomainsRaw] = useState("");
  const [ipsetName, setIpsetName] = useState("");
  const [enabled, setEnabled] = useState(true);

  const domains = domainsRaw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  const valid =
    name.trim().length > 0
    && domains.length > 0
    && IPSET_NAME_RE.test(ipsetName);

  const submit = async () => {
    if (!valid) return;
    const payload: DnsRuleCreate = {
      name: name.trim(),
      domains,
      ipset_name: ipsetName.trim(),
      enabled,
    };
    try {
      await create.mutateAsync(payload);
      onClose();
    } catch {
      // ошибка покажется ниже
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="send" size="sm" /> Добавить DNS-правило
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field-row">
            <div className="field">
              <label>Название группы</label>
              <input
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="streaming-eu"
              />
            </div>
            <div className="field">
              <label>ipset</label>
              <input
                className="input"
                value={ipsetName}
                onChange={(event) => setIpsetName(event.target.value)}
                placeholder="dns-streaming-eu"
              />
            </div>
          </div>

          <div className="field">
            <label>Домены (по одному на строку, можно с * для wildcard)</label>
            <textarea
              className="textarea"
              rows={6}
              value={domainsRaw}
              onChange={(event) => setDomainsRaw(event.target.value)}
              placeholder={"netflix.com\n*.nflxvideo.net\nyoutube.com"}
            />
            <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>
              {domains.length} доменов будет создано
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
