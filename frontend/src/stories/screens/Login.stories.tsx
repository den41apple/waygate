import type { Meta, StoryObj } from "@storybook/react";

import { LoginPage } from "../../pages/LoginPage";

// LoginPage использует useLogin (мутация — хватает QueryClient из preview) + auth-стор.
const meta: Meta<typeof LoginPage> = {
  title: "Screens/Login",
  component: LoginPage,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof LoginPage>;
export const Default: Story = {};
