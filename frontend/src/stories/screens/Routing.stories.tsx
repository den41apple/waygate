import type { Meta, StoryObj } from "@storybook/react";

import { RoutingTab } from "../../pages/RoutingTab";
import { SERVER_ID } from "../_mock/data";
import { withMockAuth, withSeededQuery } from "../_mock/withSeededQuery";

// Полный экран Routing на реальном контейнере + seeded-данных.
const meta: Meta<typeof RoutingTab> = {
  title: "Screens/Routing",
  component: RoutingTab,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => <div style={{ padding: 18, maxWidth: 1180 }}><Story /></div>,
    withSeededQuery(),
    withMockAuth,
  ],
  args: {
    serverId: SERVER_ID,
    awgContainers: ["waygate-amnezia-client-eu", "waygate-amnezia-client-sg"],
    showSpark: true,
  },
};
export default meta;

type Story = StoryObj<typeof RoutingTab>;
export const Default: Story = {};
