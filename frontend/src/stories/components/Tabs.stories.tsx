import { useState } from "react";
import type { Meta, StoryObj } from "@storybook/react";

import { type TabItem, Tabs } from "../../components/Tabs";
import type { TabId } from "../../store/ui";

const ITEMS: TabItem[] = [
  { id: "routing", label: "Routing", icon: "route", count: 6 },
  { id: "tunnels", label: "Tunnels", icon: "tunnel", count: 3 },
  { id: "lists", label: "Lists", icon: "list", count: 12 },
  { id: "metrics", label: "Metrics", icon: "activity", count: null },
];

function TabsDemo() {
  const [tab, setTab] = useState<TabId>("routing");
  return <Tabs tab={tab} onTab={setTab} tabs={ITEMS} />;
}

const meta: Meta<typeof TabsDemo> = {
  title: "Components/Tabs",
  component: TabsDemo,
  decorators: [(Story) => <div style={{ maxWidth: 880 }}><Story /></div>],
};
export default meta;

type Story = StoryObj<typeof TabsDemo>;
export const Default: Story = {};
