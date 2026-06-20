import type { StorybookConfig } from "@storybook/react-vite";

// Storybook бандлит реальные TSX-компоненты Waygate (тот же Vite, что и приложение).
// Эти же stories конвертер /design-sync превращает в preview-карточки для claude.ai/design.
const config: StorybookConfig = {
  stories: ["../src/**/*.stories.@(tsx|mdx)"],
  addons: ["@storybook/addon-essentials"],
  framework: {
    name: "@storybook/react-vite",
    options: {},
  },
  core: { disableTelemetry: true },
};

export default config;
