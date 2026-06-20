import type { Meta, StoryObj } from "@storybook/react";

import { StatusBar } from "../../components/StatusBar";
import { mockServers } from "../_mock/data";

// StatusBar читает ws-store (zustand, без провайдера) + server-проп.
const meta: Meta<typeof StatusBar> = {
  title: "Components/StatusBar",
  component: StatusBar,
  decorators: [(Story) => <div style={{ maxWidth: 880 }}><Story /></div>],
};
export default meta;

type Story = StoryObj<typeof StatusBar>;

export const WithServer: Story = {
  args: { server: mockServers[0] },
};

export const NoServer: Story = {
  args: { server: null },
};
