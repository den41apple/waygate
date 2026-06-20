import type { Meta, StoryObj } from "@storybook/react";

import {
  Badge,
  IconTile,
  Metric,
  MonoPill,
  SectionHead,
  Sparkline,
  Toggle,
  ViaPill,
} from "../../components/primitives";

// Презентационные строительные блоки из components/primitives.tsx.
const meta: Meta = {
  title: "Components/Primitives",
};
export default meta;

type Story = StoryObj;

export const Badges: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
      <Badge kind="online">online</Badge>
      <Badge kind="degraded">degraded</Badge>
      <Badge kind="offline">offline</Badge>
      <Badge kind="accent">accent</Badge>
      <Badge kind="amber">amber</Badge>
      <Badge kind="error">error</Badge>
      <Badge kind="provisioning">provisioning</Badge>
    </div>
  ),
};

export const Toggles: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
      <Toggle on={true} onClick={() => {}} />
      <Toggle on={false} onClick={() => {}} />
    </div>
  ),
};

export const Pills: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
      <MonoPill>plain</MonoPill>
      <MonoPill accent>accent</MonoPill>
      <MonoPill green>green</MonoPill>
      <MonoPill cyan>cyan</MonoPill>
      <ViaPill>awg-eu → ams-relay-02</ViaPill>
    </div>
  ),
};

export const IconTiles: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
      <IconTile color="violet" icon="route" />
      <IconTile color="green" icon="tunnel" />
      <IconTile color="cyan" icon="activity" />
      <IconTile color="orange" icon="server" />
      <IconTile color="pink" icon="globe" />
      <IconTile color="amber" icon="alert" />
      <IconTile color="violet" size="lg" icon="shield" />
      <IconTile color="green" size="sm" icon="check" />
    </div>
  ),
};

export const Sparklines: Story = {
  render: () => (
    <div style={{ display: "flex", gap: 18, color: "var(--accent)" }}>
      <Sparkline seed={3} />
      <Sparkline seed={9} color="var(--green)" />
      <Sparkline seed={17} color="var(--cyan)" />
    </div>
  ),
};

export const Metrics: Story = {
  render: () => (
    <div className="metric-row" style={{ maxWidth: 920 }}>
      <Metric label="RX последний" value="5.42 MB" delta="+1.2 MB" sub="snapshot" icon="trending-down" tileColor="cyan" sparkSeed={21} />
      <Metric label="TX последний" value="3.18 MB" delta="-0.4 MB" sub="snapshot" icon="trending-up" tileColor="violet" sparkSeed={23} />
      <Metric label="Точек в окне" value={48} sub="за 6h" icon="activity" tileColor="green" sparkSeed={25} />
      <Metric label="Server ID" value={1} sub="agent_metrics" icon="server" tileColor="orange" sparkSeed={27} />
    </div>
  ),
};

export const SectionHeads: Story = {
  render: () => (
    <div style={{ maxWidth: 720 }}>
      <SectionHead title="Правила маршрутизации" count={6}>
        <Badge kind="accent">apply</Badge>
      </SectionHead>
    </div>
  ),
};
