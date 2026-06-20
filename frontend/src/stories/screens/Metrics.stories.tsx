import type { Meta, StoryObj } from "@storybook/react";

import { MetricsTab } from "../../pages/MetricsTab";
import { SERVER_ID } from "../_mock/data";
import { withMockAuth, withSeededQuery } from "../_mock/withSeededQuery";

const meta: Meta<typeof MetricsTab> = {
  title: "Screens/Metrics",
  component: MetricsTab,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => <div style={{ padding: 18, maxWidth: 1180 }}><Story /></div>,
    withSeededQuery(),
    withMockAuth,
  ],
  args: { serverId: SERVER_ID, showSpark: true },
};
export default meta;

type Story = StoryObj<typeof MetricsTab>;
export const Default: Story = {};
