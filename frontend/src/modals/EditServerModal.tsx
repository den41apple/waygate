import { useState } from "react";

import { useEditServerSettings } from "../api/servers";
import type { ServerSummary } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";

interface Props {
  server: ServerSummary;
  onClose: () => void;
}

/**
 * Редактирование settings уже онбордженного сервера. Меняем только то, что
 * безопасно поменять без переподключения к агенту: имя в sidebar и регион.
 * Host/port/token тут не трогаем — для них либо переонбординг (host/port),
 * либо POST /token/rotate (token).
 */
export function EditServerModal({ server, onClose }: Props) {
  const update = useEditServerSettings();

  const [name, setName] = useState(server.name);
  const [region, setRegion] = useState(server.region ?? "");

  const valid = name.trim().length > 0;

  const submit = async () => {
    if (!valid) return;
    try {
      await update.mutateAsync({
        serverId: server.id,
        patch: {
          name: name.trim(),
          region: region.trim() || null,
        },
      });
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
            <IconTile color="violet" icon="settings" size="sm" /> Настройки сервера
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>
            Меняем только имя в sidebar и регион. Host/port — через переонбординг,
            токен — через «Ротация токена».
          </div>

          <div className="field">
            <label>Имя</label>
            <input
              className="input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="edge-eu"
            />
          </div>

          <div className="field">
            <label>Регион (опционально)</label>
            <input
              className="input"
              value={region}
              onChange={(event) => setRegion(event.target.value)}
              placeholder="EU"
            />
          </div>

          <div className="field">
            <label>Host:port (read-only)</label>
            <input
              className="input"
              value={`${server.host}:${server.port}`}
              disabled
            />
          </div>

          {update.error && (
            <div
              className="hint"
              style={{ background: "var(--red-tint, #4a1f1f)", color: "var(--red, #ef4444)" }}
            >
              {String(update.error)}
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
            {update.isPending ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </div>
    </div>
  );
}
