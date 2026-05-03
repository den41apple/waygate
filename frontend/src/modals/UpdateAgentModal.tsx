import { useEffect, useMemo, useRef, useState } from "react";

import { useAgentReleases } from "../api/agentReleases";
import { useRefreshServer, useServer, useUpdateServer } from "../api/servers";
import type { UpdateServerPayload } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile, Toggle } from "../components/primitives";
import { useAuthStore } from "../store/auth";

interface Props {
  serverId: number;
  currentVersion: string;
  onClose: () => void;
}

interface LogLine {
  type: string;
  message: string;
  timestamp: string;
}

const DEFAULT_WHEEL_URL =
  "https://github.com/den41apple/waygate/releases/latest/download/waygate_agent-py3-none-any.whl";

// Спец-значение в select'е чтобы переключиться в ручной ввод (для случая
// «GitHub недоступен» или «приватный wheel-URL»).
const MANUAL_TAG = "__manual__";

export function UpdateAgentModal({ serverId, currentVersion, onClose }: Props) {
  const update = useUpdateServer();
  const refresh = useRefreshServer();
  const fresh = useServer(serverId);
  const releases = useAgentReleases();
  // При открытии модалки — выдёргиваем актуальную версию (БД обновляется только
  // через healthcheck/refresh, иначе тут долго висит prевентивная).
  useEffect(() => {
    refresh.mutate(serverId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverId]);
  const displayedVersion = fresh.data?.version ?? currentVersion;

  // Пред-выбор: первый релиз (latest). При недоступности GitHub — MANUAL_TAG.
  const defaultTag = useMemo(() => {
    if (releases.data && releases.data.length > 0) return releases.data[0].tag;
    return MANUAL_TAG;
  }, [releases.data]);
  const [selectedTag, setSelectedTag] = useState<string | null>(null);
  // Эффективный tag — selectedTag (если пользователь выбрал) иначе defaultTag.
  const tag = selectedTag ?? defaultTag;

  const [manualVersion, setManualVersion] = useState("");
  const [manualWheelUrl, setManualWheelUrl] = useState(DEFAULT_WHEEL_URL);
  const [waitForReconnect, setWaitForReconnect] = useState(true);

  // Версия и URL — либо из выбранного release, либо из ручных полей.
  const selectedRelease = releases.data?.find((release) => release.tag === tag);
  const version = tag === MANUAL_TAG ? manualVersion : selectedRelease?.version ?? "";
  const wheelUrl = tag === MANUAL_TAG ? manualWheelUrl : selectedRelease?.wheel_url ?? "";

  // Step 0 — форма; step 1 — лог в стиле AddServerModal.
  const [step, setStep] = useState<0 | 1>(0);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [terminal, setTerminal] = useState<"running" | "done" | "error">("running");
  const sourceRef = useRef<EventSource | null>(null);

  const valid = version.trim().length > 0 && wheelUrl.trim().length > 0;

  const submit = async () => {
    if (!valid) return;
    setLines([]);
    setTerminal("running");
    const payload: UpdateServerPayload = {
      version: version.trim(),
      wheel_url: wheelUrl.trim(),
      wait_for_reconnect: waitForReconnect,
    };
    let response: { stream_url: string };
    try {
      response = await update.mutateAsync({ serverId, payload });
    } catch (error) {
      setTerminal("error");
      setLines((current) => [
        ...current,
        { type: "error", message: String(error), timestamp: new Date().toISOString() },
      ]);
      setStep(1);
      return;
    }
    setStep(1);

    // EventSource не передаёт Authorization-заголовок, JWT — в query-параметре.
    const accessToken = useAuthStore.getState().token ?? "";
    const url = `${response.stream_url}?access_token=${encodeURIComponent(accessToken)}`;
    const source = new EventSource(url);
    sourceRef.current = source;
    source.onmessage = (event) => {
      try {
        const parsed = JSON.parse(event.data) as LogLine | { type: "end" };
        if ("type" in parsed && parsed.type === "end") {
          source.close();
          return;
        }
        const line = parsed as LogLine;
        setLines((current) => [...current, line]);
        if (line.type === "error") setTerminal("error");
        if (line.type === "done") setTerminal("done");
      } catch (error) {
        console.warn("update-stream: не парсится:", error, event.data);
      }
    };
    source.onerror = () => {
      source.close();
    };
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
    return <span style={{ color: "var(--accent)", fontSize: 12 }}>● обновляется</span>;
  })();

  return (
    <div className="modal-veil" onClick={handleClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="download" size="sm" /> Обновить агент
          </div>
          <button className="close" onClick={handleClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          {step === 0 && (
            <>
              <div className="hint" style={{ fontSize: 12 }}>
                Текущая версия: <span className="mono">{displayedVersion || "—"}</span>
                {refresh.isPending && (
                  <span style={{ color: "var(--text-3)" }}> · обновляю…</span>
                )}
              </div>

              <div className="field">
                <label>Целевая версия</label>
                <select
                  className="select"
                  value={tag}
                  onChange={(event) => setSelectedTag(event.target.value)}
                  disabled={releases.isPending}
                >
                  {releases.data?.map((release) => (
                    <option key={release.tag} value={release.tag}>
                      {release.version}
                      {release === releases.data[0] ? " (latest)" : ""}
                      {release.version === displayedVersion ? " · уже установлено" : ""}
                    </option>
                  ))}
                  <option value={MANUAL_TAG}>— ввести вручную —</option>
                </select>
                {releases.error && (
                  <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>
                    GitHub недоступен — введи версию и URL вручную.
                  </div>
                )}
              </div>

              {tag === MANUAL_TAG && (
                <>
                  <div className="field">
                    <label>Версия (вручную)</label>
                    <input
                      className="input"
                      value={manualVersion}
                      onChange={(event) => setManualVersion(event.target.value)}
                      placeholder="0.2.0"
                    />
                  </div>
                  <div className="field">
                    <label>URL wheel-файла</label>
                    <input
                      className="input"
                      value={manualWheelUrl}
                      onChange={(event) => setManualWheelUrl(event.target.value)}
                    />
                    <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>
                      По умолчанию — `latest/download` из GitHub.
                    </div>
                  </div>
                </>
              )}

              <div
                className="field"
                style={{ flexDirection: "row", alignItems: "center", gap: 10 }}
              >
                <Toggle
                  on={waitForReconnect}
                  onClick={() => setWaitForReconnect(!waitForReconnect)}
                />
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
            </>
          )}

          {step === 1 && (
            <div>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: 12,
                }}
              >
                <div style={{ fontSize: 12.5, color: "var(--text-2)" }}>
                  Обновление агента до{" "}
                  <span className="mono" style={{ color: "var(--accent)" }}>{version}</span>
                </div>
                {statusBadge}
              </div>
              <div className="log">
                {lines.map((line, index) => (
                  <div key={index} className="log-line">
                    <span className="ts">[{ts(line.timestamp)}]</span>
                    <span
                      className={`icon ${
                        line.type === "error" ? "fail" : line.type === "done" ? "ok" : "run"
                      }`}
                    >
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
        </div>

        <div className="modal-foot">
          <button className="btn ghost" onClick={handleClose}>
            {step === 1 && terminal !== "running" ? "Закрыть" : "Отмена"}
          </button>
          {step === 0 && (
            <button
              className="btn primary"
              onClick={submit}
              disabled={!valid || update.isPending}
            >
              {update.isPending ? "Запускаю…" : "Обновить"}
            </button>
          )}
          {step === 1 && terminal === "running" && (
            <button className="btn" disabled>Обновление…</button>
          )}
          {step === 1 && terminal === "error" && (
            <button className="btn primary" onClick={() => setStep(0)}>
              <Icon name="chevron-left" size={14} /> Назад к форме
            </button>
          )}
          {step === 1 && terminal === "done" && (
            <button className="btn primary" onClick={handleClose}>Готово</button>
          )}
        </div>
      </div>
    </div>
  );
}
