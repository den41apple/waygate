import { useEffect } from "react";
import type { Decorator, Preview } from "@storybook/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

// Глобальная тема приложения — карточки выглядят 1-в-1 как Waygate.
import "../src/styles.css";

// Один общий QueryClient на сессию Storybook. Мутационные хуки (Topbar, формы) на
// рендере не стреляют, поэтому пустого провайдера достаточно; контейнеры с useQuery
// получают данные через seed-декоратор (см. src/stories/_mock/withSeededQuery).
const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
  },
});

// Тема через тулбар (dark по умолчанию, как в проде).
const withTheme: Decorator = (Story, context) => {
  const theme = (context.globals.theme as string) ?? "dark";
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  return <Story />;
};

const withQuery: Decorator = (Story) => (
  <QueryClientProvider client={queryClient}>
    <Story />
  </QueryClientProvider>
);

const preview: Preview = {
  parameters: {
    backgrounds: { disable: true },
    layout: "padded",
    controls: { matchers: { color: /(background|color)$/i, date: /Date$/i } },
  },
  globalTypes: {
    theme: {
      description: "Тема Waygate",
      defaultValue: "dark",
      toolbar: {
        title: "Theme",
        icon: "circlehollow",
        items: [
          { value: "dark", title: "Dark" },
          { value: "light", title: "Light" },
        ],
        dynamicTitle: true,
      },
    },
  },
  decorators: [withTheme, withQuery],
};

export default preview;
