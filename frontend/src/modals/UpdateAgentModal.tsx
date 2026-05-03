import { useState } from "react";

import { useUpdateServer } from "../api/servers";
import type { UpdateServerPayload } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile, Toggle } from "../components/primitives";

interface Props {
  serverId: number;
  currentVersion: string;
  onClose: () => void;
}

const DEFAULT_WHEEL_URL =
  "https://github.com/den41apple/waygate/releases/latest/download/waygate_agent-py3-none-any.whl";

export function UpdateAgentModal({ serverId, currentVersion, onClose }: Props) {
  const update = useUpdateServer();

  const [version, setVersion] = useState("");
  const [wheelUrl, setWheelUrl] = useState(DEFAULT_WHEEL_URL);
  const [waitForReconnect, setWaitForReconnect] = useState(true);

  const valid = version.trim().length > 0 && wheelUrl.trim().length > 0;

  const submit = async () => {
    if (!valid) return;
    const payload: UpdateServerPayload = {
      version: version.trim(),
      wheel_url: wheelUrl.trim(),
      wait_for_reconnect: waitForReconnect,
    };
    try {
      await update.mutateAsync({ serverId, payload });
      onClose();
    } catch {
      // отрисуется ниже
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="download" size="sm" /> Обновить агент
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="hint" style={{ fontSize: 12 }}>
            Текущая версия: <span className="mono">{currentVersion || "—"}</span>
          </div>

          <div className="field">
            <label>Целевая версия</label>
            <input
              className="input"
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              placeholder="0.1.2"
            />
          </div>

          <div className="field">
            <label>URL wheel-файла</label>
            <input
              className="input"
              value={wheelUrl}
              onChange={(event) => setWheelUrl(event.target.value)}
            />
            <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>
              По умолчанию — последний релиз агента из GitHub.
            </div>
          </div>

          <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <Toggle on={waitForReconnect} onClick={() => setWaitForReconnect(!waitForReconnect)} />
            <label>Дождаться, пока агент переподключится с новой версией</label>
          </div>

          {update.error && (
            <div
              className="hint"
              style={{ background: "var(--red-tint, #4a1f1f)", color: "var(--red, #ef4444)" }}
            >
              {String(update.error)}
            </div>
          )}
          {update.data && (
            <div className="hint" style={{ background: "var(--green-tint)", color: "var(--green)" }}>
              запущено · prev: {update.data.previous_version} · status: {update.data.status}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={onClose}>Отмена</button>
          <button
            className="btn primary"
            onClick={submit}
            disabled={!valid || update.isPending}
          >
            {update.isPending ? "Обновляю…" : "Обновить"}
          </button>
        </div>
      </div>
    </div>
  );
}
