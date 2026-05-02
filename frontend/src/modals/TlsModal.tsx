import { useState } from "react";

import { useApplyTls, useTlsConfig } from "../api/tls";
import type { AcmeChallenge, DnsProvider, TlsConfigPayload, TlsMode } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";

interface Props {
  serverId: number;
  onClose: () => void;
}

export function TlsModal({ serverId, onClose }: Props) {
  const { data: current } = useTlsConfig(serverId);
  const apply = useApplyTls(serverId);

  const [mode, setMode] = useState<TlsMode>("upload");
  const [port, setPort] = useState(7743);
  const [certPem, setCertPem] = useState("");
  const [keyPem, setKeyPem] = useState("");
  const [certPath, setCertPath] = useState("/etc/letsencrypt/live/edge.example.com/fullchain.pem");
  const [keyPath, setKeyPath] = useState("/etc/letsencrypt/live/edge.example.com/privkey.pem");
  const [domain, setDomain] = useState("edge.example.com");
  const [email, setEmail] = useState("ops@example.com");
  const [challenge, setChallenge] = useState<AcmeChallenge>("http01");
  const [provider, setProvider] = useState<DnsProvider>("cloudflare");
  const [dnsApiKey, setDnsApiKey] = useState("");

  const submit = () => {
    const payload: TlsConfigPayload = { mode, port };
    if (mode === "upload") {
      payload.cert_pem = btoa(certPem);
      payload.key_pem = btoa(keyPem);
    } else if (mode === "path") {
      payload.cert_path = certPath;
      payload.key_path = keyPath;
    } else {
      payload.domains = [domain];
      payload.email = email;
      payload.challenge = challenge;
      if (challenge === "dns01") {
        payload.dns_provider = provider;
        payload.dns_api_key = dnsApiKey;
      }
    }
    apply.mutate(payload);
  };

  return (
    <div className="modal-veil" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <div className="title"><IconTile color="violet" icon="lock" size="sm" /> TLS настройки</div>
          <button className="close" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
        <div className="modal-body">
          <div className="field">
            <label>Источник сертификата</label>
            <div className="tab-switcher">
              <button className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}>Загрузить</button>
              <button className={mode === "path" ? "active" : ""} onClick={() => setMode("path")}>Путь на сервере</button>
              <button className={mode === "acme" ? "active" : ""} onClick={() => setMode("acme")}>ACME</button>
            </div>
          </div>

          {mode === "upload" && (
            <>
              <div className="field">
                <label>cert.pem</label>
                <textarea className="textarea" value={certPem} onChange={(event) => setCertPem(event.target.value)} placeholder="-----BEGIN CERTIFICATE-----" />
              </div>
              <div className="field">
                <label>key.pem</label>
                <textarea className="textarea" value={keyPem} onChange={(event) => setKeyPem(event.target.value)} placeholder="-----BEGIN PRIVATE KEY-----" />
              </div>
            </>
          )}

          {mode === "path" && (
            <>
              <div className="field">
                <label>Путь к cert</label>
                <input className="input" value={certPath} onChange={(event) => setCertPath(event.target.value)} />
              </div>
              <div className="field">
                <label>Путь к key</label>
                <input className="input" value={keyPath} onChange={(event) => setKeyPath(event.target.value)} />
              </div>
            </>
          )}

          {mode === "acme" && (
            <>
              <div className="field">
                <label>ACME challenge</label>
                <div className="tab-switcher">
                  <button className={challenge === "http01" ? "active" : ""} onClick={() => setChallenge("http01")}>HTTP-01</button>
                  <button className={challenge === "dns01" ? "active" : ""} onClick={() => setChallenge("dns01")}>DNS-01</button>
                </div>
              </div>
              <div className="field-row">
                <div className="field">
                  <label>Домен</label>
                  <input className="input" value={domain} onChange={(event) => setDomain(event.target.value)} />
                </div>
                <div className="field">
                  <label>Email</label>
                  <input className="input" value={email} onChange={(event) => setEmail(event.target.value)} />
                </div>
              </div>
              {challenge === "dns01" && (
                <div className="field-row">
                  <div className="field">
                    <label>DNS-провайдер</label>
                    <select className="select" value={provider} onChange={(event) => setProvider(event.target.value as DnsProvider)}>
                      <option value="cloudflare">Cloudflare</option>
                      <option value="desec">deSEC</option>
                      <option value="route53">Route 53</option>
                    </select>
                  </div>
                  <div className="field">
                    <label>API-ключ</label>
                    <input className="input" type="password" value={dnsApiKey} onChange={(event) => setDnsApiKey(event.target.value)} />
                  </div>
                </div>
              )}
              <div className="hint" style={{ marginTop: 4 }}>
                ACME-клиент пока в разработке — агент вернёт 400 «не реализовано». upload и path работают полноценно.
              </div>
            </>
          )}

          <div className="field-row" style={{ marginTop: 14 }}>
            <div className="field">
              <label>Listen port</label>
              <input className="input" type="number" value={port} onChange={(event) => setPort(Number(event.target.value))} />
            </div>
          </div>

          {current && (
            <div className="field" style={{ marginTop: 8 }}>
              <label>Текущая конфигурация</label>
              <div className="cert-box">
                <div><span className="k">Mode</span><div className="v">{String(current.config["mode"] ?? "—")}</div></div>
                <div><span className="k">Истекает</span><div className="v">{current.expires_at ? new Date(current.expires_at).toLocaleString() : "—"}</div></div>
              </div>
            </div>
          )}

          {apply.error && (
            <div className="hint" style={{ background: "var(--red-tint, #4a1f1f)", color: "var(--red, #ef4444)" }}>
              {String(apply.error)}
            </div>
          )}
          {apply.data && (
            <div className="hint" style={{ background: "var(--green-tint)", color: "var(--green)" }}>
              applied · domains: {apply.data.domains.join(", ")} · expires {new Date(apply.data.expires_at).toLocaleString()}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={onClose}>Отмена</button>
          <button className="btn primary" onClick={submit} disabled={apply.isPending}>
            {apply.isPending ? "Применяю…" : "Применить"}
          </button>
        </div>
      </div>
    </div>
  );
}
