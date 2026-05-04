import { useState } from "react";

import { useEditServerSettings, type ServerSettingsPatch } from "../api/servers";
import type { ServerSummary } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";
import { type SshAuthMode, SshCredentialsFields } from "../components/SshCredentialsFields";

interface Props {
  server: ServerSummary;
  onClose: () => void;
}

/**
 * Редактирование settings уже онбордженного сервера. Меняем имя/регион +
 * SSH-доступ для server-side агентских операций (re-update). Host/port не
 * трогаем (для них переонбординг), token — через POST /token/rotate.
 */
export function EditServerModal({ server, onClose }: Props) {
  const update = useEditServerSettings();

  const [name, setName] = useState(server.name);
  const [region, setRegion] = useState(server.region ?? "");

  const [sshUser, setSshUser] = useState(server.ssh_user);
  const [sshPort, setSshPort] = useState(String(server.ssh_port));
  const [authMode, setAuthMode] = useState<SshAuthMode>(
    server.has_ssh_private_key ? "key" : "password",
  );
  // Plaintext-поля. Если уже сохранено — placeholder показывает «••• сохранено».
  // Пустая строка отправляется в бэк как «удалить cred» только если пользователь
  // явно нажмёт кнопку «Удалить». Случайно пустое поле не должно очищать.
  const [sshPassword, setSshPassword] = useState("");
  const [sshPrivateKey, setSshPrivateKey] = useState("");

  const valid = name.trim().length > 0 && /^[0-9]+$/.test(sshPort) && sshUser.trim().length > 0;

  const buildPatch = (extra: Partial<ServerSettingsPatch> = {}): ServerSettingsPatch => {
    const patch: ServerSettingsPatch = {
      name: name.trim(),
      region: region.trim() || null,
      ssh_user: sshUser.trim(),
      ssh_port: Number(sshPort),
      ...extra,
    };
    // Креды передаём только если пользователь явно ввёл новое значение —
    // отправлять `""` будет означать «удалить» и убьёт сохранённый cred.
    if (extra.ssh_password === undefined && sshPassword.length > 0) {
      patch.ssh_password = sshPassword;
    }
    if (extra.ssh_private_key === undefined && sshPrivateKey.length > 0) {
      patch.ssh_private_key = sshPrivateKey;
    }
    return patch;
  };

  const submit = async () => {
    if (!valid) return;
    try {
      await update.mutateAsync({ serverId: server.id, patch: buildPatch() });
      onClose();
    } catch {
      // ошибка ниже
    }
  };

  const removeAllCreds = async () => {
    if (!window.confirm("Удалить сохранённые SSH-креды? После этого update будет идти через self-update fallback.")) {
      return;
    }
    try {
      await update.mutateAsync({
        serverId: server.id,
        patch: buildPatch({ ssh_password: "", ssh_private_key: "" }),
      });
      setSshPassword("");
      setSshPrivateKey("");
    } catch {
      // ошибка ниже
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div
        className="modal"
        onClick={(event) => event.stopPropagation()}
        style={{ maxWidth: 600 }}
      >
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="settings" size="sm" /> Настройки сервера
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
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

          <div
            style={{
              borderTop: "1px solid var(--border)",
              marginTop: 12,
              paddingTop: 12,
            }}
          >
            <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
              SSH-доступ
            </div>
            <div className="hint" style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 10 }}>
              Нужны для server-side обновления агента (обходит self-update,
              который упирается в ProtectSystem=strict /opt). Шифруются Fernet'ом
              поверх SECRET_KEY на стороне control-plane.
            </div>

            <SshCredentialsFields
              sshUser={sshUser}
              onSshUserChange={setSshUser}
              sshPort={sshPort}
              onSshPortChange={setSshPort}
              authMode={authMode}
              onAuthModeChange={setAuthMode}
              sshPassword={sshPassword}
              onSshPasswordChange={setSshPassword}
              sshPrivateKey={sshPrivateKey}
              onSshPrivateKeyChange={setSshPrivateKey}
              hasSavedPassword={server.has_ssh_password}
              hasSavedPrivateKey={server.has_ssh_private_key}
              onRemoveCreds={removeAllCreds}
              removeBusy={update.isPending}
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
