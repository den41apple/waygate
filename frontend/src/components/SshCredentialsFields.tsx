import { Icon } from "./Icon";

export type SshAuthMode = "password" | "key";

interface Props {
  sshUser: string;
  onSshUserChange: (value: string) => void;
  sshPort: string;
  onSshPortChange: (value: string) => void;
  authMode: SshAuthMode;
  onAuthModeChange: (value: SshAuthMode) => void;
  sshPassword: string;
  onSshPasswordChange: (value: string) => void;
  sshPrivateKey: string;
  onSshPrivateKeyChange: (value: string) => void;
  /** Включить ✓ возле «Пароль» — у нас уже сохранён cred. */
  hasSavedPassword?: boolean;
  /** То же для ключа. Активирует placeholder вида «••• сохранён». */
  hasSavedPrivateKey?: boolean;
  /** Скрыть `ssh_user`/`ssh_port` (например для AddServerModal, где они выше). */
  hideUserPort?: boolean;
  /** Кнопка «Удалить сохранённые SSH-креды» (только в edit-режиме). */
  onRemoveCreds?: () => void;
  removeBusy?: boolean;
}

/**
 * Общий блок «SSH-креды» — переиспользуется AddServerModal и EditServerModal.
 * Состояние держит родитель (контролируемый компонент), мы только рендерим
 * input'ы + tab-switcher password/key + кнопку удаления сохранённых cred'ов.
 */
export function SshCredentialsFields(props: Props) {
  const {
    sshUser,
    onSshUserChange,
    sshPort,
    onSshPortChange,
    authMode,
    onAuthModeChange,
    sshPassword,
    onSshPasswordChange,
    sshPrivateKey,
    onSshPrivateKeyChange,
    hasSavedPassword = false,
    hasSavedPrivateKey = false,
    hideUserPort = false,
    onRemoveCreds,
    removeBusy = false,
  } = props;

  const passwordPlaceholder = hasSavedPassword
    ? "••••• сохранён, ввести новый чтобы заменить"
    : "введите пароль";
  const keyPlaceholder = hasSavedPrivateKey
    ? "••••• сохранён, ввести новый чтобы заменить"
    : "-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----";

  return (
    <>
      {!hideUserPort && (
        <div className="field-row">
          <div className="field">
            <label>SSH-user</label>
            <input
              className="input"
              value={sshUser}
              onChange={(event) => onSshUserChange(event.target.value)}
              placeholder="root"
            />
          </div>
          <div className="field">
            <label>SSH-port</label>
            <input
              className="input"
              value={sshPort}
              onChange={(event) => onSshPortChange(event.target.value.replace(/[^0-9]/g, ""))}
              placeholder="22"
            />
          </div>
        </div>
      )}

      <div className="field">
        <label>Аутентификация</label>
        <div className="tab-switcher">
          <button
            className={authMode === "password" ? "active" : ""}
            onClick={() => onAuthModeChange("password")}
            type="button"
          >
            Пароль
            {hasSavedPassword && <span style={{ marginLeft: 6, color: "var(--green)" }}>✓</span>}
          </button>
          <button
            className={authMode === "key" ? "active" : ""}
            onClick={() => onAuthModeChange("key")}
            type="button"
          >
            Private key
            {hasSavedPrivateKey && <span style={{ marginLeft: 6, color: "var(--green)" }}>✓</span>}
          </button>
        </div>
      </div>

      {authMode === "password" ? (
        <div className={hideUserPort ? "field-row" : "field"}>
          {hideUserPort && (
            <div className="field">
              <label>SSH-пользователь</label>
              <input
                className="input"
                value={sshUser}
                onChange={(event) => onSshUserChange(event.target.value)}
              />
            </div>
          )}
          <div className="field">
            <label>SSH-пароль</label>
            <input
              className="input"
              type="password"
              value={sshPassword}
              onChange={(event) => onSshPasswordChange(event.target.value)}
              placeholder={passwordPlaceholder}
              autoComplete="new-password"
            />
          </div>
        </div>
      ) : (
        <>
          {hideUserPort && (
            <div className="field">
              <label>SSH-пользователь</label>
              <input
                className="input"
                value={sshUser}
                onChange={(event) => onSshUserChange(event.target.value)}
              />
            </div>
          )}
          <div className="field">
            <label>Private key (PEM)</label>
            <textarea
              className="textarea"
              rows={8}
              value={sshPrivateKey}
              onChange={(event) => onSshPrivateKeyChange(event.target.value)}
              placeholder={keyPlaceholder}
              spellCheck={false}
              style={{ fontFamily: "var(--mono)", fontSize: 11 }}
            />
          </div>
        </>
      )}

      {onRemoveCreds && (hasSavedPassword || hasSavedPrivateKey) && (
        <button
          className="btn ghost"
          onClick={onRemoveCreds}
          disabled={removeBusy}
          type="button"
          style={{ fontSize: 12, color: "var(--red, #ef4444)" }}
        >
          <Icon name="x" size={12} /> Удалить сохранённые SSH-креды
        </button>
      )}
    </>
  );
}
