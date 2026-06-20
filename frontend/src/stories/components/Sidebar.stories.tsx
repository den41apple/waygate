import type { Meta, StoryObj } from "@storybook/react";

import { Sidebar } from "../../components/Sidebar";
import { mockServers } from "../_mock/data";

// Sidebar презентационный — принимает servers пропсом. Оборачиваем в фикс-ширину,
// т.к. в приложении он живёт в grid-колонке 260px.
const meta: Meta<typeof Sidebar> = {
  title: "Components/Sidebar",
  component: Sidebar,
  decorators: [
    (Story) => (
      <div style={{ width: 260, height: 600 }}>
        <Story />
      </div>
    ),
  ],
  args: {
    servers: mockServers,
    activeId: 1,
    onSelect: () => {},
    onAdd: () => {},
    onDelete: () => {},
    onUninstall: () => {},
  },
};
export default meta;

type Story = StoryObj<typeof Sidebar>;

export const WithServers: Story = {};

export const Empty: Story = {
  args: { servers: [], activeId: null },
};
