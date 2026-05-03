/**
 * Поле для ввода ISO-3166-1 alpha-2 кода страны с emoji-флагом и autocomplete.
 *
 * Использует HTML5 `<datalist>` — нативный dropdown с фильтром по тексту.
 * Имена стран на русском через `Intl.DisplayNames` (есть во всех живых браузерах).
 */

import { useMemo } from "react";

// Конвертирует "RU" → 🇷🇺. Regional Indicator Symbols в unicode.
export function flagFor(country: string): string {
  if (country.length !== 2) return "🌍";
  const codePoints = [...country.toUpperCase()].map((char) => 0x1f1a5 + char.charCodeAt(0));
  return String.fromCodePoint(...codePoints);
}

// ISO-3166-1 alpha-2 — самые востребованные страны. Не исчерпывающий, но если
// нужного нет — пользователь введёт два символа руками, валидация regex `[A-Z]{2}`
// всё равно пропустит.
const COMMON_CODES = [
  // СНГ + соседи
  "RU", "BY", "UA", "KZ", "GE", "AM", "AZ", "MD", "UZ", "KG", "TJ", "TM",
  // Европа
  "DE", "FR", "GB", "IT", "ES", "PT", "NL", "BE", "LU", "AT", "CH", "PL",
  "CZ", "SK", "HU", "RO", "BG", "GR", "FI", "SE", "NO", "DK", "IS", "IE",
  "EE", "LV", "LT", "HR", "RS", "BA", "AL", "MK", "SI", "ME", "MT", "CY",
  // Северная Америка
  "US", "CA", "MX",
  // Латинская Америка
  "BR", "AR", "CL", "CO", "PE", "VE", "UY", "EC", "BO", "PY",
  // Ближний Восток / Африка
  "TR", "IL", "AE", "SA", "QA", "KW", "BH", "OM", "JO", "LB", "EG",
  "MA", "TN", "DZ", "ZA", "NG", "KE", "ET",
  // Азия / Океания
  "CN", "JP", "KR", "KP", "TW", "HK", "MO", "SG", "MY", "ID", "TH", "VN",
  "PH", "IN", "PK", "BD", "LK", "NP", "MM", "KH", "LA", "MN",
  "AU", "NZ",
];

interface CountryItem {
  code: string;
  name: string;
}

function buildCountries(): CountryItem[] {
  // Intl.DisplayNames есть везде с 2021. Если нет — fallback на сам код.
  let displayNames: Intl.DisplayNames | null = null;
  try {
    displayNames = new Intl.DisplayNames(["ru"], { type: "region" });
  } catch {
    displayNames = null;
  }
  return COMMON_CODES.map((code) => {
    const name = displayNames?.of(code) ?? code;
    return { code, name };
  });
}

interface Props {
  value: string;
  onChange: (code: string) => void;
  required?: boolean;
  /** Опционально: id для `<datalist>`; нужен если на странице несколько селекторов. */
  listId?: string;
  className?: string;
  disabled?: boolean;
}

export function CountrySelect({
  value,
  onChange,
  required = false,
  listId = "waygate-country-list",
  className = "input",
  disabled = false,
}: Props) {
  const countries = useMemo(() => buildCountries(), []);
  const flag = flagFor(value);

  return (
    <div style={{ position: "relative" }}>
      <input
        className={className}
        value={value}
        onChange={(event) => onChange(event.target.value.toUpperCase().slice(0, 2))}
        placeholder="RU"
        maxLength={2}
        list={listId}
        required={required}
        disabled={disabled}
        autoComplete="off"
        style={{ paddingLeft: 36 }}
      />
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          left: 10,
          top: "50%",
          transform: "translateY(-50%)",
          fontSize: 18,
          pointerEvents: "none",
        }}
      >
        {value.length === 2 ? flag : "🌍"}
      </span>
      <datalist id={listId}>
        {countries.map((country) => (
          <option key={country.code} value={country.code}>
            {flagFor(country.code)} {country.name}
          </option>
        ))}
      </datalist>
    </div>
  );
}
