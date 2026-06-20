import type { Meta, StoryObj } from "@storybook/react";

// Палитра Waygate из styles.css (:root + [data-theme]). Значения читаются через
// CSS-переменные, поэтому карточка автоматически следует теме (dark/light тулбар).

interface Swatch {
  name: string;
  varName: string;
  hint?: string;
  gradient?: boolean;
}

const ACCENTS: Swatch[] = [
  { name: "accent", varName: "--accent", hint: "#a78bfa → #8b5cf6", gradient: true },
  { name: "tg", varName: "--tg", hint: "#3b9eff" },
  { name: "green", varName: "--green", hint: "#22c55e" },
  { name: "amber", varName: "--amber", hint: "#f59e0b" },
  { name: "red", varName: "--red", hint: "#ef4444" },
  { name: "orange", varName: "--orange", hint: "#fb923c" },
  { name: "cyan", varName: "--cyan", hint: "#22d3ee" },
  { name: "pink", varName: "--pink", hint: "#f472b6" },
];

const SURFACES: Swatch[] = [
  { name: "bg", varName: "--bg" },
  { name: "bg-1", varName: "--bg-1" },
  { name: "bg-2", varName: "--bg-2" },
  { name: "bg-3", varName: "--bg-3" },
  { name: "card", varName: "--card" },
  { name: "text", varName: "--text" },
  { name: "text-2", varName: "--text-2" },
  { name: "text-3", varName: "--text-3" },
];

function SwatchCell({ swatch }: { swatch: Swatch }) {
  const background = swatch.gradient
    ? "linear-gradient(135deg, var(--accent), var(--accent-2))"
    : `var(${swatch.varName})`;
  return (
    <div style={{ width: 120 }}>
      <div
        style={{
          height: 56,
          borderRadius: 12,
          border: "1px solid var(--border-2)",
          background,
        }}
      />
      <div style={{ fontSize: 12, fontWeight: 600, marginTop: 8 }}>{swatch.name}</div>
      {swatch.hint && (
        <div style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "var(--mono)" }}>
          {swatch.hint}
        </div>
      )}
    </div>
  );
}

function Colors() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
        {ACCENTS.map((swatch) => (
          <SwatchCell key={swatch.name} swatch={swatch} />
        ))}
      </div>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
        {SURFACES.map((swatch) => (
          <SwatchCell key={swatch.name} swatch={swatch} />
        ))}
      </div>
    </div>
  );
}

const meta: Meta<typeof Colors> = {
  title: "Foundation/Colors",
  component: Colors,
};
export default meta;

type Story = StoryObj<typeof Colors>;

export const Palette: Story = {};
