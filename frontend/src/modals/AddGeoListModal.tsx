import { useState } from "react";

import { useCreateGeoList, type GeoListCreate } from "../api/geoip";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";

interface Props {
  onClose: () => void;
}

const COUNTRY_RE = /^[A-Z]{2}$/;

export function AddGeoListModal({ onClose }: Props) {
  const create = useCreateGeoList();

  const [country, setCountry] = useState("");
  const [name, setName] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");

  const sourceForCountry = (cc: string) =>
    `https://www.ipdeny.com/ipblocks/data/countries/${cc.toLowerCase()}.zone`;

  const valid =
    COUNTRY_RE.test(country)
    && name.trim().length > 0
    && sourceUrl.trim().length > 0;

  const submit = async () => {
    if (!valid) return;
    const payload: GeoListCreate = {
      country,
      name: name.trim(),
      source_url: sourceUrl.trim(),
    };
    try {
      await create.mutateAsync(payload);
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
            <IconTile color="violet" icon="globe" size="sm" /> Добавить GeoIP-список
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field-row">
            <div className="field">
              <label>Страна (ISO-2)</label>
              <input
                className="input"
                value={country}
                onChange={(event) => {
                  const next = event.target.value.toUpperCase().slice(0, 2);
                  setCountry(next);
                  if (!sourceUrl && COUNTRY_RE.test(next)) setSourceUrl(sourceForCountry(next));
                  if (!name && COUNTRY_RE.test(next)) setName(next);
                }}
                placeholder="RU"
                maxLength={2}
              />
            </div>
            <div className="field">
              <label>Название</label>
              <input
                className="input"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Russia"
              />
            </div>
          </div>

          <div className="field">
            <label>URL-источник CIDR</label>
            <input
              className="input"
              value={sourceUrl}
              onChange={(event) => setSourceUrl(event.target.value)}
              placeholder="https://www.ipdeny.com/ipblocks/data/countries/ru.zone"
            />
            <div className="hint" style={{ fontSize: 11, color: "var(--text-3)" }}>
              Контрол-плейн скачает и распарсит при первой синхронизации.
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
