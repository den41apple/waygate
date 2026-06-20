// Моки на реальных типах из api/types.ts — общий датасет для всех screen-stories.
// Один «активный» сервер SERVER_ID, на него завязаны directions/clients/dns/ipset/metrics.

import type {
  AwgClient,
  Direction,
  DnsRule,
  GeoList,
  IpsetGroup,
  MetricsPoint,
  MetricsRangeResponse,
  ServerSummary,
  TunnelsResponse,
} from "../../api/types";
import type { AuthUser } from "../../store/auth";

export const SERVER_ID = 1;

const now = Date.now();
const iso = (msAgo: number) => new Date(now - msAgo).toISOString();

export const mockUser: AuthUser = { username: "admin", is_admin: true };

export const mockServers: ServerSummary[] = [
  {
    id: SERVER_ID,
    host: "5.75.142.89",
    port: 7743,
    name: "ams-relay-02",
    version: "0.2.32",
    status: "degraded",
    region: "NL",
    awg_containers: ["waygate-amnezia-client-eu", "waygate-amnezia-client-sg"],
    added_at: iso(86_400_000 * 12),
    last_seen_at: iso(45_000),
    ssh_user: "root",
    ssh_port: 22,
    has_ssh_password: true,
    has_ssh_private_key: false,
  },
  {
    id: 2,
    host: "94.130.18.221",
    port: 7743,
    name: "fra-edge-01",
    version: "0.2.32",
    status: "online",
    region: "DE",
    awg_containers: ["waygate-amnezia-client-eu"],
    added_at: iso(86_400_000 * 30),
    last_seen_at: iso(20_000),
    ssh_user: "root",
    ssh_port: 22,
    has_ssh_password: true,
    has_ssh_private_key: false,
  },
  {
    id: 3,
    host: "165.232.99.5",
    port: 7743,
    name: "sin-relay-02",
    version: "0.2.31",
    status: "offline",
    region: "SG",
    awg_containers: [],
    added_at: iso(86_400_000 * 5),
    last_seen_at: iso(3_600_000),
    ssh_user: "root",
    ssh_port: 22,
    has_ssh_password: false,
    has_ssh_private_key: true,
  },
  {
    id: 4,
    host: "192.241.137.18",
    port: 7743,
    name: "nyc-edge-01",
    version: "0.2.32",
    status: "online",
    region: "US",
    awg_containers: ["waygate-amnezia-client-eu"],
    added_at: iso(86_400_000 * 8),
    last_seen_at: iso(12_000),
    ssh_user: "root",
    ssh_port: 22,
    has_ssh_password: true,
    has_ssh_private_key: false,
  },
];

export const mockGeoLists: GeoList[] = [
  {
    id: 1,
    country: "ru",
    name: "Russia",
    source_url: "https://www.ipdeny.com/ipblocks/data/aggregated/ru-aggregated.zone",
    ipv4_count: 20_422,
    ipv6_count: 1_204,
    custom_count: 0,
    last_synced_at: iso(1_800_000),
    status: "synced",
  },
  {
    id: 2,
    country: "cn",
    name: "China",
    source_url: "https://www.ipdeny.com/ipblocks/data/aggregated/cn-aggregated.zone",
    ipv4_count: 8_310,
    ipv6_count: 940,
    custom_count: 12,
    last_synced_at: iso(3_600_000 * 5),
    status: "stale",
  },
  {
    id: 3,
    country: "ir",
    name: "Iran",
    source_url: "https://www.ipdeny.com/ipblocks/data/aggregated/ir-aggregated.zone",
    ipv4_count: 3_188,
    ipv6_count: 210,
    custom_count: 0,
    last_synced_at: null,
    status: "error",
  },
];

export const mockDnsRules: DnsRule[] = [
  {
    id: 1,
    server_id: SERVER_ID,
    name: "streaming",
    domains: ["youtube.com", "googlevideo.com", "ytimg.com"],
    ipset_name: "dns-streaming",
    enabled: true,
  },
  {
    id: 2,
    server_id: SERVER_ID,
    name: "social",
    domains: ["instagram.com", "facebook.com", "cdninstagram.com"],
    ipset_name: "dns-social",
    enabled: false,
  },
];

export const mockIpsetGroups: IpsetGroup[] = [
  {
    id: 1,
    server_id: SERVER_ID,
    name: "corp-vpn",
    cidrs: ["10.8.0.0/24", "172.16.0.0/16", "10.99.0.0/24"],
    created_at: iso(86_400_000 * 4),
    updated_at: iso(86_400_000),
  },
  {
    id: 2,
    server_id: SERVER_ID,
    name: "home-lab",
    cidrs: ["192.168.1.0/24"],
    created_at: iso(86_400_000 * 2),
    updated_at: iso(86_400_000 * 2),
  },
];

