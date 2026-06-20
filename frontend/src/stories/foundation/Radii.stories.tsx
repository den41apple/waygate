import type { Meta, StoryObj } from "@storybook/react";

const RADII = [
  { name: "--r-sm", value: "10px" },
  { name: "--r", value: "14px" },
  { name: "--r-lg", value: "18px" },
  { name: "--r-xl", value: "22px" },
];

function Radii() {
  return (
    <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
      {RADII.map((r) => (
        <div key={r.name} style={{ width: 110 }}>
          <div
            style={{
              height: 80,
              borderRadius: `var(${r.name})`,
              background: "var(--card-2)",
              border: "1px solid var(--border-2)",
            }}
          />
          <div style={{ fontSize: 12, fontWeight: 600, marginTop: 8, fontFamily: "var(--mono)" }}>{r.name}</div>
          <div style={{ fontSize: 11, color: "var(--text-3)" }}>{r.value}</div>
        </div>
      ))}
    </div>
  );
}

const meta: Meta<typeof Radii> = {
  title: "Foundation/Radii",
  component: Radii,
};
export default meta;

type Story = StoryObj<typeof Radii>;
export const Scale: Story = {};
