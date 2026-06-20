import type { Meta, StoryObj } from "@storybook/react";

import { AddAwgClientModal } from "../../modals/AddAwgClientModal";
import { SERVER_ID } from "../_mock/data";

// Drag-n-drop .conf + preview распарсенных полей (парсинг локальный, без сети).
const meta: Meta<typeof AddAwgClientModal> = {
  title: "Modals/AddAwgClient",
  component: AddAwgClientModal,
  parameters: { layout: "fullscreen" },
  args: { serverId: SERVER_ID, onClose: () => {} },
};
export default meta;

type Story = StoryObj<typeof AddAwgClientModal>;
export const Default: Story = {};