export const mockAwgClients: AwgClient[] = [
  {
    id: 1,
    server_id: SERVER_ID,
    name: "eu",
    container_name: "waygate-amnezia-client-eu",
    interface_name: "awg-eu",
    status: "running",
    country: "NL",
    peer_endpoint: "5.75.142.89:51820",
    peer_pubkey: "kP3mN8vQxZ1aB2cD4eF5gH6iJ7kL8mN9oP0qR1sT2u=",
    interface_address: "10.13.13.2/32",
    created_at: iso(86_400_000 * 6),
    updated_at: iso(3_600_000),
  },
  {
    id: 2,
    server_id: SERVER_ID,
    name: "sg",
    container_name: "waygate-amnezia-client-sg",
    interface_name: "awg-sg",
    status: "stopped",
    country: "SG",
    peer_endpoint: "165.232.99.4:51820",
    peer_pubkey: "aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV1wX2yZ3aB4=",
    interface_address: "10.14.14.2/32",
    created_at: iso(86_400_000 * 3),
    updated_at: iso(86_400_000),
  },
];

export const mockDirections: Direction[] = [
  {
    id: 1,
    server_id: SERVER_ID,
    awg_client_id: 1,
    name: "russia → tunnel-eu",
    fwmark: 0x1001,
    table_id: 0x1001,
    via_interface: "awg-eu",
    via_gateway: "10.13.13.1",
    scope: "host",
    scope_target: null,
    enabled: true,
    is_default_egress: false,
    geo_list_ids: [1],
    dns_rule_ids: [],
    ipset_group_ids: [],
    created_at: iso(86_400_000 * 5),
    updated_at: iso(86_400_000),
  },
  {
    id: 2,
    server_id: SERVER_ID,
    awg_client_id: 1,
    name: "streaming → tunnel-eu",
    fwmark: 0x1002,
    table_id: 0x1002,
    via_interface: "awg-eu",
    via_gateway: "10.13.13.1",
    scope: "host",
    scope_target: null,
    enabled: true,
    is_default_egress: false,
    geo_list_ids: [],
    dns_rule_ids: [1],
    ipset_group_ids: [],
    created_at: iso(86_400_000 * 4),
    updated_at: iso(86_400_000),
  },
  {
    id: 3,
    server_id: SERVER_ID,
    awg_client_id: 2,
    name: "corp → tunnel-sg",
    fwmark: 0x1003,
    table_id: 0x1003,
    via_interface: "awg-sg",
    via_gateway: "10.14.14.1",
    scope: "container",
    scope_target: "amnezia-awg2",
    enabled: false,
    is_default_egress: false,
    geo_list_ids: [],
    dns_rule_ids: [],
    ipset_group_ids: [1],
    created_at: iso(86_400_000 * 2),
    updated_at: iso(86_400_000),
  },
];

// Синусоидальный поток с шумом — красивый график без рандома между рендерами.
function buildPoints(count: number, stepMs: number, base: number, amp: number): MetricsPoint[] {
  const points: MetricsPoint[] = [];
  for (let i = 0; i < count; i++) {
    const t = i / count;
    const wave = Math.sin(t * Math.PI * 3) * 0.5 + 0.5;
    const noise = ((i * 9301 + 49297) % 233280) / 233280;
    const factor = 0.4 + wave * 0.5 + noise * 0.1;
    points.push({
      timestamp: iso((count - 1 - i) * stepMs),
      rx_bytes: Math.round((base + amp * factor) * 1.0),
      tx_bytes: Math.round((base * 0.6 + amp * factor * 0.8)),
    });
  }
  return points;
}

export const mockMetrics: Record<string, MetricsRangeResponse> = {
  "1h": { range: "1h", points: buildPoints(40, 90_000, 2_000_000, 6_000_000) },
  "6h": { range: "6h", points: buildPoints(48, 450_000, 2_000_000, 6_000_000) },
  "24h": { range: "24h", points: buildPoints(48, 1_800_000, 2_000_000, 6_000_000) },
};

export const mockTunnels: TunnelsResponse = {
  tunnels: [
    {
      container_name: "waygate-amnezia-client-eu",
      interface: "awg-eu",
      status: "up",
      peers: [
        {
          public_key: "kP3mN8vQxZ1aB2cD4eF5gH6iJ7kL8mN9oP0qR1sT2u=",
          endpoint: "5.75.142.89:51820",
          last_handshake: iso(35_000),
          rx_bytes: 4_870_000,
          tx_bytes: 319_000,
        },
      ],
    },
    {
      container_name: "waygate-amnezia-client-sg",
      interface: "awg-sg",
      status: "degraded",
      peers: [
        {
          public_key: "aB1cD2eF3gH4iJ5kL6mN7oP8qR9sT0uV1wX2yZ3aB4=",
          endpoint: "165.232.99.4:51820",
          last_handshake: iso(220_000),
          rx_bytes: 1_240_000,
          tx_bytes: 88_000,
        },
      ],
    },
  ],
};
