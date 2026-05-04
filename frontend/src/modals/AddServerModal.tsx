import { useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import { useProvisionServer } from "../api/servers";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";
import { type SshAuthMode, SshCredentialsFields } from "../components/SshCredentialsFields";
import { useAuthStore } from "../store/auth";

interface Props {
  onClose: () => void;
}

interface FormState {
  host: string;
  ssh_port: string;
  ssh_user: string;
  ssh_password: string;
  ssh_private_key: string;
  name: string;
  region: string;
  agent_port: string;
  authMode: SshAuthMode;
  save_ssh_credentials: boolean;
}

interface LogLine {
  type: string;
  message: string;
  timestamp: string;
}

const STEPS = [
  { id: "connect", label: "Подключение" },
  { id: "stream", label: "Установка" },
  { id: "done", label: "Готово" },
];

function Stepper({ idx }: { idx: number }) {
  return (
    <div className="stepper">
      {STEPS.map((step, index) => (
        <div key={step.id} className={`step ${index === idx ? "active" : index < idx ? "done" : ""}`}>
          <span className="num">{index < idx ? <Icon name="check" size={14} /> : index + 1}</span>
          <span>{step.label}</span>
        </div>
      ))}
    </div>
  );
}

export function AddServerModal({ onClose }: Props) {
  const [step, setStep] = useState(0);
  const [form, setForm] = useState<FormState>({
    host: "",
    ssh_port: "22",
    ssh_user: "root",
    ssh_password: "",
    ssh_private_key: "",
    name: "",
    region: "",
    agent_port: "7743",
    authMode: "password",
    save_ssh_credentials: true,
  });
  const [serverId, setServerId] = useState<number | null>(null);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [terminal, setTerminal] = useState<"running" | "done" | "error">("running");
  const sourceRef = useRef<EventSource | null>(null);
  const provisionServer = useProvisionServer();

  useEffect(() => {
    return () => {
      sourceRef.current?.close();
    };
  }, []);

  const update = (patch: Partial<FormState>) => setForm((prev) => ({ ...prev, ...patch }));

  const resetStream = () => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setLines([]);
    setTerminal("running");
    setServerId(null);
  };

  const startProvision = async () => {
    resetStream();
    try {
      const result = await provisionServer.mutateAsync({
        host: form.host,
        ssh_port: Number(form.ssh_port),
        ssh_user: form.ssh_user,
        ssh_password: form.authMode === "password" ? form.ssh_password : null,
        ssh_private_key: form.authMode === "key" ? form.ssh_private_key : null,
        name: form.name || form.host,
        region: form.region || null,
        agent_port: Number(form.agent_port),
        save_ssh_credentials: form.save_ssh_credentials,
      });
      setServerId(result.id);
      setStep(1);
      // EventSource не умеет custom headers, поэтому передаём JWT в query-параметре.
      // require_user читает ?access_token как fallback.
      const accessToken = useAuthStore.getState().token ?? "";
      const url = `/api/v1/servers/${result.id}/provision/stream?access_token=${encodeURIComponent(accessToken)}`;
      const source = new EventSource(url);
      sourceRef.current = source;
      source.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data) as LogLine | { type: "end" };
          if ("type" in parsed && parsed.type === "end") {
            // Stream-end НЕ значит «успех». Переходим на финальный экран только
            // если последний событием был `done`. Иначе остаёмся на логе с error.
            source.close();
            setTerminal((current) => {
              if (current === "done") setStep(2);
              return current;
            });
            return;
          }
          const line = parsed as LogLine;
          setLines((current) => [...current, line]);
          if (line.type === "error") setTerminal("error");
          if (line.type === "done") setTerminal("done");
        } catch (error) {
          console.warn("provision-stream: не парсится:", error, event.data);
        }
      };
      source.onerror = () => {
        source.close();
      };
    } catch (error) {
      setTerminal("error");
      setLines((current) => [
        ...current,
        { type: "error", message: String(error), timestamp: new Date().toISOString() },
      ]);
    }
  };

  const retry = async () => {
    // Сносим failed-сервер чтобы UI не накапливал хлам. Best-effort — если
    // упало, просто продолжаем (provision создаст новый Server-record).
    if (serverId !== null) {
      try {
        await api.delete<void>(`/servers/${serverId}`);
      } catch (error) {
        console.warn("retry: не удалось удалить failed-сервер:", error);
      }
    }
    await startProvision();
  };

  const goBackToForm = () => {
    sourceRef.current?.close();
    sourceRef.current = null;
    setLines([]);
    setTerminal("running");
    setServerId(null);
    setStep(0);
  };

  const handleClose = () => {
    sourceRef.current?.close();
    onClose();
  };

  const ts = (iso: string) => new Date(iso).toTimeString().slice(0, 8);

  const statusBadge = (() => {
    if (terminal === "error") {
      return <span style={{ color: "var(--red, #ef4444)", fontSize: 12 }}>● ошибка</span>;
    }
    if (terminal === "done") {
      return <span style={{ color: "var(--green)", fontSize: 12 }}>● готово</span>;
    }
    return <span style={{ color: "var(--accent)", fontSize: 12 }}>● установка идёт</span>;
  })();

  return (
    <div className="modal-veil" onClick={handleClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title"><IconTile color="violet" icon="server" size="sm" /> Новый сервер</div>
          <button className="close" onClick={handleClose}><Icon name="x" size={16} /></button>
        </div>
        <Stepper idx={step} />
        <div className="modal-body">
          {step === 0 && (
            <ConnectStep form={form} update={update} disabled={provisionServer.isPending} />
          )}
          {step === 1 && (
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
                <div style={{ fontSize: 12.5, color: "var(--text-2)" }}>
                  Онбординг сервера{" "}
                  <span className="mono" style={{ color: "var(--accent)" }}>{form.host}</span>
                  {serverId && <> (id={serverId})</>}
                </div>
                {statusBadge}
              </div>
              <div className="log">
                {lines.map((line, index) => (
                  <div key={index} className="log-line">
                    <span className="ts">[{ts(line.timestamp)}]</span>
                    <span className={`icon ${line.type === "error" ? "fail" : line.type === "done" ? "ok" : "run"}`}>
                      {line.type === "error" ? "✗" : line.type === "done" ? "✓" : "↻"}
                    </span>
                    <span className="msg">{line.message}</span>
                  </div>
                ))}
                {terminal === "running" && (
                  <div className="log-line">
                    <span className="ts">[…]</span>
                    <span className="icon run">↻</span>
                    <span className="msg">ожидаю следующее событие …</span>
                  </div>
                )}
              </div>
            </div>
          )}
          {step === 2 && (
            <div style={{ textAlign: "center", padding: "20px 0" }}>
              <div className="success-icon"><Icon name="check" size={32} /></div>
              <div style={{ fontSize: 17, fontWeight: 700, marginBottom: 6 }}>Сервер готов</div>
              <div style={{ fontSize: 12.5, color: "var(--text-3)" }}>
                {form.name || form.host}{serverId && ` · id=${serverId}`}
              </div>
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={handleClose}>{step === 2 ? "Закрыть" : "Отмена"}</button>
          <div style={{ display: "flex", gap: 8 }}>
            {step === 0 && (
              <button
                className="btn primary"
                disabled={!isFormValid(form) || provisionServer.isPending}
                onClick={startProvision}
              >
                {provisionServer.isPending ? "Создаю…" : "Запустить онбординг"}
                <Icon name="chevron-right" size={14} />
              </button>
            )}
            {step === 1 && terminal === "running" && <button className="btn" disabled>Установка…</button>}
            {step === 1 && terminal === "error" && (
              <>
                <button className="btn" onClick={goBackToForm} title="Вернуться к форме и поправить креды">
                  <Icon name="chevron-left" size={14} /> Назад
                </button>
                <button className="btn primary" onClick={retry}>
                  <Icon name="refresh" size={14} /> Повторить
                </button>
              </>
            )}
            {step === 1 && terminal === "done" && (
              <button className="btn primary" onClick={() => setStep(2)}>
                Дальше <Icon name="chevron-right" size={14} />
              </button>
            )}
            {step === 2 && <button className="btn primary" onClick={handleClose}>Готово</button>}
          </div>
        </div>
      </div>
    </div>
  );
}

function isFormValid(form: FormState): boolean {
  if (!form.host) return false;
  if (form.authMode === "password" && !form.ssh_password) return false;
  if (form.authMode === "key" && !form.ssh_private_key) return false;
  return true;
}

interface ConnectStepProps {
  form: FormState;
  update: (patch: Partial<FormState>) => void;
  disabled: boolean;
}

function ConnectStep({ form, update, disabled }: ConnectStepProps) {
  return (
    <div>
      <div className="field-row r3">
        <div className="field">
          <label>Хост / IP</label>
          <input
            className="input"
            value={form.host}
            onChange={(event) => update({ host: event.target.value })}
            placeholder="10.0.0.1"
          />
        </div>
        <div className="field">
          <label>SSH порт</label>
          <input className="input" value={form.ssh_port} onChange={(event) => update({ ssh_port: event.target.value })} />
        </div>
        <div className="field">
          <label>Имя сервера</label>
          <input className="input" value={form.name} onChange={(event) => update({ name: event.target.value })} />
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <label>Регион</label>
          <input className="input" value={form.region} onChange={(event) => update({ region: event.target.value })} placeholder="EU" />
        </div>
        <div className="field">
          <label>Порт агента</label>
          <input className="input" value={form.agent_port} onChange={(event) => update({ agent_port: event.target.value })} />
        </div>
      </div>
      <SshCredentialsFields
        sshUser={form.ssh_user}
        onSshUserChange={(value) => update({ ssh_user: value })}
        sshPort={form.ssh_port}
        onSshPortChange={(value) => update({ ssh_port: value })}
        authMode={form.authMode}
        onAuthModeChange={(value) => update({ authMode: value })}
        sshPassword={form.ssh_password}
        onSshPasswordChange={(value) => update({ ssh_password: value })}
        sshPrivateKey={form.ssh_private_key}
        onSshPrivateKeyChange={(value) => update({ ssh_private_key: value })}
        hideUserPort
      />
      <label
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          fontSize: 12,
          color: "var(--text-2)",
          marginTop: 8,
          cursor: "pointer",
        }}
      >
        <input
          type="checkbox"
          checked={form.save_ssh_credentials}
          onChange={(event) => update({ save_ssh_credentials: event.target.checked })}
        />
        Сохранить SSH-креды для будущих обновлений агента
      </label>
      <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, color: "var(--text-3)", marginTop: 4 }}>
        <Icon name="lock" size={12} />
        {form.save_ssh_credentials
          ? "Креды шифруются Fernet'ом поверх SECRET_KEY и хранятся в БД. Используются для server-side update'а агента (обходит self-update, который ломается на ProtectSystem=strict /opt)."
          : "Креды используются один раз и не пишутся в БД (минимум attack surface). Re-update будет идти через self-update fallback."}
      </div>
      {disabled && <div className="hint">Готовлю фоновую таску…</div>}
    </div>
  );
}
