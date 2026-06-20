import type { Meta, StoryObj } from "@storybook/react";

import { TlsModal } from "../../modals/TlsModal";
import { SERVER_ID } from "../_mock/data";

const meta: Meta<typeof TlsModal> = {
  title: "Modals/Tls",
  component: TlsModal,
  parameters: { layout: "fullscreen" },
  args: { serverId: SERVER_ID, onClose: () => {} },
};
export default meta;

type Story = StoryObj<typeof TlsModal>;
export const Default: Story = {};
