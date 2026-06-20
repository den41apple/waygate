import type { Meta, StoryObj } from "@storybook/react";

import { AddServerModal } from "../../modals/AddServerModal";
import { withMockAuth } from "../_mock/withSeededQuery";

// Онбординг-модалка: stepper + форма SSH-кредов (шаг «Подключение»).
const meta: Meta<typeof AddServerModal> = {
  title: "Modals/AddServer",
  component: AddServerModal,
  parameters: { layout: "fullscreen" },
  decorators: [withMockAuth],
  args: { onClose: () => {} },
};
export default meta;

type Story = StoryObj<typeof AddServerModal>;
export const Connect: Story = {};
