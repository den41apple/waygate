import type { Meta, StoryObj } from "@storybook/react";

function Typography() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22, maxWidth: 680 }}>
      <div>
        <div style={{ fontSize: 11, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>
          Inter · UI
        </div>
        <div style={{ fontSize: 26, fontWeight: 700, letterSpacing: "-0.02em" }}>Waygate control plane</div>
        <div style={{ fontSize: 16, fontWeight: 600 }}>GeoIP-маршрутизация трафика</div>
        <div style={{ fontSize: 13, color: "var(--text-2)" }}>Основной текст интерфейса — 13px / line-height 1.45</div>
        <div style={{ fontSize: 11, color: "var(--text-3)" }}>Вторичный / подписи — 11px</div>
      </div>
      <div>
        <div style={{ fontSize: 11, color: "var(--text-3)", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>
          JetBrains Mono · code
        </div>
        <div className="mono" style={{ fontSize: 14 }}>94.130.18.221 · fwmark 0x1001 · awg0</div>
        <div className="mono" style={{ fontSize: 12, color: "var(--text-2)" }}>geoip-ru-v4 · 5.16.0.0/16 · table 0x1001</div>
      </div>
    </div>
  );
}

const meta: Meta<typeof Typography> = {
  title: "Foundation/Typography",
  component: Typography,
};
export default meta;

type Story = StoryObj<typeof Typography>;
export const Scale: Story = {};
