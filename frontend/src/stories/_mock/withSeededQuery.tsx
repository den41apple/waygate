// Декораторы для screen-stories: реальные контейнеры (RoutingTab, MetricsTab, ...)
// дёргают useQuery-хуки. Здесь мы создаём свежий QueryClient и через setQueryData
// сеем СЫРЫЕ response-shape'ы под те же ключи (с учётом select в хуках), а также
// проставляем auth-стор. Сеть не трогается: staleTime=Infinity + seeded data → нет
// запроса на маунте.

import type { Decorator } from "@storybook/react";
import { type QueryKey, QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { awgClientsKey } from "../../api/awgClients";
import { directionsKey } from "../../api/directions";
import { dnsKey } from "../../api/dns";
import { GEOIP_KEY } from "../../api/geoip";
import { ipsetGroupsKey } from "../../api/ipsetGroups";
import { metricsKey } from "../../api/metrics";
import { SERVERS_KEY } from "../../api/servers";
import { tunnelsKey } from "../../api/tunnels";
import { useAuthStore } from "../../store/auth";
import {
  SERVER_ID,
  mockAwgClients,
  mockDirections,
  mockDnsRules,
  mockGeoLists,
  mockIpsetGroups,
  mockMetrics,
  mockServers,
  mockTunnels,
  mockUser,
} from "./data";

export interface Seed {
  key: QueryKey;
  data: unknown;
}

/** Полный датасет под SERVER_ID — сырые shape'ы (как их отдаёт API до select). */
export function allSeeds(serverId: number = SERVER_ID): Seed[] {
  return [
    { key: SERVERS_KEY, data: { servers: mockServers } },
    { key: directionsKey(serverId), data: { directions: mockDirections } },
    { key: awgClientsKey(serverId), data: { clients: mockAwgClients } },
    { key: GEOIP_KEY, data: { lists: mockGeoLists } },
    { key: dnsKey(serverId), data: { rules: mockDnsRules } },
    { key: ipsetGroupsKey(serverId), data: { groups: mockIpsetGroups } },
    { key: tunnelsKey(serverId), data: mockTunnels },
    { key: metricsKey(serverId, "1h"), data: mockMetrics["1h"] },
    { key: metricsKey(serverId, "6h"), data: mockMetrics["6h"] },
    { key: metricsKey(serverId, "24h"), data: mockMetrics["24h"] },
  ];
}

/** Создаёт свежий QueryClient с предзаполненным кешем. Вложенный провайдер
 *  перекрывает глобальный из preview.tsx (inner QueryClientProvider wins). */
export function withSeededQuery(seeds: Seed[] = allSeeds()): Decorator {
  return (Story) => {
    const client = new QueryClient({
      defaultOptions: {
        queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
      },
    });
    for (const seed of seeds) client.setQueryData(seed.key, seed.data);
    return (
      <QueryClientProvider client={client}>
        <Story />
      </QueryClientProvider>
    );
  };
}

/** Проставляет залогиненного пользователя (нужно Topbar'у и экранам). */
export const withMockAuth: Decorator = (Story) => {
  useAuthStore.setState({
    token: "storybook-mock-token",
    user: mockUser,
    expiresAt: Math.floor(Date.now() / 1000) + 3600,
  });
  return <Story />;
};
