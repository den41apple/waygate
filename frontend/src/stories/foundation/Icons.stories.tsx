import type { Meta, StoryObj } from "@storybook/react";

import { Icon, type IconName } from "../../components/Icon";

const NAMES: IconName[] = [
  "search", "plus", "x", "chevron-right", "chevron-left", "settings",
  "refresh", "lock", "globe", "key", "terminal", "filter", "download",
  "play", "check", "more", "shield", "edit", "alert", "credit-card",
  "wallet", "tunnel", "route", "list", "activity", "server", "send",
  "trending-up", "trending-down",
];

function Icons() {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(96px, 1fr))", gap: 12 }}>
      {NAMES.map((name) => (
        <div
          key={name}
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 8,
            padding: "14px 8px",
            borderRadius: "var(--r)",
            background: "var(--card-2)",
            border: "1px solid var(--border)",
            color: "var(--text-2)",
          }}
        >
          <Icon name={name} size={20} />
          <span style={{ fontSize: 10.5, color: "var(--text-3)", fontFamily: "var(--mono)" }}>{name}</span>
        </div>
      ))}
    </div>
  );
}

const meta: Meta<typeof Icons> = {
  title: "Foundation/Icons",
  component: Icons,
};
export default meta;

type Story = StoryObj<typeof Icons>;
export const All: Story = {};
