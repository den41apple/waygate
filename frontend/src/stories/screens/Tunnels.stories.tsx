import type { Meta, StoryObj } from "@storybook/react";

import { TunnelsTab } from "../../pages/TunnelsTab";
import { SERVER_ID } from "../_mock/data";
import { withMockAuth, withSeededQuery } from "../_mock/withSeededQuery";

const meta: Meta<typeof TunnelsTab> = {
  title: "Screens/Tunnels",
  component: TunnelsTab,
  parameters: { layout: "fullscreen" },
  decorators: [
    (Story) => <div style={{ padding: 18, maxWidth: 1180 }}><Story /></div>,
    withSeededQuery(),
    withMockAuth,
  ],
  args: { serverId: SERVER_ID, showSpark: true },
};
export default meta;

type Story = StoryObj<typeof TunnelsTab>;
export const Default: Story = {};
