import type { Meta, StoryObj } from "@storybook/react";

import { Topbar } from "../../components/Topbar";
import { mockServers } from "../_mock/data";
import { withMockAuth } from "../_mock/withSeededQuery";

// Topbar дёргает мутационные хуки (refresh/rotate/logout) — им хватает QueryClient
// из preview.tsx; залогиненного юзера даёт withMockAuth.
const meta: Meta<typeof Topbar> = {
  title: "Components/Topbar",
  component: Topbar,
  decorators: [withMockAuth],
  args: {
    onTLS: () => {},
    onUpdate: () => {},
    onEditSettings: () => {},
  },
};
export default meta;

type Story = StoryObj<typeof Topbar>;

export const WithServer: Story = {
  args: { server: mockServers[0] },
};

export const NoServer: Story = {
  args: { server: null },
};
