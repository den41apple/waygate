import { useEffect, useState } from "react";

import { useCreateAwgClient } from "../api/awgClients";
import type { AwgClientCreate } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";

interface Props {
  serverId: number;
  onClose: () => void;
}

const NAME_RE = /^[a-z0-9][a-z0-9-]{0,29}$/;
const COUNTRY_RE = /^[A-Z]{2}$/;

interface ParsedPreview {
  address?: string;
  endpoint?: string;
  has_amnezia_params: boolean;
  errors: string[];
}

/**
 * Лёгкий клиент-парсер .conf для preview.
 * Полная валидация — на бэке через Pydantic.
 */
function previewConfig(text: string): ParsedPreview {
  const result: ParsedPreview = { has_amnezia_params: false, errors: [] };
  if (!text.trim()) return result;
  if (!text.includes("[Interface]")) result.errors.push("нет секции [Interface]");
  if (!text.includes("[Peer]")) result.errors.push("нет секции [Peer]");
  if (!text.match(/PrivateKey\s*=/)) result.errors.push("PrivateKey не задан");
  if (!text.match(/PublicKey\s*=/)) result.errors.push("PublicKey не задан");
  if (!text.match(/Endpoint\s*=/)) result.errors.push("Endpoint не задан");

  const addr = text.match(/Address\s*=\s*(.+)/);
  if (addr) result.address = addr[1].trim();
  const ep = text.match(/Endpoint\s*=\s*(.+)/);
  if (ep) result.endpoint = ep[1].trim();
  if (text.match(/^(Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5])\s*=/m)) {
    result.has_amnezia_params = true;
  }
  return result;
}

export function AddAwgClientModal({ serverId, onClose }: Props) {
  const create = useCreateAwgClient(serverId);

  const [name, setName] = useState("");
  const [country, setCountry] = useState("");
  const [configText, setConfigText] = useState("");
  const [isDragging, setIsDragging] = useState(false);

  const preview = previewConfig(configText);
  const valid =
    NAME_RE.test(name)
    && (country === "" || COUNTRY_RE.test(country))
    && configText.trim().length > 0
    && preview.errors.length === 0;

  const handleFile = async (file: File) => {
    const text = await file.text();
    setConfigText(text);
    // Авто-имя из файла, если name ещё пустое.
    if (!name) {
      const stem = file.name.replace(/\.conf$/i, "").toLowerCase()
        .replace(/[^a-z0-9-]/g, "-").replace(/^-+|-+$/g, "").slice(0, 30);
      if (stem && NAME_RE.test(stem)) setName(stem);
    }
  };

  // Глобальные dragenter/dragover handlers — иначе браузер всё равно покажет файл.
  useEffect(() => {
    const stopDefault = (event: DragEvent) => event.preventDefault();
    window.addEventListener("dragover", stopDefault);
    window.addEventListener("drop", stopDefault);
    return () => {
      window.removeEventListener("dragover", stopDefault);
      window.removeEventListener("drop", stopDefault);
    };
  }, []);

  const submit = async () => {
    if (!valid) return;
    const payload: AwgClientCreate = {
      name: name.trim(),
      country: country.trim() || null,
      config_text: configText,
    };
    try {
      await create.mutateAsync(payload);
      onClose();
    } catch {
      // Ошибка отрисуется ниже из create.error
    }
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title">
            <IconTile color="violet" icon="tunnel" size="sm" /> Добавить AmneziaWG-клиента
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field-row">
            <div className="field">
              <label>Имя клиента</label>
              <input
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value.toLowerCase())}
                placeholder="us-fast"
                maxLength={30}
              />
              <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>
                a-z, 0-9, дефис. Контейнер: waygate-amnezia-client-{name || "<name>"}
              </div>
            </div>
            <div className="field">
              <label>Страна (ISO-2, опционально)</label>
              <input
                className="input"
                value={country}
                onChange={(event) => setCountry(event.target.value.toUpperCase().slice(0, 2))}
                placeholder="US"
                maxLength={2}
              />
            </div>
          </div>

          <div
            className={`field drop-zone ${isDragging ? "drag-over" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setIsDragging(true);
            }}
            onDragLeave={() => setIsDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setIsDragging(false);
              const file = event.dataTransfer.files[0];
              if (file) void handleFile(file);
            }}
          >
            <label>
              .conf-файл
              <span style={{ float: "right", fontSize: 11, color: "var(--text-3)" }}>
                перетащите файл сюда или вставьте текст
              </span>
            </label>
            <textarea
              className="textarea"
              rows={12}
              value={configText}
              onChange={(event) => setConfigText(event.target.value)}
              placeholder={"[Interface]\nAddress = 10.66.66.2/24\nPrivateKey = ...\n\n[Peer]\nPublicKey = ...\nAllowedIPs = 0.0.0.0/0\nEndpoint = vpn.example.com:51820"}
              spellCheck={false}
              style={{ fontFamily: "var(--mono)", fontSize: 12 }}
            />
            <input
              type="file"
              accept=".conf,text/plain"
              style={{ marginTop: 8 }}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void handleFile(file);
              }}
            />
          </div>

          {configText && (
            <div className="hint" style={{ fontSize: 12, marginTop: 8 }}>
              <div style={{ marginBottom: 4 }}>
                <b>Предпросмотр</b>
              </div>
              {preview.errors.length > 0 ? (
                <ul style={{ margin: 0, paddingLeft: 18, color: "var(--red, #ef4444)" }}>
                  {preview.errors.map((error) => <li key={error}>{error}</li>)}
                </ul>
              ) : (
                <>
                  <div>Address: <span className="mono">{preview.address ?? "—"}</span></div>
                  <div>Endpoint: <span className="mono">{preview.endpoint ?? "—"}</span></div>
                  <div>
                    AmneziaWG 2.0 параметры:{" "}
                    <span style={{ color: preview.has_amnezia_params ? "var(--green)" : "var(--text-3)" }}>
                      {preview.has_amnezia_params ? "обнаружены" : "нет (обычный WireGuard)"}
                    </span>
                  </div>
                </>
              )}
            </div>
          )}

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
            {create.isPending ? "Разворачиваю…" : "Развернуть клиента"}
          </button>
        </div>
      </div>
    </div>
  );
}
