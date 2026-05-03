import { useState } from "react";

import { useCreateIpsetGroup } from "../api/ipsetGroups";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";

interface Props {
  serverId: number;
  onClose: () => void;
}

const NAME_RE = /^[a-zA-Z0-9_-]{1,31}$/;
// CIDR-like: IPv4 [/mask] (мягкая валидация на клиенте; финальная — на agent
// при `ipset restore`).
const CIDR_RE = /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(\/\d{1,2})?$/;

export function AddCustomIpsetModal({ serverId, onClose }: Props) {
  const create = useCreateIpsetGroup(serverId);

  const [name, setName] = useState("");
  const [cidrsRaw, setCidrsRaw] = useState("");

  const cidrs = cidrsRaw
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);

  const invalidLines = cidrs.filter((line) => !CIDR_RE.test(line));
  const valid = NAME_RE.test(name) && cidrs.length > 0 && invalidLines.length === 0;

  const submit = async () => {
    if (!valid) return;
    try {
      await create.mutateAsync({ name: name.trim(), cidrs });
      onClose();
    } catch {
      // ошибка ниже
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="list" size="sm" /> Custom IPset (свой список CIDR'ов)
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label>Имя ipset</label>
            <input
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="custom-vpn"
              maxLength={31}
            />
            <div className="hint" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 4 }}>
              Используется для привязки в Routing-правилах. До 31 символа: a-z, 0-9, _ -.
            </div>
          </div>

          <div className="field">
            <label>CIDR'ы (по одному на строку)</label>
            <textarea
              className="textarea"
              rows={10}
              value={cidrsRaw}
              onChange={(event) => setCidrsRaw(event.target.value)}
              placeholder={"1.2.3.4\n10.0.0.0/8\n192.168.1.0/24"}
              spellCheck={false}
              style={{ fontFamily: "var(--mono)", fontSize: 12 }}
            />
            <div className="hint" style={{ fontSize: 11, color: "var(--text-3)", marginTop: 4 }}>
              {cidrs.length} CIDR'ов будет создано
              {invalidLines.length > 0 && (
                <span style={{ color: "var(--red, #ef4444)" }}>
                  {" "}· {invalidLines.length} невалидных: {invalidLines.slice(0, 3).join(", ")}
                  {invalidLines.length > 3 ? "…" : ""}
                </span>
              )}
            </div>
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
