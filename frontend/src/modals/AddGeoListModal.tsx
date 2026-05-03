import { useState } from "react";

import { useCreateGeoList, useUpdateGeoList, type GeoListCreate } from "../api/geoip";
import type { GeoList } from "../api/types";
import { CountrySelect } from "../components/CountrySelect";
import { Icon } from "../components/Icon";
import { IconTile } from "../components/primitives";

interface Props {
  editing?: GeoList;
  onClose: () => void;
}

const COUNTRY_RE = /^[A-Z]{2}$/;

export function AddGeoListModal({ editing, onClose }: Props) {
  const create = useCreateGeoList();
  const update = useUpdateGeoList();
  const isEdit = editing !== undefined;
  const mutation = isEdit ? update : create;

  const [country, setCountry] = useState(editing?.country ?? "");
  const [name, setName] = useState(editing?.name ?? "");
  const [sourceUrl, setSourceUrl] = useState(editing?.source_url ?? "");

  const sourceForCountry = (cc: string) =>
    `https://www.ipdeny.com/ipblocks/data/countries/${cc.toLowerCase()}.zone`;

  const valid =
    COUNTRY_RE.test(country)
    && name.trim().length > 0
    && sourceUrl.trim().length > 0;

  const submit = async () => {
    if (!valid) return;
    try {
      if (isEdit && editing) {
        // country менять нельзя (см. backend GeoListUpdate) — отправляем только
        // name + source_url. Если они не менялись — экономим запрос.
        await update.mutateAsync({
          id: editing.id,
          patch: { name: name.trim(), source_url: sourceUrl.trim() },
        });
      } else {
        const payload: GeoListCreate = {
          country,
          name: name.trim(),
          source_url: sourceUrl.trim(),
        };
        await create.mutateAsync(payload);
      }
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
            <IconTile color="violet" icon="globe" size="sm" />{" "}
            {isEdit ? "Редактировать GeoIP-список" : "Добавить GeoIP-список"}
          </div>
          <button className="close" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className="modal-body">
          <div className="field-row">
            <div className="field">
              <label>Страна</label>
              <CountrySelect
                value={country}
                onChange={(next) => {
                  if (isEdit) return;  // country в edit-режиме менять нельзя
                  setCountry(next);
                  if (!sourceUrl && COUNTRY_RE.test(next)) setSourceUrl(sourceForCountry(next));
                  if (!name && COUNTRY_RE.test(next)) setName(next);
                }}
                listId="geolist-country-list"
                disabled={isEdit}
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

          {mutation.error && (
            <div
              className="hint"
              style={{ background: "var(--red-tint, #4a1f1f)", color: "var(--red, #ef4444)" }}
            >
              {String(mutation.error)}
            </div>
          )}
        </div>
        <div className="modal-foot">
          <button className="btn ghost" onClick={onClose}>Отмена</button>
          <button
            className="btn primary"
            onClick={submit}
            disabled={!valid || mutation.isPending}
          >
            {mutation.isPending
              ? isEdit ? "Сохраняю…" : "Создаю…"
              : isEdit ? "Сохранить" : "Создать"}
          </button>
        </div>
      </div>
    </div>
  );
}
