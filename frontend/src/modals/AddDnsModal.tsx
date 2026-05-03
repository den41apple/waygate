import { useEffect, useState } from "react";

import { useCreateDnsRule, useUpdateDnsRule, type DnsRuleCreate } from "../api/dns";
import type { DnsRule } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  editing?: DnsRule;
  onClose: () => void;
}

const IPSET_NAME_RE = /^[a-zA-Z0-9_-]{1,31}$/;

// Чистим имя от не-ipset-friendly символов: оставляем a-z 0-9 дефис.
function sanitizeForIpset(raw: string): string {
  return raw
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 24); // 24 + "dns-" (4) = 28 < 31 (max ipset name)
}

export function AddDnsModal({ serverId, editing, onClose }: Props) {
  const create = useCreateDnsRule(serverId);
  const update = useUpdateDnsRule(serverId);
  const isEdit = editing !== undefined;
  const mutation = isEdit ? update : create;

  const [name, setName] = useState(editing?.name ?? "");
  const [domainsRaw, setDomainsRaw] = useState(editing?.domains.join("\n") ?? "");
  const [ipsetName, setIpsetName] = useState(editing?.ipset_name ?? "");
  // В edit-режиме сразу считаем что ipset уже «touched», чтобы не сбрасывать
  // существующее значение при первом рендере (если name не менялось).
  const [ipsetTouched, setIpsetTouched] = useState(isEdit);
  const [enabled, setEnabled] = useState(editing?.enabled ?? true);

  // Auto-suggest `dns-<sanitized-name>` пока пользователь не редактирует ipset вручную.
  // Это закрывает 95% случаев и устраняет необходимость думать про consistency
  // между DNS-правилом и Routing-правилом.
  useEffect(() => {
    if (ipsetTouched) return;
    const suggestion = name.trim() ? `dns-${sanitizeForIpset(name)}` : "";
    setIpsetName(suggestion);
  }, [name, ipsetTouched]);

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
      if (isEdit && editing) {
        await update.mutateAsync({ ruleId: editing.id, patch: payload });
      } else {
        await create.mutateAsync(payload);
      }
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
            <IconTile color="violet" icon="send" size="sm" />{" "}
            {isEdit ? "Редактировать DNS-правило" : "Добавить DNS-правило"}
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
                onChange={(event) => {
                  setIpsetName(event.target.value);
                  setIpsetTouched(true);
                }}
                placeholder="dns-streaming-eu"
              />
              <div className="hint" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 4 }}>
                Имя ipset, которое потом нужно указать в Routing-правиле:
                dnsmasq резолвит домены и кладёт IP в этот set, а правило
                маршрутизации помечает пакеты с dst в нём. Заполняется
                автоматически из имени группы — поправь, если хочешь иначе.
              </div>
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
              : isEdit ? "Сохранить" : "Создать"}
          </button>
        </div>
      </div>
    </div>
  );
}
